"""
Ollama Provider Mock测试

用Mock模拟Ollama API响应，测试Provider的错误处理、状态管理、
模型发现等逻辑。不需要实际Ollama服务运行。
"""

import asyncio
import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from providers.base_provider import (
    ChatMessage,
    ProviderConfig,
    ProviderError,
    ProviderStatus,
    ProviderTier,
    ProviderType,
)
from providers.ollama_provider import (
    OllamaModelInfo,
    OllamaProvider,
    create_ollama_llama_provider,
    create_ollama_mistral_provider,
    create_ollama_provider,
    create_ollama_qwen_provider,
)


# ==================== Mock辅助函数 ====================


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


def _make_ollama_chat_response(
    content: str = "你好！我是Ollama。",
    model: str = "qwen2.5:3b",
    tokens_in: int = 10,
    tokens_out: int = 20,
) -> dict:
    """创建Ollama /api/chat 的mock响应数据"""
    return {
        "model": model,
        "created_at": "2024-07-11T00:00:00Z",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "prompt_eval_count": tokens_in,
        "eval_count": tokens_out,
        "total_duration": 1000000000,
        "load_duration": 500000000,
        "prompt_eval_duration": 200000000,
        "eval_duration": 300000000,
    }


def _make_ollama_tags_response(
    models: list[dict] | None = None,
) -> dict:
    """创建Ollama /api/tags 的mock响应数据"""
    if models is None:
        models = [
            {
                "name": "qwen2.5:3b",
                "model": "qwen2.5:3b",
                "size": 2000000000,
                "digest": "abc123def456789",
                "details": {
                    "format": "gguf",
                    "family": "qwen",
                    "parameter_size": "3B",
                    "quantization_level": "Q4_K_M",
                },
            },
            {
                "name": "llama3.2:3b",
                "model": "llama3.2:3b",
                "size": 2000000000,
                "digest": "def789ghi012345",
                "details": {
                    "format": "gguf",
                    "family": "llama",
                    "parameter_size": "3B",
                    "quantization_level": "Q4_K_M",
                },
            },
        ]
    return {"models": models}


# ==================== OllamaModelInfo 测试 ====================


class TestOllamaModelInfo:
    """OllamaModelInfo 数据类测试"""

    def test_creation(self):
        """测试创建模型信息"""
        info = OllamaModelInfo(
            name="qwen2.5:3b",
            size=2000000000,
            digest="abc123def456",
            family="qwen",
            parameter_size="3B",
            quantization="Q4_K_M",
        )
        assert info.name == "qwen2.5:3b"
        assert info.size == 2000000000
        assert info.family == "qwen"
        assert info.parameter_size == "3B"
        assert info.quantization == "Q4_K_M"

    def test_defaults(self):
        """测试默认值"""
        info = OllamaModelInfo(name="test:latest")
        assert info.size == 0
        assert info.digest == ""
        assert info.family == ""
        assert info.parameter_size == ""
        assert info.quantization == ""

    def test_to_dict(self):
        """测试序列化"""
        info = OllamaModelInfo(
            name="qwen2.5:3b",
            size=2097152,  # 2MB
            digest="abcdef1234567890",
            family="qwen",
            parameter_size="3B",
            quantization="Q4_K_M",
        )
        d = info.to_dict()
        assert d["name"] == "qwen2.5:3b"
        assert d["size_mb"] == 2.0
        assert d["digest"] == "abcdef123456"  # 截断到12字符
        assert d["family"] == "qwen"
        assert d["parameter_size"] == "3B"
        assert d["quantization"] == "Q4_K_M"

    def test_to_dict_zero_size(self):
        """测试零大小时的序列化"""
        info = OllamaModelInfo(name="test")
        d = info.to_dict()
        assert d["size_mb"] == 0


# ==================== 工厂函数测试 ====================


