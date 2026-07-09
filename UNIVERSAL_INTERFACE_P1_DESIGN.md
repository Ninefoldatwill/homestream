# P1 设计：HomeStream 自适应接口与跨平台扩展

> 设计日期：2026-07-09
> 目标：为 HomeStream 规划未来 1-2 个版本的普大众化接口增强
> 原则：与现有 ICP v1.1 / EventStream / 千面设计市场架构协同，不引入重型框架
> 合规：全部原创，仅借鉴 A2UI / NAI / OpenHuman 的概念知识

---

## 一、P1-1：HS-UI 协议（HomeStream UI 描述协议）

### 1.1 定位

HS-UI 是一个让 HomeStream Agent 返回结构化 UI 描述的协议。它不是可执行代码，而是声明式 JSON，由客户端的可信组件目录本地渲染。核心思想借鉴 A2UI 的"安全像数据、表现力像代码"，但格式、字段和组件目录完全原创。

### 1.2 设计原则

1. **与 ICP v1.1 共生**：UI 交互产生的事件是 `EventStream` 中的 `ASK`/`TASK`/`DONE` 消息。
2. **主题即渲染器**：每个主题可以定义自己的组件样式映射。
3. **安全优先**：白名单组件目录，拒绝任意 HTML/JS/CSS。
4. **渐进增强**：无 HS-UI 能力的客户端可降级为纯文本/按钮。

### 1.3 JSON Schema 草案

```json
{
  "version": "1.0.0",
  "surface_id": "dashboard-welcome",
  "title": "欢迎回家",
  "components": [
    {
      "id": "welcome-card",
      "type": "card",
      "props": {
        "title": "HomeStream 已就绪",
        "subtitle": "本地模型 Qwen2.5-7B 运行中",
        "icon": "home"
      }
    },
    {
      "id": "quick-actions",
      "type": "button-group",
      "props": {
        "buttons": [
          {
            "label": "开始对话",
            "action": { "type": "navigate", "target": "/chat" },
            "variant": "primary"
          },
          {
            "label": "查看观测台",
            "action": { "type": "navigate", "target": "/observatory" },
            "variant": "secondary"
          }
        ]
      }
    },
    {
      "id": "agent-status-list",
      "type": "list",
      "props": {
        "items": [
          { "label": "澜舟", "value": "在线", "status": "success" },
          { "label": "澜澜", "value": "处理任务中", "status": "warning" }
        ]
      }
    }
  ]
}
```

### 1.4 基础组件目录（V1 版）

| 组件类型 | 用途 | 必要属性 |
|:---------|:-----|:---------|
| `text` | 静态文本 | `content` |
| `heading` | 标题 | `level`, `content` |
| `button` | 按钮 | `label`, `action` |
| `button-group` | 按钮组 | `buttons` |
| `card` | 卡片容器 | `title`, `children` |
| `list` | 列表 | `items` |
| `input` | 文本输入 | `name`, `placeholder` |
| `select` | 下拉选择 | `name`, `options` |
| `image` | 图片 | `src`, `alt` |
| `chart` | 图表 | `type`, `data` |
| `progress` | 进度条 | `value`, `max` |
| `badge` | 标签徽章 | `label`, `variant` |
| `divider` | 分隔线 | — |
| `spacer` | 间距 | `height` |

### 1.5 事件绑定

用户交互后，客户端生成 ICP 消息并发送到 `/api/v7/icp`：

```json
{
  "event_type": "ASK",
  "sender": "user",
  "recipient": "澜舟",
  "content": "点击了 dashboard-welcome.quick-actions 的"开始对话"",
  "handoff": "ui:navigate:/chat",
  "cause": "welcome-card"
}
```

### 1.6 与千面设计市场集成

每个主题可在 `theme.json` 中声明支持的 HS-UI 组件映射：

```json
{
  "hsui_components": {
    "button": {
      "render": "<button class='hsui-btn hsui-btn-{variant}'>{label}</button>",
      "styles": ".hsui-btn { min-height: 44px; border-radius: 8px; }"
    },
    "card": {
      "render": "<div class='hsui-card'><h3>{title}</h3><p>{subtitle}</p></div>"
    }
  }
}
```

### 1.7 安全规则

1. 服务端验证 HS-UI JSON schema 后才能发送给客户端。
2. 客户端渲染器只允许渲染白名单组件类型。
3. 禁止在 `props` 中出现 `script`、`javascript:`、`on*` 事件处理器。
4. 所有 `action` 必须是已注册的 ICP 动作或导航目标。

---

## 二、P1-2：Tauri 2.0 轻量封装路线图

### 2.1 为什么选 Tauri 2.0？

| 方案 | 优势 | 劣势 | 与 HomeStream 适配度 |
|:-----|:-----|:-----|:--------------------|
| Tauri 2.0 | Rust + Web 前端，同时覆盖桌面和移动，包体积小 | 需要学习 Rust 和移动端签名 | 高：复用现有 Web 资源 |
| Electron | 成熟，桌面体验好 | 包体积大，不支持移动端 | 中 |
| Capacitor | 纯 Web 技术，移动端友好 | 桌面端不如 Tauri 原生 | 中 |
| Flutter | 性能最好，原生体验 | 重写前端，成本高 | 低 |

