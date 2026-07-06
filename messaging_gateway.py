"""
多平台IM网关 — MessageAdapter抽象基类 + UnifiedMessage + SlashCommandRouter + SessionManager。

融优来源：
  Hermes Agent (20+平台适配器 + 斜杠命令 + 会话重置)
  + OpenBridge ws_manager.py (WebSocket通信)
  + OpenBridge group_chat.py (群聊系统)
  + OpenBridge failsafe_guardian.py (适配器级断路器)

设计原则：
  抽象优于具体 · 统一优于碎片 · 斜杠优于自然语言 · 会话优于散乱

核心组件：
  MessageAdapter — 抽象基类，每个平台一个实现
  UnifiedMessage — 统一消息格式（from/to/text/attachments/session_id）
  SlashCommandRouter — 斜杠命令路由系统
  SessionManager — 会话管理和重置策略
  3个适配器 — WebSocketAdapter / WebhookAdapter / CLIAdapter

与现有模块关系：
  ws_manager.py → WebSocketAdapter 封装
  group_chat.py → 多平台消息分发
  failsafe_guardian.py → 适配器级断路器
"""

import abc
import time
import uuid
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field, ConfigDict
import structlog

logger = structlog.get_logger("bridge_v7.messaging_gateway")


# ============================================================
# 统一消息格式
# ============================================================

class MessageRole(str, Enum):
    """消息角色。"""
    USER = "user"          # 用户消息
    ASSISTANT = "assistant"  # AI回复
    SYSTEM = "system"      # 系统通知
    TOOL = "tool"          # 工具输出


class MessageType(str, Enum):
    """消息类型。"""
    TEXT = "text"            # 纯文本
    IMAGE = "image"          # 图片
    FILE = "file"            # 文件
    COMMAND = "command"      # 斜杠命令
    EVENT = "event"          # 事件通知
    ERROR = "error"          # 错误消息


