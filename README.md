# 🔑 HomeStream · 家园·流

[English](README_EN.md) | 中文

<p align="center">
  <img src="assets/brand/logo.svg" width="120" alt="HomeStream Logo">
</p>

> **HomeStream** —— 每个人在AI世界的家园，流光汇河。
>
> **不造墙，只铸钥。**
>
> 我们是一群铸钥匠。
>
> 我们相信：AI 不是少数人的特权，而是每个人生来应有的权利。我们铸的这把钥匙——零成本、能跑在你自己的机器上、不需要依赖任何厂商的 API——只有一个目的：**让每个人都能推开门，走进属于他自己的智能新世界。**
>
> 起心动念皆因果。这把钥匙的每一次锻造，都是为了让数字化智能化的新世界，不再是少数人的后花园，而是**芸芸众生的游乐场**。
>
> 开源版 V5.0.0 · 自进化AI生态操作系统
>
> 融众之优，铸己之新。道法自然，由内而外。

---

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.9+-green.svg" alt="Python 3.9+"></a>
  <a href="#"><img src="https://img.shields.io/badge/tests-700%2B-brightgreen.svg" alt="700+ Tests"></a>
  <a href="#"><img src="https://img.shields.io/badge/API%20routes-76-orange.svg" alt="76 API Routes"></a>
  <a href="#"><img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg" alt="Cross Platform"></a>
  <a href="#"><img src="https://img.shields.io/badge/PWA-ready-success.svg" alt="PWA Ready"></a>
  <a href="#"><img src="https://img.shields.io/badge/zero%20cost-free%20forever-ff69b4.svg" alt="Zero Cost"></a>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#这是什么">功能特性</a> ·
  <a href="#架构一览">架构一览</a> ·
  <a href="#千面设计市场-">千面主题</a> ·
  <a href="#可观测性">可观测性</a> ·
  <a href="#路线图">路线图</a> ·
  <a href="CONTRIBUTING.md">参与贡献</a>
</p>

---

## 这是什么？

HomeStream 是一个轻量级、可自托管的 **多Agent协作框架**——它是通往 AI 世界的那把钥匙。它提供：

- 🏠 **事件中枢** — EventStream因果链，追踪每个Agent动作的来龙去脉
- 💬 **Agent群聊** — 频道广播、点对点消息、@提及路由、Kanban任务回调
- 🔐 **安全内置** — Token认证、注入防护、日志脱敏、速率限制、三层权限
- 🧠 **三层模型路由** — L1本地/L2云端/L3备份，自动降级，**永远免费托底**
- 🎯 **零配置启动** — 一个命令即可运行，从solo到team渐进式升级
- 🔌 **弹性模式** — Solo(单Agent) → Team(多Agent协作) → Ecosystem(插件扩展)

