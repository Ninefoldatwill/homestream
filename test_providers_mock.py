"""
GLM/DeepSeek Provider Mock测试

用Mock模拟API响应，测试Provider的错误处理、状态管理、费用估算等逻辑。
不需要实际API Key。
"""

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from providers.base_provider import (
    ChatMessage,
    ProviderError,
    ProviderStatus,
    ProviderTier,
)
from providers.deepseek_provider import (
    create_deepseek_flash_provider,
    create_deepseek_reasoner_provider,
)
from providers.glm_provider import create_glm_flash_provider, create_glm_plus_provider


def _make_mock_response(data: dict, status: int = 200) -> MagicMock:
    """创建mock HTTP响应"""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(data).encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_mock_http_error(status: int, body: str = "") -> urllib.error.HTTPError:
    """创建mock HTTPError"""
    return urllib.error.HTTPError(
        url="http://test/api",
        code=status,
        msg=f"HTTP {status}",
        hdrs=None,
        fp=BytesIO(body.encode("utf-8")),
    )


# ==================== GLM Provider Mock测试 ====================


class TestGLMProviderMock:
    """GLM Provider Mock测试"""

    def test_create_flash_provider(self):
        """测试创建Flash Provider"""
        provider = create_glm_flash_provider("test_key_123")
        assert provider.name == "glm_flash"
        assert provider.config.tier == ProviderTier.L2
        assert provider.config.api_key == "test_key_123"
        assert provider.config.model_name == "glm-4-flash"
        assert provider.config.cost_per_1k_input == 0.0  # Flash免费

    def test_create_plus_provider(self):
        """测试创建Plus Provider"""
        provider = create_glm_plus_provider("test_key_456")
        assert provider.name == "glm_plus"
        assert provider.config.tier == ProviderTier.L3
        assert provider.config.model_name == "glm-4-plus"
        assert provider.config.cost_per_1k_input > 0  # Plus付费

    @pytest.mark.asyncio
    async def test_chat_success_mock(self):
        """测试Mock成功响应"""
        provider = create_glm_flash_provider("test_key")
        mock_data = {
            "choices": [{"message": {"content": "你好！我是GLM。"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            response = await provider.chat([ChatMessage(role="user", content="你好")])

        assert response.content == "你好！我是GLM。"
        assert response.provider == "glm_flash"
        assert response.tier == ProviderTier.L2
        assert response.tokens_in == 10
        assert response.tokens_out == 20
        assert provider.status == ProviderStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_chat_401_error(self):
        """测试401认证失败"""
        provider = create_glm_flash_provider("invalid_key")
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_mock_http_error(401, '{"error":"invalid key"}'),
        ):
            with pytest.raises(ProviderError) as exc:
                await provider.chat([ChatMessage(role="user", content="test")])
        assert exc.value.status_code == 401
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_chat_429_rate_limit(self):
        """测试429频率限制"""
        provider = create_glm_flash_provider("test_key")
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_mock_http_error(429, '{"error":"rate limit"}'),
        ):
            with pytest.raises(ProviderError) as exc:
                await provider.chat([ChatMessage(role="user", content="test")])
        assert exc.value.status_code == 429
        assert provider.status == ProviderStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_chat_network_error(self):
        """测试网络错误"""
        provider = create_glm_flash_provider("test_key")
        with patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")
        ):
            with pytest.raises(ProviderError) as exc:
                await provider.chat([ChatMessage(role="user", content="test")])
        assert "网络错误" in str(exc.value)
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_health_check_no_key(self):
        """测试无Key时健康检查"""
        provider = create_glm_flash_provider("")
        result = await provider.health_check()
        assert result is False
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """测试健康检查成功"""
        provider = create_glm_flash_provider("test_key")
        mock_data = {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            result = await provider.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_stats_tracking(self):
        """测试统计追踪"""
        provider = create_glm_flash_provider("test_key")
        mock_data = {
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            await provider.chat([ChatMessage(role="user", content="test")])

        stats = provider.stats
        assert stats["requests"] == 1
        assert stats["errors"] == 0
        assert stats["avg_latency_ms"] > 0

    def test_default_api_base(self):
        """测试默认API地址"""
        provider = create_glm_flash_provider("key")
        assert provider.config.api_base == "https://open.bigmodel.cn/api/paas/v4"


# ==================== DeepSeek Provider Mock测试 ====================


class TestDeepSeekProviderMock:
    """DeepSeek Provider Mock测试"""

    def test_create_flash_provider(self):
        """测试创建Flash Provider"""
        provider = create_deepseek_flash_provider("ds_key_123")
        assert provider.name == "deepseek_flash"
        assert provider.config.tier == ProviderTier.L3
        assert provider.config.api_key == "ds_key_123"
        assert provider.config.model_name == "deepseek-chat"
        assert provider.config.cost_per_1k_input > 0  # 付费

    def test_create_reasoner_provider(self):
        """测试创建Reasoner Provider"""
        provider = create_deepseek_reasoner_provider("ds_key_456")
        assert provider.name == "deepseek_reasoner"
        assert provider.config.tier == ProviderTier.L3
        assert provider.config.model_name == "deepseek-reasoner"
        assert provider.config.temperature == 0.0  # 推理模式低温度
        assert provider.config.max_tokens == 4096

    @pytest.mark.asyncio
    async def test_chat_success_mock(self):
        """测试Mock成功响应"""
        provider = create_deepseek_flash_provider("test_key")
        mock_data = {
            "choices": [{"message": {"content": "我是DeepSeek。"}}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 25},
        }

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            response = await provider.chat([ChatMessage(role="user", content="你好")])

        assert response.content == "我是DeepSeek。"
        assert response.provider == "deepseek_flash"
        assert response.tier == ProviderTier.L3
        assert response.tokens_in == 15
        assert response.tokens_out == 25
        assert response.cost_estimate > 0  # 付费API有费用
        assert provider.status == ProviderStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_chat_401_error(self):
        """测试401认证失败"""
        provider = create_deepseek_flash_provider("invalid")
        with patch(
            "urllib.request.urlopen", side_effect=_make_mock_http_error(401, '{"error":"bad key"}')
        ):
            with pytest.raises(ProviderError) as exc:
                await provider.chat([ChatMessage(role="user", content="test")])
        assert exc.value.status_code == 401
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_chat_429_rate_limit(self):
        """测试429频率限制"""
        provider = create_deepseek_flash_provider("test_key")
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_mock_http_error(429, '{"error":"slow down"}'),
        ):
            with pytest.raises(ProviderError) as exc:
                await provider.chat([ChatMessage(role="user", content="test")])
        assert exc.value.status_code == 429
        assert provider.status == ProviderStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_chat_network_error(self):
        """测试网络错误"""
        provider = create_deepseek_flash_provider("test_key")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Timeout")):
            with pytest.raises(ProviderError) as exc:
                await provider.chat([ChatMessage(role="user", content="test")])
        assert "网络错误" in str(exc.value)
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_health_check_no_key(self):
        """测试无Key时健康检查"""
        provider = create_deepseek_flash_provider("")
        result = await provider.health_check()
        assert result is False
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """测试健康检查成功"""
        provider = create_deepseek_flash_provider("test_key")
        mock_data = {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            result = await provider.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_cost_estimation(self):
        """测试费用估算"""
        provider = create_deepseek_flash_provider("key")
        # Flash: 0.001/千input + 0.002/千output
        cost = provider._estimate_cost(tokens_in=1000, tokens_out=500)
        assert cost == pytest.approx(0.002)  # 0.001 + 0.001

    def test_default_api_base(self):
        """测试默认API地址"""
        provider = create_deepseek_flash_provider("key")
        assert provider.config.api_base == "https://api.deepseek.com/v1"

    @pytest.mark.asyncio
    async def test_error_count_tracking(self):
        """测试错误计数"""
        provider = create_deepseek_flash_provider("test_key")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
            try:
                await provider.chat([ChatMessage(role="user", content="test")])
            except ProviderError:
                pass

        stats = provider.stats
        assert stats["requests"] == 1
        assert stats["errors"] == 1