class TestOllamaFactoryFunctions:
    """Ollama Provider工厂函数测试"""

    def test_create_ollama_provider_defaults(self):
        """测试通用工厂默认参数"""
        provider = create_ollama_provider()
        assert provider.name == "ollama_qwen2_5_3b"
        assert provider.config.tier == ProviderTier.L1
        assert provider.config.provider_type == ProviderType.LOCAL
        assert provider.config.model_name == "qwen2.5:3b"
        assert provider.config.api_base == "http://localhost:11434"
        assert provider.config.priority == 10

    def test_create_ollama_provider_custom(self):
        """测试自定义参数"""
        provider = create_ollama_provider(
            model_name="llama3.2:3b",
            api_base="http://192.168.1.100:11434",
            display_name="我的Llama",
            keep_alive="30m",
            priority=5,
        )
        assert provider.name == "ollama_llama3_2_3b"
        assert provider.config.api_base == "http://192.168.1.100:11434"
        assert provider.config.display_name == "我的Llama"
        assert provider.config.priority == 5
        assert provider._keep_alive == "30m"

    def test_create_ollama_qwen_provider(self):
        """测试Qwen工厂"""
        provider = create_ollama_qwen_provider()
        assert "qwen" in provider.config.model_name
        assert "Qwen" in provider.config.display_name
        assert provider.config.tier == ProviderTier.L1

    def test_create_ollama_llama_provider(self):
        """测试Llama工厂"""
        provider = create_ollama_llama_provider()
        assert "llama" in provider.config.model_name.lower()
        assert "Llama" in provider.config.display_name
        assert provider.config.tier == ProviderTier.L1

    def test_create_ollama_mistral_provider(self):
        """测试Mistral工厂"""
        provider = create_ollama_mistral_provider()
        assert "mistral" in provider.config.model_name.lower()
        assert "Mistral" in provider.config.display_name
        assert provider.config.tier == ProviderTier.L1

    def test_provider_local_no_cost(self):
        """测试本地模型零成本"""
        provider = create_ollama_provider()
        assert provider.config.cost_per_1k_input == 0.0
        assert provider.config.cost_per_1k_output == 0.0

    def test_default_api_base(self):
        """测试默认API地址"""
        provider = OllamaProvider(
            ProviderConfig(
                name="test",
                display_name="test",
                provider_type=ProviderType.LOCAL,
                tier=ProviderTier.L1,
                model_name="test:latest",
            )
        )
        assert provider.config.api_base == "http://localhost:11434"

    def test_keep_alive_from_extra(self):
        """测试从extra读取keep_alive"""
        provider = OllamaProvider(
            ProviderConfig(
                name="test",
                display_name="test",
                provider_type=ProviderType.LOCAL,
                tier=ProviderTier.L1,
                model_name="test:latest",
                extra={"keep_alive": "10m", "format": "json"},
            )
        )
        assert provider._keep_alive == "10m"
        assert provider._format == "json"

    def test_keep_alive_default(self):
        """测试keep_alive默认值"""
        provider = create_ollama_provider()
        assert provider._keep_alive == "5m"
        assert provider._format == ""


# ==================== chat() 测试 ====================


