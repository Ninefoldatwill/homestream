"""
messaging_gateway.py 测试 — 多平台IM网关验证

覆盖范围：
- UnifiedMessage 格式与转换
- SlashCommandRouter 命令路由
- SessionManager 会话管理
- MessageAdapter 适配器(ws/webhook/cli)
- MessagingGateway 协调器
"""

import time

import pytest

from messaging_gateway import (
    CLIAdapter,
    MessageAdapter,
    MessageRole,
    MessageType,
    MessagingGateway,
    Session,
    SessionManager,
    SessionResetMode,
    SlashCommand,
    SlashCommandRouter,
    UnifiedMessage,
    WebhookAdapter,
    WebSocketAdapter,
    create_gateway,
    format_message_for_platform,
)

# ============================================================
# UnifiedMessage 测试
# ============================================================


class TestUnifiedMessage:
    """统一消息格式测试。"""

    def test_basic_message(self):
        """基本消息创建。"""
        msg = UnifiedMessage(text="你好")
        assert msg.text == "你好"
        assert msg.role == MessageRole.USER
        assert msg.type == MessageType.TEXT
        assert msg.id.startswith("msg_")

    def test_custom_message(self):
        """自定义消息。"""
        msg = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            type=MessageType.COMMAND,
            from_platform="ws",
            from_user="user1",
            text="/status",
            session_id="ses_123",
        )
        assert msg.role == MessageRole.ASSISTANT
        assert msg.from_platform == "ws"

    def test_is_command(self):
        """斜杠命令检测。"""
        cmd_msg = UnifiedMessage(text="/new")
        assert cmd_msg.is_command() is True

        normal_msg = UnifiedMessage(text="你好世界")
        assert normal_msg.is_command() is False

    def test_extract_command(self):
        """命令提取。"""
        msg = UnifiedMessage(text="/model L2")
        cmd, args = msg.extract_command()
        assert cmd == "/model"
        assert args == "L2"

        msg = UnifiedMessage(text="/help")
        cmd, args = msg.extract_command()
        assert cmd == "/help"
        assert args == ""

    def test_to_dict(self):
        """转为字典。"""
        msg = UnifiedMessage(text="test")
        d = msg.to_dict()
        assert "text" in d
        assert d["text"] == "test"

    def test_message_role_enum(self):
        """消息角色枚举。"""
        assert MessageRole.USER.value == "user"
        assert MessageRole.ASSISTANT.value == "assistant"
        assert MessageRole.SYSTEM.value == "system"

    def test_message_type_enum(self):
        """消息类型枚举。"""
        assert MessageType.TEXT.value == "text"
        assert MessageType.COMMAND.value == "command"
        assert MessageType.IMAGE.value == "image"


# ============================================================
# SlashCommandRouter 测试
# ============================================================


