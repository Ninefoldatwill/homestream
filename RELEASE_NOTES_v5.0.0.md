# HomeStream V5.0.0 Release Notes

> **不造墙，只铸钥。**
>
> 开源版 V5.0.0 · 自进化AI生态操作系统 · 2026-07-10

---

## 🔑 版本亮点

HomeStream V5.0.0 是首个正式开源发布版本。这不是一个 AI 工具，而是一把**通往 AI 世界的钥匙**——零成本、可自托管、永远免费托底。

### 六大核心能力

| 能力 | 描述 | 差异化 |
|:-----|:-----|:-------|
| 🧠 三层模型路由 | L1本地/L2云端/L3备份，自动降级 | **竞品全依赖付费API，我们永远免费托底** |
| 🏠 EventStream 因果链 | 每个 Agent 动作完整可追溯 | **不是事后日志，而是原生事件谱系** |
| 💬 Agent 群聊 | 频道广播、点对点、@提及路由 | **ICP v1.1 协议，BLUF ≤500字符** |
| 🎨 千面设计市场 | 9种内置主题 + 主题市场 | **不造一面墙，只铸千万门** |
| 📊 可观测性 | 10面板仪表盘 + 架构可视化 + 数据守卫 | **纯HTML+ECharts，无React构建链** |
| 📱 PWA 移动端 | 可安装到手机主屏幕 + 离线支持 | **从桌面到口袋，普大众化接口** |

---

## 📦 完整功能清单

### 核心引擎
- **EventStream** — 事件因果链，9种事件类型（INFO/ASK/TASK/UPD/DONE/WARN/ACK/PING/LOG）
- **ICP v1.1 协议** — Agent 间通信标准，hmac.compare_digest 防时序攻击
- **三层模型路由** — L1 Qwen2.5-7B(本地) → L2 GLM(云端) → L3 DeepSeek(备份)
- **双线路保障** — 主线路 + 复线，asyncio.wait_for 超时自动切换
- **弹性模式** — Solo(单Agent) → Team(多Agent协作) → Ecosystem(插件扩展)
- **A2A 协作协议** — Agent 发现、能力声明、任务委派、结果回传

### 安全防护
- Token 认证（6个 Agent 独立令牌）
- 注入防护（SQL/XSS/Path Traversal 检测）
- 日志脱敏（敏感字段自动遮蔽）
- 速率限制（令牌桶算法）
- 三层权限（L1公开/L2插件/L3核心）

### 可观测性（10面板）
1. HTTP 成功率
2. 延迟百分位（P50/P90/P99）
3. Token 使用量
4. 事件分布
5. ICP 消息统计
6. 技能调用排行
7. 成本拆分
8. Provider 状态
9. **架构可视化**（Agent 拓扑图 / 事件因果链流向图 / 三层路由状态图）
10. **数据质量守卫**（因果链完整性 / 时间戳连续性 / 事件类型合法性 / Agent身份有效性）

### 千面设计市场
- 9种内置主题：液态玻璃 · 赛博朋克 · 终端绿 · 极简禅意 · 水墨国风 · 新粗野主义 · 像素复古 · 暗夜极光 · 粘土拟态
- 主题标准接口：安装/切换/预览 API
- **WCAG 2.1 AA 无障碍审计器**：对比度检查 + 色盲友好性检测

### PWA 普大众化接口
- PWA 清单（manifest.json）
- Service Worker 离线缓存
- 移动端响应式 CSS（触控优化 + 安全区适配）
- 底部导航栏（≤768px 自动显示）
- PWA 图标集（192/512/maskable/apple-touch-icon）

### 开发者工具
- 76 个 API 路由
- 850+ 测试用例
- CLI 安装脚本（GitHub + Gitee 双源）
- 完整文档（中英双语 README + CHANGELOG + CONTRIBUTING）

---

## 🚀 快速开始

```bash
# 一键安装（GitHub）
curl -fsSL https://raw.githubusercontent.com/Ninefoldatwill/homestream/main/install.sh | bash

# 一键安装（Gitee 镜像，国内推荐）
curl -fsSL https://gitee.com/the-warrior-king/homestream/raw/main/install.sh | bash

# 手动安装
git clone https://github.com/Ninefoldatwill/homestream.git
cd homestream
pip install -r requirements.txt
python bridge_v7_server.py
```

