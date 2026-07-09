"""
theme_a11y.py — 千面设计市场无障碍审计器
==========================================

基于 WCAG 2.1 AA 标准 (W3C, Royalty-Free) 对主题配色方案进行无障碍审计。
检查主题 CSS 变量（--text, --bg, --accent 等）的对比度、可读性和色盲友好性。

标准来源:
  - WCAG 2.1: https://www.w3.org/TR/WCAG21/
  - 对比度技术 G17: https://www.w3.org/WAI/WCAG22/Techniques/general/G17
  - 相对亮度公式: https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum
  - W3C 专利政策 (RF): https://www.w3.org/policies/patent-policy/

设计理念（铸钥匠🔑）:
  不造一面墙，只铸千万门——每扇门都应该是无障碍的。
  千面设计市场收录的主题，不仅好看，更要"人人可用"。

IP 边界:
  - 颜色对比度公式为 W3C 公开算法（免版税），非任何工具专有
  - 完全原创实现，零第三方依赖，仅用 Python 标准库
  - 不复制任何 W3C 规范文本原文，用自己的语言描述检查规则

九重生态 · 澜舟开发 · 2026-07-09
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# WCAG 2.1 AA 对比度阈值（W3C 公开标准）
# ============================================================

WCAG_AA_NORMAL: float = 4.5   # 普通文本（<18pt 或 <14pt 粗体）最小对比度
WCAG_AA_LARGE: float = 3.0    # 大文本（≥18pt 或 ≥14pt 粗体）最小对比度
WCAG_AA_UI: float = 3.0       # UI 组件（图标、边框等）最小对比度
WCAG_AAA_NORMAL: float = 7.0  # AAA 级普通文本（增强对比度）
WCAG_AAA_LARGE: float = 4.5   # AAA 级大文本

# ============================================================
# 色盲模拟矩阵（基于 Brettel/Viénot 修正模型）
# 来源: 公开学术算法，非专利
# ============================================================

# 红色盲（Protanopia）— 约1%男性
PROTANOPIA_MATRIX: Tuple[Tuple[float, float, float], ...] = (
    (0.567, 0.433, 0.000),
    (0.558, 0.442, 0.000),
    (0.000, 0.242, 0.758),
)

# 绿色盲（Deuteranopia）— 约1%男性
DEUTERANOPIA_MATRIX: Tuple[Tuple[float, float, float], ...] = (
    (0.625, 0.375, 0.000),
    (0.700, 0.300, 0.000),
    (0.000, 0.300, 0.700),
)

# 蓝色盲（Tritanopia）— 极罕见
TRITANOPIA_MATRIX: Tuple[Tuple[float, float, float], ...] = (
    (0.950, 0.050, 0.000),
    (0.000, 0.433, 0.567),
    (0.000, 0.475, 0.525),
)

# ============================================================
# 主题对比对定义（对接 ThemeManager.CANONICAL_TOKENS）
# ============================================================

@dataclass
class ContrastPair:
    """一对需要检查对比度的 CSS 变量。"""
    fg_token: str          # 前景色变量名（如 "--text"）
    bg_token: str          # 背景色变量名（如 "--bg"）
    label: str             # 人类可读描述
    threshold: float       # WCAG AA 阈值
    category: str          # "normal" | "large" | "ui"


# 千面设计市场核心对比对（覆盖正文/次要文本/强调色/状态色场景）
DEFAULT_CONTRAST_PAIRS: List[ContrastPair] = [
    # 正文文本
    ContrastPair("--text", "--bg", "正文 vs 主背景", WCAG_AA_NORMAL, "normal"),
    ContrastPair("--text", "--card", "正文 vs 卡片背景", WCAG_AA_NORMAL, "normal"),
    ContrastPair("--text", "--panel", "正文 vs 面板背景", WCAG_AA_NORMAL, "normal"),
    # 次要文本
    ContrastPair("--text2", "--bg", "次要文本 vs 主背景", WCAG_AA_NORMAL, "normal"),
    ContrastPair("--text3", "--bg", "三级文本 vs 主背景", WCAG_AA_LARGE, "large"),
    # 强调色
    ContrastPair("--accent", "--bg", "强调色 vs 主背景", WCAG_AA_LARGE, "large"),
    ContrastPair("--accent2", "--bg", "次强调色 vs 主背景", WCAG_AA_LARGE, "large"),
    # 状态色
    ContrastPair("--green", "--bg", "成功色 vs 主背景", WCAG_AA_UI, "ui"),
    ContrastPair("--red", "--bg", "错误色 vs 主背景", WCAG_AA_UI, "ui"),
    ContrastPair("--yellow", "--bg", "警告色 vs 主背景", WCAG_AA_UI, "ui"),
    # 边框
    ContrastPair("--border", "--bg", "边框 vs 主背景", WCAG_AA_UI, "ui"),
]

# ============================================================
# 颜色解析与 W3C 对比度计算
# ============================================================

_HEX_PATTERN = re.compile(r'^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$')
_RGB_PATTERN = re.compile(
    r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*[\d.]+)?\s*\)'
)


def parse_color(color_str: str) -> Optional[Tuple[int, int, int]]:
    """解析 CSS 颜色值为 (R, G, B) 三元组。

    支持: #RGB, #RRGGBB, #RRGGBBAA, rgb(r,g,b), rgba(r,g,b,a)
    不支持的格式返回 None。
    """
    if not color_str:
        return None
    color_str = color_str.strip()

    # hex 格式
    m = _HEX_PATTERN.match(color_str)
    if m:
        hex_part = m.group(1)
        if len(hex_part) == 3:
            r = int(hex_part[0] * 2, 16)
            g = int(hex_part[1] * 2, 16)
            b = int(hex_part[2] * 2, 16)
            return (r, g, b)
        elif len(hex_part) >= 6:
            r = int(hex_part[0:2], 16)
            g = int(hex_part[2:4], 16)
            b = int(hex_part[4:6], 16)
            return (r, g, b)

    # rgb/rgba 格式
    m = _RGB_PATTERN.match(color_str)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    return None


def _srgb_to_linear(c: float) -> float:
    """将 sRGB 分量 [0,1] 转为线性光（W3C 公开公式）。

    参考: https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum
    """
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: Tuple[int, int, int]) -> float:
    """计算颜色的相对亮度（W3C 公开算法）。

    公式: L = 0.2126*R + 0.7152*G + 0.0722*B
    其中 R/G/B 为线性化后的分量。

    参考: https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum
    """
    r, g, b = rgb
    r_lin = _srgb_to_linear(r / 255.0)
    g_lin = _srgb_to_linear(g / 255.0)
    b_lin = _srgb_to_linear(b / 255.0)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(fg: str, bg: str) -> Optional[float]:
    """计算两色之间的对比度比率（W3C 公开公式）。

    公式: (L1 + 0.05) / (L2 + 0.05)，其中 L1 > L2

    参考: https://www.w3.org/WAI/WCAG22/Techniques/general/G17

    返回 None 如果颜色无法解析。
    """
    fg_rgb = parse_color(fg)
    bg_rgb = parse_color(bg)
    if fg_rgb is None or bg_rgb is None:
        return None
    l1 = relative_luminance(fg_rgb)
    l2 = relative_luminance(bg_rgb)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ============================================================
# 色盲友好性检查
# ============================================================

def simulate_colorblind(
    rgb: Tuple[int, int, int],
    matrix: Tuple[Tuple[float, float, float], ...],
) -> Tuple[int, int, int]:
    """使用变换矩阵模拟色盲视觉（公开学术算法）。"""
    r, g, b = rgb
    new_r = matrix[0][0] * r + matrix[0][1] * g + matrix[0][2] * b
    new_g = matrix[1][0] * r + matrix[1][1] * g + matrix[1][2] * b
    new_b = matrix[2][0] * r + matrix[2][1] * g + matrix[2][2] * b
    return (
        max(0, min(255, round(new_r))),
        max(0, min(255, round(new_g))),
        max(0, min(255, round(new_b))),
    )


def _color_distance(c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> float:
    """计算两色之间的欧氏距离（简化版 ΔRGB）。"""
    return ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2) ** 0.5


# 色盲可区分性阈值（RGB 空间欧氏距离）
_COLORBLIND_DIST_THRESHOLD: float = 30.0


def check_colorblind_pair(
    fg: str,
    bg: str,
    matrix: Tuple[Tuple[float, float, float], ...],
) -> Optional[float]:
    """检查颜色对在色盲模拟下的可区分性。

    返回模拟后的 RGB 距离，距离越小越难区分。
    返回 None 如果颜色无法解析。
    """
    fg_rgb = parse_color(fg)
    bg_rgb = parse_color(bg)
    if fg_rgb is None or bg_rgb is None:
        return None
    fg_sim = simulate_colorblind(fg_rgb, matrix)
    bg_sim = simulate_colorblind(bg_rgb, matrix)
    return _color_distance(fg_sim, bg_sim)


# ============================================================
# 审计结果数据结构
# ============================================================

@dataclass
class CheckResult:
    """单项检查结果。"""
    check_type: str          # "contrast" | "colorblind"
    label: str               # 检查项描述
    status: str              # "pass" | "warn" | "error"
    value: float             # 测量值（对比度比率 或 色盲距离）
    threshold: float         # 合格阈值
    detail: str = ""         # 详细说明


@dataclass
class AuditReport:
    """主题无障碍审计报告。"""
    theme_id: str = ""
    theme_name: str = ""
    total_checks: int = 0
    passed: int = 0
    warnings: int = 0
    errors: int = 0
    overall_score: float = 0.0   # 0.0-1.0
    overall_status: str = "pass"  # "pass" | "warn" | "error"
    results: List[CheckResult] = field(default_factory=list)
    missing_tokens: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "theme_id": self.theme_id,
            "theme_name": self.theme_name,
            "total_checks": self.total_checks,
            "passed": self.passed,
            "warnings": self.warnings,
            "errors": self.errors,
            "overall_score": round(self.overall_score, 3),
            "overall_status": self.overall_status,
            "results": [
                {
                    "check_type": r.check_type,
                    "label": r.label,
                    "status": r.status,
                    "value": round(r.value, 2),
                    "threshold": r.threshold,
                    "detail": r.detail,
                }
                for r in self.results
            ],
            "missing_tokens": self.missing_tokens,
        }


# ============================================================
# 核心审计函数
# ============================================================

def extract_tokens_from_css(css_text: str) -> Dict[str, str]:
    """从 CSS 文本中提取 CSS 变量定义。

    匹配 :root { --var: #color; } 格式。
    """
    tokens: Dict[str, str] = {}
    # 匹配 --token-name: value;
    pattern = re.compile(r'(--[a-zA-Z0-9-]+)\s*:\s*([^;]+)\s*;')
    for match in pattern.finditer(css_text):
        token_name = match.group(1)
        token_value = match.group(2).strip()
        # 只保留颜色值（hex 或 rgb）
        if parse_color(token_value) is not None:
            tokens[token_name] = token_value
    return tokens


def extract_tokens_from_manifest(manifest: Dict[str, Any]) -> Dict[str, str]:
    """从 theme.json manifest 中提取颜色 token。

    优先使用 manifest["colors"] 字典，其次从 manifest["css"] 文本提取。
    """
    tokens: Dict[str, str] = {}
    # 方式1: manifest 中直接有 colors 字段
    colors = manifest.get("colors", {})
    if isinstance(colors, dict):
        for key, value in colors.items():
            if isinstance(value, str) and parse_color(value) is not None:
                token = key if key.startswith("--") else f"--{key}"
                tokens[token] = value
    # 方式2: manifest 中有 css 文本
    css_text = manifest.get("css", "")
    if css_text:
        tokens.update(extract_tokens_from_css(css_text))
    return tokens


def audit_contrast(
    tokens: Dict[str, str],
    pairs: Optional[List[ContrastPair]] = None,
) -> List[CheckResult]:
    """审计主题配色变量的对比度。

    对每对 ContrastPair 计算对比度比率，与 WCAG AA 阈值比较。
    """
    if pairs is None:
        pairs = DEFAULT_CONTRAST_PAIRS

    results: List[CheckResult] = []
    for pair in pairs:
        fg = tokens.get(pair.fg_token)
        bg = tokens.get(pair.bg_token)
        if fg is None or bg is None:
            results.append(CheckResult(
                check_type="contrast",
                label=pair.label,
                status="warn",
                value=0.0,
                threshold=pair.threshold,
                detail=f"缺少 token: {pair.fg_token} 或 {pair.bg_token}",
            ))
            continue

        ratio = contrast_ratio(fg, bg)
        if ratio is None:
            results.append(CheckResult(
                check_type="contrast",
                label=pair.label,
                status="warn",
                value=0.0,
                threshold=pair.threshold,
                detail=f"无法解析颜色: {fg} / {bg}",
            ))
            continue

        if ratio >= pair.threshold:
            status = "pass"
            detail = f"对比度 {ratio:.2f}:1 ≥ {pair.threshold}:1"
        elif ratio >= pair.threshold * 0.8:
            status = "warn"
            detail = f"对比度 {ratio:.2f}:1 接近阈值 {pair.threshold}:1"
        else:
            status = "error"
            detail = f"对比度 {ratio:.2f}:1 < {pair.threshold}:1"

        results.append(CheckResult(
            check_type="contrast",
            label=pair.label,
            status=status,
            value=ratio,
            threshold=pair.threshold,
            detail=detail,
        ))

    return results


def audit_colorblindness(
    tokens: Dict[str, str],
    pairs: Optional[List[ContrastPair]] = None,
) -> List[CheckResult]:
    """审计主题配色变量的色盲友好性。

    对正文/强调色对比对，在三种色盲模拟下检查可区分性。
    """
    if pairs is None:
        # 只检查正文和强调色对比对（非 UI 类）
        pairs = [p for p in DEFAULT_CONTRAST_PAIRS if p.category in ("normal", "large")]

    results: List[CheckResult] = []
    cb_types = [
        ("protanopia", "红色盲", PROTANOPIA_MATRIX),
        ("deuteranopia", "绿色盲", DEUTERANOPIA_MATRIX),
        ("tritanopia", "蓝色盲", TRITANOPIA_MATRIX),
    ]

    for pair in pairs:
        fg = tokens.get(pair.fg_token)
        bg = tokens.get(pair.bg_token)
        if fg is None or bg is None:
            continue

        for cb_id, cb_name, matrix in cb_types:
            dist = check_colorblind_pair(fg, bg, matrix)
            if dist is None:
                continue

            if dist >= _COLORBLIND_DIST_THRESHOLD:
                status = "pass"
                detail = f"{cb_name}模拟下 RGB距离 {dist:.1f} ≥ {_COLORBLIND_DIST_THRESHOLD}"
            elif dist >= _COLORBLIND_DIST_THRESHOLD * 0.5:
                status = "warn"
                detail = f"{cb_name}模拟下 RGB距离 {dist:.1f} 接近阈值"
            else:
                status = "error"
                detail = f"{cb_name}模拟下 RGB距离 {dist:.1f} 难以区分"

            results.append(CheckResult(
                check_type="colorblind",
                label=f"{pair.label} ({cb_name})",
                status=status,
                value=dist,
                threshold=_COLORBLIND_DIST_THRESHOLD,
                detail=detail,
            ))

    return results


def audit_theme(
    tokens: Dict[str, str],
    theme_id: str = "",
    theme_name: str = "",
    pairs: Optional[List[ContrastPair]] = None,
) -> AuditReport:
    """对主题 token 字典执行完整无障碍审计。

    审计维度:
      1. WCAG 2.1 AA 对比度（11 对核心对比对）
      2. 色盲友好性（3 种色盲 × 正文/强调色对比对）

    返回 AuditReport 包含所有检查结果和整体评分。
    """
    report = AuditReport(theme_id=theme_id, theme_name=theme_name)

    # 检查缺失 token
    all_tokens_needed = set()
    for p in (pairs or DEFAULT_CONTRAST_PAIRS):
        all_tokens_needed.add(p.fg_token)
        all_tokens_needed.add(p.bg_token)
    for token in sorted(all_tokens_needed):
        if token not in tokens:
            report.missing_tokens.append(token)

    # 对比度审计
    contrast_results = audit_contrast(tokens, pairs)
    report.results.extend(contrast_results)

    # 色盲友好性审计
    cb_results = audit_colorblindness(tokens, pairs)
    report.results.extend(cb_results)

    # 汇总统计
    report.total_checks = len(report.results)
    report.passed = sum(1 for r in report.results if r.status == "pass")
    report.warnings = sum(1 for r in report.results if r.status == "warn")
    report.errors = sum(1 for r in report.results if r.status == "error")

    # 评分: pass=1.0, warn=0.5, error=0.0
    if report.total_checks > 0:
        score_sum = report.passed * 1.0 + report.warnings * 0.5
        report.overall_score = score_sum / report.total_checks

    # 整体状态
    if report.errors > 0:
        report.overall_status = "error"
    elif report.warnings > 0:
        report.overall_status = "warn"
    else:
        report.overall_status = "pass"

    return report


def audit_theme_file(theme_json_path: Path) -> AuditReport:
    """从 theme.json 文件加载主题并执行审计。

    读取 theme.json 中的 colors 字段和 css 字段提取颜色 token。
    """
    import json

    theme_json_path = Path(theme_json_path)
    if not theme_json_path.exists():
        return AuditReport(theme_id="", overall_status="error")

    try:
        manifest = json.loads(theme_json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AuditReport(theme_id="", overall_status="error")

    tokens = extract_tokens_from_manifest(manifest)

    # 也尝试读取同目录的 theme.css
    css_path = theme_json_path.parent / manifest.get("entry", "theme.css")
    if css_path.exists():
        css_text = css_path.read_text(encoding="utf-8")
        tokens.update(extract_tokens_from_css(css_text))

    return audit_theme(
        tokens=tokens,
        theme_id=manifest.get("id", theme_json_path.parent.name),
        theme_name=manifest.get("name", manifest.get("id", "")),
    )


def format_report(report: AuditReport) -> str:
    """将审计报告格式化为人类可读文本。"""
    lines = [
        f"无障碍审计报告 · {report.theme_name or report.theme_id}",
        f"{'='*50}",
        f"总检查项: {report.total_checks} | 通过: {report.passed} | "
        f"警告: {report.warnings} | 错误: {report.errors}",
        f"综合评分: {report.overall_score:.1%} | 状态: {report.overall_status.upper()}",
    ]

    if report.missing_tokens:
        lines.append(f"\n缺失 Token: {', '.join(report.missing_tokens)}")

    lines.append(f"\n{'─'*50}")
    lines.append("对比度检查:")
    for r in report.results:
        if r.check_type == "contrast":
            icon = {"pass": "✅", "warn": "⚠️", "error": "❌"}[r.status]
            lines.append(f"  {icon} {r.label}: {r.detail}")

    cb_results = [r for r in report.results if r.check_type == "colorblind"]
    if cb_results:
        lines.append(f"\n{'─'*50}")
        lines.append("色盲友好性检查:")
        for r in cb_results:
            icon = {"pass": "✅", "warn": "⚠️", "error": "❌"}[r.status]
            lines.append(f"  {icon} {r.label}: {r.detail}")

    return "\n".join(lines)


# ============================================================
# 模块入口
# ============================================================

if __name__ == "__main__":
    # 演示：审计一个示例主题
    sample_tokens = {
        "--bg": "#f5f7fb",
        "--card": "#ffffff",
        "--panel": "#ffffff",
        "--text": "#1a1a2e",
        "--text2": "#5a6a7e",
        "--text3": "#999999",
        "--border": "#e2e8f0",
        "--accent": "#4a90d9",
        "--accent2": "#7c5ce0",
        "--green": "#22c55e",
        "--red": "#ef4444",
        "--yellow": "#f59e0b",
    }
    report = audit_theme(sample_tokens, theme_id="demo", theme_name="演示主题")
    print(format_report(report))
