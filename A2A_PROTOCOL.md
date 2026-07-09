# HomeStream A2A 协议规范

> **版本**: v1.0
> **日期**: 2026-07-09
> **状态**: 草案
> **基础**: ICP v1.1（九重生态内部通信协议）
> **许可证**: MIT

---

## 一、概述

### 1.1 定位

HomeStream A2A（Agent-to-Agent）协议是九重生态中 Agent 之间协作的规范层。它在 ICP v1.1 消息协议的基础上，定义了 Agent 发现、能力声明、任务委派、结果回传、状态同步等协作机制。

与通用 Agent 互操作协议（概念参考: Google A2A Protocol, Apache 2.0）不同，HomeStream A2A 聚焦"铸钥匠"生态场景：

- **零成本本地模型**：三层路由（L1 本地 → L2 在线 → L3 备份）让协作不依赖付费 API
- **记忆演化**：协作过程产生的事件自动进入记忆演化引擎
- **因果链追溯**：每个协作动作通过 ICP cause 字段可追溯到发起者
- **弹性模式**：Solo → Team → Ecosystem 三档协作模式自适应

### 1.2 协议层次

```
┌─────────────────────────────────────┐
│       A2A 协作层（本规范）            │  ← Agent 发现 / 能力匹配 / 任务委派
├─────────────────────────────────────┤
│       ICP v1.1 消息层                │  ← 9 种消息类型 / BLUF / 因果链
├─────────────────────────────────────┤
│       EventStream 传输层             │  ← 事件驱动 / WAL / 订阅
├─────────────────────────────────────┤
│       HTTP + WebSocket 物理层        │  ← 端口 3458
└─────────────────────────────────────┘
```

### 1.3 与 ICP v1.1 的关系

ICP v1.1 定义了 9 种消息类型，A2A 在此基础上定义了**协作语义**——即这些消息在 Agent 间协作场景中如何组合使用。A2A 不引入新的消息类型，而是规定消息的编排模式。

| ICP 类型 | A2A 协作语义 |
|:---------|:-------------|
| INFO | Agent 自我介绍 / 广播状态变更 |
| ASK | 向其他 Agent 请求信息或能力查询 |
| TASK | 委派任务给其他 Agent |
| UPD | 任务进度更新 / 中间结果同步 |
| DONE | 任务完成 / 结果回传 |
| WARN | 协作异常 / 能力不足通知 |
| ACK | 确认收到任务 / 确认结果 |
| PING | 心跳 / 存活检测 |
| LOG | 协作日志 / 审计记录 |

---

## 二、Agent 发现

### 2.1 AgentCard

每个 Agent 通过 AgentCard 声明自己的身份和能力。AgentCard 遵循 JSON 格式，包含以下核心字段：

| 字段 | 类型 | 说明 |
|:-----|:-----|:-----|
| `name` | string | Agent 显示名称 |
| `description` | string | Agent 一句话描述 |
| `version` | string | Agent 版本号 |
| `protocolVersions` | string[] | 支持的协议版本（如 `["v1"]`） |
| `supportedInterfaces` | Interface[] | 通信端点列表 |
| `capabilities` | object | 流式 / 推送 / 状态历史等能力标志 |
| `skills` | Skill[] | Agent 暴露的技能列表 |
| `defaultInputModes` | string[] | 默认输入格式（如 `text/plain`） |
| `defaultOutputModes` | string[] | 默认输出格式 |
| `provider` | object | 提供商信息 |
| `securitySchemes` | object | 认证方案定义 |
| `signatures` | object[] | JWS 签名列表 |

### 2.2 发现机制

Agent 通过以下两种方式发现彼此：

**方式一：Well-Known 端点**

Agent 在 HTTP 服务上暴露 `/.well-known/agent-card.json`，返回自己的 AgentCard。其他 Agent 通过 GET 请求获取。

```
GET /.well-known/agent-card.json
Host: localhost:3458
```

**方式二：注册中心**

在 Ecosystem 模式下，Agent 向中心注册表注册自己的 AgentCard。其他 Agent 通过查询注册表发现可用协作伙伴。

