# OpenScience 调研报告 — 与 HomeStream 的对比及借鉴分析

> 调研日期: 2026-07-08
> 调研对象: OpenScience (synthetic-sciences/openscience)
> 来源: 九重分享截图 + GitHub 公开信息 + 第三方评测
> 调研目的: 评估 OpenScience 对 HomeStream 的启发、竞争关系与可借鉴点
> 合规边界: 借鉴知识/概念，不照搬代码；OpenScience 使用 Apache 2.0 许可证

---

## 一、OpenScience 是什么

OpenScience 是由 **Synthetic Sciences**（YC W26 孵化）推出的**开源 AI 科研工作台**，定位为 Claude Science 的替代方案。它于 **2026 年 7 月 4 日**首次提交，7 月 6 日正式发布，是一个刚出生几天的项目，但已经引起 AI/科研社区关注。

### 核心特征

| 维度 | 内容 |
|:-----|:-----|
| 许可证 | Apache 2.0 |
| 技术栈 | TypeScript + Bun + SolidJS |
| 运行方式 | 本地服务器 + 浏览器工作区 |
| 模型支持 | 75+ 提供商（Anthropic/OpenAI/Google/DeepSeek/GLM 等） |
| 技能数量 | 290+ 科研技能包 |
| 科学数据库 | 30+（UniProt/PDB/PubChem/arXiv/OpenAlex 等） |
| 工作区 | 浏览器 IDE（文件树、编辑器、终端、内联科学渲染） |
| 商业路径 | Atlas 托管平台（可选） |
| 研究循环 | 文献综述 → 假设 → 代码 → 实验 → 分析 → 论文撰写 |

### 架构概览

```
  Browser workspace (frontend/workspace, SolidJS)
        |  HTTP + SSE, localhost only
        v
  Local server (backend/cli/src/server)
        |
        +--  Agent runtime      sessions, message loop, model routing
        +--  Tool layer         shell, edit, LSP, MCP, science connectors
        +--  Skills             bundled and user-installed skill packs
        +--  Providers          Anthropic, OpenAI, Google, 75+ more
        |
        +--  Atlas client       optional: managed models, wallet, graph
```

关键组件：
- `src/session`: 代理运行时（消息循环、工具调度、compaction、provenance、blind reviewer gate）
- `src/agent`: 代理注册表和 prompts（research/biology/physics/ml/plan）
- `src/provider`: 模型路由（模型定义来自 models.dev）
- `src/tool` + `src/science`: 工具层（shell、编辑器、LSP、MCP 客户端、科学连接器）
- `src/skill`: 技能包（按需加载）

---

## 二、与 HomeStream 的对比

### 2.1 直接对比表

| 维度 | OpenScience | HomeStream |
|:-----|:------------|:-----------|
| **定位** | 科研 AI 工作台 | 通用 AI 生态操作系统（铸钥匠） |
| **目标用户** | 科研人员、实验室 | 普通大众、AI 生态参与者 |
| **场景** | ML/生物/物理/化学 | 通用 AI 接入与生态协作 |
| **许可证** | Apache 2.0 | MIT |
| **技术栈** | TypeScript + Bun | Python |
| **模型路由** | 按请求路由，75+ 提供商 | 三层免费路由（L1/L2/L3）+ 双线路保障 |
| **本地优先** | 是（BYOK，密钥本地保留） | 是（L3 Ollama 本地模型） |
| **技能生态** | 290+ 科研技能包 | 千面设计市场 + 技能生态 |
| **协议** | 内部消息循环 | ICP v1.1 + A2A扩展 |
| **质量控制** | Critique 子代理 + Artifact 验证 | Ratchet Loop（Maker + Reviewer） |
| **记忆/溯源** | Provenance 本地持久化 | EventStore + data_guardian |
| **工作区** | 浏览器 IDE | 千面设计主题市场 |
| **商业路径** | Atlas 托管平台 | 分享型开源 > 商业 |
| **发布时长** | 4 天（2026-07-04 首次提交） | V8 已迭代许久 |

