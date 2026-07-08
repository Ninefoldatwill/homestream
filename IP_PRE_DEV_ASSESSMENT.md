# HomeStream 开源版 — 明日开发项 IP 预排雷评估报告

> 评估日期: 2026-07-08
> 评估对象: theme_a11y.py (主题无障碍审计器) + A2A_PROTOCOL.md (Agent协作协议规范)
> 评估目的: 在 7/9 开发前完成 IP 排雷扫障，确保两项新开发模块符合 IP 边界合规要求
> 评估方法: 四维分析框架 (版权 / 专利 / 许可证 / 原创性)

---

## 一、theme_a11y.py — 主题无障碍审计器

### 1.1 模块定位

为千面设计市场的主题配色方案提供 WCAG 2.1 AA 级无障碍审计，检查主题变量（前景色、背景色、强调色等）的对比度、可读性、色盲友好性等指标。

### 1.2 四维排雷评估

#### 维度一: 版权 — SAFE

| 检查项 | 结论 | 依据 |
|:-------|:----:|:-----|
| WCAG 标准文本版权 | SAFE | W3C 文档许可证允许为实现规范而创建衍生作品（在软件中） |
| 颜色对比度公式 | SAFE | 相对亮度公式和对比度比率是 W3C 公开算法，无数工具使用 |
| 同类工具代码 | SAFE | 完全原创实现，不复制任何工具代码 |

**关键依据**: W3C 文档许可证 (2023版) 明确规定："To facilitate implementation of the technical specifications set forth in this document, anyone may prepare and distribute derivative works and portions of this document in software" — 即任何人可以为实现技术规范而在软件中创建和分发衍生作品。

#### 维度二: 专利 — SAFE

| 检查项 | 结论 | 依据 |
|:-------|:----:|:-----|
| WCAG 成功标准专利 | SAFE | W3C 专利政策 (2025版) 确保规范以免版税 (RF) 方式可实施 |
| 颜色对比度算法专利 | SAFE | W3C 公开公式，WebAIM/fwdtools/无数工具均使用，无专利限制 |
| 无障碍审计算法专利 | SAFE | 未发现相关专利；检查配色对比度是通用方法论 |

**关键依据**: W3C 专利政策第5节规定 RF 许可证须"向全世界所有人提供"，"不得以支付版税、费用或其他对价为条件"。WCAG 成功标准描述"需要达到什么"而非"如何实现"，多数标准可通过多种技术方式实现，不触发"必要权利要求"。

#### 维度三: 许可证 — SAFE

| 检查项 | 结论 | 依据 |
|:-------|:----:|:-----|
| HomeStream MIT 许可证 | SAFE | MIT 是最宽松的开源许可证之一，无传染性 |
| 第三方依赖 | SAFE | 零第三方依赖，纯 Python 标准库实现 |
| 同类工具许可证 | SAFE | wcag-checker(MIT)、a11y-checker(MIT)、OpenA11y(MPL 2.0) 均兼容 |

#### 维度四: 原创性 — SAFE

| 检查项 | 结论 | 依据 |
|:-------|:----:|:-----|
| 差异化定位 | SAFE | 审计对象 = 主题配色变量 (非 HTML DOM)，7款同类工具无一覆盖 |
| 实现方式 | SAFE | 针对 HomeStream 主题系统设计，可编程式 API 调用 |
| 领域空白 | SAFE | 现有工具均面向网页内容检查，无面向设计主题的编程式审计 |

### 1.3 同类工具生态对比

| 工具 | 许可证 | 审计对象 | 可编程性 | 与 theme_a11y 重叠 |
|:-----|:-------|:---------|:---------|:------------------:|
| wcag-checker (PyPI) | MIT | HTML 内容 | 库 (可编程) | 低 — 面向 DOM |
| a11y-checker (PyPI) | MIT | HTML 内容 | API 客户端 | 低 — 面向 DOM |
| WEBLY-Scanner | - | URL/HTML | 脚本 | 低 — 面向 DOM |
| OpenA11y Lib | MPL 2.0 | HTML 评估 | 库 | 低 — 面向 DOM |
| WCAG-Theme-Inspector | - | 配色方案 | 交互式 Web | 中 — 但非编程式 |
| accessible-palette-builder | - | 调色板 | 交互式 Web | 中 — 但面向生成非审计 |
| **HomeStream theme_a11y** | **MIT** | **主题变量** | **库 (可编程)** | **—** |