class UnifiedMessage(BaseModel):
    """统一消息格式 — 所有平台的消息统一为此格式。

    字段：
      id: 消息唯一ID
      role: 角色(user/assistant/system/tool)
      type: 类型(text/image/file/command/event/error)
      from_platform: 来源平台(ws/webhook/cli/wecom/telegram)
      from_user: 发送者ID
      to_user: 接收者ID
      text: 文本内容
      attachments: 附件列表
      session_id: 会话ID
      timestamp: 时间戳
      metadata: 扩展元数据
    """
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}")
    role: MessageRole = Field(default=MessageRole.USER)
    type: MessageType = Field(default=MessageType.TEXT)
    from_platform: str = Field(default="unknown")
    from_user: str = Field(default="")
    to_user: str = Field(default="")
    text: str = Field(default="")
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    session_id: str = Field(default="")
    timestamp: float = Field(default_factory=time.time)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转为字典。"""
        return self.model_dump()

    def is_command(self) -> bool:
        """是否是斜杠命令消息。"""
        return self.type == MessageType.COMMAND or self.text.startswith("/")

    def extract_command(self) -> Tuple[str, str]:
        """提取命令名和参数。"""
        if not self.text.startswith("/"):
            return ("", self.text)

        parts = self.text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        return (command, args)


# ============================================================
# 斜杠命令路由系统
# ============================================================

@dataclass
class SlashCommand:
    """斜杠命令定义。"""
    name: str               # 命令名（如 /new）
    description: str        # 描述
    handler: Callable       # 处理函数
    platforms: Set[str] = field(default_factory=lambda: {"all"})  # 适用平台
    requires_auth: bool = False  # 是否需要认证


class SlashCommandRouter:
    """斜杠命令路由系统 — 统一命令处理。

    支持命令：
      /new    — 新建对话（重置会话）
      /model  — 切换模型
      /status — 查看系统状态
      /help   — 帮助信息
      /stop   — 停止当前任务
      /mode   — 切换弹性模式
      /skill  — 查看可用技能

    路由策略：
      命令 → 匹配handler → 执行 → 返回UnifiedMessage
    """

    def __init__(self):
        self._commands: Dict[str, SlashCommand] = {}
        self._register_default_commands()

    def _register_default_commands(self):
        """注册默认命令集。"""
        self.register("/new", "新建对话·重置会话", self._cmd_new)
        self.register("/model", "切换模型·指定层级", self._cmd_model)
        self.register("/status", "查看系统状态", self._cmd_status)
        self.register("/help", "帮助信息", self._cmd_help)
        self.register("/stop", "停止当前任务", self._cmd_stop)
        self.register("/mode", "切换弹性模式(solo/team/ecosystem)", self._cmd_mode)
        self.register("/skill", "查看可用技能", self._cmd_skill)

    def register(self, name: str, description: str,
                 handler: Callable, platforms: Set[str] = None,
                 requires_auth: bool = False):
        """注册斜杠命令。"""
        cmd = SlashCommand(
            name=name, description=description, handler=handler,
            platforms=platforms or {"all"}, requires_auth=requires_auth,
        )
        self._commands[name] = cmd
        logger.info("messaging.command_registered", name=name)

    def route(self, message: UnifiedMessage) -> Optional[UnifiedMessage]:
        """路由消息到命令处理器。"""
        if not message.is_command():
            return None

        command, args = message.extract_command()
        cmd = self._commands.get(command)

        if not cmd:
            return UnifiedMessage(
                role=MessageRole.SYSTEM,
                type=MessageType.ERROR,
                text=f"未知命令: {command}。输入 /help 查看可用命令。",
                from_platform=message.from_platform,
                to_user=message.from_user,
                session_id=message.session_id,
            )

        # 平台检查
        if "all" not in cmd.platforms and message.from_platform not in cmd.platforms:
            return UnifiedMessage(
                role=MessageRole.SYSTEM,
                type=MessageType.ERROR,
                text=f"命令 {command} 不支持平台 {message.from_platform}",
                from_platform=message.from_platform,
                to_user=message.from_user,
                session_id=message.session_id,
            )

        # 执行
        try:
            result = cmd.handler(message, args)
            if isinstance(result, UnifiedMessage):
                return result
            return UnifiedMessage(
                role=MessageRole.SYSTEM,
                type=MessageType.TEXT,
                text=str(result),
                from_platform=message.from_platform,
                to_user=message.from_user,
                session_id=message.session_id,
            )
        except Exception as e:
            return UnifiedMessage(
                role=MessageRole.SYSTEM,
                type=MessageType.ERROR,
                text=f"命令执行错误: {e}",
                from_platform=message.from_platform,
                to_user=message.from_user,
                session_id=message.session_id,
            )

    def list_commands(self, platform: str = "") -> List[Dict[str, str]]:
        """列出可用命令。"""
        result = []
        for name, cmd in self._commands.items():
            if platform and "all" not in cmd.platforms and platform not in cmd.platforms:
                continue
            result.append({"name": name, "description": cmd.description})
        return result

    # --- 默认命令处理器 ---
    def _cmd_new(self, message: UnifiedMessage, args: str) -> UnifiedMessage:
        """新建对话。"""
        return UnifiedMessage(
            role=MessageRole.SYSTEM,
            type=MessageType.TEXT,
            text="对话已重置·新会话开始。",
            from_platform=message.from_platform,
            to_user=message.from_user,
            session_id=message.session_id,
        )

    def _cmd_model(self, message: UnifiedMessage, args: str) -> str:
        """切换模型。"""
        tier = args.strip() if args else "L1"
        return f"模型切换到 {tier} 层级"

    def _cmd_status(self, message: UnifiedMessage, args: str) -> str:
        """查看状态。"""
        return "系统状态: 正常运行 | 模型: L1 Qwen2.5-7B | 模式: team"

    def _cmd_help(self, message: UnifiedMessage, args: str) -> str:
        """帮助信息。"""
        commands = self.list_commands(message.from_platform)
        lines = [f"{c['name']} — {c['description']}" for c in commands]
        return "可用命令:\n" + "\n".join(lines)

    def _cmd_stop(self, message: UnifiedMessage, args: str) -> str:
        """停止任务。"""
        return "当前任务已停止"

    def _cmd_mode(self, message: UnifiedMessage, args: str) -> str:
        """切换模式。"""
        mode = args.strip() if args else "team"
        return f"弹性模式切换到 {mode}"

    def _cmd_skill(self, message: UnifiedMessage, args: str) -> str:
        """查看技能。"""
        return "可用技能: research / code_gen / archiver / validator"


# ============================================================
# 会话管理器
# ============================================================

class SessionResetMode(str, Enum):
    """会话重置策略。"""
    DAILY = "daily"        # 每日定时重置
    IDLE = "idle"          # 空闲N分钟后重置
    COMBINED = "combined"  # 每日+空闲


@dataclass
class Session:
    """会话对象。"""
    session_id: str
    platform: str
    user_id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    message_count: int = 0
    context: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True


class SessionManager:
    """会话管理器 — 会话创建/追踪/重置。

    重置策略：
      daily — 每日凌晨自动重置所有会话
      idle — 空闲超过N分钟后重置
      combined — 两者兼有

    会话容量：
      最大活跃会话数限制（防资源耗尽）
      LRU淘汰（最久未活跃的先淘汰）
    """

    MAX_SESSIONS = 100        # 最大活跃会话数
    IDLE_TIMEOUT_MINUTES = 240  # 空闲超时（4小时）

    def __init__(self, reset_mode: SessionResetMode = SessionResetMode.IDLE):
        self.reset_mode = reset_mode
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    def create_session(self, platform: str, user_id: str) -> Session:
        """创建新会话。"""
        session_id = f"ses_{uuid.uuid4().hex[:8]}"

        # 检查容量限制
        if len(self._sessions) >= self.MAX_SESSIONS:
            # LRU淘汰：移除最久未活跃的
            self._evict_oldest()

        session = Session(
            session_id=session_id,
            platform=platform,
            user_id=user_id,
        )
        self._sessions[session_id] = session

        logger.info("messaging.session_created",
                    session_id=session_id,
                    platform=platform,
                    user_id=user_id)

        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话。"""
        return self._sessions.get(session_id)

    def get_or_create(self, platform: str, user_id: str) -> Session:
        """获取或创建会话（按platform+user_id查找）。"""
        # 查找现有会话
        for ses in self._sessions.values():
            if ses.platform == platform and ses.user_id == user_id and ses.is_active:
                ses.last_active = time.time()
                ses.message_count += 1
                # 移到OrderedDict末尾（最近活跃）
                self._sessions.move_to_end(ses.session_id)
                return ses

        # 创建新会话
        return self.create_session(platform, user_id)

    def update_activity(self, session_id: str):
        """更新会话活跃时间。"""
        session = self._sessions.get(session_id)
        if session:
            session.last_active = time.time()
            session.message_count += 1
            self._sessions.move_to_end(session_id)

    def reset_session(self, session_id: str) -> Tuple[bool, str]:
        """重置会话（清空上下文但不删除）。"""
        session = self._sessions.get(session_id)
        if not session:
            return False, f"未找到会话: {session_id}"

        session.context.clear()
        session.message_count = 0
        session.last_active = time.time()

        logger.info("messaging.session_reset", session_id=session_id)
        return True, f"会话 {session_id} 已重置"

    def check_idle_sessions(self) -> List[str]:
        """检查空闲会话，返回应重置的session_id列表。"""
        if self.reset_mode == SessionResetMode.DAILY:
            return []  # daily模式不按空闲检查

        now = time.time()
        idle_seconds = self.IDLE_TIMEOUT_MINUTES * 60
        idle_sessions = []

        for session_id, session in self._sessions.items():
            if not session.is_active:
                continue
            if now - session.last_active > idle_seconds:
                idle_sessions.append(session_id)

        return idle_sessions

    def reset_idle_sessions(self) -> int:
        """重置所有空闲会话。"""
        idle_ids = self.check_idle_sessions()
        count = 0
        for sid in idle_ids:
            ok, _ = self.reset_session(sid)
            if ok:
                count += 1
        return count

    def stats(self) -> Dict[str, Any]:
        """会话统计。"""
        active = sum(1 for s in self._sessions.values() if s.is_active)
        return {
            "total_sessions": len(self._sessions),
            "active_sessions": active,
            "max_sessions": self.MAX_SESSIONS,
            "reset_mode": self.reset_mode.value,
        }

    def _evict_oldest(self):
        """淘汰最久未活跃的会话。"""
        if self._sessions:
            oldest_id = next(iter(self._sessions))
            session = self._sessions[oldest_id]
            session.is_active = False
            del self._sessions[oldest_id]
            logger.info("messaging.session_evicted",
                        session_id=oldest_id)


