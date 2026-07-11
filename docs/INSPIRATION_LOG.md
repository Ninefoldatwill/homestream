# HomeStream 灵感来源声明与干净室实现记录

> 本文档记录 HomeStream v5.1.0 各模块的灵感来源、设计决策和干净室实现证据。
>
> 依据：17 U.S.C. § 102(b) — 思想、程序、过程、系统、操作方法不受版权保护。

---

## 1. OllamaProvider（`providers/ollama_provider.py`）

### 灵感来源
- **Ollama 官方 API 文档**（https://ollama.com/docs/api）— 公开 API 规范
- **HomeStream 既有 Provider 架构**（`providers/base_provider.py`）— 自有代码

### 设计决策
- 使用 Ollama 原生 API（`/api/chat` + `/api/tags`）而非 OpenAI 兼容端点
- 理由：原生 API 提供 `keep_alive`、`format`、模型自动发现等独有功能
- `keep_alive` 参数允许模型常驻内存，避免重复加载延迟

### 干净室证据
- **未参考任何第三方 Ollama Python 客户端源代码**
- 实现基于 Ollama 官方公开 API 文档的 HTTP 端点描述
- 数据结构（`OllamaModelInfo`）为 HomeStream 原创设计
- 4个工厂函数为 HomeStream 原创设计
- 测试用例全部使用 Mock，未依赖 Ollama 实际运行

### 商标声明
- "Ollama" 是 Ollama Inc. 的商标
- `OllamaProvider` 是与 Ollama API 的适配层，不包含 Ollama 的模型权重或源代码
- 与 Ollama Inc. 无关联、未获其背书

---

## 2. OpenClaw Tool Bridge（`openclaw_bridge.py`）

### 灵感来源
- **OpenClaw 项目理念**："给 AI 装上手和脚"（公开宣传口号）
- **OpenAI Function Calling 规范**（https://platform.openai.com/docs/guides/function-calling）— 公开 API 规范
- **HomeStream 既有安全设计**（`permission_guard.py`、`prompt_security.py`）— 自有代码

### 设计决策
- 采用 `dangerous` 标记区分安全/危险工具（受 Unix 权限模型启发）
- 使用 JSON Schema 进行参数验证（OpenAI Function Calling 规范）
- 支持同步和异步 handler（Python `inspect.iscoroutinefunction` 自动检测）
- GatewayBridge 设计用于未来连接外部工具网关

### 干净室证据
- **未阅读、未参考、未派生 OpenClaw 的任何源代码**
- 6个内置工具（file_read/file_write/file_list/shell_exec/http_get/http_post）为通用软件工程实践
- `ToolDefinition` 数据结构基于 OpenAI Function Calling 公开规范
- `ToolResult` 数据结构为 HomeStream 原创设计
- 安全控制逻辑（dangerous标记/白名单/超时/参数验证）为 HomeStream 原创设计
- 所有测试用例独立编写，未使用 OpenClaw 的测试代码

### 商标声明
- "OpenClaw" 是其各自所有者的商标
- 本模块受 OpenClaw 理念启发但为完全独立实现（干净室）
- 与 OpenClaw 无关联、未获其背书

---

## 3. OpenAI 兼容 API 端点（`openai_compat_endpoint.py`）

### 灵感来源
- **OpenAI API 参考文档**（https://platform.openai.com/docs/api-reference）— 公开 API 规范
- **FastAPI 官方文档**（https://fastapi.tiangolo.com）— 公开框架文档
- **HomeStream 既有 ModelRouter**（`model_router.py`）— 自有代码

### 设计决策
- 实现标准 OpenAI Chat Completions API 格式
- SSE 流式响应：将完整响应分成 20 字符 chunk 模拟流式
- HomeStream 扩展字段（provider/tier/latency_ms/cost_estimate）附加在标准响应中
- 可选 API Key 认证（通过环境变量控制）

### 干净室证据
- **未参考任何第三方 OpenAI 兼容服务器源代码**（如 LiteLLM、LocalAI 等）
- 实现完全基于 OpenAI 公开 API 文档的请求/响应格式描述
- Pydantic 模型（`ChatCompletionMessage`、`ChatCompletionRequest`）基于 OpenAI 公开规范
- `_resolve_tier()` 路由逻辑为 HomeStream 原创设计
- SSE 流式实现使用标准 Python `asyncio` + FastAPI `StreamingResponse`
- 所有测试用例使用 Mock Provider，独立编写

### 商标声明
- "OpenAI" 是 OpenAI Inc. 的商标
- 本模块实现 OpenAI API 格式规范，不使用 OpenAI 的任何专有代码
- 与 OpenAI Inc. 无关联、未获其背书

