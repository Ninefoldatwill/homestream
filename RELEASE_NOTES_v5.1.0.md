# HomeStream V5.1.0 Release Notes

> **从单维度对话，到三维立体化生态。**
>
> 开源版 V5.1.0 · 三维立体化对接 + 学习优化 · 2026-07-11

---

## 🔑 版本亮点

V5.1.0 是 V5.0.0 首次开源发布后的第一个功能版本。如果说 V5.0.0 铸造了钥匙的形状，V5.1.0 则为这把钥匙赋予了三个维度的立体结构——**让 AI 能想、能做、能被接入**。

### 三维升级一览

| 维度 | 名称 | 能力 | 核心模块 | 差异化 |
|:-----|:-----|:-----|:---------|:-------|
| X 轴 | 模型维度 | AI 能"想" | OllamaProvider | **原生 API 而非兼容层，获取 keep_alive/自动发现独有能力** |
| Y 轴 | 执行维度 | AI 能"做" | Tool Bridge | **干净室实现，OpenAI Function Calling 兼容，6个内置工具** |
| Z 轴 | 交互维度 | 外部能"接入" | OpenAI 兼容 API | **任何 OpenAI 客户端零改造接入，流式 + 非流式** |

### 学习优化三引擎

| 引擎 | 理论基础 | 效果 |
|:-----|:---------|:-----|
| 递归 CTE 因果链 | SQL WITH RECURSIVE | 因果链遍历从 Python 下推到 SQLite 引擎，减少内存开销 |
| SurprisalGate | 香农信息熵 + 预测编码 | 自适应过滤低信息量内容，减少记忆噪声 |
| RouterScore | 多准则决策分析(MCDA) | 6维度加权评分，SMART 策略自动选择最优 Provider |

---

## 🚀 快速开始

### 升级到 V5.1.0

```bash
# 已有 V5.0.0 用户：拉取最新代码即可
git pull origin main
pip install -r requirements.txt  # 无新增依赖

# 新用户：一键安装
curl -fsSL https://raw.githubusercontent.com/Ninefoldatwill/homestream/main/install.sh | bash

# 国内用户推荐 Gitee 镜像
curl -fsSL https://gitee.com/the-warrior-king/homestream/raw/main/install.sh | bash
```

### X轴：接入 Ollama 本地模型

```python
from providers.ollama_provider import create_qwen_ollama

# 创建 Ollama Provider（需要本地安装 Ollama + 拉取模型）
provider = create_qwen_ollama(
    api_base="http://localhost:11434",
    model_name="qwen2.5:3b"
)

# 检查健康状态
health = await provider.health_check()
print(f"Status: {health.status}")  # HealthStatus.HEALTHY

# 对话
response = await provider.chat(
    messages=[{"role": "user", "content": "你好，请介绍一下自己"}]
)
print(response.content)
```

**环境变量自动注册**（无需写代码）：

```bash
export OLLAMA_API_BASE=http://localhost:11434
export OLLAMA_MODEL_NAME=qwen2.5:3b
# ModelRouter 启动时自动检测并注册 Ollama Provider
```

### Y轴：给 AI 装上手和脚

```python
from openclaw_bridge import ToolBridge, ToolDefinition

bridge = ToolBridge()

# 注册自定义工具
bridge.register(ToolDefinition(
    name="get_weather",
    description="获取指定城市的天气",
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名称"}
        },
        "required": ["city"]
    },
    handler=lambda city: {"city": city, "temp": 25, "weather": "晴"}
))

# 查看所有工具（OpenAI Function Calling 格式）
tools = bridge.to_openai_tools()
# -> [{"type": "function", "function": {"name": "get_weather", ...}}]

# 执行工具调用
result = await bridge.execute("get_weather", {"city": "北京"})
print(result.output)  # {"city": "北京", "temp": 25, "weather": "晴"}
```

**6个内置工具**：`file_read` / `file_write` / `file_list` / `shell_exec` / `http_get` / `http_post`

### Z轴：OpenAI 兼容 API 接入

```python
# 任何 OpenAI 客户端都可以直接接入 HomeStream
from openai import OpenAI

client = OpenAI(
    api_key="your-api-key",  # 如果设置了 OPENAI_COMPAT_API_KEY
    base_url="http://localhost:3458/v1"
)

# 非流式
response = client.chat.completions.create(
    model="auto",  # 自动路由到最优 Provider
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)

# 流式
stream = client.chat.completions.create(
    model="L1",  # 指定使用本地模型
    messages=[{"role": "user", "content": "讲个故事"}],
    stream=True
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")
```

**model 参数路由格式**：

