# Changelog

All notable changes to HomeStream are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [5.2.0] — 2026-07-12

### 🎙️ 语音触达 — 让 AI 不只能看,还能听能说

HomeStream V5.2.0 — 从"文字/视觉交互"升级为"全模态实时语音交互"。用户对着浏览器说话,AI 实时听懂、思考、语音回复,延迟 < 600ms。

> **铸钥匠原则坚守**: 全自托管、零注册、零账号、零云端依赖。LiveKit SFU + FunASR 2-pass + CosyVoice2 全部本地运行,只吃你自己的 CPU/GPU。

---

### ✨ 新增 (Added)

#### VoiceBridge 语音栈（`voice/` 模块，14 文件, ~1500 行）
- **自托管 LiveKit Server**（`voice/docker-compose.yml` + `voice/livekit.yaml`）
  - Apache 2.0 协议，Docker 一键启动，**零注册**（本地 generate-keys 自建凭证）
  - WebRTC SFU（端口 7880/7881 + UDP 50000-50100），支持 barge-in 实时打断
  - 与铸钥匠"自托底"基因对齐：完全替代 LiveKit Cloud
- **FunASR 2-pass STT**（`voice/stt_adapter.py`）
  - Pass 1: `paraformer-zh-streaming`（流式 Paraformer，~80ms 延迟，边说边出字）
  - Pass 2: `SenseVoiceSmall`（句末整段重写，高准确率 + 情感 + 事件标签）
  - WebSocket 客户端（`ws://localhost:10096`）+ 自动重连 + 标签解析
  - **情感白送**：`<|HAPPY|><|SAD|><|ANGRY|><|NEUTRAL|>` 7 种 + `<|Speech|><|Music|><|Applause|>` 4 事件
- **CosyVoice2 TTS**（`voice/tts_adapter.py`）
  - 启用 `use_flow_cache=True` 官方流式优化（150ms 首包延迟）
  - 10 个内置声音（longxiaochun/longwan/longcheng...）
  - 18+ 中文方言，零样本声音克隆（3秒参考音频）
- **HomeStreamLLM 接入**（`voice/llm_adapter.py`）
  - 覆写 LiveKit `llm_node`，将对话路由到三层 ModelRouter
  - 策略：`SPEED_FIRST`（语音场景默认，本地 Ollama 优先）
- **Silero VAD 预加载**（`voice/agent.py`）
  - Worker 启动时预热模型，减少首次连接延迟
- **完整 Docker 部署栈**（`voice/docker-compose.yml`）
  - 2 个服务：LiveKit + FunASR 2-pass
  - 模型预下载脚本（`voice/predownload_funasr_models.py`，从 ModelScope 拉 1.6GB）
  - 容器内链接脚本（`voice/link-and-start.sh`）解决 snapshot_download 目录结构问题
  - 热词文件占位（`voice/funasr-hotwords.txt`）
- **配置系统**（`voice/config.py`）
  - 全环境变量可配，缺省即自托管 localhost 模式
  - 支持 FunASR URI / TTS 模型路径 / 声音 / 语速 / VAD 阈值等 16 个配置项

### 🔧 变更 (Changed)
- `.env.example`：新增 12 个 VoiceBridge 环境变量（funasr_ws_uri / tts_model_path / tts_voice / vad_threshold 等）
- `requirements.txt`：新增 `livekit-agents`、`funasr`、`websockets`、`numpy` 依赖
- `.gitignore`：排除 `voice/funasr-models/`（1.6GB 本地模型）和 `voice/__pycache__/`

### 🧪 测试 (Tests)
- **新增测试套件**：
  - `test_voice_stt_tts.py`（31 tests）：FunASR 2-pass 消息解析、SenseVoice 标签解析、帧→PCM 转换、TTS 引擎
  - `test_voice_llm_adapter.py`（16 tests）：HomeStreamLLM 初始化、ChatContext 转换、路由调用、LLM 节点流
- **总测试数**：47 个 v5.2.0 新增测试，全部通过 ✅
- **执行时间**：0.62s（无 IO，纯单测）

### 📦 新增依赖
- `livekit-agents~=1.4`（LiveKit Agent SDK）
- `livekit-plugins-silero`（VAD）
- `livekit-plugins-turn-detector`（多语种端点检测）
- `websockets`（FunASR WebSocket 客户端）
- `funasr`（本地 STT 测试用）
- `modelscope`（模型预下载用）
- `numpy`（音频处理）