启动后访问 `http://localhost:3458`，开始使用你的 AI 生态钥匙。

---

## 🏗️ 架构概览

```
HomeStream 开源版 V5.0.0
├── 核心引擎
│   ├── event_stream.py        # EventStream 因果链
│   ├── event_store.py         # SQLite 事件持久化
│   ├── model_router.py        # 三层模型路由
│   ├── agent_card.py          # Agent 身份与发现
│   └── actions.py             # Agent 动作原语
├── 安全层
│   ├── middleware.py          # 可观测性中间件
│   ├── config.py              # 配置与令牌管理
│   └── logging_config.py      # 结构化日志
├── 可观测性
│   ├── observatory.py         # 10面板仪表盘
│   ├── arch_visualizer.py     # 架构可视化引擎（原创SVG）
│   └── data_guardian.py       # 事件数据质量守卫（四维校验）
├── 千面设计市场
│   ├── theme_manager.py       # 主题引擎
│   ├── theme_a11y.py          # WCAG 2.1 AA 无障碍审计器
│   └── themes/                # 9种内置主题
├── PWA 移动端
│   ├── manifest.json          # PWA 清单
│   ├── sw.js                  # Service Worker
│   ├── offline.html           # 离线回退页面
│   └── assets/mobile.css      # 移动响应式样式
├── 协议规范
│   ├── A2A_PROTOCOL.md        # Agent 协作协议
│   └── ICP v1.1               # 9种消息类型
└── 入口
    ├── bridge_v7_server.py    # FastAPI 主服务（76路由）
    ├── cli.py                 # CLI 命令行工具
    └── install.sh             # 一键安装脚本
```

---

## 📊 技术指标

| 指标 | 数值 |
|:-----|:-----|
| 代码行数 | ~20,000 行 |
| 测试用例 | 850+ |
| API 路由 | 76 个 |
| 事件类型 | 9 种 |
| 内置主题 | 9 种 |
| 可观测性面板 | 10 个 |
| 依赖项 | 极简（FastAPI + pydantic + structlog + Typer） |
| 许可证 | MIT |
| Python 版本 | 3.9+ |

---

## 🛣️ 路线图

### V5.0.0（本次发布）
- ✅ 核心引擎 + 三层路由 + EventStream
- ✅ 千面设计市场 9 主题 + 无障碍审计
- ✅ 可观测性 10 面板 + 架构可视化 + 数据守卫
- ✅ PWA 移动端 + 响应式
- ✅ A2A 协作协议 + ICP v1.1

### V5.1.0（计划中）
- HS-UI 协议（Agent 驱动的声明式 UI 描述）
- 自适应接口模式（设备/角色/场景检测）
- Tauri 2.0 桌面/移动 App 封装

### V5.2.0（计划中）
- 可视化工作流编排器（拖拽式 Agent 工作流）
- 可观测性主题面板
- 浏览器自动化插件市场

---

## 🙏 致谢

HomeStream 的诞生离不开开源社区的智慧：

- **FastAPI** — 高性能 Python Web 框架
- **pydantic** — 数据验证的黄金标准
- **Typer + Rich** — 终端美学的巅峰组合
- **structlog** — 结构化日志的最佳实践
- **Qwen** — 本地运行的开源大模型
- **ECharts** — 数据可视化的标杆

以及以下项目的理念启发（均为概念参考，未使用其代码）：
- OpenScience (Apache 2.0) — 模型无关设计、技能包分类、Provenance 溯源、Critique 子代理
- Google A2A (Apache 2.0) — Agent 间协作协议概念
- W3C WCAG 2.1 — 无障碍标准（免版税实施）

融众之优，铸己之新。不造墙，只铸钥。

---

## 📄 许可证

MIT License — 见 [LICENSE](LICENSE)

"HomeStream" 是九重工作室的商标 — 见 [TRADEMARK.md](TRADEMARK.md)

---

<p align="center">
  铸钥匠 · 九重工作室 · 2026<br>
  🔑 每个人在 AI 世界的家园，流光汇河。
</p>