class TestSlashCommandRouter:
    """斜杠命令路由测试。"""

    def test_default_commands(self):
        """默认命令集注册。"""
        router = SlashCommandRouter()
        commands = router.list_commands()
        assert len(commands) >= 7
        names = [c["name"] for c in commands]
        assert "/new" in names
        assert "/help" in names
        assert "/status" in names
        assert "/model" in names
        assert "/stop" in names
        assert "/mode" in names
        assert "/skill" in names

    def test_route_command(self):
        """命令路由处理。"""
        router = SlashCommandRouter()
        msg = UnifiedMessage(
            from_platform="ws",
            from_user="user1",
            text="/new",
            session_id="ses_1",
        )
        response = router.route(msg)
        assert response is not None
        assert "重置" in response.text or "新会话" in response.text

    def test_route_help_command(self):
        """帮助命令。"""
        router = SlashCommandRouter()
        msg = UnifiedMessage(
            from_platform="ws",
            from_user="user1",
            text="/help",
        )
        response = router.route(msg)
        assert response is not None
        assert "/new" in response.text

    def test_route_model_command(self):
        """模型切换命令。"""
        router = SlashCommandRouter()
        msg = UnifiedMessage(
            from_platform="ws",
            from_user="user1",
            text="/model L2",
        )
        response = router.route(msg)
        assert response is not None
        assert "L2" in response.text

    def test_route_unknown_command(self):
        """未知命令。"""
        router = SlashCommandRouter()
        msg = UnifiedMessage(
            from_platform="ws",
            from_user="user1",
            text="/unknown_cmd",
        )
        response = router.route(msg)
        assert response is not None
        assert response.type == MessageType.ERROR
        assert "未知命令" in response.text

    def test_route_non_command(self):
        """非命令消息不路由。"""
        router = SlashCommandRouter()
        msg = UnifiedMessage(text="你好世界")
        response = router.route(msg)
        assert response is None

    def test_register_custom_command(self):
        """注册自定义命令。"""
        router = SlashCommandRouter()
        router.register("/custom", "自定义命令", lambda msg, args: f"自定义响应: {args}")
        msg = UnifiedMessage(
            from_platform="ws",
            from_user="user1",
            text="/custom 参数1",
        )
        response = router.route(msg)
        assert response is not None
        assert "自定义响应" in response.text

    def test_command_with_platform_filter(self):
        """平台过滤命令。"""
        router = SlashCommandRouter()
        router.register(
            "/admin",
            "管理员命令",
            lambda msg, args: "管理操作",
            platforms={"ws"},
            requires_auth=True,
        )
        # ws平台可用
        msg = UnifiedMessage(from_platform="ws", text="/admin")
        response = router.route(msg)
        assert "管理操作" in response.text

        # cli平台不可用
        msg = UnifiedMessage(from_platform="cli", text="/admin")
        response = router.route(msg)
        assert response.type == MessageType.ERROR
        assert "不支持" in response.text


# ============================================================
# SessionManager 测试
# ============================================================


class TestSessionManager:
    """会话管理器测试。"""

    def test_create_session(self):
        """创建会话。"""
        manager = SessionManager()
        session = manager.create_session("ws", "user1")
        assert session.session_id.startswith("ses_")
        assert session.platform == "ws"
        assert session.user_id == "user1"
        assert session.is_active is True

    def test_get_or_create(self):
        """获取或创建会话。"""
        manager = SessionManager()
        session1 = manager.get_or_create("ws", "user1")
        session2 = manager.get_or_create("ws", "user1")
        assert session1.session_id == session2.session_id

    def test_get_or_create_different_user(self):
        """不同用户创建不同会话。"""
        manager = SessionManager()
        session1 = manager.get_or_create("ws", "user1")
        session2 = manager.get_or_create("ws", "user2")
        assert session1.session_id != session2.session_id

    def test_update_activity(self):
        """更新活跃时间。"""
        manager = SessionManager()
        session = manager.create_session("ws", "user1")
        old_time = session.last_active
        time.sleep(0.01)
        manager.update_activity(session.session_id)
        assert session.last_active > old_time
        assert session.message_count == 1

    def test_reset_session(self):
        """重置会话。"""
        manager = SessionManager()
        session = manager.create_session("ws", "user1")
        session.context["key"] = "value"
        session.message_count = 10
        ok, msg = manager.reset_session(session.session_id)
        assert ok is True
        assert session.message_count == 0
        assert len(session.context) == 0

    def test_reset_nonexistent(self):
        """重置不存在会话。"""
        manager = SessionManager()
        ok, msg = manager.reset_session("nonexistent")
        assert ok is False

    def test_check_idle_sessions(self):
        """检查空闲会话。"""
        manager = SessionManager(reset_mode=SessionResetMode.IDLE)
        session = manager.create_session("ws", "user1")
        # 手动设置last_active为很久以前
        session.last_active = time.time() - 300 * 60  # 5小时前
        idle = manager.check_idle_sessions()
        assert len(idle) >= 1

    def test_daily_mode_no_idle_check(self):
        """daily模式不检查空闲。"""
        manager = SessionManager(reset_mode=SessionResetMode.DAILY)
        session = manager.create_session("ws", "user1")
        session.last_active = time.time() - 300 * 60
        idle = manager.check_idle_sessions()
        assert len(idle) == 0

    def test_lru_eviction(self):
        """LRU淘汰机制。"""
        manager = SessionManager()
        manager.MAX_SESSIONS = 3
        s1 = manager.create_session("ws", "user1")
        s2 = manager.create_session("ws", "user2")
        s3 = manager.create_session("ws", "user3")
        # 创建第4个 → 淘汰第1个
        s4 = manager.create_session("ws", "user4")
        stats = manager.stats()
        assert stats["total_sessions"] == 3

    def test_stats(self):
        """会话统计。"""
        manager = SessionManager()
        manager.create_session("ws", "user1")
        stats = manager.stats()
        assert stats["total_sessions"] == 1
        assert stats["active_sessions"] == 1


