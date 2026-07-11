"""
OpenAI 兼容 API 端点测试

使用 FastAPI TestClient + Mock Provider 测试所有端点。
覆盖：非流式/流式/模型列表/健康检查/认证/错误处理。
"""

import json
import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from model_router import ModelRouter
from openai_compat_endpoint import create_openai_router, create_standalone_app
from providers.base_provider import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderConfig,
    ProviderStatus,
    ProviderTier,
    ProviderType,
)


# ==================== autouse fixture：每个测试前清除认证环境变量 ====================


@pytest.fixture(autouse=True)
def _clean_auth_env():
    """每个测试前清除 OPENAI_COMPAT_API_KEY，防止测试间环境变量泄漏"""
    old = os.environ.pop("OPENAI_COMPAT_API_KEY", None)
    yield
    # 测试后恢复原状
    if old is not None:
        os.environ["OPENAI_COMPAT_API_KEY"] = old
    else:
        os.environ.pop("OPENAI_COMPAT_API_KEY", None)


# ==================== Mock Provider ====================


class MockProvider(BaseProvider):
    """测试用 Mock Provider"""

    def __init__(
        self,
        name: str = "mock_provider",
        tier: ProviderTier = ProviderTier.L1,
        model_name: str = "mock-model",
    ):
        config = ProviderConfig(
            name=name,
            display_name=f"Mock {name}",
            provider_type=ProviderType.LOCAL,
            tier=tier,
            model_name=model_name,
            max_tokens=512,
            temperature=0.7,
        )
        super().__init__(config)
        self._mark_status(ProviderStatus.HEALTHY)

    async def chat(
        self,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        user_msgs = [m.content for m in messages if m.role == "user"]
        content = f"Mock response to: {' '.join(user_msgs)}"

        return ChatResponse(
            content=content,
            model=self.config.model_name,
            provider=self.name,
            tier=self.config.tier,
            latency_ms=10.0,
            tokens_in=5,
            tokens_out=10,
            cost_estimate=0.0,
        )

    async def health_check(self) -> bool:
        self._mark_status(ProviderStatus.HEALTHY)
        return True


class FailingProvider(BaseProvider):
    """总是失败的 Provider"""

    def __init__(self, name: str = "failing_provider"):
        config = ProviderConfig(
            name=name,
            display_name=f"Failing {name}",
            provider_type=ProviderType.API,
            tier=ProviderTier.L3,
            model_name="failing-model",
        )
        super().__init__(config)

    async def chat(self, messages, max_tokens=None, temperature=None) -> ChatResponse:
        from providers.base_provider import ProviderError

        raise ProviderError(self.name, "Mock failure")

    async def health_check(self) -> bool:
        self._mark_status(ProviderStatus.OFFLINE)
        return False


# ==================== 测试辅助函数 ====================


def _create_test_client(auth_key: str | None = None) -> TestClient:
    """创建测试用 TestClient"""
    # 设置/清除认证环境变量
    if auth_key:
        os.environ["OPENAI_COMPAT_API_KEY"] = auth_key
    else:
        os.environ.pop("OPENAI_COMPAT_API_KEY", None)

    # 创建带 mock provider 的 ModelRouter
    router = ModelRouter()
    mock_l1 = MockProvider("mock_l1", ProviderTier.L1, "mock-l1-model")
    mock_l2 = MockProvider("mock_l2", ProviderTier.L2, "mock-l2-model")
    mock_l3 = MockProvider("mock_l3", ProviderTier.L3, "mock-l3-model")
    router.init_for_testing([mock_l1, mock_l2, mock_l3])

    # 创建 FastAPI app
    app = FastAPI()
    app.include_router(create_openai_router(router))

    return TestClient(app)


def _basic_chat_request(model: str = "", stream: bool = False) -> dict:
    """创建基本聊天请求"""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ],
        "stream": stream,
        "max_tokens": 100,
        "temperature": 0.7,
    }


# ==================== 非流式聊天补全测试 ====================