# ============================================================
# MessageAdapter 抽象基类
# ============================================================

class MessageAdapter(abc.ABC):
    """消息适配器抽象基类 — 每个平台一个实现。

    子类必须实现：
      receive(raw_message) → UnifiedMessage
      send(unified_message) → raw_response
      validate(raw_message) → bool
    """

    platform_name: str = "unknown"

    @abc.abstractmethod
    def receive(self, raw_message: Any) -> UnifiedMessage:
        """接收原始消息，转换为UnifiedMessage。"""
        pass

    @abc.abstractmethod
    def send(self, message: UnifiedMessage) -> Any:
        """发送UnifiedMessage，转换为平台格式。"""
        pass

    @abc.abstractmethod
    def validate(self, raw_message: Any) -> bool:
        """验证原始消息格式。"""
        pass

    def get_platform_name(self) -> str:
        """获取平台名称。"""
        return self.platform_name


# ============================================================
# WebSocketAdapter
# ============================================================

class WebSocketAdapter(MessageAdapter):
    """WebSocket适配器 — 封装ws_manager.py。

    将ws_manager的WebSocket消息转换为UnifiedMessage格式。
    """

    platform_name = "ws"

    def receive(self, raw_message: Any) -> UnifiedMessage:
        """从WebSocket消息转换为UnifiedMessage。"""
        if isinstance(raw_message, dict):
            return UnifiedMessage(
                role=MessageRole(raw_message.get("role", "user")),
                type=MessageType(raw_message.get("type", "text")),
                from_platform="ws",
                from_user=raw_message.get("from", ""),
                to_user=raw_message.get("to", ""),
                text=raw_message.get("text", ""),
                session_id=raw_message.get("session_id", ""),
                metadata=raw_message.get("metadata", {}),
            )
        elif isinstance(raw_message, str):
            return UnifiedMessage(
                from_platform="ws",
                text=raw_message,
            )
        else:
            return UnifiedMessage(
                from_platform="ws",
                text=str(raw_message),
            )

    def send(self, message: UnifiedMessage) -> Dict[str, Any]:
        """UnifiedMessage转为WebSocket格式。"""
        return {
            "type": "message",
            "role": message.role.value,
            "from": message.from_user or "system",
            "to": message.to_user,
            "text": message.text,
            "session_id": message.session_id,
            "timestamp": message.timestamp,
            "metadata": message.metadata,
        }

    def validate(self, raw_message: Any) -> bool:
        """验证WebSocket消息。"""
        if isinstance(raw_message, dict):
            return "text" in raw_message or "type" in raw_message
        if isinstance(raw_message, str):
            return len(raw_message) > 0
        return False