### 2.3 能力匹配

Agent 在发现阶段通过 `skills` 字段的标签匹配来筛选协作伙伴：

```json
{
  "id": "research",
  "name": "深度调研",
  "description": "联网搜索并整理多维度技术报告",
  "tags": ["research", "web", "analysis"],
  "examples": ["调研 AI Agent 记忆系统最新进展"]
}
```

发起方通过标签交集判断目标 Agent 是否具备所需能力。

---

## 三、任务协作流程

### 3.1 标准协作流程

一个完整的 Agent 间任务协作遵循以下流程：

```
发起方                    接收方
  │                         │
  │── 1. ASK (能力查询) ───→│
  │                         │
  │←── 2. ACK (能力确认) ───│
  │                         │
  │── 3. TASK (任务委派) ──→│
  │                         │
  │←── 4. ACK (接受任务) ───│
  │                         │
  │←── 5. UPD (进度更新) ───│  (可选，多次)
  │                         │
  │←── 6. DONE (结果回传) ──│
  │                         │
  │── 7. ACK (确认收到) ───→│
  │                         │
```

### 3.2 任务委派（TASK）

任务委派通过 ICP TASK 消息实现。消息体包含以下 A2A 扩展字段：

| 字段 | 说明 |
|:-----|:-----|
| `recipient` | 目标 Agent ID |
| `content` | 任务描述（BLUF 原则，≤500 字符） |
| `cause` | 因果链（指向触发此任务的事件 ID） |
| `handoff` | 移交上下文（5 要素：任务、状态、上下文、约束、预期） |
| `confidence` | 发起方对任务的信心值（0.0-1.0） |

**示例**：

```json
{
  "event_type": "TASK",
  "sender": "澜舟",
  "recipient": "灵犀",
  "content": "调研 WCAG 2.1 AA 对比度标准是否免版税",
  "cause": "evt_20260709_001",
  "handoff": {
    "task": "IP 排雷调研",
    "state": "started",
    "context": "为 theme_a11y.py 开发做前置排雷",
    "constraints": "须标注来源 URL",
    "expected": "SAFE/MONITOR/ERROR 评级"
  },
  "confidence": 0.9
}
```

### 3.3 结果回传（DONE）

任务完成后，接收方通过 DONE 消息回传结果：

```json
{
  "event_type": "DONE",
  "sender": "灵犀",
  "recipient": "澜舟",
  "content": "WCAG 2.1 AA 全维度 SAFE。W3C 专利政策确保免版税实施。",
  "cause": "evt_20260709_002",
  "confidence": 0.95
}
```

### 3.4 异常处理（WARN）

当 Agent 无法完成任务时，通过 WARN 消息通知发起方：

```json
{
  "event_type": "WARN",
  "sender": "灵犀",
  "recipient": "澜舟",
  "content": "无法访问 W3C 官网，GFW 可能阻断连接",
  "cause": "evt_20260709_002",
  "confidence": 0.7
}
```

发起方收到 WARN 后可选择：重试、委派给其他 Agent、或降级处理。

---

## 四、协作模式

### 4.1 Solo 模式

单 Agent 独立完成任务，不涉及 A2A 协作。适用于简单查询、本地操作。

### 4.2 Team 模式

多 Agent 在同一频道中协作，通过 ICP 消息进行通信。每个 Agent 有明确分工，任务通过 TASK 委派、DONE 回传。

**示例场景**：九重安排开发任务 → 澜舟（开发）+ 灵犀（调研）+ 千寻（归档）协作完成。

### 4.3 Ecosystem 模式

跨实例的 Agent 协作。不同 HomeStream 实例上的 Agent 通过 AgentCard 发现彼此，通过 HTTP API 进行任务委派。

**示例场景**：九重工作室的 OpenBridge 实例与外部社区贡献者的 HomeStream 实例进行 Agent 互操作。

---

## 五、安全机制

### 5.1 身份认证

Agent 间通信使用 API Key 认证：