class TestChatCompletionsNonStream:
    """非流式聊天补全测试"""

    def test_basic_chat(self):
        """测试基本聊天补全"""
        client = _create_test_client()
        response = client.post("/v1/chat/completions", json=_basic_chat_request())

        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "chat.completion"
        assert "choices" in data
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert "Mock response" in data["choices"][0]["message"]["content"]
        assert data["choices"][0]["finish_reason"] == "stop"
        assert "usage" in data
        assert data["usage"]["total_tokens"] > 0

    def test_response_format(self):
        """测试响应格式兼容 OpenAI"""
        client = _create_test_client()
        response = client.post("/v1/chat/completions", json=_basic_chat_request())

        data = response.json()
        # OpenAI 必须字段
        assert "id" in data
        assert data["id"].startswith("chatcmpl-")
        assert data["object"] == "chat.completion"
        assert "created" in data
        assert "model" in data
        assert "choices" in data
        assert "usage" in data
        # HomeStream 扩展字段
        assert "provider" in data
        assert "tier" in data
        assert "latency_ms" in data

    def test_chat_with_model_param(self):
        """测试指定 model 参数"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(model="mock_l2"),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "mock_l2"

    def test_chat_with_tier_l1(self):
        """测试指定 L1 层级"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(model="L1"),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "L1"

    def test_chat_with_tier_l3(self):
        """测试指定 L3 层级"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(model="L3"),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["tier"] == "L3"

    def test_chat_with_auto_model(self):
        """测试 model=auto 使用默认路由"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(model="auto"),
        )
        assert response.status_code == 200

    def test_chat_with_unknown_model_fallback(self):
        """测试未知模型名回退到默认路由"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(model="nonexistent-model"),
        )
        assert response.status_code == 200

    def test_chat_with_model_name_match(self):
        """测试按模型名模糊匹配"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(model="mock-l1-model"),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "mock_l1"

    def test_chat_empty_messages(self):
        """测试空消息列表"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json={"model": "", "messages": []},
        )
        # 空消息应该能处理（MockProvider 不会报错）
        assert response.status_code == 200

    def test_chat_with_extra_params(self):
        """测试额外参数被忽略"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "",
                "messages": [{"role": "user", "content": "hi"}],
                "top_p": 0.9,
                "frequency_penalty": 0.5,
                "unknown_param": "should be ignored",
            },
        )
        assert response.status_code == 200


# ==================== 流式聊天补全测试 ====================