### 2.2 竞争关系判断：不构成直接竞争

OpenScience 和 HomeStream 虽然都属于"开源 AI 工作台/生态"范畴，但**目标用户和场景完全不同**：

- **OpenScience** 是"科研实验室"，让科研人员做 ML/生物/化学研究，目标是写论文
- **HomeStream** 是"AI 生态钥匙"，让普通大众低成本接入 AI 世界，目标是生态普惠

两者不是零和竞争关系，而是不同细分市场的探索。未来可能出现交叉（例如科研主题入驻千面设计市场），但短期内不存在直接替代关系。

---

## 三、OpenScience 对 HomeStream 的启发

### 3.1 可借鉴概念（符合 IP 边界）

| 概念 | OpenScience 实现 | HomeStream 可借鉴方向 | 安全边界 |
|:-----|:-----------------|:----------------------|:---------|
| **模型无关（Model-Agnostic）** | 支持 75+ 提供商，按请求路由 | 强化三层路由叙事：L1 本地 + L2 在线 + L3 备份，比 OpenScience 更彻底 | 只借鉴概念，不复制代码 |
| **技能包（Skill Packs）** | 290+ 技能按领域分类 | 优化 Skill 生态组织：按场景/职业分类（参考 SkillsMP 分类） | 只借鉴分类思路 |
| **浏览器 IDE 工作区** | 文件树 + 编辑器 + 终端 + 渲染 | 千面设计市场可扩展为"主题化工作区"，不只是 UI 皮肤 | 不复制其 UI 设计 |
| **Provenance 溯源** | 会话数据本地持久化 | HomeStream 的 EventStore + data_guardian 已领先，可进一步宣传"可观测性" | 自有实现，概念参考 |
| **Critique 子代理** | 批判性审查子代理 | 与 Ratchet Loop Reviewer 机制对齐，可强化"双层工坊"叙事 | 概念借鉴 |
| **MCP 集成** | 工具层支持 MCP 服务器 | HomeStream 已有 MCP 底座，可继续扩展 | 已有能力，无需借鉴 |
| **本地优先叙事** | 数据本地处理，密钥不离开机器 | 强化"零成本本地模型"卖点，与 OpenScience 的 BYOK 形成差异 | 叙事借鉴 |

### 3.2 不太适合借鉴的方面

| 方面 | 原因 |
|:-----|:-----|
| 科研垂直场景 | HomeStream 定位是通用生态，不是科研工具 |
| Atlas 托管平台商业路径 | 九重选择分享型开源 > 商业化，不直接对标 |
| TypeScript/Bun 技术栈 | HomeStream 是 Python 生态，迁移成本极高 |
| 30+ 科学数据库连接器 | 属于科研场景，不是通用需求 |
| 研究循环到论文撰写 | 科研专用流程，与 HomeStream 生态场景不符 |

---

## 四、HomeStream 的相对优势（与 OpenScience 对比）

### 4.1 真正的差异化

| 优势 | 说明 |
|:-----|:-----|
| **三层免费路由** | OpenScience 仍依赖 API 密钥（BYOK 或 Atlas 付费），HomeStream 的 L3 Ollama 本地模型真正实现零成本 |
| **双线路自动切换** | OpenScience 没有明确的高可用路由机制，HomeStream 主备自动切换更鲁棒 |
| **铸钥匠哲学** | OpenScience 是商业公司（Synthetic Sciences）产品，HomeStream 是分享型开源社区定位 |
| **千面设计市场** | OpenScience 是固定 UI 的浏览器 IDE，HomeStream 是"每个人自己的门" |
| **ICP + Ratchet Loop** | OpenScience 的消息循环没有公开协议规范，HomeStream 有 ICP v1.1 和 A2A 扩展文档 |
| **生态 OS 定位** | OpenScience 是"工作台"，HomeStream 是"生态操作系统"，维度更高 |