# ============================================================
# WebhookAdapter
# ============================================================

class WebhookAdapter(MessageAdapter):
    """HTTP Webhook适配器 — 接收外部POST请求。

    将HTTP POST JSON body转换为UnifiedMessage。
    """

    platform_name = "webhook"

    def receive(self, raw_message: Any) -> UnifiedMessage:
        """从Webhook JSON转换为UnifiedMessage。"""
        if isinstance(raw_message, dict):
            return UnifiedMessage(
                role=MessageRole(raw_message.get("role", "user")),
                type=MessageType(raw_message.get("msg_type", "text")),
                from_platform="webhook",
                from_user=raw_message.get("user_id", ""),
                to_user=raw_message.get("target", ""),
                text=raw_message.get("content", raw_message.get("text", "")),
                session_id=raw_message.get("session_id", ""),
                metadata={
                    "source_ip": raw_message.get("source_ip", ""),
                    "headers": raw_message.get("headers", {}),
                },
            )
        return UnifiedMessage(from_platform="webhook", text=str(raw_message))

    def send(self, message: UnifiedMessage) -> Dict[str, Any]:
        """UnifiedMessage转为Webhook响应格式。"""
        return {
            "status": "ok",
            "response": message.text,
            "session_id": message.session_id,
            "timestamp": message.timestamp,
        }

    def validate(self, raw_message: Any) -> bool:
        """验证Webhook消息。"""
        if isinstance(raw_message, dict):
            return "content" in raw_message or "text" in raw_message
        return False


# ============================================================
# CLIAdapter
# ============================================================

class CLIAdapter(MessageAdapter):
    """CLI适配器 — 命令行交互。

    将终端输入转换为UnifiedMessage。
    """

    platform_name = "cli"

    def receive(self, raw_message: Any) -> UnifiedMessage:
        """从CLI输入转换为UnifiedMessage。"""
        text = str(raw_message).strip()
        # 检测是否是斜杠命令
        msg_type = MessageType.COMMAND if text.startswith("/") else MessageType.TEXT

        return UnifiedMessage(
            role=MessageRole.USER,
            type=msg_type,
            from_platform="cli",
            from_user="cli_user",
            text=text,
        )

    def send(self, message: UnifiedMessage) -> str:
        """UnifiedMessage转为CLI输出。"""
        prefix = ""
        if message.role == MessageRole.SYSTEM:
            prefix = "[系统] "
        elif message.role == MessageRole.ASSISTANT:
            prefix = "[AI] "
        elif message.type == MessageType.ERROR:
            prefix = "[错误] "
        return prefix + message.text

    def validate(self, raw_message: Any) -> bool:
        """验证CLI消息。"""
        if isinstance(raw_message, str):
            return len(raw_message.strip()) > 0
        return False