### 🏗️ 架构

```
用户浏览器 ──WebRTC──→ LiveKit SFU (localhost:7880, 自托管Docker)
                              │
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
               Silero VAD   STT     TTS
              (本地,免费)   (FunASR  (CosyVoice2
                           2-pass)   本地GPU,
                    │         │      use_flow_cache)
                    │         ▼         ▲
                    │   HomeStreamLLM (llm_node 覆写)
                    │   ┌──────────────────────┐
                    │   │  ModelRouter          │
                    │   │  L1 本地Ollama        │ ← 离线可用
                    │   │  L2 免费GLM API       │ ← 主线路
                    │   │  L3 付费DeepSeek      │ ← 复线
                    │   │  双保障自动切换        │
                    │   └──────────────────────┘
                    │         │
                    └─────────┴─────────┘
                     barge-in (DataChannel, 零延迟)
```

### 🚀 6 步起手

```bash
# 1. 预下载模型 (Windows 本机, 1.6GB, 5 分钟)
cd voice && python predownload_funasr_models.py

# 2. 启动双 Docker (零注册)
docker compose up -d
# → LiveKit:7880  FunASR:10096

# 3. 验证服务
curl http://localhost:7880/rtc/health  →  OK

# 4. 装 SDK
pip install "livekit-agents[turn-detector,silero]~=1.4" websockets

# 5. 启动 Agent Worker
cd .. && python -m voice.agent dev

# 6. 浏览器连 LiveKit Playground (http://localhost:7880) 说话测试
```

### ⚠️ 已知限制
- FunASR Docker 镜像首次拉取约 1.27GB（阿里云杭州镜像，国内速度尚可）
- CosyVoice2 本地推理需要 GPU（RTX 4050 6GB 已验证，CPU 也可但慢 5-10 倍）
- FunASR 的 `/health` 端点路径 404（容器 healthcheck 显示 unhealthy，但服务实际正常）
- 浏览器端需要支持 WebRTC（Chrome / Edge / Firefox 均可）

### 🎯 v5.2.0 核心价值
- **零门槛托底**：用户零注册、零账号、零云端依赖即可使用语音 Agent
- **情感白送**：SenseVoice 一次推理返回情感+事件，下游 Agent 可基于情感调整响应
- **多模型协同**：FunASR 2-pass（生产级实时 ASR）+ CosyVoice2（中文自然度第一）
- **维度延续**：把"自托底"基因从 LLM 路由延伸到语音栈

---

## [5.1.0] — 2026-07-11

### 🚀 三维立体化对接 + 学习优化

HomeStream V5.1.0 — 从"单维度对话"升级为"三维立体化生态"，让 AI 能想、能做、能被接入。

---

### ✨ 新增 (Added)

#### 三维立体化对接（板块一）

**X 轴：模型维度 — OllamaProvider**（`providers/ollama_provider.py`，~310行）
- 使用 Ollama 原生 API（`/api/chat` + `/api/tags`），非 OpenAI 兼容端点
- `keep_alive` 模型常驻内存 + `format` 结构化输出 + `options` 精细参数控制
- 模型自动发现（`list_models()`）+ 快速检测（`is_running()`）
- 4个工厂函数：通用 / Qwen系列 / Llama系列 / Mistral系列
- 零成本设计（本地模型 = $0.00）→ RouterScore 成本维度满分
- ModelRouter 自动检测注册（`OLLAMA_API_BASE` + `OLLAMA_MODEL_NAME` 环境变量）
- 测试：38 tests ✅

**Y 轴：执行维度 — OpenClaw Tool Bridge**（`openclaw_bridge.py`，~570行）
- 受 OpenClaw "给AI装上手和脚"理念启发，**干净室独立实现**
- `ToolDefinition` 数据类 + OpenAI Function Calling 兼容格式（`to_openai_dict()`）
- 安全控制：`dangerous`标记 / 白名单模式 / 超时控制 / JSON Schema 参数验证
- 6个内置工具：file_read / file_write / file_list / shell_exec / http_get / http_post
- `GatewayBridge`：连接外部工具网关，支持工具发现和注册
- 同步/异步 handler 自动检测（`inspect.iscoroutinefunction`）
- 测试：54 tests ✅

