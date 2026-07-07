# 千面设计市场 · 主题 Manifest 规范

> 理念：**不造一面墙，只铸千万门。** 🔑
> 前端不应该是我们定死的一张脸，而是让每个人打开自己那扇门的钥匙。
> HomeStream 提供"主题市场"基础设施：收录 GitHub Skills + 国内平台的设计资源，
> 提供安装 / 切换 / 预览的标准接口，让每个人都有独一无二的特色前端。

---

## 1. 目录结构

```
themes/
  <theme_id>/
    theme.json      # 主题 Manifest（必填）
    theme.css       # 覆盖样式（必填，含 :root 变量覆盖）
    preview.svg     # 预览图（可选，建议提供）
    preview.png     # 预览图备选（可选）
theme_registry.json  # 本地注册表（由 ThemeManager 自动维护）
```

安装方式（CLI）：

```bash
openbridge themes install <path-to-theme-folder>
openbridge themes list
openbridge themes activate <theme_id>
openbridge themes preview <theme_id>
```

---

## 2. theme.json 字段规范

| 字段 | 类型 | 必填 | 说明 |
|:-----|:-----|:----:|:-----|
| `id` | string | ✅ | 主题唯一标识，小写+连字符（如 `liquid-glass`） |
| `name` | string | ✅ | 展示名称 |
| `version` | string | ✅ | SemVer，如 `1.0.0` |
| `author` | string | ✅ | 作者 / 工作室 |
| `description` | string | ✅ | 一句话描述（≤200 字） |
| `category` | string | ✅ | 分类，见下表 |
| `preview` | string | ⬜ | 预览图文件名（如 `preview.svg`） |
| `entry` | string | ⬜ | 样式入口文件，默认 `theme.css` |
| `tokens` | array | ⬜ | 本主题覆盖的 token 清单（用于校验/文档） |
| `dependencies` | array | ⬜ | 依赖的其他主题/资源 |
| `homestream` | string | ⬜ | 最低兼容版本，如 `>=5.0.0` |
| `signature` | string | ⬜ | Ed25519 签名（发布到官方市场时填） |
| `source` | string | ⬜ | 来源仓库 / 作者主页 |
| `license` | string | ⬜ | 许可证，默认 `MIT` |

### 分类（category）

| 值 | 含义 |
|:----|:-----|
| `glass` | 液态玻璃 |
| `pixel` | 像素艺术 |
| `animation` | 动画叙事 |
| `minimal` | 极简禅意 |
| `cyberpunk` | 赛博朋克 |
| `other` | 其他 |

---

## 3. theme.css 规范

`theme.css` 是一段 **`:root { ... }` 覆盖样式**。HomeStream 在渲染页面时，会把激活主题的
`theme.css` 内容注入到每个页面 `<head>` 之前（`<style id="homestream-theme">`）。

### 统一 Token 字典

为兼容四套历史页面（变量命名不统一），主题应**同时定义等价命名**，确保全覆盖：

```
--bg           页面背景
--card         卡片背景        （历史别名：--panel）
--text         主文字
--text2        次要文字        （历史别名：--text3 仅更淡）
--border        边框
--accent        主强调色
--accent2       次强调色
--self-bg      自己消息气泡    （历史别名：--user-bg）
--other-bg     对方消息气泡    （历史别名：--ai-bg）
--meeting-bg   会议室背景
--meeting-border 会议室边框
--green/--red/--yellow/--cyan/--pink  状态色
--shadow        轻阴影
--shadow-lg     重阴影
--radius        大圆角
--radius-sm     小圆角
```

> 示例：同时定义 `--card` 与 `--panel` 为相同值，可让仪表盘与会议室页面都生效。

### 示例（液态玻璃）

```css
:root {
  --bg: linear-gradient(135deg, #e8eef7, #f3eefb, #eaf4fb);
  --card: rgba(255,255,255,0.55);
  --panel: rgba(255,255,255,0.45);   /* 等价映射 */
  --text: #1c2333;
  --accent: #5b8def;
  --accent2: #9b7be0;
  --self-bg: linear-gradient(135deg, #5b8def, #7c6ce0);
  --user-bg: linear-gradient(135deg, #5b8def, #7c6ce0);  /* 等价映射 */
  --shadow: 0 8px 32px rgba(90,120,180,0.18);
  --radius: 18px;
}
.card, .panel { backdrop-filter: blur(18px) saturate(160%); }
```

---

## 4. 安装 / 切换 / 预览 接口

| 操作 | CLI | API（内部） |
|:-----|:----|:------------|
| 安装 | `openbridge themes install <dir>` | `ThemeManager.install_theme()` |
| 列举 | `openbridge themes list` | `ThemeManager.list_themes()` |
| 激活 | `openbridge themes activate <id>` | `ThemeManager.activate()` |
| 预览 | `openbridge themes preview <id>` | `ThemeManager.preview_html()` |
| 网页预览 | 访问 `/?theme=<id>` 或 `/theme/<id>/preview` | `ThemeManager.apply_theme()` |

激活状态持久化于 `theme_registry.json` 的 `active` 字段，对所有页面统一生效。

---

## 5. 收录与分发（市场层）

- **官方收录**：GitHub `Ninefoldatwill/homestream` 仓库 `themes/` 目录 + 国内平台设计资源聚合。
- **社区贡献**：Fork → 新增 `themes/<your-id>/` → PR。
- **质量门槛**：建议通过 `skill_quality.py` 同款评审（视觉一致性、可读性、兼容性）。
- **签名**：发布到官方市场时填写 `signature`（Ed25519），由 `plugin_signing` 校验。

---

## 6. 与 PluginRegistry 的关系

主题在统一注册表中以 `PluginType.THEME` 类型登记，复用 `register / verify_and_install`
生命周期。实际文件系统操作（CSS 注入、激活状态）由 `theme_manager.py` 的 `ThemeManager` 负责，
`PluginRegistry` 的 `install_theme / activate_theme / list_themes` 方法委托给它。

> 一句话：PluginRegistry 管"元数据与生命周期"，ThemeManager 管"样式与渲染"。