---

## 4. 递归 CTE 因果链优化（`event_store.py`）

### 灵感来源
- **SQLite WITH RECURSIVE 文档**（https://www.sqlite.org/lang_with.html）— 公开 SQL 规范
- **HomeStream 既有 EventStore**（`event_store.py`）— 自有代码

### 设计决策
- 用 SQL `WITH RECURSIVE` CTE 替换 Python while 循环
- 理由：将遍历逻辑下推到 SQLite 引擎，减少 Python↔SQLite 跨语言调用
- 添加 `max_depth` 安全阀防止无限递归
- 路径字符串 `'/id1/id2/'` 实现循环检测

### 干净室证据
- SQL CTE 语法基于 SQLite 官方公开文档
- `query_descendants()` 和 `get_cause_depth()` 为 HomeStream 原创设计
- 所有测试用例独立编写

---

## 5. SurprisalGate 信息密度过滤（`surprisal_gate.py`）

### 灵感来源
- **香农信息熵理论**（Claude Shannon, 1948）— 公开学术理论
- **预测编码理论**（Karl Friston, 2005）— 公开学术理论
- **HomeStream 既有 EventStream**（`event_stream.py`）— 自有代码

### 设计决策
- 三维度评估：内容信息量 + 上下文信息量 + 新词奖励
- 自适应阈值：warmup 观察期后基于历史统计动态调整
- 词汇表上限保护：防止内存无限增长
- 中英文混合分词：CJK字符逐字 + ASCII按空格/标点

### 干净室证据
- **未参考任何第三方信息论实现代码**
- surprisal 计算公式基于香农信息熵公开数学定义：`I(x) = -log2(P(x))`
- Laplace 平滑为标准统计技术
- 指数衰减新鲜度评分为 HomeStream 原创设计
- 自适应阈值算法为 HomeStream 原创设计
- 所有测试用例独立编写

### 理论引用
- Shannon, C. E. (1948). "A Mathematical Theory of Communication". Bell System Technical Journal.
- Friston, K. (2005). "A theory of cortical responses". Philosophical Transactions of the Royal Society B.

---

## 6. RouterScore 多维度路由评分（`router_score.py`）

### 灵感来源
- **多准则决策分析（MCDA）** — 公开学术理论
- **HomeStream 既有 ModelRouter**（`model_router.py`）— 自有代码
- **HomeStream 既有 BaseProvider 统计接口**（`providers/base_provider.py`）— 自有代码

### 设计决策
- 6维度加权评分：延迟/成本/健康度/新鲜度/成功率/负载
- 各维度归一化到 [0, 1]，综合评分也在 [0, 1]
- 新 Provider 使用中性分数 0.5 给予公平机会
- 4种预设权重方案：均衡/成本优先/速度优先/可靠性优先
- 线程安全：RLock 保护元数据操作

### 干净室证据
- **未参考任何第三方路由评分系统源代码**
- 加权求和公式为标准 MCDA 方法
- 指数衰减新鲜度评分为 HomeStream 原创设计
- `ProviderMeta` 运行时元数据追踪为 HomeStream 原创设计
- 4种预设权重方案为 HomeStream 原创设计
- 所有测试用例独立编写

---

## 7. 总体声明

### 7.1 干净室原则

HomeStream v5.1.0 的所有新增模块均遵循干净室实现原则：
1. **仅参考公开文档和公开学术理论**
2. **未阅读、未复制、未派生任何第三方源代码**
3. **所有数据结构、算法逻辑、测试用例均为独立编写**
4. **灵感来源和设计决策完整记录于本文档**

### 7.2 依据

- 17 U.S.C. § 102(b): 思想、程序、过程、系统、操作方法不受版权保护
- 公开 API 规范属于事实描述，不受版权保护
- 学术理论属于思想范畴，不受版权保护

### 7.3 商标清单

| 商标 | 所有者 | 使用方式 | 关系 |
|:-----|:-------|:---------|:-----|
| Ollama | Ollama Inc. | API 适配层名称 | 无关联 |
| OpenClaw | 各自所有者 | 理念引用 | 无关联 |
| OpenAI | OpenAI Inc. | API 格式兼容 | 无关联 |
| Qwen | 阿里巴巴 | 模型名称 | 描述性使用 |
| DeepSeek | 深度求索 | 模型名称 | 描述性使用 |
| GLM | 智谱AI | 模型名称 | 描述性使用 |

### 7.4 维护

本文档随版本更新维护。每次新增模块时，必须同步添加对应的灵感来源声明和干净室证据。