**Z 轴：交互维度 — OpenAI 兼容 API 端点**（`openai_compat_endpoint.py`，~350行）
- `POST /v1/chat/completions`（流式 SSE + 非流式）
- `GET /v1/models` / `GET /v1/health` / `GET /v1/`
- model 参数多格式路由：`auto` / `L1`-`L3` / Provider名称 / 模型名模糊匹配
- HomeStream 扩展字段：provider / tier / latency_ms / cost_estimate
- 可选 API Key 认证（`OPENAI_COMPAT_API_KEY` 环境变量）
- `create_standalone_app()` 独立运行 + `create_openai_router()` 嵌入现有 FastAPI
- 测试：36 tests ✅

#### 学习优化（板块二）

**递归 CTE 因果链优化**（`event_store.py`）
- `query_cause_chain()` 从 Python while 循环 → SQL `WITH RECURSIVE` CTE
- 新增 `query_descendants()`：正向遍历（查找某事件的所有后代）
- 新增 `get_cause_depth()`：快速获取因果链深度（不反序列化事件对象）
- `max_depth` 安全阀 + 路径字符串循环检测
- 测试：19 new tests（总计 63 tests）✅

**SurprisalGate 信息密度过滤**（`surprisal_gate.py`，~280行）
- 基于香农信息熵 + 预测编码理论的自研公式
- 三维度评估：内容信息量（token surprisal）+ 上下文信息量 + 新词奖励
- 自适应阈值：warmup 观察期后基于历史统计动态调整
- 中英文混合分词（CJK逐字 + ASCII按空格/标点）
- 词汇表上限保护 + 线程安全
- Laplace 平滑解决冷启动问题
- 测试：59 tests ✅

**RouterScore 多维度路由评分**（`router_score.py`，~520行）
- 6维度加权评分：延迟 / 成本 / 健康度 / 新鲜度 / 成功率 / 负载
- 综合评分公式：`score = Σ(weight_i × dimension_score_i)`
- 4种预设权重方案：均衡 / 成本优先 / 速度优先 / 可靠性优先
- `SMART` 路由策略：ModelRouter 自动按评分排序 Provider
- 请求追踪：`on_request_start/success/failure` 更新运行时元数据
- 新鲜度指数衰减（6小时半衰期）+ 负载实时感知
- 线程安全（RLock）+ 评分看板（`get_scoreboard()`）
- 测试：77 tests ✅

#### 文档（板块三）
- `docs/ECOSYSTEM_3D_ARCHITECTURE.md` — 三维立体化架构设计文档
- `docs/DIMENSION_INTERFACE_SPEC.md` — 三套开源接口规范
- `docs/INSPIRATION_LOG.md` — 灵感来源声明与干净室实现记录

### 📊 新增规模

| 模块 | 代码行数 | 测试数 |
|:-----|:---------|:-------|
| OllamaProvider | ~310行 | 38 |
| ToolBridge | ~570行 | 54 |
| OpenAI兼容端点 | ~350行 | 36 |
| CTE因果链优化 | ~120行 | 19 |
| SurprisalGate | ~280行 | 59 |
| RouterScore | ~520行 | 77 |
| **合计** | **~2150行** | **283 tests** |

### 🏗️ 架构决策

- **三维正交设计** — 模型/执行/交互三轴独立可用，任意组合
- **Ollama 原生 API** — 放弃 OpenAI 兼容端点，获取 keep_alive/format/自动发现独有能力
- **干净室实现** — ToolBridge 受 OpenClaw 理念启发但完全独立实现
- **SQL CTE 下推** — 将因果链遍历从 Python 下推到 SQLite 引擎
- **预测编码 + 信息熵** — SurprisalGate 融合两个学术理论实现信息密度过滤
- **6维度 MCDA** — RouterScore 基于多准则决策分析实现智能路由

### 🚀 快速开始

```bash
# 升级（V5.0.0 用户）
git pull origin main  # 无新增依赖

# 新用户一键安装
curl -fsSL https://raw.githubusercontent.com/Ninefoldatwill/homestream/main/install.sh | bash
```

