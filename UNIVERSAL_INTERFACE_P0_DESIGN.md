# P0 设计：HomeStream 普大众化接口（Web + PWA + 移动响应式）

> 设计日期：2026-07-09
> 目标：让 HomeStream 从"桌面浏览器可用"升级为"手机浏览器可用、可安装到主屏幕"
> 原则：零第三方依赖、纯标准 Web 技术、与现有 Python FastAPI 架构无缝集成
> 合规：全部原创实现，不依赖 WorkBuddy / OpenHuman / A2UI 代码

---

## 一、P0-1：PWA 渐进式 Web 应用化

### 1.1 新增文件

```
HomeStream-开源版/
├── assets/
│   ├── icon-192.png          # 192x192 主图标
│   ├── icon-512.png          # 512x512 启动屏图标
│   ├── icon-maskable-512.png # 自适应图标（Android 12+）
│   └── apple-touch-icon.png  # 180x180 iOS 主屏幕图标
├── manifest.json             # PWA 清单
├── sw.js                     # Service Worker
├── offline.html              # 离线回退页面
└── bridge_v7_server.py       # 修改：注入 PWA 注册和 link 标签
```

### 1.2 manifest.json 设计

```json
{
  "name": "HomeStream - 你的 AI 生态钥匙",
  "short_name": "HomeStream",
  "description": "零成本本地模型 + 三层免费路由 + 多模态 AI 生态",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#f5f7fb",
  "theme_color": "#4a90d9",
  "orientation": "portrait-primary",
  "icons": [
    {
      "src": "/assets/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/assets/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "/assets/icon-maskable-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable"
    }
  ],
  "categories": ["productivity", "ai", "developer"],
  "lang": "zh-CN",
  "dir": "ltr"
}
```

### 1.3 sw.js 设计（最小可行版）

```javascript
const CACHE_NAME = 'homestream-v5-0-0';
const PRECACHE_ASSETS = [
  '/',
  '/offline.html',
  '/assets/icon-192.png',
  '/assets/icon-512.png'
];

// install: 预缓存核心静态资源
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_ASSETS))
  );
  self.skipWaiting();
});

// activate: 清理旧缓存
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// fetch: 静态资源优先缓存，动态 API 走网络
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // 动态 API、WebSocket 不缓存
  if (url.pathname.startsWith('/api/') || url.protocol === 'ws:') {
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).catch(() => caches.match('/offline.html'));
    })
  );
});
```

### 1.4 bridge_v7_server.py 注入点

在每个 HTML 页面的 `<head>` 中追加：

```html
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<meta name="theme-color" content="#4a90d9">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<script>
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(console.error);
  }
</script>
```

### 1.5 offline.html 设计