| 格式 | 示例 | 行为 |
|:-----|:-----|:-----|
| `auto` | `auto` | RouterScore 自动选择最优 Provider |
| 层级 | `L1` / `L2` / `L3` | 指定使用某一层的 Provider |
| Provider名 | `ollama` / `deepseek` | 指定具体 Provider |
| 模型名 | `qwen2.5:3b` | 模糊匹配包含该名称的 Provider |

### SMART 路由策略

```python
from model_router import ModelRouter, RouterStrategy

# 启用 SMART 策略（按 6 维度评分自动排序 Provider）
router = ModelRouter(strategy=RouterStrategy.SMART)

# RouterScore 会追踪每次请求并动态调整评分
# 6个维度：延迟 / 成本 / 健康度 / 新鲜度 / 成功率 / 负载

# 查看评分看板
from router_score import RouterScore
scorer = RouterScore()
print(scorer.get_scoreboard())
```

**4种预设权重方案**：

| 方案 | 适用场景 | 权重侧重 |
|:-----|:---------|:---------|
| `create_balanced_weights()` | 通用 | 6维度均衡 |
| `create_cost_optimized_weights()` | 成本敏感 | 成本维度权重最高 |
| `create_speed_optimized_weights()` | 实时场景 | 延迟维度权重最高 |
| `create_reliability_optimized_weights()` | 生产环境 | 成功率+健康度权重最高 |

---

## 📊 新增规模

| 模块 | 代码行数 | 测试数 | 状态 |
|:-----|:---------|:-------|:-----|
| OllamaProvider | ~310行 | 38 | ✅ |
| Tool Bridge | ~570行 | 54 | ✅ |
| OpenAI 兼容端点 | ~350行 | 36 | ✅ |
| CTE 因果链优化 | ~120行 | 19 | ✅ |
| SurprisalGate | ~280行 | 59 | ✅ |
| RouterScore | ~520行 | 77 | ✅ |
| **合计** | **~2150行** | **283 tests** | **全绿** |

### 累计指标

| 指标 | V5.0.0 | V5.1.0 | 增量 |
|:-----|:-------|:-------|:-----|
| 测试用例 | 853* | 363** | +283 |
| 代码行数 | ~20,000 | ~22,150 | +2,150 |
| API 路由 | 76 | 80+ | +4 |
| Provider 类型 | 3 | 4 | +1 (Ollama) |

> \* V5.0.0 统计含内部模块测试；\*\* V5.1.0 开源版精简后统计

---

## 🏗️ 架构决策

### 1. 三维正交设计

模型(X) / 执行(Y) / 交互(Z) 三轴完全独立，可单独使用也可任意组合：
- 只用 X 轴：OllamaProvider 作为独立 LLM 接入
- 只用 Y 轴：ToolBridge 作为工具执行引擎
- 只用 Z 轴：OpenAI 兼容 API 作为模型代理
- 三轴组合：完整的 AI Agent 生态

### 2. Ollama 原生 API（而非 OpenAI 兼容层）

放弃 Ollama 的 `/v1/chat/completions` 兼容端点，改用原生 `/api/chat` + `/api/tags`：
- 获取 `keep_alive`（模型常驻内存）独有能力
- 获取 `format`（结构化输出 JSON）独有能力
- 获取 `options`（temperature/top_p/num_ctx 等精细参数）独有能力
- 模型自动发现（`list_models()`）

### 3. 干净室实现（Clean Room Implementation）

ToolBridge 受 OpenClaw "给AI装上手和脚"理念启发，但完全独立实现：
- 未参考 OpenClaw 源代码
- 数据结构自行设计（ToolDefinition / ToolResult）
- 安全模型自行设计（dangerous标记 / 白名单 / 超时 / 参数验证）
- 依据：17 U.S.C. S 102(b) — 思想/方法不受版权保护

### 4. SQL CTE 下推

将因果链遍历从 Python `while` 循环下推到 SQLite `WITH RECURSIVE` CTE：
- 减少内存开销（不需要在 Python 中维护 visited 集合）
- 路径字符串循环检测（`path LIKE '%event_id%'`）
- `max_depth` 安全阀防止无限递归

### 5. 预测编码 + 信息熵

SurprisalGate 融合两个学术理论：
- **香农信息熵**：`surprisal = -log2(P(token))`，衡量 token 的意外程度
- **预测编码理论**：大脑优先处理预测误差大的信息
- 三维度评估：内容信息量 + 上下文信息量 + 新词奖励

### 6. 6维度 MCDA 路由评分

RouterScore 基于多准则决策分析（Multi-Criteria Decision Analysis）：
- 6个维度：延迟 / 成本 / 健康度 / 新鲜度 / 成功率 / 负载
- 加权评分：`score = Sigma(weight_i * dimension_score_i)`
- 新鲜度指数衰减（6小时半衰期）
- 负载实时感知（并发请求数）
- 线程安全（RLock）