```python
# X轴：Ollama 本地模型
from providers.ollama_provider import create_qwen_ollama
provider = create_qwen_ollama("http://localhost:11434", "qwen2.5:3b")

# Y轴：Tool Bridge
from openclaw_bridge import ToolBridge
bridge = ToolBridge()  # 6个内置工具开箱即用

# Z轴：OpenAI 兼容 API
from openai import OpenAI
client = OpenAI(base_url="http://localhost:3458/v1", api_key="any")
client.chat.completions.create(model="auto", messages=[...])

# SMART 路由
from model_router import ModelRouter, RouterStrategy
router = ModelRouter(strategy=RouterStrategy.SMART)
```

### 🔧 环境变量

| 变量 | 用途 | 默认值 |
|:-----|:-----|:-------|
| `OLLAMA_API_BASE` | Ollama API 地址 | `http://localhost:11434` |
| `OLLAMA_MODEL_NAME` | 默认模型名 | 自动发现 |
| `OPENAI_COMPAT_API_KEY` | API 认证（可选） | 无（不认证） |

### 🔄 迁移指南

V5.1.0 **完全向后兼容** V5.0.0，所有现有代码无需修改。新增功能均为可选启用。

| 路由策略 | V5.0.0 | V5.1.0 |
|:---------|:-------|:-------|
| ROUND_ROBIN | ✅ | ✅ |
| FAILOVER | ✅ | ✅ |
| SMART | — | ✅ 新增 |

完整发布说明见 [RELEASE_NOTES_v5.1.0.md](RELEASE_NOTES_v5.1.0.md)

---

## [5.0.0] — 2026-07-10

### 🎉 首次开源发布

HomeStream V5.0.0 — 自进化AI生态操作系统，铸钥匠品牌的首把钥匙。

---

### ✨ 新增 (Added)

#### 核心引擎
- **EventStream 因果链引擎** — 每个 Event 携带 `cause` 字段，形成完整因果追踪链
- **三层模型路由** — L1 本地 Qwen2.5 / L2 云端 GLM / L3 备份 DeepSeek，asyncio.wait_for 超时自动降级，**永远免费托底**
- **弹性模式三档** — Solo（单Agent）→ Team（多Agent协作）→ Ecosystem（插件扩展），渐进式升级
- **ICP v1.1 协议** — 9种消息类型（INFO/ASK/TASK/UPD/DONE/WARN/ACK/PING/LOG），BLUF结论先行 ≤500字符，hmac.compare_digest 防时序攻击

#### 安全防护
- **Prompt 注入防护** — 13种危险模式检测 + ICP内容过滤
- **三层权限分治** — L1公开 / L2插件 / L3核心分级访问
- **令牌桶限流** — 防滥用速率限制
- **日志脱敏** — 自动过滤 token/key/password 敏感信息
- **Token 认证** — hmac.compare_digest 防时序攻击

#### 记忆系统
- **MemoryEvolution 三引擎** — ForgettingEngine（认知衰减）/ MergingEngine（Jaccard相似度聚类合并）/ ReconstructionEngine（反思+实体关系重构）
- **HybridRetriever** — BM25 + 向量检索 + RRF融合 + MMR去重 + 因果加成（V5.0新增causal_context参数）
- **CausalMemoryBridge** — 因果记忆桥接引擎：EventStream因果链↔记忆演化系统完整闭环（念起/溯源/涌现）
- **AutoCausalBridge** — 自动事件→记忆桥接：按事件类型自动创建不同认知记忆（DONE→反思/TASK→程序/WARN→情感）
- **ReMeCompressor** — 结构化 key:value 压缩 + TTL过期
- **Soul 配置** — Agent角色模板系统

#### 协作工具
- **Agent 群聊** — 频道广播、点对点消息、@提及路由、Kanban任务回调
- **Worktree 隔离** — Git worktree 实现Agent工作空间隔离
- **可视化工作流引擎** — 多步串行/并行执行 + 前置条件校验
- **Ratchet Loop** — 棘轮锁定 + 双层工坊（Maker+Reviewer）+ 失败自动归档，只进不退
- **Failsafe Guardian** — 失败自动降级到替代方案，永不硬报错

#### 千面设计市场
- **ThemeManager** — 纯Python文件系统主题管理器，零外部依赖
- **统一CSS Token字典** — 24个规范Token覆盖全部历史页面
- **PluginType.THEME** — 插件注册表统一生命周期管理
- **液态玻璃示例主题** — 首个官方设计主题