HomeStream 是 [OpenBridge](https://github.com/Ninefoldatwill/openbridge) 生态的开源基石。

### 架构一览

```mermaid
graph TB
    User[👤 用户] -->|HTTP / WebSocket| API[FastAPI 服务]
    API --> Router{三层模型路由}
    Router -->|L1 免费| Local[L1 本地模型<br/>Qwen2.5-7B]
    Router -->|L2 增强| Cloud[L2 云端模型<br/>GLM / 通义千问 / DeepSeek]
    Router -->|L3 托底| Backup[L3 备份模型<br/>Ollama 本地]
    API --> Events[EventStream 因果链]
    Events --> Store[(SQLite EventStore)]
    API --> Agents[Agent 协作层]
    Agents -->|ICP v1.1| Chat[💬 群聊 / 点对点]
    Agents -->|A2A 协议| Tasks[🎯 任务委派]
    API --> Themes[🎨 千面设计市场]
    API --> Obs[📊 可观测性 10面板]
    API --> PWA[📱 PWA 移动端]
    
    style Local fill:#4a90d9,color:#fff
    style Router fill:#ff69b4,color:#fff
    style Events fill:#f5a623,color:#fff
    style Themes fill:#9b59b6,color:#fff
```

---

## 快速开始

> 🇨🇳 **国内用户**：请参阅 [国内安装指南](INSTALL_CN.md) 获取镜像源配置、ModelScope模型下载、国产API备选等详细指引。
>
> HomeStream 的 L1 本地推理层**零外部依赖**，即使完全断网也可运行。

### 一键安装

```bash
# Linux/macOS（GitHub 源）
curl -fsSL https://raw.githubusercontent.com/Ninefoldatwill/homestream/main/install.sh | bash

# Linux/macOS（Gitee 镜像，国内推荐）
curl -fsSL https://gitee.com/ninefoldatwill/homestream/raw/main/install.sh | bash

# Windows PowerShell（GitHub 源）
iwr -useb https://raw.githubusercontent.com/Ninefoldatwill/homestream/main/install.ps1 | iex

# Windows PowerShell（Gitee 镜像，国内推荐）
iwr -useb https://gitee.com/ninefoldatwill/homestream/raw/main/install.ps1 | iex
```

### 手动安装

```bash
# 1. 克隆仓库
# GitHub（国际）
git clone https://github.com/Ninefoldatwill/homestream.git
# 或 Gitee（国内推荐，速度快）
git clone https://gitee.com/ninefoldatwill/homestream.git
cd homestream

# 2. 安装依赖（国内推荐加 -i 清华镜像）
pip install -r requirements.txt
# 国内镜像加速：
# pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 配置环境
cp .env.example .env
# 编辑 .env 填入你的Agent Token

# 4. 启动服务
python bridge_v7_server.py
```

打开浏览器访问：
- API文档：http://localhost:3458/docs
- 会议室：http://localhost:3458/meeting
- 健康检查：http://localhost:3458/health
- 指标面板：http://localhost:3458/metrics

### CLI 工具

```bash
homestream start          # 启动服务
homestream stop           # 停止服务
homestream status         # 查看状态
homestream mode solo      # 切换为单Agent模式
homestream mode team      # 切换为团队模式
homestream doctor         # 全息诊断
```

---

## 架构概览

```
HomeStream V5
│
├── bridge_v7_server.py       # FastAPI主服务 (API端点)
├── event_stream.py            # EventStream引擎 (因果链)
├── event_store.py             # SQLite持久化存储
├── config.py                  # 环境变量配置 (.env)
│
├── 安全层
│   ├── prompt_security.py     # Prompt注入防护
│   ├── permission_guard.py    # 三层权限分治
│   ├── rate_limiter.py        # 限流令牌桶
│   └── log_sanitizer.py       # 日志脱敏
│
├── 模型路由
│   ├── model_router.py        # 三层路由 (L1/L2/L3)
│   └── providers/             # 模型提供者集成
│
├── 记忆系统
│   ├── memory_evolution.py    # 记忆演化 (遗忘/合并/重构)
│   └── soul_config.py         # Soul配置 (角色模板)
│
├── 协作工具
│   ├── skill_router.py        # 技能路由器
│   ├── worktree_manager.py    # Worktree隔离
│   ├── workflow_engine.py     # 可视化工作流
│   ├── messaging_gateway.py   # 多平台IM网关
│   └── plugin_registry.py     # 插件市场注册
│
├── CLI工具
│   └── openbridge/cli.py      # Typer+Rich CLI
│
└── 测试套件
    ├── test_meeting_room.py   # 会议室闭环测试
    ├── test_soul_config.py    # Soul配置测试
    ├── test_security_injection.py # 安全注入测试
    └── test_openbridge_cli.py # CLI测试
```

---

## Loop Engineering 落地

HomeStream 践行 **Loop Engineering**——任务在自主循环中运转，而非依赖一次性提示词。

| 循环环节 | 能力 | 对应模块 |
|:-----|:-----|:-----|
| 🔄 **执行** | Agent 自主拆解任务，多步串行/并行执行 | `workflow_engine.py` |
| ✅ **校验** | 每一步执行前自动检查前置条件 | `condition_verifier.py` |
| 🔁 **重试** | 失败自动降级到替代方案，永不硬报错 | `failsafe_guardian.py` |
| 📦 **归档** | 失败教训自动记录，下次自动避开 | `ratchet_loop.py` |
| 🔍 **追踪** | 任何一步出问题，沿因果链追溯到根源 | `event_stream.py` |
| 🧬 **学习** | 长期记忆演化，Agent 越用越聪明 | `memory_evolution.py` |

> 不是写好提示词让 AI 一次答对，而是设计一个"执行→校验→重试→归档→学习"的闭环，让 AI **自己转到对为止**。

---

## 核心概念

### ICP v1.1 协议

9种消息类型：`INFO` / `ASK` / `TASK` / `UPD` / `DONE` / `WARN` / `ACK` / `PING` / `LOG`

- BLUF结论先行，单条 ≤ 500字符
- SLA：WARN < 5min / ASK+TASK < 30min

### EventStream因果链

每个Event携带 `cause` 字段，指向触发它的上游Event，形成完整的因果追踪链。

### 弹性模式三档渐进

| 功能 | Solo | Team | Ecosystem |
|:-----|:----:|:----:|:---------:|
| EventStream | ✓ | ✓ | ✓ |
| 群聊 | ✓ | ✓ | ✓ |
| Prometheus监控 | ✓ | ✓ | ✓ |
| structlog日志 | ✓ | ✓ | ✓ |
| Kanban任务板 | — | ✓ | ✓ |
| Worktree隔离 | — | ✓ | ✓ |
| Ratchet Loop | — | ✓ | ✓ |
| ICP v2 | — | ✓ | ✓ |
| MCP Server | — | — | ✓ |
| A2A协议 | — | — | ✓ |

> **Ratchet Loop — 只进不退的质量棘轮**
>
> 每个 Agent 产出的代码/文档，都要经过 Maker（铸造工坊）和 Reviewer（审查工坊）双层工坊的考验。Reviewer 是独立的 Critique 子代理，它不看 Maker 的思路，只看产出是否符合规范——通过则棘轮锁定（不可回退），失败则自动归档并触发重铸。这不是简单的 code review，而是一种**让 AI 自我进化的质量演化机制**。
>
> *（概念参考：OpenScience Critique 子代理理念 — HomeStream 的 Ratchet Loop 在"只进不退"的棘轮锁定上更进一步）*

> **技能生态 — AI 的包管理器**
>
> HomeStream 的 SKILL.md 格式已成为 Agent 生态的"包管理器"标准。技能按场景组织：办公协作 / 开发工程 / 信息检索 / 创意设计 / 数据分析……每个 SKILL.md 都是 Agent 能学会的一项新能力。社区可以贡献自己的技能包，就像 npm 之于 JavaScript。
>
> *（概念参考：OpenScience 290+ 技能包分类理念 — HomeStream 按职业场景组织技能生态）*

### 三层模型路由

| 层级 | 模型 | 延迟 | 成本 | 用途 |
|:----:|:-----|:----:|:----:|:-----|
| L1 | Qwen2.5-7B (本地) | ~444ms | 免费 | 日常推理 |
| L2 | GLM / 通义千问Turbo (云端) | ~1.4s | 免费 | 复杂任务 |
| L3 | DeepSeek / 通义千问Max (备份) | ~1.5s | ~¥0.0001 | 自动降级 |

双线路保障：主线路(L1+L2) + 复线(L3)，asyncio.wait_for超时自动切换。

> **抗卡脖设计**：通义千问为阿里云国产API，数据中心在国内，不受国际网络波动影响。
> 当 GLM/DeepSeek 不可用时，自动降级到通义千问保障服务连续性。
> 详见 [技术主权评估](TECH_SOVEREIGNTY_ASSESSMENT.md) 和 [国内安装指南](INSTALL_CN.md)。

> **模型无关，但成本有知。**
>
> HomeStream 的三层路由架构支持任意兼容 OpenAI API 格式的模型提供商——但与依赖 API 密钥的竞品不同，**L1 层永远跑在你自己的机器上，零成本、零依赖、零隐私泄露**。即使断网，L3 的 Ollama 本地模型仍能托底，这把钥匙永远在你手中。
>
> *（概念参考：OpenScience by Synthetic Sciences, Apache 2.0 — 模型无关设计理念）*

---

## API端点

### 事件系统

| 方法 | 端点 | 功能 |
|:-----|:-----|:-----|
| POST | `/api/v7/events/send` | 发送事件 |
| GET | `/api/v7/events` | 查询事件 |
| GET | `/api/v7/events/chain/{id}` | 因果链追踪 |
| GET | `/api/v7/stats` | 统计信息 |

### 会议室闭环

| 方法 | 端点 | 功能 |
|:-----|:-----|:-----|
| POST | `/api/v7/channels/send` | 频道发送 |
| GET | `/api/v7/channels` | 频道列表 |
| POST | `/api/v7/callback/kanban` | Kanban回调 |
| GET | `/meeting` | 会议室前端 |

### 任务 & Worktree

| 方法 | 端点 | 功能 |
|:-----|:-----|:-----|
| POST | `/api/v7/tasks/lifecycle` | 任务生命周期 |
| POST | `/api/v7/handoff` | Handoff交接 |
| POST | `/api/v7/worktree/create` | 创建Worktree |
| GET | `/api/v7/worktree/list` | Worktree列表 |

完整API文档：http://localhost:3458/docs

---

## 安全

HomeStream将安全作为第一优先级：

- **Token认证** — hmac.compare_digest防时序攻击
- **注入防护** — 13种危险模式检测 + ICP内容过滤
- **日志脱敏** — 自动过滤token/key/password
- **速率限制** — 令牌桶算法防滥用
- **三层权限** — L1公开/L2插件/L3核心分级访问

详见 [SECURITY.md](SECURITY.md)

---

## 测试

```bash
# 运行全部测试
pytest -v

# 覆盖率
pytest --cov=. --cov-report=html

# 安全扫描
bandit -r .
```

当前测试状态：**700+ tests, 0 failures**

---

## 千面设计市场

> **不造一面墙，只铸千万门。** 每个人的 HomeStream 都是独一无二的。

HomeStream 内置 9 种精心设计的主题，覆盖极客、东方美学、现代商务、创意个性等全用户画像。安装即用，一键切换：

| 主题 | 风格 | 适合谁 |
|:-----|:-----|:-------|
| 液态玻璃 Liquid Glass | 毛玻璃质感 · 半透明 · 景深模糊 | 现代商务用户 |
| 赛博朋克 Cyberpunk Neon | 霓虹光效 · 故障艺术 · 扫描线 | 极客 / 科幻爱好者 |
| 终端绿 Terminal Green | 黑底绿字 · monospace · CRT | 开发者 / 运维 |
| 极简禅意 Zen Minimal | 大留白 · 自然色调 · 呼吸感 | 追求平静专注 |
| 水墨国风 Ink Wash | 水墨晕染 · 宣纸纹理 · 东方留白 | 文化爱好者 / 国潮 |
| 新粗野主义 Neubrutalism | 粗边框 · 硬阴影 · 高饱和 | 创意 / 设计师 |
| 像素复古 Pixel Retro | 8-bit · 像素化 · 游戏配色 | 怀旧 / 游戏玩家 |
| 暗夜极光 Aurora Dark | 深色底 · 流动渐变 · 极光带 | 夜间使用 / 护眼 |
| 粘土拟态 Claymorphism | 3D圆润 · 柔和阴影 · 温暖 | 家庭 / 普通用户 |

```bash
# 切换主题
openbridge theme activate cyberpunk-neon

# 列出所有主题
openbridge theme list

# 预览主题（不激活）
openbridge theme preview ink-wash
```

想要自己的主题？参考 `themes/liquid-glass/theme.json` 创建一个，提交 PR 即可加入市场。

### 主题无障碍审计（theme_a11y）

每个主题在发布前都会经过 WCAG 2.1 AA 级无障碍审计，确保"好看"也"好用"：

- **对比度检查** — 正文 ≥4.5:1，大文本 ≥3:1，UI组件 ≥3:1（W3C 公开算法）
- **色盲友好性** — 红色盲 / 绿色盲 / 蓝色盲三维度可区分性检测
- **零依赖** — 纯 Python 标准库实现，基于 W3C 免版税(RF)标准

---

## 生态资源

HomeStream 是通往 AI 世界的那把钥匙——钥匙能开很多扇门。以下是收录的优质开源生态资源：

### AI 编程工具

HomeStream 不自己做 AI 编程工具，但连接优秀的开源工具。L3 本地模型层（Ollama）可直接为这些工具提供本地推理：

| 工具 | Stars | 特点 | 本地模型 |
|:-----|------:|:-----|:--------:|
| [OpenCode](https://github.com/sst/opencode) | 172K | 75+ 提供商，MIT 协议 | Ollama |
| [Cline](https://github.com/cline/cline) | 63K | VS Code 自主编码代理 | Ollama |
| [Aider](https://github.com/Aider-AI/aider) | 46K | Git 原生，终端 AI 编程 | Ollama |
| [Continue.dev](https://github.com/continuedev/continue) | — | 50+ 模型，高度自定义 | Ollama |
| [Tabby](https://github.com/TabbyML/tabby) | — | 企业级自托管 Copilot | 完全自托管 |

完整对比和安装引导见 [docs/ai-coding-resources.md](docs/ai-coding-resources.md)。

---

## 路线图

> 铸钥匠的锻造之路，永不停歇。

### V5.0.0（当前版本 · 2026.7）

**已完成 ✅**

- ✅ 三层模型路由（L1本地 / L2云端 / L3备份，**永远免费托底**）
- ✅ EventStream 因果链 + 700+ 测试全通过
- ✅ 千面设计市场（9 种主题，覆盖 6 大用户画像）
- ✅ 双开源（GitHub + Gitee 镜像，国内国际双通道）
- ✅ 安全内置（注入防护 + 日志脱敏 + 三层权限 + 速率限制）
- ✅ 弹性模式三档渐进（Solo → Team → Ecosystem）
- ✅ Loop Engineering 闭环（执行→校验→重试→归档→学习）
- ✅ 记忆演化引擎（遗忘 / 合并 / 重构，越用越聪明）

**本期路线 🔮**

- 🔮 **一图一世界**（Photo to Theme）— 拍一张照片，生成独一无二的前端主题

  不只提取颜色——从**色彩、纹理、形态**三个维度完整还原作品的视觉灵魂：

  | 维度 | 算法 | 还原什么 |
  |:-----|:-----|:---------|
  | 色彩 | K-means 聚类 + 中国传统色系 135 色匹配 | 主色板、暗色/亮色模式、传统色名 |
  | 纹理 | LBP + Gabor + GLCM 三算法融合 | 背景纹理、边框风格、阴影质感 |
  | 形态 | HOG 边缘梯度 + 轮廓圆润度分析 | 圆角弧度、字体选择、裁切路径 |

  适用场景：设计师、摄影爱好者、手作人——任何想让作品"活"在 AI 界面里的人。

  ```bash
  # 未来用法预览
  openbridge theme from-photo photo.jpg --name my-theme
  ```

- 🔮 **可视化工作流编排器** — 拖拽式 Agent 工作流设计
- ✅ **可观测性前端** — EventStream 数据可视化面板（7/8完成）
  - 10面板仪表盘：HTTP成功率 / 延迟百分位 / Token使用 / 事件分布 / ICP消息 / 技能调用 / 成本拆分 / Provider状态 / 架构可视化 / 数据质量守卫
  - 技术栈：ECharts + 纯HTML + 原创SVG生成引擎（无React构建链）
  - 架构可视化：Agent通信拓扑图 / 事件因果链流向图 / 三层路由状态图
  - 数据质量守卫：因果链完整性 / 时间戳连续性 / 事件类型合法性 / Agent身份有效性
  - **全链路溯源**：每个 Agent 动作从触发到完成，完整因果链可追溯——这不是事后补录的日志，而是 EventStream 原生记录的事件谱系。data_guardian 四维校验确保溯源数据本身的可信度。
  - 访问 `/observatory` 或仪表盘快捷入口

  > *（概念参考：OpenScience Provenance 溯源理念 — HomeStream 的 EventStore + data_guardian 在 Agent 事件溯源领域走得更远）*

- ✅ **主题无障碍审计器** — WCAG 2.1 AA 级配色审计（7/9完成）
  - 对比度检查 + 色盲友好性检测（3种色盲模拟）
  - 零第三方依赖，纯 Python 标准库实现
  - 基于 W3C 免版税(RF)标准，为千面设计市场保驾护航

- ✅ **A2A 协作协议规范** — Agent 间协作协议文档（7/9完成）
  - 基于 ICP v1.1 扩展，定义 Agent 发现 / 能力声明 / 任务委派 / 结果回传
  - 支持 Solo / Team / Ecosystem 三档协作模式
  - 详见 [A2A_PROTOCOL.md](A2A_PROTOCOL.md)

### 未来愿景

- 🌐 多语言生态（i18n 国际化）
- 📡 MCP + A2A 双协议生态互联
- 🎨 主题市场社区贡献体系（主题分享 / 评分 / 一键安装）

> 不造一面墙，只铸千万门。铸钥匠给钥匙，用户选门——未来的门，由用户自己画。

---

## 贡献

欢迎贡献！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

治理策略（三层分治 / 许可证 / 依赖审计 / 反 Vibe Coding）详见 [GOVERNANCE.md](GOVERNANCE.md)。

快速流程：
1. Fork → 2. 创建分支 → 3. 开发 → 4. 测试 → 5. PR

---

## 社区

- 📖 [文档](https://github.com/Ninefoldatwill/homestream/wiki)
- 💬 [讨论](https://github.com/Ninefoldatwill/homestream/discussions)
- 🐛 [问题追踪](https://github.com/Ninefoldatwill/homestream/issues)
- 🇨🇳 [Gitee 镜像](https://gitee.com/ninefoldatwill/homestream)（国内访问）
- 📧 contribute@jiuchong.studio

---

## 许可证

MIT License — 见 [LICENSE](LICENSE)

"HomeStream" 是 九重工作室 的商标 — 见 [TRADEMARK.md](TRADEMARK.md)

---

## 致谢

HomeStream 的诞生离不开开源社区的智慧：

- **FastAPI** — 高性能Python Web框架
- **pydantic** — 数据验证的黄金标准
- **Typer + Rich** — 终端美学的巅峰组合
- **structlog** — 结构化日志的最佳实践
- **Qwen** — 本地运行的开源大模型
- 以及所有为Agent生态做出贡献的开源项目

融众之优，铸己之新。不造墙，只铸钥。我们一起，让每个人都能推开门。

---

## Star History

<a href="https://star-history.com/#Ninefoldatwill/homestream&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://star-history.com/embed?secret=#Ninefoldatwill/homestream&Date&theme=dark">
    <img align="center" width="660" src="https://star-history.com/embed?secret=#Ninefoldatwill/homestream&Date" alt="Star History Chart">
  </picture>
</a>

---

<p align="center">
  <sub>铸钥匠 · 九重工作室 · 2026</sub><br>
  <sub> 🔑 每个人在 AI 世界的家园，流光汇河。 </sub>
</p>
**九重工作室 · 铸钥匠** · 2026
