"""
VoiceBridge LLM 适配器单元测试

测试 HomeStreamLLM 与三层 ModelRouter 的集成:
  - route() 简化路由入口
  - route_messages() 完整路由入口
  - llm_node() LiveKit 节点覆写
  - _convert_chat_ctx() 消息格式转换
  - health_check() / get_status() 状态查询

运行: pytest test_voice_llm_adapter.py -v
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voice.llm_adapter import HomeStreamLLM
from voice.config import VoiceBridgeConfig


class TestHomeStreamLLMInit:
    """HomeStreamLLM 初始化测试"""

    def test_init_with_strategy(self):
        """测试策略设置"""
        llm = HomeStreamLLM(strategy="SPEED_FIRST")
        assert llm._strategy_name == "SPEED_FIRST"
        assert llm._router is None
        assert llm._initialized is False

    def test_init_default_strategy(self):
        """默认策略为 SPEED_FIRST"""
        llm = HomeStreamLLM()
        assert llm._strategy_name == "SPEED_FIRST"

    def test_init_with_custom_router(self):
        """传入已初始化的 router"""
        mock_router = MagicMock()
        mock_router._initialized = True
        llm = HomeStreamLLM(router=mock_router, strategy="COST_FIRST")
        assert llm._router is mock_router
        assert llm._strategy_name == "COST_FIRST"


class TestConvertChatCtx:
    """ChatContext 转换测试"""

    def test_convert_none(self):
        """None 输入返回空列表"""
        llm = HomeStreamLLM()
        assert llm._convert_chat_ctx(None) == []

    def test_convert_list_of_dicts(self):
        """列表格式消息转换"""
        llm = HomeStreamLLM()
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        result = llm._convert_chat_ctx(messages)
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "你好"}
        assert result[1] == {"role": "assistant", "content": "你好！"}

    def test_convert_object_with_messages(self):
        """对象格式 (有 messages 属性) 转换"""
        llm = HomeStreamLLM()

        msg1 = MagicMock()
        msg1.role = "user"
        msg1.content = "测试"

        msg2 = MagicMock()
        msg2.role = "assistant"
        msg2.content = "收到"

        chat_ctx = MagicMock()
        chat_ctx.messages = [msg1, msg2]

        result = llm._convert_chat_ctx(chat_ctx)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "测试"

    def test_convert_role_mapping(self):
        """role 枚举映射"""
        llm = HomeStreamLLM()

        msg = MagicMock()
        msg.role = "ChatRole.USER"
        msg.content = "test"

        chat_ctx = MagicMock()
        chat_ctx.messages = [msg]

        result = llm._convert_chat_ctx(chat_ctx)
        assert result[0]["role"] == "user"

    def test_convert_multimodal_content(self):
        """多模态 content (列表) 提取文本"""
        llm = HomeStreamLLM()

        msg = MagicMock()
        msg.role = "user"
        msg.content = [{"text": "你好"}, {"text": "世界"}]

        chat_ctx = MagicMock()
        chat_ctx.messages = [msg]

        result = llm._convert_chat_ctx(chat_ctx)
        assert "你好" in result[0]["content"]
        assert "世界" in result[0]["content"]


class TestRoute:
    """路由方法测试 (使用 Mock Router)"""

    def _make_mock_router(self):
        """创建 Mock ModelRouter"""
        router = MagicMock()
        router._initialized = True
        router.chat_simple = AsyncMock(return_value="你好！我是 HomeStream 语音助手。")
        router.chat = AsyncMock()
        router.health_check_all = AsyncMock(
            return_value={"L1": True, "L2": True, "L3": False}
        )
        router.get_status.return_value = {"strategy": "SPEED_FIRST", "tiers": ["L1", "L2"]}
        router.get_available_tiers.return_value = ["L1", "L2"]
        return router

    @pytest.mark.asyncio
    async def test_route_simple(self):
        """route() 简化入口"""
        mock_router = self._make_mock_router()
        llm = HomeStreamLLM(router=mock_router)
        llm._initialized = True

        result = await llm.route("你好", system="你是助手")
        assert result == "你好！我是 HomeStream 语音助手。"
        mock_router.chat_simple.assert_called_once_with(
            prompt="你好", system="你是助手", max_tokens=512
        )

    @pytest.mark.asyncio
    async def test_route_messages(self):
        """route_messages() 完整入口"""
        mock_router = self._make_mock_router()
        mock_response = MagicMock()
        mock_response.content = "回复内容"
        mock_response.tier = "L1"
        mock_router.chat = AsyncMock(return_value=mock_response)

        llm = HomeStreamLLM(router=mock_router)
        llm._initialized = True

        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
        response = await llm.route_messages(messages=messages)

        assert response.content == "回复内容"
        assert response.tier == "L1"
        mock_router.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check(self):
        """health_check() 状态查询"""
        mock_router = self._make_mock_router()
        llm = HomeStreamLLM(router=mock_router)
        llm._initialized = True

        result = await llm.health_check()
        assert result["L1"] is True
        assert result["L2"] is True
        assert result["L3"] is False

    def test_get_status(self):
        """get_status() 状态查询"""
        mock_router = self._make_mock_router()
        llm = HomeStreamLLM(router=mock_router)
        llm._initialized = True

        status = llm.get_status()
        assert "strategy" in status


class TestLLMNode:
    """llm_node LiveKit 节点覆写测试"""

    @pytest.mark.asyncio
    async def test_llm_node_yields_content(self):
        """llm_node 产出文本内容"""
        mock_router = MagicMock()
        mock_router._initialized = True
        mock_response = MagicMock()
        mock_response.content = "测试回复"
        mock_response.tier = "L1"
        mock_response.model = "qwen2.5:3b"
        mock_response.latency_ms = 120
        mock_router.chat = AsyncMock(return_value=mock_response)

        llm = HomeStreamLLM(router=mock_router)
        llm._initialized = True

        # 构造 mock chat_ctx
        msg = MagicMock()
        msg.role = "user"
        msg.content = "你好"
        chat_ctx = MagicMock()
        chat_ctx.messages = [msg]

        chunks = []
        async for chunk in llm.llm_node(chat_ctx, [], None):
            chunks.append(chunk)

        assert len(chunks) >= 1
        # chunk 可能是 ChatChunk 或纯文本
        first = chunks[0]
        if hasattr(first, "content"):
            assert first.content == "测试回复"
        else:
            assert "测试回复" in str(first)


class TestVoiceBridgeConfig:
    """配置测试"""

    def test_default_config(self):
        """默认配置为 localhost 自托管"""
        config = VoiceBridgeConfig()
        assert "localhost:7880" in config.livekit_url
        assert config.livekit_api_key == "devkey"
        assert config.router_strategy == "SPEED_FIRST"
        assert config.funasr_ws_uri == "ws://localhost:10096"
        assert config.tts_mode == "local"
        assert config.tts_voice == "longxiaochun"
        assert config.allow_interruptions is True

    def test_config_to_dict(self):
        """配置序列化"""
        config = VoiceBridgeConfig()
        d = config.to_dict()
        assert "livekit_url" in d
        assert "router_strategy" in d
        assert "funasr_ws_uri" in d
        assert "tts_voice" in d

    def test_config_from_env(self):
        """从环境变量加载"""
        config = VoiceBridgeConfig.from_env()
        assert isinstance(config, VoiceBridgeConfig)