**结论**: theme_a11y.py 在"主题配色变量 x 可编程库"象限中无直接竞争者，领域差异化明确。

### 1.4 开发指导建议

1. **docstring 标注**: 在模块头部注明"基于 WCAG 2.1 AA 标准 (W3C, Royalty-Free)"，标注灵感来源
2. **算法实现**: 使用 W3C 公开的相对亮度公式和对比度比率公式，注明公式来源 URL
3. **不复制文本**: 不复制 W3C 规范文本原文，用自己的语言描述检查规则
4. **零依赖**: 仅使用 Python 标准库 (colorsys/math/re)，不引入任何第三方包

---

## 二、A2A_PROTOCOL.md — Agent 协作协议规范

### 2.1 模块定位

HomeStream Agent 间协作协议规范文档，定义 Agent 发现、能力声明、任务委派、结果回传等协作机制。

### 2.2 四维排雷评估

#### 维度一: 版权 — SAFE (需注意)

| 检查项 | 结论 | 依据 |
|:-------|:----:|:-----|
| Google A2A 规范文本 | SAFE | Apache 2.0 允许创建衍生作品，但需保留版权声明 |
| FIPA ACL 规范 | SAFE | IEEE 标准，开源实现存在 (amlhubs/fipa-acl) |
| 自有协议文本 | SAFE | 用自己的语言描述协议，不复制任何现有规范文本 |

**关键注意**: 如果直接引用 Google A2A 的概念术语（如 Agent Card、Task State 等），需在文档中注明"概念参考: Google A2A Protocol (Apache 2.0)"。但最佳实践是基于 HomeStream 自有的 ICP v1.1 协议扩展，而非从 Google A2A 派生。

#### 维度二: 专利 — SAFE

| 检查项 | 结论 | 依据 |
|:-------|:----:|:-----|
| Agent 通信协议专利 | SAFE | FIPA ACL (IEEE 标准)、Google A2A (Apache 2.0 含专利授权)、ANP 均开放 |
| 协议设计模式专利 | SAFE | Agent 发现/能力声明/任务委派是通用设计模式，无专利壁垒 |

**关键依据**: Apache 2.0 许可证第3条明确包含专利授权条款："Each Contributor hereby grants to You a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable (except as stated in this section) patent license"。

#### 维度三: 许可证 — SAFE

| 检查项 | 结论 | 依据 |
|:-------|:----:|:-----|
| HomeStream MIT 许可证 | SAFE | MIT 不限制文档内容 |
| 不引入 Apache 2.0 依赖 | SAFE | 文档是原创作品，不复制 Apache 2.0 保护的文本 |
| 概念引用合规 | SAFE | 协议概念/设计模式不受版权保护，只有表达形式受保护 |

#### 维度四: 原创性 — MONITOR

| 检查项 | 结论 | 依据 |
|:-------|:----:|:-----|
| 协议文本原创 | MONITOR | 须确保用自己的语言描述，不照搬 Google A2A 规范文本 |
| 基于 ICP 扩展 | SAFE | HomeStream 已有 ICP v1.1 (9种消息类型)，以此为基础扩展最安全 |
| 差异化定位 | SAFE | HomeStream A2A 聚焦"铸钥匠"生态，与 Google A2A 的企业级场景不同 |

### 2.3 同类协议生态对比

| 协议 | 许可证 | 场景 | 与 HomeStream A2A 关系 |
|:-----|:-------|:-----|:----------------------|
| Google A2A | Apache 2.0 | 企业级 Agent 互操作 | 概念参考，不照搬 |
| FIPA ACL | IEEE 标准 | 多 Agent 系统通信 | 历史参考，speech-act 理论 |
| Agent Network Protocol (ANP) | 开源 | Agent 网络连接 | 同期探索，可互参考 |
| MCP (Anthropic) | 开源 | Agent-工具连接 | 互补关系 (A2A = Agent-Agent, MCP = Agent-Tool) |
| **HomeStream ICP v1.1** | **MIT** | **九重生态 Agent 通信** | **自有基础，A2A 的扩展起点** |