### 2.2 架构设计

```
┌─────────────────────────────────────┐
│  Tauri 层（Rust）                     │
│  - 窗口管理 / 系统托盘                  │
│  - 本地通知 / 文件系统沙箱               │
│  - 启动时拉起 Python 子进程             │
├─────────────────────────────────────┤
│  Web 前端（现有 HTML/CSS/JS）          │
│  - 与 FastAPI 通过 localhost 通信        │
├─────────────────────────────────────┤
│  Python FastAPI 后端（HomeStream）     │
│  - 业务逻辑、模型路由、EventStore       │
└─────────────────────────────────────┘
```

### 2.3 目录结构（未来）

```
HomeStream-开源版/
├── desktop-mobile/
│   ├── src-tauri/              # Rust 后端
│   │   ├── Cargo.toml
│   │   ├── src/
│   │   │   └── main.rs
│   │   ├── capabilities/
│   │   └── gen/
│   ├── public/                 # 静态 Web 资源（符号链接到项目根）
│   └── tauri.conf.json
└── README.md                   # 新增桌面端安装说明
```

### 2.4 启动流程

1. Tauri 启动时检测 Python 环境。
2. 如果本地没有安装 HomeStream，自动提示用户运行 `pip install homestream`。
3. 启动 Python 子进程：`python -m homestream.server --port 0`（自动分配端口）。
4. Tauri 窗口加载 `http://localhost:<port>`。
5. 关闭窗口时优雅终止 Python 进程。

### 2.5 移动端路线

- **Phase 1**：先完成桌面端 Tauri 封装（Win/Mac/Linux）。
- **Phase 2**：Tauri 2.0 支持 iOS/Android 构建，复用同一套前端。
- **Phase 3**：针对手机优化 HS-UI 组件渲染和触摸交互。

---

## 三、P1-3：自适应接口模式

### 3.1 设计目标

根据用户设备、输入方式、角色和当前场景，自动选择最合适的界面形态。

### 3.2 检测维度

| 维度 | 检测方式 | 取值示例 |
|:-----|:---------|:---------|
| 设备宽度 | `window.innerWidth` | mobile / tablet / desktop |
| 输入方式 | `'ontouchstart' in window` | touch / pointer |
| 用户角色 | 登录态 / token | guest / member / admin |
| 部署模式 | 后端配置 | solo / team / ecosystem |
|  prefers-color-scheme | CSS 媒体查询 | light / dark |
| 网络状态 | `navigator.connection` | fast / slow / offline |

### 3.3 模式映射表

| 设备 | 部署模式 | 角色 | 默认界面 | 适配策略 |
|:-----|:---------|:-----|:---------|:---------|
| 手机 | solo | guest | 精简聊天 | 隐藏高级设置，底部输入栏 |
| 手机 | solo | member | 聊天 + 快捷入口 | 显示最近任务、常用 Agent |
| 手机 | team | member | 任务列表 | 看板缩略，支持滑动切换状态 |
| 平板 | team | member | 分栏看板 | 左侧任务列表，右侧详情 |
| 桌面 | solo | guest | 完整仪表盘 | 全部 10 面板可观测性 |
| 桌面 | ecosystem | admin | 编排器视图 | 显示 Agent 拓扑、工作流编排 |

### 3.4 实现方案

1. 后端在 `bridge_v7_server.py` 渲染 HTML 时，根据 `User-Agent` + 用户 token 决定注入哪些 CSS 和 JS 模块。
2. 前端提供 `AdaptiveLayout` 类，根据窗口尺寸动态切换布局。
3. 主题市场支持 `breakpoints` 配置，允许主题作者自定义断点。

```json
{
  "breakpoints": {
    "mobile": { "max": 768 },
    "tablet": { "min": 769, "max": 1200 },
    "desktop": { "min": 1201 }
  }
}
```

---

## 四、P1 与 P0 的协同关系

```
P0（本月）: PWA + 移动响应式
    ↓
P1-1（7 月底-8 月）: HS-UI 协议，让 Agent 能生成界面
    ↓
P1-3（8 月）: 自适应接口模式，根据设备动态选择 HS-UI 组件布局
    ↓
P1-2（9 月）: Tauri 2.0 封装，把 Web 体验打包为桌面/移动 App
```

---

## 五、IP 边界声明

- **A2UI**：Apache 2.0，仅借鉴"声明式 UI 描述 + 可信组件目录"思想。HS-UI 的 schema、字段、组件命名、事件绑定全部原创。
- **NAI**：研究概念，仅借鉴"Agent 根据用户能力调整界面"的思想。实现原创。
- **Tauri 2.0**：MIT 协议，可合法使用。封装代码原创，不参考 OpenHuman/Cherry Studio 的 Tauri 实现。
- **OpenHuman**：仅借鉴"本地优先"产品叙事，不参考其桌面架构或代码。
