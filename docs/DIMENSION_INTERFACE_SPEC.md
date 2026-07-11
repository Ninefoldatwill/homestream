# HomeStream 三套开源接口规范

> v5.1.0 接口规范 — Provider 接口 / Tool Bridge 接口 / OpenAI 兼容 API 接口

---

## 接口一：Provider 扩展接口

### 1.1 目的

第三方开发者只需继承 `BaseProvider` 并实现两个方法，即可接入自定义模型。

### 1.2 核心接口

```python
from providers.base_provider import (
    BaseProvider, ChatMessage, ChatResponse,
    ProviderConfig, ProviderTier, ProviderType,
)

class MyProvider(BaseProvider):
    """自定义 Provider 示例"""

    def __init__(self):
        config = ProviderConfig(
            name="my_provider",
            display_name="My Custom Model",
            provider_type=ProviderType.API,
            tier=ProviderTier.L2,
            model_name="my-model-v1",
            api_base="https://api.example.com/v1",
            api_key="your-api-key",
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.002,
        )
        super().__init__(config)

    async def chat(
        self,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        """核心方法：发送聊天请求"""
        # 实现你的 API 调用逻辑
        ...
        return ChatResponse(
            content="response text",
            model="my-model-v1",
            provider=self.name,
            tier=self.config.tier,
            latency_ms=150.0,
            tokens_in=10,
            tokens_out=20,
            cost_estimate=self._estimate_cost(10, 20),
        )

    async def health_check(self) -> bool:
        """健康检查，返回是否可用"""
        ...
```

### 1.3 注册到 ModelRouter

```python
from model_router import ModelRouter, RouterStrategy

router = ModelRouter(strategy=RouterStrategy.SMART)
provider = MyProvider()
router.registry.register(provider)

# 使用
response = await router.chat([ChatMessage(role="user", content="hello")])
```

### 1.4 自动初始化（环境变量）

设置以下环境变量，ModelRouter 会自动检测并注册：

| 环境变量 | 用途 | 默认值 |
|:---------|:-----|:-------|
| `LLAMA_SERVER_URL` | llama.cpp 服务地址 | `http://localhost:8080` |
| `GLM_API_KEY` | 智谱 GLM API Key | — |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | — |
| `QWEN_API_KEY` | 通义千问 API Key | — |
| `OLLAMA_API_BASE` | Ollama API 地址 | `http://localhost:11434` |
| `OLLAMA_MODEL_NAME` | Ollama 模型名称 | 自动发现 |

### 1.5 ProviderConfig 字段说明

| 字段 | 类型 | 说明 | 默认值 |
|:-----|:-----|:-----|:-------|
| `name` | str | Provider 唯一名称 | — |
| `display_name` | str | 显示名称 | — |
| `provider_type` | ProviderType | LOCAL / API | — |
| `tier` | ProviderTier | L1 / L2 / L3 | — |
| `enabled` | bool | 是否启用 | `True` |
| `priority` | int | 优先级（数字越小越高） | `100` |
| `api_base` | str | API 地址 | `""` |
| `api_key` | str | API 密钥 | `""` |
| `model_name` | str | 模型名称 | `""` |
| `max_tokens` | int | 最大输出 token | `512` |
| `temperature` | float | 温度 | `0.7` |
| `timeout` | int | 超时秒数 | `30` |
| `cost_per_1k_input` | float | 每1K输入token费用 | `0.0` |
| `cost_per_1k_output` | float | 每1K输出token费用 | `0.0` |
| `extra` | dict | 额外配置 | `{}` |

---

## 接口二：Tool Bridge 工具接口

### 2.1 目的

开发者可以注册自定义工具，让 LLM 通过 Function Calling 调用。

### 2.2 注册自定义工具

```python
from openclaw_bridge import ToolBridge, ToolDefinition, ToolResult

bridge = ToolBridge()

# 定义工具
def calculate(expression: str) -> str:
    """计算数学表达式"""
    try:
        result = eval(expression)  # 注意：生产环境需要安全沙箱
        return str(result)
    except Exception as e:
        return f"Error: {e}"

tool = ToolDefinition(
    name="calculator",
    description="计算数学表达式，如 '2 + 3 * 4'",
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "数学表达式",
            }
        },
        "required": ["expression"],
    },
    category="math",
    dangerous=False,
    enabled=True,
    handler=calculate,
)

bridge.register_tool(tool)

# 调用工具
result = bridge.call_tool("calculator", {"expression": "2 + 3 * 4"})
print(result.output)  # "14"
```

### 2.3 异步工具 handler

```python
async def async_fetch(url: str) -> str:
    """异步获取URL内容"""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.text()

tool = ToolDefinition(
    name="async_fetch",
    description="异步获取URL内容",
    parameters={...},
    handler=async_fetch,  # 异步函数自动检测
)
```