### 2.4 开发指导建议

1. **基于 ICP 扩展**: 以 HomeStream 现有的 ICP v1.1 (9种消息类型) 为基础，扩展 Agent 间协作机制，而非从 Google A2A 派生
2. **概念引用标注**: 如果参考了 Google A2A 的概念（如 Agent 发现、能力声明），在文档中注明"概念参考: Google A2A Protocol (Apache 2.0, Linux Foundation)"
3. **用自己的语言**: 所有协议描述使用自己的语言组织，不复制任何现有规范的文本段落
4. **差异化定位**: 聚焦 HomeStream "铸钥匠" 生态场景（零成本本地模型、三层路由、记忆演化），与企业级 Agent 互操作场景区分

---

## 三、综合评估总结

### 3.1 风险矩阵

| 模块 | 版权 | 专利 | 许可证 | 原创性 | 综合 |
|:-----|:----:|:----:|:------:|:------:|:----:|
| theme_a11y.py | SAFE | SAFE | SAFE | SAFE | **全绿灯** |
| A2A_PROTOCOL.md | SAFE | SAFE | SAFE | MONITOR | **黄灯可发布** |

### 3.2 关键发现

1. **WCAG 标准实施零风险**: W3C 专利政策确保免版税实施，文档许可证允许软件中创建衍生作品，颜色对比度公式是公开算法。theme_a11y.py 在所有四个维度上全绿灯。

2. **A2A 协议需注意文本原创**: Google A2A 使用 Apache 2.0（含专利授权），概念可自由参考。但规范文本受版权保护，须用自己的语言描述。最佳路径是基于 ICP v1.1 扩展。

3. **两个领域空白确认**:
   - 无障碍审计: 7款同类工具无一覆盖"主题配色变量 x 可编程库"象限
   - Agent 协作协议: HomeStream 的"零成本本地模型 + 三层路由 + 铸钥匠生态"定位独一无二

### 3.3 开发合规清单

- [ ] theme_a11y.py: docstring 标注 WCAG 2.1 AA 标准来源 (W3C, RF)
- [ ] theme_a11y.py: 颜色对比度公式注明 W3C 公开算法 URL
- [ ] theme_a11y.py: 零第三方依赖，仅用 Python 标准库
- [ ] A2A_PROTOCOL.md: 基于 ICP v1.1 扩展，非从 Google A2A 派生
- [ ] A2A_PROTOCOL.md: 概念引用处标注"参考: Google A2A Protocol (Apache 2.0)"
- [ ] A2A_PROTOCOL.md: 所有协议描述使用自己的语言，不复制规范文本
- [ ] 两项均通过 ast.parse 语法检查
- [ ] 两项均通过测试 (theme_a11y 需配套测试)

---

## 四、参考资料

### WCAG / W3C
- W3C 专利政策 (2025): https://www.w3.org/policies/patent-policy/
- W3C 文档许可证 (2023): https://www.w3.org/copyright/document-license/
- WCAG 2.1 标准: https://www.w3.org/WAI/standards-guidelines/wcag/
- WCAG 对比度技术 G17: https://www.w3.org/WAI/WCAG22/Techniques/general/G17

### Agent 协议
- Google A2A 协议 (Apache 2.0): https://github.com/a2aproject/A2A
- A2A 协议规范: https://a2a-protocol.org/latest/specification/
- FIPA ACL 规范: https://www.fipa.org/specs/fipa00061/
- Agent Network Protocol: https://github.com/agent-network-protocol/AgentNetworkProtocol

### 同类工具
- wcag-checker (MIT, PyPI): https://pypi.org/project/wcag-checker/
- a11y-checker (MIT, PyPI): https://pypi.org/project/a11y-checker/
- OpenA11y Evaluation Library (MPL 2.0): https://opena11y.github.io/evaluation-library/
- WCAG-Theme-Inspector: https://github.com/Ashirvaad/WCAG-Theme-Inspector
- accessible-palette-builder: https://github.com/mngtolbert/accessible-palette-builder

### 国家指引
- 国家保密局开源软件 IP 风险指引: https://www.gjbmj.gov.cn/n1/2024/1209/c411145-40378565.html
