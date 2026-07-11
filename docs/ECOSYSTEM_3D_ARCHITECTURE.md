# HomeStream 三维立体化架构

> v5.1.0 核心设计文档 — 让 AI 能想、能做、能被接入

---

## 1. 设计哲学

HomeStream v5.1.0 的核心突破在于将 AI 系统从"单维度对话"升级为"三维立体化生态"：

| 维度 | 名称 | 能力 | 对应模块 | 代码量 |
|:-----|:-----|:-----|:---------|:-------|
| **X 轴** | 模型维度 | AI 能"想" | `providers/ollama_provider.py` | ~310行 |
| **Y 轴** | 执行维度 | AI 能"做" | `openclaw_bridge.py` | ~570行 |
| **Z 轴** | 交互维度 | 外部能"接入" | `openai_compat_endpoint.py` | ~350行 |

三轴正交，任意组合——这就是"立体化"的含义：

```
                    Z (交互维度)
                    │
                    │  / OpenAI兼容API
                    │ /
                    │/
     ───────────────╫─────────────── X (模型维度)
                   /│
         Ollama   / │
        Provider  / │
                 /  │
                    │ Y (执行维度)
                    │ ToolBridge
                    │
```

---

## 2. X 轴：模型维度 — OllamaProvider

### 2.1 定位

让 AI 能"想"——通过 Ollama 原生 API 接入本地大模型，实现真正的离线推理。

### 2.2 核心设计

**使用 Ollama 原生 API 而非 OpenAI 兼容端点**，因为原生 API 提供：
- `keep_alive`：模型常驻内存，避免重复加载
- `format`：结构化输出（JSON / 正则约束）
- `/api/tags`：模型自动发现
- `options`：精细参数控制（temperature / num_predict / top_p）

### 2.3 架构

```
OllamaProvider (BaseProvider)
├── chat()          → POST /api/chat
├── health_check()  → GET /api/tags + 模型匹配
├── list_models()   → 模型自动发现
├── is_running()    → 快速检测 Ollama 是否在线
└── 工厂函数
    ├── create_ollama_provider()      — 通用
    ├── create_ollama_qwen_provider() — Qwen系列
    ├── create_ollama_llama_provider() — Llama系列
    └── create_ollama_mistral_provider() — Mistral系列
```

### 2.4 自动发现机制

在 `ModelRouter.auto_init_from_env()` 中：
1. 读取 `OLLAMA_API_BASE` + `OLLAMA_MODEL_NAME` 环境变量
2. 有模型名 → 直接注册
3. 无模型名 → `is_running()` 检测 + `list_models()` 自动发现第一个可用模型

### 2.5 零成本设计

Ollama 本地运行的模型成本为 $0.00：
```python
self.config.cost_per_1k_input = 0.0
self.config.cost_per_1k_output = 0.0
```

在 RouterScore 多维度评分中，零成本 = 成本维度满分。

---

## 3. Y 轴：执行维度 — OpenClaw Tool Bridge

### 3.1 定位

让 AI 能"做"——给大模型装上"手和脚"，让它能读写文件、执行命令、发起网络请求。

### 3.2 干净室实现声明

> **受 OpenClaw "给AI装上手和脚"理念启发，但所有代码均为独立实现。**
>
> 本模块没有复制、参考或派生自 OpenClaw 的任何源代码。
> 实现细节基于通用软件工程实践和 HomeStream 项目自身需求。
> "OpenClaw" 是其各自所有者的商标，本模块与 OpenClaw 无关联、未获其背书。

### 3.3 核心架构

```
ToolBridge
├── 工具注册
│   ├── register_tool()         — 注册自定义工具
│   ├── unregister_tool()       — 注销工具
│   ├── list_tools()            — 列出所有可用工具
│   ├── get_tool_schema()       — 获取工具JSON Schema
│   └── to_openai_functions()   — 转换为OpenAI Function Calling格式
│
├── 工具调用
│   ├── call_tool()             — 直接调用（同步/异步handler）
│   └── call_from_llm_response() — 解析LLM tool_calls响应
│
├── 安全控制
│   ├── dangerous标记            — 危险工具默认隐藏
│   ├── 白名单模式              — 仅允许白名单内工具
│   ├── 超时控制                — 防止工具执行卡死
│   └── 参数验证                — JSON Schema校验
│
├── 6个内置工具
│   ├── file_read   (safe)      — 读取文件
│   ├── file_write  (dangerous) — 写入文件
│   ├── file_list   (safe)      — 列出目录
│   ├── shell_exec  (dangerous) — 执行Shell命令
│   ├── http_get    (safe)      — HTTP GET请求
│   └── http_post   (dangerous) — HTTP POST请求
│
└── GatewayBridge
    ├── health_check()           — 网关健康检查
    ├── discover_tools()         — 发现网关工具
    └── register_gateway_tools() — 注册网关工具
```