---

## 🔧 环境变量参考

### 新增环境变量

| 变量名 | 用途 | 默认值 | 示例 |
|:-------|:-----|:-------|:-----|
| `OLLAMA_API_BASE` | Ollama API 地址 | `http://localhost:11434` | `http://192.168.1.100:11434` |
| `OLLAMA_MODEL_NAME` | 默认模型名 | 自动发现 | `qwen2.5:3b` |
| `OPENAI_COMPAT_API_KEY` | API 认证密钥（可选） | 无（不认证） | `sk-homestream-xxx` |

### 兼容环境变量（V5.0.0 已有）

| 变量名 | 用途 |
|:-------|:-----|
| `OPENBRIDGE_TOKEN` | 主服务认证 Token |
| `DEEPSEEK_API_KEY` | DeepSeek Provider |
| `GLM_API_KEY` | GLM Provider |
| `SILICONFLOW_API_KEY` | 硅基流动 Provider |

---

## 📚 新增文档

| 文档 | 内容 |
|:-----|:-----|
| `docs/ECOSYSTEM_3D_ARCHITECTURE.md` | 三维立体化架构设计（X/Y/Z轴定位、协同流程、技术选型） |
| `docs/DIMENSION_INTERFACE_SPEC.md` | 三套开源接口规范（Provider扩展/Tool Bridge/OpenAI兼容） |
| `docs/INSPIRATION_LOG.md` | 灵感来源声明与干净室实现记录（6模块 + 商标声明） |

---

## 🔄 迁移指南（V5.0.0 -> V5.1.0）

### 无破坏性变更

V5.1.0 完全向后兼容 V5.0.0，所有现有代码无需修改。

### 可选升级

1. **启用 Ollama 本地模型**：安装 Ollama + 设置环境变量即可
2. **启用 SMART 路由**：`ModelRouter(strategy=RouterStrategy.SMART)`
3. **启用 OpenAI 兼容 API**：在 FastAPI 中挂载 `create_openai_router()`
4. **启用 Tool Bridge**：`from openclaw_bridge import ToolBridge`

### 路由策略对比

| 策略 | V5.0.0 | V5.1.0 新增 |
|:-----|:-------|:------------|
| ROUND_ROBIN | ✅ | — |
| FAILOVER | ✅ | — |
| SMART | — | ✅ 按6维度评分自动排序 |

---

## 📜 商标与合规

- **HomeStream** 是九重工作室的商标（见 [TRADEMARK.md](TRADEMARK.md)）
- **Ollama** 是 Ollama Inc. 的商标，HomeStream 仅提供 API 适配器
- **OpenClaw** 理念启发已声明（见 `docs/INSPIRATION_LOG.md`），代码为干净室独立实现
- **OpenAI** API 格式为行业标准开放规范，兼容使用不涉及商标侵权

---

## 🛣️ 路线图

### V5.1.0（本次发布）
- ✅ 三维立体化对接（Ollama + ToolBridge + OpenAI兼容API）
- ✅ 学习优化（CTE因果链 + SurprisalGate + RouterScore）
- ✅ 三份设计文档 + 灵感声明

### V5.2.0（下一个版本）
- HS-UI 协议（Agent 驱动的声明式 UI 描述）
- 自适应接口模式（设备/角色/场景检测）
- Tauri 2.0 桌面/移动 App 封装

### V5.3.0（计划中）
- 可视化工作流编排器（拖拽式 Agent 工作流）
- 浏览器自动化插件市场
- 书阁知识库完善
- 开源终版

---

## 🙏 致谢

### 理念启发（均为概念参考，未使用其代码）

- **OpenClaw** (MIT, Peter Steinberger) — "给AI装上手和脚"的执行维度理念
- **Claude Code / Claude Agent SDK** (Anthropic) — Tool 使用模式参考
- **OpenAI Function Calling** — 行业标准工具调用格式
- **香农信息论** — SurprisalGate 的理论基础
- **预测编码理论** (Karl Friston) — SurprisalGate 的认知科学基础
- **MCDA 多准则决策分析** — RouterScore 的决策科学基础

融众之优，铸己之新。不造墙，只铸钥。

---

## 📄 许可证

MIT License — 见 [LICENSE](LICENSE)

"HomeStream" 是九重工作室的商标 — 见 [TRADEMARK.md](TRADEMARK.md)

---

<p align="center">
  铸钥匠 · 九重工作室 · 2026<br>
  🔑 从单维度对话，到三维立体化生态。<br>
  每个人在 AI 世界的家园，流光汇河。
</p>