class TestOllamaChat:
    """Ollama chat() 方法测试"""

    @pytest.mark.asyncio
    async def test_chat_success(self):
        """测试成功对话"""
        provider = create_ollama_provider(model_name="qwen2.5:3b")
        mock_data = _make_ollama_chat_response(
            content="你好！我是Ollama运行的Qwen模型。",
            tokens_in=15,
            tokens_out=25,
        )

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            response = await provider.chat([ChatMessage(role="user", content="你好")])

        assert response.content == "你好！我是Ollama运行的Qwen模型。"
        assert response.provider == "ollama_qwen2_5_3b"
        assert response.tier == ProviderTier.L1
        assert response.tokens_in == 15
        assert response.tokens_out == 25
        assert response.cost_estimate == 0.0  # 本地模型免费
        assert provider.status == ProviderStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_chat_404_model_not_found(self):
        """测试模型不存在(404)"""
        provider = create_ollama_provider(model_name="nonexistent:latest")
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_mock_http_error(404, '{"error":"model not found"}'),
        ):
            with pytest.raises(ProviderError) as exc:
                await provider.chat([ChatMessage(role="user", content="test")])

        assert exc.value.status_code == 404
        assert "ollama pull" in str(exc.value)  # 提示用户拉取模型
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_chat_500_internal_error(self):
        """测试Ollama内部错误(500)"""
        provider = create_ollama_provider()
        error_body = '{"error":"OOM: not enough memory"}'
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_mock_http_error(500, error_body),
        ):
            with pytest.raises(ProviderError) as exc:
                await provider.chat([ChatMessage(role="user", content="test")])

        assert exc.value.status_code == 500
        assert "OOM" in str(exc.value)
        assert provider.status == ProviderStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_chat_network_error(self):
        """测试网络连接错误"""
        provider = create_ollama_provider()
        with patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")
        ):
            with pytest.raises(ProviderError) as exc:
                await provider.chat([ChatMessage(role="user", content="test")])

        assert "无法连接Ollama服务" in str(exc.value)
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_chat_stats_tracking(self):
        """测试统计追踪"""
        provider = create_ollama_provider()
        mock_data = _make_ollama_chat_response(tokens_in=5, tokens_out=3)

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            await provider.chat([ChatMessage(role="user", content="test")])

        stats = provider.stats
        assert stats["requests"] == 1
        assert stats["errors"] == 0
        assert stats["avg_latency_ms"] > 0

    @pytest.mark.asyncio
    async def test_chat_error_count(self):
        """测试错误计数"""
        provider = create_ollama_provider()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
            try:
                await provider.chat([ChatMessage(role="user", content="test")])
            except ProviderError:
                pass

        stats = provider.stats
        assert stats["requests"] == 1
        assert stats["errors"] == 1

    @pytest.mark.asyncio
    async def test_chat_keep_alive_in_payload(self):
        """测试keep_alive参数被发送"""
        provider = create_ollama_provider(keep_alive="30m")
        mock_data = _make_ollama_chat_response()

        captured_payload = {}

        def capture_request(req, **kwargs):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            return _make_mock_response(mock_data)

        with patch("urllib.request.urlopen", side_effect=capture_request):
            await provider.chat([ChatMessage(role="user", content="test")])

        assert captured_payload["data"]["keep_alive"] == "30m"

    @pytest.mark.asyncio
    async def test_chat_format_in_payload(self):
        """测试format参数被发送"""
        provider = OllamaProvider(
            ProviderConfig(
                name="test",
                display_name="test",
                provider_type=ProviderType.LOCAL,
                tier=ProviderTier.L1,
                model_name="test:latest",
                extra={"format": "json"},
            )
        )
        mock_data = _make_ollama_chat_response()

        captured_payload = {}

        def capture_request(req, **kwargs):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            return _make_mock_response(mock_data)

        with patch("urllib.request.urlopen", side_effect=capture_request):
            await provider.chat([ChatMessage(role="user", content="test")])

        assert captured_payload["data"]["format"] == "json"

    @pytest.mark.asyncio
    async def test_chat_no_format_by_default(self):
        """测试默认不发送format参数"""
        provider = create_ollama_provider()
        mock_data = _make_ollama_chat_response()

        captured_payload = {}

        def capture_request(req, **kwargs):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            return _make_mock_response(mock_data)

        with patch("urllib.request.urlopen", side_effect=capture_request):
            await provider.chat([ChatMessage(role="user", content="test")])

        assert "format" not in captured_payload["data"]

    @pytest.mark.asyncio
    async def test_chat_uses_native_api_endpoint(self):
        """测试使用Ollama原生API端点(/api/chat)"""
        provider = create_ollama_provider()
        mock_data = _make_ollama_chat_response()

        captured_url = {}

        def capture_request(req, **kwargs):
            captured_url["url"] = req.full_url
            return _make_mock_response(mock_data)

        with patch("urllib.request.urlopen", side_effect=capture_request):
            await provider.chat([ChatMessage(role="user", content="test")])

        assert captured_url["url"].endswith("/api/chat")

    @pytest.mark.asyncio
    async def test_chat_custom_temperature(self):
        """测试自定义温度"""
        provider = create_ollama_provider()
        mock_data = _make_ollama_chat_response()

        captured_payload = {}

        def capture_request(req, **kwargs):
            captured_payload["data"] = json.loads(req.data.decode("utf-8"))
            return _make_mock_response(mock_data)

        with patch("urllib.request.urlopen", side_effect=capture_request):
            await provider.chat(
                [ChatMessage(role="user", content="test")],
                temperature=0.1,
                max_tokens=100,
            )

        assert captured_payload["data"]["options"]["temperature"] == 0.1
        assert captured_payload["data"]["options"]["num_predict"] == 100


# ==================== health_check() 测试 ====================