### 3.4 OpenAI Function Calling 兼容

`ToolDefinition.to_openai_dict()` 输出标准 OpenAI 格式：
```json
{
  "type": "function",
  "function": {
    "name": "file_read",
    "description": "读取文件内容",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {"type": "string", "description": "文件路径"}
      },
      "required": ["path"]
    }
  }
}
```

这使得 ToolBridge 可以无缝对接任何支持 Function Calling 的大模型。

---

## 4. Z 轴：交互维度 — OpenAI 兼容 API 端点

### 4.1 定位

让外部能"接入"——将 HomeStream 的三层模型路由暴露为标准 OpenAI API，任何 OpenAI 客户端可直接接入。

### 4.2 API 端点

| 方法 | 路径 | 功能 |
|:-----|:-----|:-----|
| POST | `/v1/chat/completions` | 聊天补全（流式 + 非流式） |
| GET | `/v1/models` | 模型列表 |
| GET | `/v1/health` | 健康检查 |
| GET | `/v1/` | API 信息 |

### 4.3 模型路由参数

`model` 参数支持多种格式，自动路由到对应 Provider：

| model 参数 | 路由行为 |
|:----------|:---------|
| `"auto"` | 按 RouterStrategy 自动选择最优 Provider |
| `"L1"` / `"L2"` / `"L3"` | 指定 Tier 层级 |
| Provider 名称 | 精确匹配 Provider（如 `"ollama"`） |
| 模型名称 | 模糊匹配（如 `"qwen"` 匹配含 "qwen" 的 Provider） |

### 4.4 流式响应

SSE（Server-Sent Events）流式响应：
- 将完整响应分成 20 字符的 chunk
- 通过 `text/event-stream` 发送
- 每个 chunk 遵循 OpenAI `delta` 格式
- 最后发送 `[DONE]` 标记

### 4.5 HomeStream 扩展字段

非流式响应中包含 HomeStream 独有扩展字段：
```json
{
  "provider": "ollama",        // 使用的 Provider
  "tier": "L1",                 // Provider 层级
  "latency_ms": 152.3,          // 响应延迟
  "cost_estimate": 0.0          // 费用估算
}
```

### 4.6 可选认证

通过 `OPENAI_COMPAT_API_KEY` 环境变量启用 API Key 认证：
- 未设置 → 无认证（适合本地使用）
- 已设置 → 请求需携带 `Authorization: Bearer <key>`

---

## 5. 三轴协同

### 5.1 典型流程

```
外部客户端
    │
    │ POST /v1/chat/completions  ← Z轴（交互维度）
    ▼
OpenAI 兼容端点
    │
    │ 解析 model 参数 → 路由到 Provider  ← X轴（模型维度）
    ▼
OllamaProvider.chat()
    │
    │ LLM 返回 tool_calls  ← Y轴（执行维度）
    ▼
ToolBridge.call_from_llm_response()
    │
    │ 执行工具 → 返回结果
    ▼
OllamaProvider.chat()  ← 二次调用，携带工具结果
    │
    ▼
最终响应 → 通过 Z 轴返回给客户端
```

### 5.2 独立可用

三个维度可以独立使用：
- **只用 X 轴**：通过 ModelRouter 直接调用 OllamaProvider
- **只用 Y 轴**：通过 ToolBridge 给任何 LLM 添加工具能力
- **只用 Z 轴**：将现有三层路由暴露为 OpenAI API
- **三轴组合**：完整的 Agentic AI 体验

---

## 6. 测试覆盖

| 模块 | 测试文件 | 测试数 | 状态 |
|:-----|:---------|:-------|:-----|
| OllamaProvider | `test_ollama_provider.py` | 38 | ✅ 全绿 |
| ToolBridge | `test_openclaw_bridge.py` | 54 | ✅ 全绿 |
| OpenAI兼容端点 | `test_openai_compat_endpoint.py` | 36 | ✅ 全绿 |
| **合计** | | **128** | ✅ |

---

## 7. 商标与知识产权声明

- **Ollama** 是 Ollama Inc. 的商标。HomeStream 的 `OllamaProvider` 是与 Ollama API 的适配层，不包含 Ollama 的模型权重或源代码。
- **OpenClaw** 是其各自所有者的商标。`openclaw_bridge.py` 受其理念启发但为独立实现（干净室），与 OpenClaw 无关联。
- **OpenAI** 是 OpenAI Inc. 的商标。HomeStream 的兼容端点实现 OpenAI API 格式规范，不使用 OpenAI 的任何专有代码。

详见 `TRADEMARK.md` 和 `NOTICE`。
