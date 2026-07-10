"""
千面设计市场 — 主题管理器（ThemeManager）

设计理念（铸钥匠🔑）：
  不造一面墙，只铸千万门。
  前端不应该是我们定死的一张脸，而是让每个人打开自己那扇门的钥匙。
  HomeStream 提供"主题市场"基础设施：收录 GitHub Skills + 国内平台的设计资源，
  提供安装 / 切换 / 预览的标准接口，让用户都有属于自己独一无二的特色。

核心能力：
  1. discover()    — 扫描 themes/ 目录发现所有已安装主题
  2. list_themes() — 列出主题（含分类、作者、预览图）
  3. get_theme()   — 读取单个主题 manifest
  4. get_override_css() — 生成可注入 <head> 的 :root 覆盖样式
  5. activate()    — 激活主题（写入 theme_registry.json）
  6. get_active()  — 读取当前激活主题
  7. install_theme() — 从主题包（含 theme.json）安装到 themes/ 目录
  8. preview_html() — 生成整页预览（?theme=<id> 用）

统一 Token 字典（一套命名，覆盖四套历史页面变量）：
  --bg --card --panel --text --text2 --text3 --border --accent --accent2
  --self-bg --other-bg --user-bg --ai-bg --meeting-bg --meeting-border
  --green --red --yellow --cyan --pink --shadow --shadow-lg --radius --radius-sm

规范详见 docs/theme-manifest-spec.md
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================
# 统一 Token 字典（规范定义，供主题校验与文档生成）
# ============================================================

CANONICAL_TOKENS: list[str] = [
    "--bg",
    "--card",
    "--panel",
    "--text",
    "--text2",
    "--text3",
    "--border",
    "--accent",
    "--accent2",
    "--self-bg",
    "--other-bg",
    "--user-bg",
    "--ai-bg",
    "--meeting-bg",
    "--meeting-border",
    "--green",
    "--red",
    "--yellow",
    "--cyan",
    "--pink",
    "--shadow",
    "--shadow-lg",
    "--radius",
    "--radius-sm",
]

THEME_CATEGORIES: list[str] = [
    "glass",  # 液态玻璃
    "pixel",  # 像素艺术
    "animation",  # 动画叙事
    "minimal",  # 极简禅意
    "cyberpunk",  # 赛博朋克
    "other",  # 其他
]


@dataclass
class ThemeInfo:
    """主题概要信息。"""

    id: str
    name: str
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    category: str = "other"
    preview: str = ""
    entry: str = "theme.css"
    tokens: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    homestream: str = ">=5.0.0"
    signature: str = ""
    source: str = ""
    license: str = "MIT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "category": self.category,
            "preview": self.preview,
            "entry": self.entry,
            "tokens": self.tokens,
            "dependencies": self.dependencies,
            "homestream": self.homestream,
            "signature": self.signature,
            "source": self.source,
            "license": self.license,
        }

    @classmethod
    def from_manifest(cls, data: dict[str, Any]) -> ThemeInfo:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", data.get("id", "unknown")),
            version=data.get("version", "1.0.0"),
            author=data.get("author", ""),
            description=data.get("description", ""),
            category=data.get("category", "other"),
            preview=data.get("preview", ""),
            entry=data.get("entry", "theme.css"),
            tokens=data.get("tokens", []),
            dependencies=data.get("dependencies", []),
            homestream=data.get("homestream", ">=5.0.0"),
            signature=data.get("signature", ""),
            source=data.get("source", ""),
            license=data.get("license", "MIT"),
        )


class ThemeError(Exception):
    """主题操作异常。"""


class ThemeManager:
    """千面设计市场 — 主题管理器。

    纯文件系统实现，无外部依赖，便于单测。
    主题存放于 <themes_dir>/<theme_id>/theme.json + theme.css。
    激活状态持久化于 <registry_file>（默认 theme_registry.json）。
    """

    def __init__(
        self,
        themes_dir: Path | None = None,
        registry_file: Path | None = None,
    ):
        self.themes_dir = (themes_dir or Path.cwd() / "themes").resolve()
        self.registry_file = (
            registry_file or (self.themes_dir.parent / "theme_registry.json")
        ).resolve()
        self.themes_dir.mkdir(parents=True, exist_ok=True)
        self._registry: dict[str, Any] = self._load_registry()

    # --- 注册表持久化 ---

    def _load_registry(self) -> dict[str, Any]:
        if self.registry_file.exists():
            try:
                return json.loads(self.registry_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"active": None, "installed": [], "version": "1.0"}

    def _save_registry(self) -> None:
        self.registry_file.write_text(
            json.dumps(self._registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # --- 发现与列举 ---

    def discover(self) -> list[ThemeInfo]:
        """扫描 themes/ 目录，返回所有已安装主题。"""
        themes: list[ThemeInfo] = []
        if not self.themes_dir.exists():
            return themes
        for child in sorted(self.themes_dir.iterdir()):
            manifest_path = child / "theme.json"
            if child.is_dir() and manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                data.setdefault("id", child.name)
                themes.append(ThemeInfo.from_manifest(data))
        return themes

    def list_themes(self) -> list[dict[str, Any]]:
        """列出所有主题（字典形式，含激活标记）。"""
        active = self.get_active()
        result = []
        for t in self.discover():
            d = t.to_dict()
            d["active"] = t.id == active
            result.append(d)
        return result

    def get_theme(self, theme_id: str) -> ThemeInfo | None:
        """读取单个主题。"""
        for t in self.discover():
            if t.id == theme_id:
                return t
        return None

    # --- 覆盖样式生成 ---

    def get_override_css(self, theme_id: str | None = None) -> str:
        """生成可注入 <head> 的 :root 覆盖样式。

        theme_id 为 None 时使用当前激活主题；无激活主题返回空串。
        """
        tid = theme_id or self.get_active()
        if not tid:
            return ""
        theme = self.get_theme(tid)
        if not theme:
            return ""

        css_path = self.themes_dir / tid / theme.entry
        if not css_path.exists():
            return ""
        return css_path.read_text(encoding="utf-8")

    def apply_theme(self, html: str, theme_id: str | None = None) -> str:
        """将主题覆盖样式注入 HTML 的 </head> 之前。

        若无可应用主题，原样返回。不改写任何页面常量，零风险。
        """
        css = self.get_override_css(theme_id)
        if not css:
            return html
        # 兼容 <head> 与 <HEAD>
        for marker in ("</head>", "</HEAD>"):
            if marker in html:
                return html.replace(
                    marker,
                    f'<style id="homestream-theme">\n{css}\n</style>\n{marker}',
                    1,
                )
        # 无 head 标签则原样返回
        return html

    # --- 激活 ---

    def activate(self, theme_id: str) -> tuple[bool, str]:
        """激活主题。"""
        if not self.get_theme(theme_id):
            return False, f"主题不存在: {theme_id}"
        self._registry["active"] = theme_id
        if theme_id not in self._registry["installed"]:
            self._registry["installed"].append(theme_id)
        self._save_registry()
        return True, f"已激活主题: {theme_id}"

    def get_active(self) -> str | None:
        """读取当前激活主题 id。"""
        return self._registry.get("active")

    def deactivate(self) -> tuple[bool, str]:
        """取消激活（恢复默认）。"""
        self._registry["active"] = None
        self._save_registry()
        return True, "已恢复默认主题"

    # --- 安装 ---

    def install_theme(self, theme_json_path: Path) -> tuple[bool, str]:
        """从主题包（含 theme.json）安装到 themes/<id>/。

        theme_json_path 为源 theme.json；同目录的 theme.css / preview.* 一并复制。
        """
        theme_json_path = Path(theme_json_path)
        if not theme_json_path.exists():
            return False, f"主题包不存在: {theme_json_path}"
        try:
            data = json.loads(theme_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return False, f"主题 manifest 解析失败: {e}"

        tid = data.get("id") or theme_json_path.parent.name
        if not tid:
            return False, "主题 manifest 缺少 id 字段"

        # 基础校验
        if data.get("category") and data["category"] not in THEME_CATEGORIES:
            return False, f"未知分类: {data['category']}（应为 {THEME_CATEGORIES}）"

        dest = self.themes_dir / tid
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(theme_json_path, dest / "theme.json")

        # 复制同目录资源（theme.css / preview 图）
        src_dir = theme_json_path.parent
        for ext in ("css", "svg", "png", "jpg", "jpeg", "webp"):
            for f in src_dir.glob(f"*.{ext}"):
                shutil.copy2(f, dest / f.name)

        if tid not in self._registry["installed"]:
            self._registry["installed"].append(tid)
        self._save_registry()
        return True, f"主题已安装: {tid} → {dest}"

    def uninstall_theme(self, theme_id: str) -> tuple[bool, str]:
        """卸载主题（删除目录 + 从注册表移除）。"""
        dest = self.themes_dir / theme_id
        if not dest.exists():
            return False, f"主题未安装: {theme_id}"
        shutil.rmtree(dest)
        if theme_id in self._registry["installed"]:
            self._registry["installed"].remove(theme_id)
        if self._registry.get("active") == theme_id:
            self._registry["active"] = None
        self._save_registry()
        return True, f"已卸载主题: {theme_id}"

    # --- 预览 ---

    # 预览页静态样式（普通字符串，避免 f-string 花括号转义陷阱）
    _PREVIEW_CSS = """\
