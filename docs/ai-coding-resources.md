# AI 编程开源生态资源

> HomeStream 是通往 AI 世界的那把钥匙——这把钥匙能开很多扇门，AI 编程是其中一扇。
>
> 我们不自己造 AI 编程工具，我们连接优秀的开源工具，为用户提供入口。

## 为什么收录 AI 编程资源？

HomeStream 定位为 **AI 生态操作系统**，不是单一应用。操作系统的职责是连接——连接模型、连接工具、连接用户。

AI 编程是 2026 年增长最快的 AI 方向之一（GitHub Top 3 热门方向）。虽然 HomeStream 自身不做 AI 编程工具，但我们的用户中有人想尝试。

收录这些资源的核心理念：

1. **铸钥匠哲学** — 不造门，铸钥。钥匙能开各种门，包括 AI 编程这扇门
2. **L3 本地路由协同** — 5/7 头部工具支持 Ollama 本地模型，而 HomeStream 的 L3 层已经在跑 Ollama
3. **生态多元化托底** — 题材丰富，不同需求的用户都能找到入口

---

## GitHub 头部 AI 编程开源项目（2026 年 7 月数据）

### 完整对比表

| 排名 | 项目 | GitHub Stars | 协议 | 类型 | 本地模型支持 |
|:----:|:-----|------------:|:-----|:-----|:------------|
| 1 | [OpenCode](https://github.com/sst/opencode) | 172,198 | MIT | 终端 / VS Code | Ollama, LM Studio, 75+ 提供商 |
| 2 | [Gemini CLI](https://github.com/google-gemini/gemini-cli) | 105,104 | Apache-2.0 | 终端 | 通过提供商支持 |
| 3 | [OpenAI Codex](https://github.com/openai/codex) | 89,991 | Apache-2.0 | CLI / IDE / 云端 | API 密钥模式 |
| 4 | [Cline](https://github.com/cline/cline) | 62,996 | Apache-2.0 | IDE 扩展 + CLI | Ollama, LM Studio |
| 5 | [Goose](https://github.com/aaif-goose/goose) | 48,542 | Apache-2.0 | CLI + 桌面 (Rust) | Ollama, 15+ 提供商 |
| 6 | [Aider](https://github.com/Aider-AI/aider) | 45,945 | Apache-2.0 | 终端 (Python) | Ollama, OpenAI 兼容 API |
| 7 | [Kilo Code](https://github.com/Kilo-Org/kilocode) | 19,968 | MIT | IDE 扩展 + CLI | BYOK, 500+ 模型, Ollama |

### 补充项目

| 项目 | 协议 | 定位 | 本地模型支持 |
|:-----|:-----|:-----|:------------|
| [Continue.dev](https://github.com/continuedev/continue) | Apache-2.0 | 最灵活的开源 AI 编程框架 | Ollama, 50+ 提供商 |
| [Tabby](https://github.com/TabbyML/tabby) | Apache-2.0 | 企业级自托管 Copilot 替代 | 完全自托管 |
| [Roo Code](https://github.com/roo-code/roo-code) | Apache-2.0 | 全功能免费 AI 编程助手 | Ollama, LM Studio |

---

## 按使用场景分类

### 场景一：终端 + 本地模型（与 HomeStream L3 协同最佳）

| 工具 | 特点 | 与 HomeStream 协同 |
|:-----|:-----|:-------------------|
| **Aider** | Git 原生，每次修改自动提交，`--model ollama/<name>` 一行命令连本地模型 | L3 层 Ollama 已在跑，直接 `aider --model ollama/qwen2.5-coder` 即可 |
| **OpenCode** | 75+ 提供商，MIT 协议，社区最活跃 | L3 层作为 OpenCode 的本地后端，零额外配置 |

**快速上手（Aider + HomeStream L3）**：
```bash
# 前提：HomeStream 已启动，L3 层 Ollama 在运行
# 安装 Aider
python -m pip install aider-install && aider-install

# 拉取编程模型
ollama pull qwen2.5-coder:7b

# 启动 AI 编程（连接 HomeStream 本地模型）
aider --model ollama/qwen2.5-coder:7b
```

### 场景二：IDE 原生 + 自托管

| 工具 | 特点 | 适用人群 |
|:-----|:-----|:---------|
| **Cline** | VS Code + JetBrains 双支持，自主编码代理，Plan/Act 双模式 | IDE 重度用户，想要 AI 自主完成任务 |
| **Continue.dev** | 50+ 模型提供商，`.continue/config.json` 高度自定义 | 注重隐私、喜欢深度配置的开发者 |
| **Kilo Code** | 500+ 模型，BYOK 按原价计费 | 需要最多模型选择的团队 |

### 场景三：企业自托管

| 工具 | 特点 | 适用人群 |
|:-----|:-----|:---------|
| **Tabby** | 完全自托管，Docker 一键部署，GPU 加速，访问控制 | 企业团队，代码不能出境 |
| **Goose** | Rust 构建，70+ MCP 扩展，Linux Foundation 治理 | 需要通用 Agent（非纯编码）的团队 |

### 场景四：免费快速上手

| 工具 | 特点 | 限制 |
|:-----|:-----|:-----|
| **Gemini CLI** | Google 账户即可，60 请求/分钟，1000 请求/天 | 依赖 Google 云端 |
| **OpenAI Codex** | Terminal-Bench 排名第一（GPT-5.5: 83.4%） | 需 ChatGPT 订阅 |

---

## 与 HomeStream 的协同关系

### 本地模型协同

HomeStream 的三层模型路由中，L3 层使用 Ollama 本地模型（qwen2.5:3b）作为托底。
上述 7 个头部工具中有 5 个支持 Ollama——这意味着：

```
HomeStream L3 (Ollama 本地模型)
    ↓ 同一个 Ollama 实例
    ├── HomeStream 对话路由（qwen2.5:3b）
    ├── Aider AI 编程（qwen2.5-coder:7b）
    ├── Cline IDE 代理（qwen2.5-coder:7b）
    └── Continue.dev 代码补全（starcoder2:3b）
```

用户不需要为每个工具单独配置 Ollama——HomeStream 已经把本地模型基础设施搭好了。

### MCP 协议协同

HomeStream 支持 MCP（Model Context Protocol）协议。Goose 有 70+ MCP 扩展，Cline 也支持 MCP。
这意味着 HomeStream 的 MCP 工具可以被这些 AI 编程工具调用，反过来也一样。

### 生态定位

| 层次 | 谁负责 | 说明 |
|:-----|:-------|:-----|
| 操作系统 | HomeStream | 模型路由 + 记忆演化 + 工作流 + 主题市场 |
| AI 编程 | 开源工具 | OpenCode / Aider / Cline / Continue 等 |
| 协同方式 | 资源收录 | 本文档 + 未来的一键安装引导 |

HomeStream 不与这些工具竞争——我们提供基础设施，它们提供专业能力，用户得到完整体验。

---

## 选择决策指南

```
你想用 AI 编程吗？
│
├── 想完全本地/离线
│   ├── 终端用户 → Aider（Git 原生 + Ollama）
│   └── IDE 用户 → Cline（VS Code + Ollama）
│
├── 想要最多模型选择
│   ├── 终端 → OpenCode（75+ 提供商）
│   └── IDE → Kilo Code（500+ 模型）
│
├── 企业团队自托管
│   └── Tabby（Docker + GPU + 访问控制）
│
└── 想免费快速尝试
    ├── 有 Google 账户 → Gemini CLI（1000 请求/天免费）
    └── 有 ChatGPT 订阅 → OpenAI Codex
```

---

## 免责声明

- 本文档收录的项目均为第三方独立开源项目，与 HomeStream 无从属关系
- 各项目版权归各自所有者，使用前请阅读各自的开源协议
- HomeStream 不对这些项目的安全性和稳定性做担保
- 如有问题，请向对应项目提交 Issue

---

*本文档数据采集于 2026 年 7 月。AI 编程领域更新极快，建议定期检查各项目的最新动态。*