### 4.2 营销叙事建议

基于 OpenScience 的出现，HomeStream 可以强化以下叙事：

- **"我们不只服务科研人员，我们托底普大众化接入 AI 世界"**
- **"OpenScience 让你自带 API 密钥；HomeStream 让你零成本本地运行"**
- **"OpenScience 给你一个科研实验室；HomeStream 给你千万把钥匙，每把钥匙打开不同的门"**
- **"科研需要 OpenScience，生活/工作/学习需要 HomeStream"**

---

## 五、IP 边界与合规建议

### 5.1 OpenScience 的许可证

OpenScience 使用 **Apache License 2.0**，这意味着：
- 可以自由查看、使用、修改、分发其代码
- 允许创建衍生作品
- 需要保留版权声明和许可证声明
- 包含专利授权条款

### 5.2 HomeStream 的借鉴原则

九重已经明确过：**借鉴知识，不照搬**。具体到 OpenScience：

| 做法 | 是否合规 | 说明 |
|:-----|:--------:|:-----|
| 阅读其架构文档 | ✅ | 学习设计理念 |
| 借鉴"模型无关"概念 | ✅ | 概念不受版权保护 |
| 借鉴"技能包分类"思路 | ✅ | 组织方式属于思想 |
| 复制其 TypeScript 代码到 Python | ❌ | 代码表达受版权保护 |
| 复制其 prompts 或架构文档原文 | ❌ | 文本表达受版权保护 |
| 复制其 UI 设计 | ❌ | 视觉设计可能受版权保护 |
| 使用其 a2a.proto 类似结构 | ✅ 但需谨慎 | 如果通用，可重新设计；如果特定，需避免 |

### 5.3 开发建议

如果未来 HomeStream 想扩展与 OpenScience 类似的"研究工作台"能力：

1. 优先强化现有差异化（三层路由、千面设计、ICP 协议）
2. 不直接复制 OpenScience 的代码或 prompts
3. 如参考其概念，在文档中标注"概念参考：OpenScience by Synthetic Sciences (Apache 2.0)"
4. 保持 Python 生态，不盲目迁移到 TypeScript/Bun

---

## 六、结论

### 6.1 总体判断

**OpenScience 是一个值得关注的项目，但不对 HomeStream 构成直接威胁。** 它验证了"开源 + 模型无关 + 本地优先 + 技能生态"这条产品路径的可行性，而 HomeStream 在这条路径上有自己独特的维度（免费三层路由、铸钥匠哲学、千面设计市场）。

### 6.2 对九重的建议

1. **把 OpenScience 当"行业冲浪"素材，而非竞争对手**：它的出现说明 Skills/Agent 工作台市场正在爆发，HomeStream 的赛道判断是正确的。

2. **强化差异化叙事**：在 README 和社区推广中，突出"零成本本地模型"和"千面设计市场"这两个 OpenScience 不具备的特点。

3. **考虑未来互补**：如果未来 HomeStream 的 Skill 生态足够丰富，可以吸引科研人员贡献科研类技能，形成与 OpenScience 的互补而非竞争。

4. **保持 IP 边界**：如参考 OpenScience 概念，仅借鉴思想层面，所有实现均原创，文档中标注灵感来源。

---

## 七、参考资料

- OpenScience GitHub: https://github.com/synthetic-sciences/openscience
- OpenScience 架构文档: https://github.com/synthetic-sciences/openscience/blob/main/ARCHITECTURE.md
- OpenScience 文档: https://openscience.sh/docs
- The AI Dude 评测: https://theaidude.net/tools/openscience
- OmniTools 中文报道: https://www.omnitools.ai/news/news_mr8s979f37bb001152c44b49
- ai-bot 中文报道: https://ai-bot.cn/openscience/
- Saipien 评测: https://saipien.org/openscience-a-local-model-agnostic-lab-notebook-and-agent-workbench-for-secure-rd/