### 2.4 安全控制

```python
# 启用危险工具（file_write, shell_exec, http_post）
bridge.enable_dangerous_tools()

# 启用白名单模式（仅允许白名单内工具）
bridge.enable_whitelist(["file_read", "file_list"])

# 禁用白名单
bridge.disable_whitelist()
```

### 2.5 ToolDefinition 字段说明

| 字段 | 类型 | 说明 | 默认值 |
|:-----|:-----|:-----|:-------|
| `name` | str | 工具唯一名称 | — |
| `description` | str | 工具描述（LLM 用于决策） | — |
| `parameters` | dict | JSON Schema 参数定义 | — |
| `category` | str | 工具分类 | `"general"` |
| `dangerous` | bool | 是否危险（默认隐藏） | `False` |
| `enabled` | bool | 是否启用 | `True` |
| `handler` | Callable | 工具执行函数（同步或异步） | — |

### 2.6 OpenAI Function Calling 格式

```python
# 转换为 OpenAI functions 格式
functions = bridge.to_openai_functions()
# → [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}, ...]

# 从 LLM 响应中解析并执行工具
result = bridge.call_from_llm_response(llm_response_dict)
# → ToolResult(success=True, output="...", execution_time_ms=15.2)
```

---

## 接口三：OpenAI 兼容 API 接口

### 3.1 目的

将 HomeStream 的三层模型路由暴露为标准 OpenAI API，任何 OpenAI 客户端可直接接入。

### 3.2 启动服务

```python
from openai_compat_endpoint import create_standalone_app
import uvicorn

app = create_standalone_app()
uvicorn.run(app, host="0.0.0.0", port=8000)
```

### 3.3 API 端点

#### POST /v1/chat/completions

**请求格式**（与 OpenAI 完全兼容）：
```json
{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "max_tokens": 512,
  "temperature": 0.7,
  "stream": false
}
```

**非流式响应**：
```json
{
  "id": "chatcmpl-xxxxx",
  "object": "chat.completion",
  "created": 1720000000,
  "model": "qwen2.5:3b",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Hello! How can I help you?"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 8,
    "total_tokens": 18
  },
  "provider": "ollama",
  "tier": "L1",
  "latency_ms": 152.3,
  "cost_estimate": 0.0
}
```

**流式响应**（`stream: true`）：
```
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":"Hello"}}],"model":"qwen2.5:3b"}

data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":"! How"}}],"model":"qwen2.5:3b"}

data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":" can I"}}],"model":"qwen2.5:3b"}

data: {"id":"chatcmpl-xxx","choices":[{"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

#### GET /v1/models

```json
{
  "object": "list",
  "data": [
    {
      "id": "ollama",
      "object": "model",
      "created": 1720000000,
      "owned_by": "homestream",
      "tier": "L1"
    }
  ]
}
```

#### GET /v1/health

```json
{
  "status": "healthy",
  "providers": {
    "ollama": "healthy",
    "glm": "healthy"
  }
}
```

### 3.4 model 参数路由

| model 值 | 路由行为 |
|:---------|:---------|
| `"auto"` | SMART 策略自动选择最优 Provider |
| `"L1"` | 仅使用 L1 层级 Provider |
| `"L2"` | 仅使用 L2 层级 Provider |
| `"L3"` | 仅使用 L3 层级 Provider |
| `"ollama"` | 精确匹配 Provider 名称 |
| `"qwen2.5"` | 模糊匹配模型名称 |

### 3.5 认证

```bash
# 启用认证
export OPENAI_COMPAT_API_KEY="your-secret-key"

# 请求时携带
curl -H "Authorization: Bearer your-secret-key" \
     -H "Content-Type: application/json" \
     -d '{"model":"auto","messages":[{"role":"user","content":"hi"}]}' \
     http://localhost:8000/v1/chat/completions
```

### 3.6 使用 OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-secret-key",  # 或 None（如果未启用认证）
    base_url="http://localhost:8000/v1",
)

response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

### 3.7 集成到现有 FastAPI 应用

```python
from fastapi import FastAPI
from openai_compat_endpoint import create_openai_router

app = FastAPI()
router = create_openai_router()
app.include_router(router)
```

---

## 版本兼容性

| 接口 | 版本 | 兼容性 |
|:-----|:-----|:-------|
| Provider 接口 | v1.0 (v5.1.0) | 向后兼容 |
| Tool Bridge 接口 | v1.0 (v5.1.0) | 向后兼容 |
| OpenAI 兼容 API | v1 (OpenAI API spec) | 跟随 OpenAI 规范更新 |