极简页面：品牌 logo + 标题 + "当前处于离线状态，请连接网络后刷新" + 刷新按钮。

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HomeStream - 离线</title>
  <style>
    body { margin:0; height:100vh; display:flex; flex-direction:column; align-items:center; justify-content:center; font-family:system-ui; background:#f5f7fb; color:#1a1a2e; }
    .logo { width:96px; height:96px; margin-bottom:24px; }
    h1 { font-size:20px; margin:0 0 12px; }
    p { font-size:14px; color:#666; margin:0 0 24px; }
    button { padding:10px 24px; border:none; border-radius:8px; background:#4a90d9; color:#fff; font-size:14px; }
  </style>
</head>
<body>
  <img class="logo" src="/assets/icon-192.png" alt="HomeStream">
  <h1>HomeStream 离线中</h1>
  <p>请连接网络后刷新，继续使用你的 AI 生态钥匙。</p>
  <button onclick="location.reload()">刷新</button>
</body>
</html>
```

---

## 二、P0-2：移动端响应式补强

### 2.1 设计目标

- 宽度 320px-768px 设备：无横向滚动、按钮可触控、文本可读
- 宽度 768px-1200px 设备：平板优化，双栏或单栏自适应
- 宽度 ≥1200px：桌面完整体验

### 2.2 新增文件

```
HomeStream-开源版/
├── assets/
│   └── mobile.css            # 移动响应式样式（仅新增此文件）
```

### 2.3 注入方式

在 `bridge_v7_server.py` 生成的 HTML `<head>` 中追加：

```html
<link rel="stylesheet" href="/assets/mobile.css">
```

### 2.4 mobile.css 核心规则

```css
/* ==================== 基础移动优先规则 ==================== */

/* 全局触控优化 */
* { -webkit-tap-highlight-color: transparent; }
button, a, select, input, textarea { min-height: 44px; min-width: 44px; }

/* 聊天页底部固定输入栏 */
.chat-input-bar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 12px 16px env(safe-area-inset-bottom, 12px);
  background: var(--bg);
  border-top: 0.5px solid var(--border);
  z-index: 100;
}

.chat-input-bar .input-wrap {
  max-width: 100%;
  margin: 0;
}

/* 消息气泡 */
.msg { max-width: 92%; }

/* 侧边栏折叠 */
@media (max-width: 768px) {
  .sidebar { display: none; }
  .main { grid-template-columns: 1fr; padding: 16px; }
  .content-grid { grid-template-columns: 1fr; }
  .stats-row { grid-template-columns: repeat(2, 1fr); }
  .quick-launch { grid-template-columns: 1fr; }
  .mode-bar { flex-wrap: wrap; gap: 8px; }
  .modal { width: 92%; max-width: 92%; margin: 16px; }
  .msg-row { max-width: 90%; }
  .input-wrap select { width: 80px; }
  .meeting-modal .modal-box { width: 90vw; }
}

/* 小屏手机 */
@media (max-width: 480px) {
  .stats-row { grid-template-columns: 1fr; }
  .header { padding: 12px 16px; }
  .header h1 { font-size: 18px; }
  .card { padding: 14px; }
  .agent-card { flex-direction: column; align-items: flex-start; }
}

/* 安全区适配（刘海屏） */
@supports (padding: max(0px)) {
  .header, .chat-input-bar, .footer {
    padding-left: max(16px, env(safe-area-inset-left));
    padding-right: max(16px, env(safe-area-inset-right));
  }
}

/* 禁止缩放 */
input, textarea, select { font-size: 16px; }
```

### 2.5 页面级修改清单

| 页面 | 文件位置 | 修改点 |
|:-----|:---------|:-------|
| Dashboard 仪表盘 | `bridge_v7_server.py` 主 HTML | 主网格改为 mobile-first：默认单列，≥768px 双列，≥1200px 三列 |
| Observatory 可观测性 | `bridge_v7_server.py`  observatory HTML | 10 面板默认单列，≥768px 双列，≥1200px 四列 |
| Group Chat 群聊 | `bridge_v7_server.py` 群聊 HTML | 侧边栏隐藏，消息区占满，底部固定输入栏 |
| Chat 对话 | `bridge_v7_server.py` 聊天 HTML | 底部输入栏固定，消息气泡 92% 宽度 |
| Meeting 会议 | `bridge_v7_server.py` 会议 HTML | 模态框宽度 90vw，参会者头像 flex-wrap |

### 2.6 移动端交互优化

1. **底部导航栏**：当屏幕宽度 ≤768px 时，顶部导航栏切换为底部 4 项 Tab（仪表盘 / 聊天 / 观测台 / 更多）。
2. **下拉刷新**：聊天页面支持下拉刷新历史消息（通过 touchstart/touchmove/touchend 模拟）。
3. **长按菜单**：消息气泡长按弹出复制/引用菜单。
4. **软键盘适配**：输入框聚焦时，底部输入栏跟随 `visualViewport` 上移，避免被键盘遮挡。

---

## 三、验收标准

| 检查项 | 通过标准 |
|:-----|:---------|
| Lighthouse PWA | 评分 ≥ 80 |
| Chrome DevTools 模拟 iPhone 14 | 无横向滚动，所有按钮可点击 |
| 安卓 Chrome "添加到主屏幕" | 出现安装提示，启动后 standalone |
| Safari "添加到主屏幕" | 出现图标，启动无地址栏 |
| 离线访问 | 断网后显示 offline.html |
| 回归测试 | 桌面端样式与功能不变，420 测试全通过 |

---

## 四、IP 边界与合规

- 全部使用 W3C / WHATWG 标准 API（Service Worker、Web App Manifest、CSS Media Queries）。
- A2UI / OpenHuman / PWA 最佳实践仅作为概念参考，所有代码由 HomeStream 原创实现。
- 图标设计基于 HomeStream 现有 logo 和主题色，不参考竞品图标。