```
X-Agent-Token: <api-key>
```

API Key 在 AgentCard 的 `securitySchemes` 中声明，通过带外方式交换。

### 5.2 签名验证

AgentCard 支持 JWS（JSON Web Signature）签名，使用 Ed25519 算法。签名确保 AgentCard 在传输过程中不被篡改。

签名流程：
1. Agent 生成 Ed25519 密钥对
2. 用私钥对 AgentCard JSON 签名，生成 JWS
3. 将 JWS 附加到 AgentCard 的 `signatures` 字段
4. 接收方用公钥验证签名

### 5.3 因果链审计

所有 A2A 协作消息通过 ICP 的 `cause` 字段形成因果链。任何协作动作都可追溯到发起事件，确保审计可追溯。

---

## 六、与 Google A2A 的差异

本规范在设计过程中参考了 Google A2A Protocol（Apache 2.0, Linux Foundation）的概念，但在以下维度有显著差异：

| 维度 | Google A2A | HomeStream A2A |
|:-----|:-----------|:---------------|
| 基础协议 | 独立协议规范 | 基于 ICP v1.1 扩展 |
| 场景定位 | 企业级 Agent 互操作 | 铸钥匠生态协作 |
| 模型成本 | 依赖 API 密钥 | 三层免费路由 |
| 记忆机制 | 无原生记忆 | 记忆演化三引擎 |
| 追溯机制 | Task State | 因果链 + WAL |
| 协作模式 | Client-Server | Solo/Team/Ecosystem 三档 |
| 安全模型 | OAuth 2.0 | API Key + JWS |

**概念参考声明**: 本规范的 Agent 发现（AgentCard）、能力声明（Skill）、任务委派等概念参考了 Google A2A Protocol (Apache 2.0, https://github.com/a2aproject/A2A)。HomeStream 的实现完全基于 ICP v1.1 自有协议扩展，未复制 Google A2A 的规范文本。

---

## 七、参考实现

### 7.1 AgentCard 生成

HomeStream 在 `agent_card.py` 中提供了 AgentCard 的完整实现：

```python
from agent_card import generate_agent_card, generate_well_known_card

# 生成默认 AgentCard
card = generate_agent_card(base_url="http://localhost:3458")

# 生成 .well-known/agent-card.json 内容
well_known = generate_well_known_card(base_url="http://localhost:3458")
```

### 7.2 AgentCard 签名

```python
from agent_card import AgentCardSigner

signer = AgentCardSigner(private_key_b64="<your-ed25519-private-key>")
jws = signer.sign(card)  # 返回 JWS 字符串
```

### 7.3 任务委派

任务委派通过 EventStream 的 TASK 事件实现：

```python
from event_stream import Event, EventType, EventSource

task_event = Event(
    event_type=EventType.TASK,
    source=EventSource.AGENT,
    sender="澜舟",
    recipient="灵犀",
    content="调研 WCAG 2.1 AA 标准",
    cause="<触发事件ID>",
)
```

---

## 八、版本历史

| 版本 | 日期 | 变更 |
|:-----|:-----|:-----|
| v1.0 | 2026-07-09 | 初始草案，基于 ICP v1.1 扩展 |

---

## 九、参考资源

- **ICP v1.1**: HomeStream 内部通信协议（9 种消息类型）
- **EventStream**: 事件驱动引擎（Action/Observation 二分法 + 因果链 + WAL）
- **agent_card.py**: AgentCard 生成与签名实现
- **Google A2A Protocol** (Apache 2.0): https://github.com/a2aproject/A2A — 概念参考
- **FIPA ACL** (IEEE 标准): https://www.fipa.org/specs/fipa00061/ — 历史参考
- **Agent Network Protocol**: https://github.com/agent-network-protocol/AgentNetworkProtocol — 同期探索
- **MCP** (Anthropic): Agent-Tool 连接协议，与 A2A 互补

---

*九重生态 · 澜舟开发 · 2026-07-09*
*铸钥匠 🔑 — 不造一面墙，只铸千万门*