# ============================================================
# MessageAdapter 测试
# ============================================================


class TestWebSocketAdapter:
    """WebSocket适配器测试。"""

    def test_receive_dict(self):
        """接收字典消息。"""
        adapter = WebSocketAdapter()
        msg = adapter.receive(
            {
                "role": "user",
                "text": "你好",
                "from": "user1",
                "session_id": "ses_1",
            }
        )
        assert msg.from_platform == "ws"
        assert msg.text == "你好"

    def test_receive_string(self):
        """接收字符串消息。"""
        adapter = WebSocketAdapter()
        msg = adapter.receive("纯文本消息")
        assert msg.from_platform == "ws"
        assert msg.text == "纯文本消息"

    def test_send(self):
        """发送消息。"""
        adapter = WebSocketAdapter()
        msg = UnifiedMessage(text="回复消息", session_id="ses_1")
        result = adapter.send(msg)
        assert result["type"] == "message"
        assert result["text"] == "回复消息"

    def test_validate_dict(self):
        """验证字典消息。"""
        adapter = WebSocketAdapter()
        assert adapter.validate({"text": "ok"}) is True
        assert adapter.validate({"other": "no_text"}) is False

    def test_validate_string(self):
        """验证字符串消息。"""
        adapter = WebSocketAdapter()
        assert adapter.validate("hello") is True
        assert adapter.validate("") is False


class TestWebhookAdapter:
    """Webhook适配器测试。"""

    def test_receive_json(self):
        """接收Webhook JSON。"""
        adapter = WebhookAdapter()
        msg = adapter.receive(
            {
                "content": "webhook消息",
                "user_id": "ext_user",
                "session_id": "ses_ext",
            }
        )
        assert msg.from_platform == "webhook"
        assert msg.text == "webhook消息"

    def test_send(self):
        """发送Webhook响应。"""
        adapter = WebhookAdapter()
        msg = UnifiedMessage(text="回复", session_id="ses_1")
        result = adapter.send(msg)
        assert result["status"] == "ok"
        assert result["response"] == "回复"

    def test_validate(self):
        """验证Webhook消息。"""
        adapter = WebhookAdapter()
        assert adapter.validate({"content": "ok"}) is True
        assert adapter.validate({"other": "no_content"}) is False