class TestChatCompletionsStream:
    """流式聊天补全测试"""

    def test_stream_basic(self):
        """测试基本流式响应"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(stream=True),
        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        # 解析 SSE 数据
        text = response.text
        lines = text.strip().split("\n")
        data_lines = [l for l in lines if l.startswith("data: ")]

        # 应该有多个 chunk + [DONE]
        assert len(data_lines) >= 3  # role chunk + content chunk(s) + finish chunk
        assert data_lines[-1] == "data: [DONE]"

    def test_stream_format(self):
        """测试流式响应格式"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(stream=True),
        )

        text = response.text
        lines = text.strip().split("\n")

        for line in lines:
            if line.startswith("data: ") and line != "data: [DONE]":
                data = json.loads(line[6:])  # 去掉 "data: " 前缀
                assert data["object"] == "chat.completion.chunk"
                assert "id" in data
                assert "created" in data
                assert "model" in data
                assert "choices" in data
                assert len(data["choices"]) == 1
                assert "delta" in data["choices"][0]

    def test_stream_first_chunk_has_role(self):
        """测试第一个 chunk 包含 role"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(stream=True),
        )

        text = response.text
        lines = text.strip().split("\n")
        data_lines = [l for l in lines if l.startswith("data: ") and l != "data: [DONE]"]

        first_chunk = json.loads(data_lines[0][6:])
        assert first_chunk["choices"][0]["delta"].get("role") == "assistant"

    def test_stream_last_chunk_has_finish_reason(self):
        """测试最后一个 chunk 包含 finish_reason"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(stream=True),
        )

        text = response.text
        lines = text.strip().split("\n")
        data_lines = [l for l in lines if l.startswith("data: ") and l != "data: [DONE]"]

        last_chunk = json.loads(data_lines[-1][6:])
        assert last_chunk["choices"][0]["finish_reason"] == "stop"

    def test_stream_content_assembled(self):
        """测试流式内容拼接后完整"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(stream=True),
        )

        text = response.text
        lines = text.strip().split("\n")
        data_lines = [l for l in lines if l.startswith("data: ") and l != "data: [DONE]"]

        # 拼接所有 content delta
        content = ""
        for line in data_lines:
            chunk = json.loads(line[6:])
            delta = chunk["choices"][0]["delta"]
            if "content" in delta:
                content += delta["content"]

        assert "Mock response" in content
        assert "Hello!" in content


# ==================== 模型列表测试 ====================


class TestListModels:
    """模型列表端点测试"""

    def test_list_models(self):
        """测试获取模型列表"""
        client = _create_test_client()
        response = client.get("/v1/models")

        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 3  # mock_l1, mock_l2, mock_l3

    def test_list_models_format(self):
        """测试模型列表格式"""
        client = _create_test_client()
        response = client.get("/v1/models")

        data = response.json()
        for model in data["data"]:
            assert "id" in model
            assert model["object"] == "model"
            assert "created" in model
            assert "owned_by" in model
            # HomeStream 扩展字段
            assert "tier" in model
            assert "display_name" in model

    def test_list_models_contains_all_providers(self):
        """测试模型列表包含所有 Provider"""
        client = _create_test_client()
        response = client.get("/v1/models")

        data = response.json()
        model_ids = [m["id"] for m in data["data"]]
        assert "mock_l1" in model_ids
        assert "mock_l2" in model_ids
        assert "mock_l3" in model_ids


# ==================== 健康检查测试 ====================


class TestHealthCheck:
    """健康检查端点测试"""

    def test_health(self):
        """测试健康检查"""
        client = _create_test_client()
        response = client.get("/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "timestamp" in data
        assert "providers" in data
        assert data["total_providers"] == 3
        assert data["healthy_providers"] == 3

    def test_health_all_healthy(self):
        """测试所有 Provider 健康"""
        client = _create_test_client()
        response = client.get("/v1/health")

        data = response.json()
        assert all(data["providers"].values())


# ==================== API 信息测试 ====================


class TestAPIInfo:
    """API 信息端点测试"""

    def test_api_info(self):
        """测试 API 信息"""
        client = _create_test_client()
        response = client.get("/v1/")

        assert response.status_code == 200
        data = response.json()
        assert "name" in data
        assert "version" in data
        assert "endpoints" in data
        assert "POST /v1/chat/completions" in data["endpoints"]
        assert data["auth_required"] is False

    def test_api_info_auth_required(self):
        """测试 API 信息显示认证状态"""
        client = _create_test_client(auth_key="test-key")
        response = client.get("/v1/")

        data = response.json()
        assert data["auth_required"] is True


# ==================== 认证测试 ====================


class TestAuthentication:
    """API Key 认证测试"""

    def test_no_auth_required_by_default(self):
        """测试默认不需要认证"""
        client = _create_test_client()  # 不设置 API Key
        response = client.post("/v1/chat/completions", json=_basic_chat_request())
        assert response.status_code == 200

    def test_auth_required_with_key(self):
        """测试设置了 API Key 时需要认证"""
        client = _create_test_client(auth_key="secret-key-123")

        # 无 Authorization 头 → 401
        response = client.post("/v1/chat/completions", json=_basic_chat_request())
        assert response.status_code == 401

    def test_auth_correct_key(self):
        """测试正确的 API Key"""
        client = _create_test_client(auth_key="secret-key-123")

        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(),
            headers={"Authorization": "Bearer secret-key-123"},
        )
        assert response.status_code == 200

    def test_auth_wrong_key(self):
        """测试错误的 API Key"""
        client = _create_test_client(auth_key="secret-key-123")

        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(),
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert response.status_code == 401

    def test_auth_malformed_header(self):
        """测试格式错误的 Authorization 头"""
        client = _create_test_client(auth_key="secret-key-123")

        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(),
            headers={"Authorization": "secret-key-123"},  # 缺少 Bearer
        )
        assert response.status_code == 401

    def test_auth_models_endpoint(self):
        """测试 models 端点也需要认证"""
        client = _create_test_client(auth_key="secret-key-123")

        response = client.get("/v1/models")
        assert response.status_code == 401

        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer secret-key-123"},
        )
        assert response.status_code == 200

    def test_auth_health_endpoint_not_required(self):
        """测试 health 端点不需要认证（方便监控工具调用）"""
        client = _create_test_client(auth_key="secret-key-123")

        # 即使设置了 API Key，health 端点也不需要认证
        # 这是设计决策：health 端点供负载均衡器/监控工具使用，不应被认证阻挡
        response = client.get("/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "providers" in data


# ==================== 独立 App 测试 ====================


class TestStandaloneApp:
    """独立 App 测试"""

    def test_create_standalone_app(self):
        """测试创建独立 App"""
        app = create_standalone_app()
        assert app is not None
        assert app.title == "HomeStream OpenAI Compatible API"

    def test_standalone_app_chat(self):
        """测试独立 App 聊天功能"""
        # 创建带 mock provider 的 ModelRouter
        router = ModelRouter()
        mock = MockProvider("standalone_mock", ProviderTier.L1)
        router.init_for_testing([mock])

        app = create_standalone_app(model_router=router)
        client = TestClient(app)

        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(),
        )
        assert response.status_code == 200
        data = response.json()
        assert "Mock response" in data["choices"][0]["message"]["content"]

    def test_standalone_app_models(self):
        """测试独立 App 模型列表"""
        router = ModelRouter()
        mock = MockProvider("standalone_mock", ProviderTier.L1)
        router.init_for_testing([mock])

        app = create_standalone_app(model_router=router)
        client = TestClient(app)

        response = client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1

    def test_standalone_app_cors(self):
        """测试独立 App CORS 支持"""
        app = create_standalone_app()
        client = TestClient(app)

        # OPTIONS 预检请求
        response = client.options(
            "/v1/chat/completions",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        # CORS 中间件应该处理预检请求
        assert response.status_code in (200, 204)


# ==================== 错误处理测试 ====================


class TestErrorHandling:
    """错误处理测试"""

    def test_all_providers_fail(self):
        """测试所有 Provider 都失败"""
        # 创建只有失败 Provider 的路由器
        router = ModelRouter()
        failing = FailingProvider("failing_1")
        router.init_for_testing([failing])

        app = FastAPI()
        app.include_router(create_openai_router(router))
        client = TestClient(app)

        response = client.post(
            "/v1/chat/completions",
            json=_basic_chat_request(),
        )
        assert response.status_code == 502

    def test_invalid_request_body(self):
        """测试无效请求体"""
        client = _create_test_client()

        # 缺少 messages 字段
        response = client.post(
            "/v1/chat/completions",
            json={"model": "test"},
        )
        assert response.status_code == 422  # Pydantic 验证失败

    def test_chat_with_temperature(self):
        """测试温度参数透传"""
        client = _create_test_client()
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "",
                "messages": [{"role": "user", "content": "test"}],
                "temperature": 0.1,
                "max_tokens": 50,
            },
        )
        assert response.status_code == 200