:root{--bg:#f5f7fb;--card:#fff;--panel:#fff;--text:#1a1a2e;--text2:#5a6a7e;--text3:#999;
--border:#e2e8f0;--accent:#4a90d9;--accent2:#7c5ce0;--self-bg:#4a90d9;--other-bg:#fff;
--user-bg:#4a90d9;--ai-bg:#f0f0f5;--meeting-bg:#FFF8E1;--meeting-border:#FFD54F;
--green:#22c55e;--red:#ef4444;--yellow:#f59e0b;--cyan:#06b6d4;--pink:#ec4899;
--shadow:0 1px 3px rgba(0,0,0,.06);--shadow-lg:0 10px 25px rgba(0,0,0,.08);
--radius:12px;--radius-sm:8px}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--text);padding:32px;line-height:1.6}
.wrap{max-width:900px;margin:0 auto}
h1{color:var(--accent);margin-bottom:8px}
.meta{color:var(--text2);margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
box-shadow:var(--shadow);padding:24px;margin-bottom:16px}
.btn{display:inline-block;background:var(--accent);color:#fff;border:none;
border-radius:var(--radius-sm);padding:10px 20px;cursor:pointer;font-size:14px}
.btn2{display:inline-block;background:var(--accent2);color:#fff;border:none;
border-radius:var(--radius-sm);padding:10px 20px;cursor:pointer;font-size:14px;margin-left:8px}
.tag{display:inline-block;background:var(--panel);border:1px solid var(--border);
border-radius:20px;padding:4px 12px;font-size:12px;color:var(--text2);margin-right:6px}
pre{background:var(--ai-bg);border-radius:var(--radius-sm);padding:16px;overflow:auto;
font-size:12px;color:var(--text)}
"""

    def preview_html(self, theme_id: str) -> str:
        """生成整页预览（用于 /theme/<id>/preview 或 ?theme=<id>）。"""
        theme = self.get_theme(theme_id)
        if not theme:
            return "<h1>主题未找到: " + theme_id + "</h1>"
        css = self.get_override_css(theme_id)
        return (
            '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
            '<meta charset="UTF-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1.0">\n'
            "<title>主题预览 · " + theme.name + "</title>\n"
            "<style>\n" + self._PREVIEW_CSS + "</style>\n"
            '<style id="homestream-theme">\n' + css + "\n</style>\n"
            '</head>\n<body>\n<div class="wrap">\n'
            "  <h1>" + theme.name + "</h1>\n"
            '  <div class="meta">作者：'
            + theme.author
            + " · 版本："
            + theme.version
            + " · 分类："
            + theme.category
            + " · 许可："
            + theme.license
            + "</div>\n"
            '  <div class="card">\n'
            "    <p>" + theme.description + "</p>\n"
            '    <div style="margin-top:12px">\n'
            '      <span class="tag">ID: ' + theme.id + "</span>\n"
            '      <span class="tag">兼容: ' + theme.homestream + "</span>\n"
            '      <span class="tag">Tokens: ' + str(len(theme.tokens)) + "</span>\n"
            "    </div>\n  </div>\n"
            '  <div class="card">\n'
            '    <button class="btn">主操作按钮</button>\n'
            '    <button class="btn2">次操作按钮</button>\n'
            "  </div>\n"
            '  <div class="card">\n'
            "    <strong>覆盖样式预览（theme.css）：</strong>\n"
            "    <pre>" + css + "</pre>\n"
            "  </div>\n"
            "</div>\n</body>\n</html>"
        )


# ============================================================
# 模块级便捷函数（供 CLI / 测试直接调用）
# ============================================================

_default_manager: ThemeManager | None = None


def get_theme_manager(
    themes_dir: Path | None = None,
    registry_file: Path | None = None,
) -> ThemeManager:
    """获取（或创建）全局主题管理器实例。"""
    global _default_manager
    if _default_manager is None or themes_dir is not None:
        _default_manager = ThemeManager(themes_dir=themes_dir, registry_file=registry_file)
    return _default_manager