class TestCLIAdapter:
    """CLI适配器测试。"""

    def test_receive_normal(self):
        """接收普通CLI输入。"""
        adapter = CLIAdapter()
        msg = adapter.receive("你好世界")
        assert msg.from_platform == "cli"
        assert msg.text == "你好世界"
        assert msg.type == MessageType.TEXT

    def test_receive_command(self):
        """接收CLI命令输入。"""
        adapter = CLIAdapter()
        msg = adapter.receive("/status")
        assert msg.type == MessageType.COMMAND

    def test_send(self):
        """发送CLI输出。"""
        adapter = CLIAdapter()
        msg = UnifiedMessage(
            role=MessageRole.ASSISTANT,
            text="这是AI的回复",
        )
        result = adapter.send(msg)
        assert "[AI]" in result

    def test_send_system(self):
        """发送系统消息。"""
        adapter = CLIAdapter()
        msg = UnifiedMessage(
            role=MessageRole.SYSTEM,
            text="系统通知",
        )
        result = adapter.send(msg)
        assert "[系统]" in result

    def test_validate(self):
        """验证CLI输入。"""
        adapter = CLIAdapter()
        assert adapter.validate("hello") is True
        assert adapter.validate("") is False


# ============================================================
# MessagingGateway 协调器测试
# ============================================================


class TestMessagingGateway:
    """消息网关协调器测试。"""

    def test_default_adapters(self):
        """默认3个适配器已注册。"""
        gw = MessagingGateway()
        adapters = gw.list_adapters()
        assert "ws" in adapters
        assert "webhook" in adapters
        assert "cli" in adapters

    def test_process_ws_message(self):
        """处理WebSocket消息。"""
        gw = MessagingGateway()
        result = gw.process_message("ws", {"text": "你好", "from": "user1"})
        assert result.from_platform == "ws"
        assert result.session_id != ""

    def test_process_cli_command(self):
        """处理CLI命令消息。"""
        gw = MessagingGateway()
        result = gw.process_message("cli", "/help")
        assert result.from_platform == "cli"
        # 命令被路由后返回命令响应

    def test_process_webhook_message(self):
        """处理Webhook消息。"""
        gw = MessagingGateway()
        result = gw.process_message("webhook", {"content": "外部消息"})
        assert result.from_platform == "webhook"

    def test_process_unknown_platform(self):
        """不支持的平台。"""
        gw = MessagingGateway()
        result = gw.process_message("telegram", {"text": "消息"})
        assert result.type == MessageType.ERROR
        assert "不支持" in result.text

    def test_process_invalid_message(self):
        """无效消息。"""
        gw = MessagingGateway()
        result = gw.process_message("ws", {})
        assert result.type == MessageType.ERROR

    def test_send_response(self):
        """发送回复。"""
        gw = MessagingGateway()
        msg = UnifiedMessage(text="回复", session_id="ses_1")
        result = gw.send_response("ws", msg)
        assert result["type"] == "message"

    def test_register_custom_adapter(self):
        """注册自定义适配器。"""
        gw = MessagingGateway()

        class CustomAdapter(MessageAdapter):
            platform_name = "custom"

            def receive(self, raw):
                return UnifiedMessage(from_platform="custom", text=str(raw))

            def send(self, msg):
                return f"custom: {msg.text}"

            def validate(self, raw):
                return True

        gw.register_adapter(CustomAdapter())
        assert "custom" in gw.list_adapters()

    def test_register_custom_command(self):
        """注册自定义命令。"""
        gw = MessagingGateway()
        gw.register_command("/ping", "Ping测试", lambda msg, args: "pong!")
        commands = gw.list_commands()
        names = [c["name"] for c in commands]
        assert "/ping" in names

    def test_stats(self):
        """网关统计。"""
        gw = MessagingGateway()
        gw.process_message("ws", {"text": "你好"})
        stats = gw.stats()
        assert len(stats["adapters"]) >= 3
        assert stats["commands"] >= 7

    def test_create_gateway(self):
        """便捷创建网关。"""
        gw = create_gateway(SessionResetMode.COMBINED)
        assert isinstance(gw, MessagingGateway)

    def test_format_message(self):
        """便捷格式化消息。"""
        msg = UnifiedMessage(text="回复内容")
        ws_result = format_message_for_platform(msg, "ws")
        assert ws_result["type"] == "message"

        cli_result = format_message_for_platform(msg, "cli")
        assert "回复内容" in cli_result