#### 质量评估
- **SkillsBench 12维评分系统** — 清晰度/完整性/正确性/安全性/效率/健壮性/可维护性/可用性/模块化/文档/兼容性/可测试性
- **SecurityAudit** — 5维安全子审计（注入风险/危险操作/网络访问/文件系统/凭据泄露）
- **质量分级** — CRITICAL(≥9.0) / GOOD(≥7.0) / FAIR(≥4.0) / LOW(<4.0)

#### 开发者体验
- **CLI 工具** — Typer + Rich 终端美学（start/stop/status/mode/doctor）
- **一键安装脚本** — install.sh（Linux/macOS）+ install.ps1（Windows）
- **CI/CD 流水线** — pre-commit钩子 + 安全扫描 + 自动测试（GitHub Actions）
- **OpenAPI 文档** — FastAPI 自动生成的交互式API文档

#### 协议与集成
- **MCP Server 支持** — Model Context Protocol 标准接口
- **A2A 协议** — Agent-to-Agent 通信协议
- **AgentCard** — 标准化Agent能力描述卡片
- **多平台IM网关** — 统一消息路由接口

### 🔧 融优记录 (Integration Log)

| 阶段 | 日期 | 内容 | 规模 |
|:-----|:----:|:-----|:-----|
| P0 三缺口 | 07-02 | 间接注入防护 + 记忆演化 + 致命三要素 | ~950行 |
| L1 直接融 | 07-03 | curl/SKILL.md/AgentCard/混合召回/降级链 | ~1200行 |
| L2 融合改造 | 07-03 | 灵魂配置 + 工作流 + 插件市场 + IM网关 | ~2120行 |
| 开源裁剪 | 07-05~06 | Step0-5 + 铸钥匠品牌 + Loop Engineering + DeerFlow | 97文件 |
| 冲浪融优 | 07-06 | SkillsBench + ReMe + CLI skills + SharedRegistry | ~2274行 |
| 设计市场 | 07-06~07 | ThemeManager + PluginType.THEME + 液态玻璃主题 | ~680行 |

### 🏗️ 架构决策 (Architecture Decisions)

- **双线分治** — 主线路(L1+L2) + 复线(L3)，asyncio.wait_for 超时自动切换
- **SQLite 跨线程** — `check_same_thread=False` + WAL模式
- **Bulkhead 隔离** — Agent工作空间互不干扰
- **FREE-MAD 无共识** — 去中心化多Agent决策
- **SLM 路由 30x** — 小模型路由降低延迟30倍

### 📊 项目规模

| 指标 | 数值 |
|:-----|:-----|
| 核心代码行数 | ~20,000行 |
| 测试用例 | 709 tests |
| API 路由 | 76 routes |
| 追踪文件 | 112 files |
| Python 依赖 | 15 核心包 |
| 外部服务依赖 | 0（纯本地运行） |

### 📜 品牌确立

- **铸钥匠（KeySmith）** 品牌基因正式确立
- **定位**：不造墙，只铸钥——HomeStream 是通往 AI 世界的那把钥匙
- **使命**：托底普大众化接入 AI 世界
- **三重意蕴**：铸（零成本本地模型锻造）/ 钥（ICP+Agent协作+记忆演化）/ 匠（匠人之心非商人之术）
- **商标注册启动**：类别9（软件）/ 38（通信）/ 42（技术服务）

---

## 版本号规则

| 版本段 | 含义 | 示例 |
|:-------|:-----|:-----|
| MAJOR | 不兼容的API变更 | 5.x.x → 6.0.0 |
| MINOR | 向后兼容的新功能 | 5.0.x → 5.1.0 |
| PATCH | 向后兼容的Bug修复 | 5.0.0 → 5.0.1 |

---

## 历史版本

HomeStream V5.0.0 是首次开源发布版本。在此之前的 V1-V4 为内部演进版本，未公开发布。

| 版本 | 时期 | 里程碑 |
|:-----|:-----|:-----|
| V1-V3 | 2026 Q1 | 单Agent原型 → 事件中枢 → 群聊闭环 |
| V4 | 2026 Q2 | 三层路由 + 安全防护 + ICP v1.0 |
| V5.0.0 | 2026-07-10 | 首次开源发布：千面设计市场 + SkillsBench + MemoryEvolution |
| V5.1.0 | 2026-07-11 | 三维立体化对接 + 学习优化（CTE因果链 + SurprisalGate + RouterScore） |

---

**融众之优，铸己之新。不造墙，只铸钥。**

九重工作室 · 铸钥匠 · 2026
