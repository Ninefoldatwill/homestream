# Changelog

All notable changes to HomeStream are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- **HybridRetriever** — BM25 + 向量检索 + RRF融合 + MMR去重
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

---

**融众之优，铸己之新。不造墙，只铸钥。**

九重工作室 · 铸钥匠 · 2026