class TestOllamaHealthCheck:
    """Ollama health_check() 方法测试"""

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """测试健康检查成功"""
        provider = create_ollama_provider(model_name="qwen2.5:3b")
        mock_tags = _make_ollama_tags_response()

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_tags)):
            result = await provider.health_check()

        assert result is True
        assert provider.status == ProviderStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_health_check_fuzzy_match(self):
        """测试模糊匹配（用户写qwen2.5，实际是qwen2.5:3b）"""
        provider = create_ollama_provider(model_name="qwen2.5")  # 没有tag
        mock_tags = _make_ollama_tags_response()

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_tags)):
            result = await provider.health_check()

        assert result is True
        assert provider.status == ProviderStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_health_check_model_not_found(self):
        """测试模型不存在"""
        provider = create_ollama_provider(model_name="nonexistent:latest")
        mock_tags = _make_ollama_tags_response()

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_tags)):
            result = await provider.health_check()

        assert result is False
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_health_check_ollama_offline(self):
        """测试Ollama服务不在线"""
        provider = create_ollama_provider()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = await provider.health_check()

        assert result is False
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_health_check_no_models(self):
        """测试Ollama在线但无已安装模型"""
        provider = create_ollama_provider()
        mock_tags = _make_ollama_tags_response(models=[])

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_tags)):
            result = await provider.health_check()

        assert result is False
        assert provider.status == ProviderStatus.OFFLINE


# ==================== list_models() 测试 ====================


class TestOllamaListModels:
    """Ollama list_models() 静态方法测试"""

    def test_list_models_success(self):
        """测试成功列出模型"""
        mock_tags = _make_ollama_tags_response()

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_tags)):
            models = OllamaProvider.list_models()

        assert len(models) == 2
        assert models[0].name == "qwen2.5:3b"
        assert models[0].family == "qwen"
        assert models[0].parameter_size == "3B"
        assert models[1].name == "llama3.2:3b"
        assert models[1].family == "llama"

    def test_list_models_empty(self):
        """测试无已安装模型"""
        mock_tags = _make_ollama_tags_response(models=[])

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_tags)):
            models = OllamaProvider.list_models()

        assert len(models) == 0

    def test_list_models_connection_error(self):
        """测试连接错误"""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with pytest.raises(ProviderError) as exc:
                OllamaProvider.list_models()

        assert "无法连接Ollama服务" in str(exc.value)

    def test_list_models_custom_api_base(self):
        """测试自定义API地址"""
        mock_tags = _make_ollama_tags_response()

        captured_url = {}

        def capture_request(req, **kwargs):
            captured_url["url"] = req.full_url
            return _make_mock_response(mock_tags)

        with patch("urllib.request.urlopen", side_effect=capture_request):
            OllamaProvider.list_models(api_base="http://192.168.1.100:11434")

        assert "192.168.1.100" in captured_url["url"]
        assert "/api/tags" in captured_url["url"]


# ==================== is_running() 测试 ====================


class TestOllamaIsRunning:
    """Ollama is_running() 静态方法测试"""

    def test_is_running_true(self):
        """测试Ollama在运行"""
        mock_tags = _make_ollama_tags_response()

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_tags)):
            assert OllamaProvider.is_running() is True

    def test_is_running_false(self):
        """测试Ollama没在运行"""
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            assert OllamaProvider.is_running() is False


# ==================== 集成测试 ====================


class TestOllamaProviderIntegration:
    """OllamaProvider 集成测试"""

    @pytest.mark.asyncio
    async def test_full_flow_chat_then_health(self):
        """测试完整流程：先对话后健康检查"""
        provider = create_ollama_provider(model_name="qwen2.5:3b")

        # 1. chat
        chat_data = _make_ollama_chat_response(content="你好！", tokens_in=5, tokens_out=3)
        with patch("urllib.request.urlopen", return_value=_make_mock_response(chat_data)):
            response = await provider.chat([ChatMessage(role="user", content="你好")])
        assert response.content == "你好！"
        assert provider.status == ProviderStatus.HEALTHY

        # 2. health_check
        tags_data = _make_ollama_tags_response()
        with patch("urllib.request.urlopen", return_value=_make_mock_response(tags_data)):
            healthy = await provider.health_check()
        assert healthy is True

    @pytest.mark.asyncio
    async def test_multiple_models_different_providers(self):
        """测试多个Ollama模型注册为不同Provider"""
        qwen = create_ollama_qwen_provider(model_name="qwen2.5:3b")
        llama = create_ollama_llama_provider(model_name="llama3.2:3b")

        assert qwen.name != llama.name
        assert qwen.config.model_name == "qwen2.5:3b"
        assert llama.config.model_name == "llama3.2:3b"

    @pytest.mark.asyncio
    async def test_provider_repr(self):
        """测试__repr__"""
        provider = create_ollama_provider(model_name="qwen2.5:3b")
        repr_str = repr(provider)
        assert "OllamaProvider" in repr_str
        assert "L1" in repr_str
        assert "local" in repr_str