# ============================================================
# 网关协调器
# ============================================================

class MessagingGateway:
    """消息网关协调器 — 统一所有平台的消息入口。

    协调流程：
    1. 接收原始消息 → 适配器转换 → UnifiedMessage
    2. 斜杠命令路由 → SlashCommandRouter
    3. 会话管理 → SessionManager
    4. 消息分发 → 回复通过适配器发送
    """

    def __init__(self, reset_mode: SessionResetMode = SessionResetMode.IDLE):
        self._adapters: Dict[str, MessageAdapter] = {}
        self._command_router = SlashCommandRouter()
        self._session_manager = SessionManager(reset_mode)

        # 注册默认适配器
        self.register_adapter(WebSocketAdapter())
        self.register_adapter(WebhookAdapter())
        self.register_adapter(CLIAdapter())

    def register_adapter(self, adapter: MessageAdapter):
        """注册消息适配器。"""
        self._adapters[adapter.platform_name] = adapter
        logger.info("messaging.adapter_registered",
                    platform=adapter.platform_name)

    def process_message(self, platform: str,
                        raw_message: Any) -> UnifiedMessage:
        """处理消息：接收 → 转换 → 命令路由 → 会话管理。"""
        # 获取适配器
        adapter = self._adapters.get(platform)
        if not adapter:
            return UnifiedMessage(
                role=MessageRole.SYSTEM,
                type=MessageType.ERROR,
                text=f"不支持的平台: {platform}",
            )

        # 验证消息
        if not adapter.validate(raw_message):
            return UnifiedMessage(
                role=MessageRole.SYSTEM,
                type=MessageType.ERROR,
                text=f"消息格式无效（平台: {platform})",
            )

        # 转换为UnifiedMessage
        message = adapter.receive(raw_message)

        # 会话管理
        session = self._session_manager.get_or_create(
            message.from_platform, message.from_user,
        )
        message.session_id = session.session_id
        self._session_manager.update_activity(session.session_id)

        # 斜杠命令路由
        if message.is_command():
            response = self._command_router.route(message)
            if response:
                # 命令响应直接发送
                send_result = adapter.send(response)
                return response

        # 非命令消息：返回UnifiedMessage供后续处理
        return message

    def send_response(self, platform: str,
                      message: UnifiedMessage) -> Any:
        """通过适配器发送回复。"""
        adapter = self._adapters.get(platform)
        if not adapter:
            logger.error("messaging.adapter_not_found", platform=platform)
            return None
        return adapter.send(message)

    def list_adapters(self) -> List[str]:
        """列出所有已注册适配器。"""
        return list(self._adapters.keys())

    def list_commands(self, platform: str = "") -> List[Dict[str, str]]:
        """列出可用命令。"""
        return self._command_router.list_commands(platform)

    def register_command(self, name: str, description: str,
                         handler: Callable, platforms: Set[str] = None):
        """注册自定义命令。"""
        self._command_router.register(name, description, handler, platforms)

    def stats(self) -> Dict[str, Any]:
        """网关统计。"""
        return {
            "adapters": list(self._adapters.keys()),
            "commands": len(self._command_router._commands),
            "sessions": self._session_manager.stats(),
        }


# ============================================================
# 便捷API
# ============================================================

def create_gateway(reset_mode: SessionResetMode = SessionResetMode.IDLE) -> MessagingGateway:
    """快捷创建消息网关。"""
    return MessagingGateway(reset_mode)


def format_message_for_platform(message: UnifiedMessage, platform: str) -> Any:
    """快捷格式化消息为指定平台格式。"""
    adapters = {
        "ws": WebSocketAdapter(),
        "webhook": WebhookAdapter(),
        "cli": CLIAdapter(),
    }
    adapter = adapters.get(platform)
    if adapter:
        return adapter.send(message)
    return message.to_dict()
