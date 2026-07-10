"""
test_theme_a11y.py — theme_a11y.py 无障碍审计器测试套件

覆盖:
  - 颜色解析 (parse_color)
  - W3C 相对亮度 (relative_luminance)
  - W3C 对比度比率 (contrast_ratio)
  - 色盲模拟 (simulate_colorblind / check_colorblind_pair)
  - CSS token 提取 (extract_tokens_from_css / extract_tokens_from_manifest)
  - 对比度审计 (audit_contrast)
  - 色盲审计 (audit_colorblindness)
  - 完整审计 (audit_theme)
  - 文件审计 (audit_theme_file)
  - 报告格式化 (format_report)
  - 边界情况
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from theme_a11y import (
    _COLORBLIND_DIST_THRESHOLD,
    DEFAULT_CONTRAST_PAIRS,
    DEUTERANOPIA_MATRIX,
    PROTANOPIA_MATRIX,
    TRITANOPIA_MATRIX,
    WCAG_AA_LARGE,
    WCAG_AA_NORMAL,
    WCAG_AA_UI,
    AuditReport,
    CheckResult,
    ContrastPair,
    _color_distance,
    audit_colorblindness,
    audit_contrast,
    audit_theme,
    audit_theme_file,
    check_colorblind_pair,
    contrast_ratio,
    extract_tokens_from_css,
    extract_tokens_from_manifest,
    format_report,
    parse_color,
    relative_luminance,
    simulate_colorblind,
)

# ============================================================
# 颜色解析测试
# ============================================================


class TestParseColor:
    def test_hex_3digit(self):
        assert parse_color("#fff") == (255, 255, 255)
        assert parse_color("#000") == (0, 0, 0)
        assert parse_color("f00") == (255, 0, 0)

    def test_hex_6digit(self):
        assert parse_color("#ffffff") == (255, 255, 255)
        assert parse_color("#1a1a2e") == (26, 26, 46)
        assert parse_color("#4a90d9") == (74, 144, 217)

    def test_hex_8digit(self):
        assert parse_color("#ffffffff") == (255, 255, 255)
        assert parse_color("#4a90d9ff") == (74, 144, 217)

    def test_rgb_format(self):
        assert parse_color("rgb(255, 255, 255)") == (255, 255, 255)
        assert parse_color("rgb(0, 0, 0)") == (0, 0, 0)

    def test_rgba_format(self):
        assert parse_color("rgba(74, 144, 217, 0.5)") == (74, 144, 217)
        assert parse_color("rgba(255, 255, 255, 1)") == (255, 255, 255)

    def test_invalid(self):
        assert parse_color("") is None
        assert parse_color("not-a-color") is None
        assert parse_color("#gggggg") is None
        assert parse_color(None) is None

    def test_with_spaces(self):
        assert parse_color("  #ffffff  ") == (255, 255, 255)
        assert parse_color("  rgb(  10,  20,  30  )  ") == (10, 20, 30)


# ============================================================
# W3C 相对亮度测试
# ============================================================


class TestRelativeLuminance:
    def test_white(self):
        assert relative_luminance((255, 255, 255)) == pytest.approx(1.0, abs=0.01)

    def test_black(self):
        assert relative_luminance((0, 0, 0)) == pytest.approx(0.0, abs=0.01)

    def test_red(self):
        # 纯红 #ff0000 的相对亮度约为 0.2126
        assert relative_luminance((255, 0, 0)) == pytest.approx(0.2126, abs=0.01)

    def test_green(self):
        # 纯绿 #00ff00 的相对亮度约为 0.7152
        assert relative_luminance((0, 255, 0)) == pytest.approx(0.7152, abs=0.01)

    def test_blue(self):
        # 纯蓝 #0000ff 的相对亮度约为 0.0722
        assert relative_luminance((0, 0, 255)) == pytest.approx(0.0722, abs=0.01)

    def test_ordering(self):
        # 白色 > 黄色 > 绿色 > 红色 > 蓝色 > 黑色
        white = relative_luminance((255, 255, 255))
        yellow = relative_luminance((255, 255, 0))
        green = relative_luminance((0, 255, 0))
        red = relative_luminance((255, 0, 0))
        blue = relative_luminance((0, 0, 255))
        black = relative_luminance((0, 0, 0))
        assert white > yellow > green > red > blue > black


# ============================================================
# W3C 对比度比率测试
# ============================================================


class TestContrastRatio:
    def test_black_on_white(self):
        # 黑色 vs 白色 = 21:1（W3C 最大值）
        ratio = contrast_ratio("#000000", "#ffffff")
        assert ratio == pytest.approx(21.0, abs=0.1)

    def test_white_on_black(self):
        ratio = contrast_ratio("#ffffff", "#000000")
        assert ratio == pytest.approx(21.0, abs=0.1)

    def test_same_color(self):
        ratio = contrast_ratio("#ffffff", "#ffffff")
        assert ratio == pytest.approx(1.0, abs=0.01)

    def test_known_pair(self):
        # #1a1a2e vs #f5f7fb 的对比度应 > 4.5
        ratio = contrast_ratio("#1a1a2e", "#f5f7fb")
        assert ratio is not None
        assert ratio > 4.5

    def test_invalid_color(self):
        assert contrast_ratio("invalid", "#ffffff") is None
        assert contrast_ratio("#ffffff", None) is None

    def test_symmetric(self):
        # 对比度比率应对称
        r1 = contrast_ratio("#4a90d9", "#f5f7fb")
        r2 = contrast_ratio("#f5f7fb", "#4a90d9")
        assert r1 == pytest.approx(r2, abs=0.01)


# ============================================================
# 色盲模拟测试
# ============================================================


class TestColorblindSimulation:
    def test_simulate_protanopia(self):
        # 红色在红色盲下应偏向黄褐色
        result = simulate_colorblind((255, 0, 0), PROTANOPIA_MATRIX)
        assert result[0] > 100  # R 分量应变化

    def test_simulate_deuteranopia(self):
        result = simulate_colorblind((0, 255, 0), DEUTERANOPIA_MATRIX)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_simulate_tritanopia(self):
        result = simulate_colorblind((0, 0, 255), TRITANOPIA_MATRIX)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_simulate_clamp(self):
        # 确保结果在 0-255 范围内
        result = simulate_colorblind((255, 255, 255), PROTANOPIA_MATRIX)
        assert all(0 <= c <= 255 for c in result)

    def test_check_colorblind_pair(self):
        dist = check_colorblind_pair("#1a1a2e", "#f5f7fb", PROTANOPIA_MATRIX)
        assert dist is not None
        assert dist >= 0

    def test_check_colorblind_invalid(self):
        assert check_colorblind_pair("invalid", "#ffffff", PROTANOPIA_MATRIX) is None

    def test_color_distance(self):
        d = _color_distance((0, 0, 0), (255, 255, 255))
        assert d == pytest.approx(441.67, abs=1.0)  # sqrt(3*255^2)

    def test_color_distance_same(self):
        d = _color_distance((100, 100, 100), (100, 100, 100))
        assert d == 0.0


# ============================================================
# CSS Token 提取测试
# ============================================================


class TestTokenExtraction:
    def test_extract_from_css(self):
        css = ":root { --text: #1a1a2e; --bg: #f5f7fb; --accent: #4a90d9; }"
        tokens = extract_tokens_from_css(css)
        assert tokens["--text"] == "#1a1a2e"
        assert tokens["--bg"] == "#f5f7fb"
        assert tokens["--accent"] == "#4a90d9"

    def test_extract_from_css_rgb(self):
        css = ":root { --text: rgb(26, 26, 46); }"
        tokens = extract_tokens_from_css(css)
        assert "--text" in tokens

    def test_extract_from_css_ignores_non_colors(self):
        css = ":root { --radius: 12px; --text: #1a1a2e; }"
        tokens = extract_tokens_from_css(css)
        assert "--text" in tokens
        assert "--radius" not in tokens

    def test_extract_from_css_empty(self):
        assert extract_tokens_from_css("") == {}

    def test_extract_from_manifest_colors(self):
        manifest = {
            "colors": {
                "--text": "#1a1a2e",
                "--bg": "#f5f7fb",
            }
        }
        tokens = extract_tokens_from_manifest(manifest)
        assert tokens["--text"] == "#1a1a2e"
        assert tokens["--bg"] == "#f5f7fb"

    def test_extract_from_manifest_css_field(self):
        manifest = {"css": ":root { --text: #1a1a2e; }"}
        tokens = extract_tokens_from_manifest(manifest)
        assert tokens["--text"] == "#1a1a2e"

    def test_extract_from_manifest_key_without_prefix(self):
        manifest = {
            "colors": {
                "text": "#1a1a2e",
            }
        }
        tokens = extract_tokens_from_manifest(manifest)
        assert tokens["--text"] == "#1a1a2e"


# ============================================================
# 对比度审计测试
# ============================================================


class TestAuditContrast:
    def test_all_pass(self):
        # 高对比度主题应全部通过
        tokens = {
            "--text": "#000000",
            "--bg": "#ffffff",
            "--card": "#ffffff",
            "--panel": "#ffffff",
            "--text2": "#333333",
            "--text3": "#666666",
            "--accent": "#0033cc",
            "--accent2": "#6600cc",
            "--green": "#006600",
            "--red": "#990000",
            "--yellow": "#664400",
            "--border": "#999999",
        }
        results = audit_contrast(tokens)
        assert len(results) == len(DEFAULT_CONTRAST_PAIRS)
        # 至少大部分应通过
        passed = sum(1 for r in results if r.status == "pass")
        assert passed >= len(results) * 0.8

    def test_low_contrast_error(self):
        # 低对比度主题应有错误
        tokens = {
            "--text": "#cccccc",
            "--bg": "#ffffff",
            "--card": "#ffffff",
            "--panel": "#ffffff",
            "--text2": "#dddddd",
            "--text3": "#eeeeee",
            "--accent": "#eeeeff",
            "--accent2": "#ffeeff",
            "--green": "#ccffcc",
            "--red": "#ffcccc",
            "--yellow": "#ffffcc",
            "--border": "#f0f0f0",
        }
        results = audit_contrast(tokens)
        errors = [r for r in results if r.status == "error"]
        assert len(errors) > 0

    def test_missing_token_warn(self):
        tokens = {"--text": "#000000", "--bg": "#ffffff"}
        results = audit_contrast(tokens)
        warns = [r for r in results if r.status == "warn"]
        assert len(warns) > 0  # 缺少其他 token 的应 warn

    def test_custom_pairs(self):
        tokens = {"--text": "#000000", "--bg": "#ffffff"}
        custom = [ContrastPair("--text", "--bg", "自定义", 4.5, "normal")]
        results = audit_contrast(tokens, custom)
        assert len(results) == 1
        assert results[0].status == "pass"

    def test_warn_threshold(self):
        # 对比度在阈值 80%-100% 之间应为 warn
        tokens = {
            "--text": "#767676",
            "--bg": "#ffffff",  # 对比度约 4.54:1
            "--card": "#ffffff",
            "--panel": "#ffffff",
            "--text2": "#767676",
            "--text3": "#767676",
            "--accent": "#767676",
            "--accent2": "#767676",
            "--green": "#767676",
            "--red": "#767676",
            "--yellow": "#767676",
            "--border": "#767676",
        }
        results = audit_contrast(tokens)
        # 应该有一些 warn 或 pass，不应全是 error
        non_errors = [r for r in results if r.status != "error"]
        assert len(non_errors) > 0


# ============================================================
# 色盲审计测试
# ============================================================


class TestAuditColorblindness:
    def test_basic_run(self):
        tokens = {
            "--text": "#1a1a2e",
            "--bg": "#f5f7fb",
            "--text2": "#5a6a7e",
            "--accent": "#4a90d9",
            "--accent2": "#7c5ce0",
        }
        results = audit_colorblindness(tokens)
        # 正文 + 次要文本 + 强调色 + 次强调色 = 4 对 × 3 种色盲 = 12
        assert len(results) > 0
        assert all(r.check_type == "colorblind" for r in results)

    def test_high_contrast_passes(self):
        tokens = {
            "--text": "#000000",
            "--bg": "#ffffff",
            "--text2": "#000000",
            "--accent": "#000080",
            "--accent2": "#000080",
        }
        results = audit_colorblindness(tokens)
        passed = [r for r in results if r.status == "pass"]
        assert len(passed) > 0

    def test_missing_token_skipped(self):
        tokens = {"--text": "#000000"}  # 缺 --bg
        results = audit_colorblindness(tokens)
        assert len(results) == 0  # 缺少 bg 的对比对被跳过


# ============================================================
# 完整审计测试
# ============================================================


class TestAuditTheme:
    def test_full_audit_good_theme(self):
        tokens = {
            "--text": "#1a1a2e",
            "--bg": "#f5f7fb",
            "--card": "#ffffff",
            "--panel": "#ffffff",
            "--text2": "#5a6a7e",
            "--text3": "#999999",
            "--border": "#e2e8f0",
            "--accent": "#4a90d9",
            "--accent2": "#7c5ce0",
            "--green": "#22c55e",
            "--red": "#ef4444",
            "--yellow": "#f59e0b",
        }
        report = audit_theme(tokens, theme_id="test", theme_name="测试主题")
        assert report.theme_id == "test"
        assert report.theme_name == "测试主题"
        assert report.total_checks > 0
        assert report.passed + report.warnings + report.errors == report.total_checks
        assert 0.0 <= report.overall_score <= 1.0
        assert report.overall_status in ("pass", "warn", "error")

    def test_full_audit_bad_theme(self):
        tokens = {
            "--text": "#eeeeee",
            "--bg": "#ffffff",
            "--card": "#ffffff",
            "--panel": "#ffffff",
            "--text2": "#eeeeee",
            "--text3": "#f5f5f5",
            "--border": "#f0f0f0",
            "--accent": "#eeeeff",
            "--accent2": "#ffeeff",
            "--green": "#ccffcc",
            "--red": "#ffcccc",
            "--yellow": "#ffffcc",
        }
        report = audit_theme(tokens, theme_id="bad")
        assert report.errors > 0
        assert report.overall_status == "error"
        assert report.overall_score < 0.5

    def test_missing_tokens_detected(self):
        tokens = {"--text": "#000000", "--bg": "#ffffff"}
        report = audit_theme(tokens)
        assert len(report.missing_tokens) > 0
        assert "--card" in report.missing_tokens

    def test_empty_tokens(self):
        report = audit_theme({})
        assert report.total_checks > 0
        assert report.warnings > 0
        assert report.overall_status in ("warn", "error")

    def test_to_dict(self):
        tokens = {"--text": "#000000", "--bg": "#ffffff"}
        report = audit_theme(tokens, theme_id="x", theme_name="Y")
        d = report.to_dict()
        assert d["theme_id"] == "x"
        assert d["theme_name"] == "Y"
        assert "results" in d
        assert "missing_tokens" in d
        assert isinstance(d["results"], list)


# ============================================================
# 文件审计测试
# ============================================================


class TestAuditThemeFile:
    """文件审计测试 — 使用项目内 .pytest_tmp 目录避免沙箱拦截。"""

    _TMP_BASE = Path(__file__).parent / ".pytest_tmp" / "a11y"

    def setup_method(self):
        self._TMP_BASE.mkdir(parents=True, exist_ok=True)

    def test_valid_file(self):
        theme_dir = self._TMP_BASE / "test-theme"
        theme_dir.mkdir(exist_ok=True)
        manifest = {
            "id": "test-theme",
            "name": "测试主题",
            "colors": {
                "--text": "#1a1a2e",
                "--bg": "#f5f7fb",
            },
        }
        (theme_dir / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
        report = audit_theme_file(theme_dir / "theme.json")
        assert report.theme_id == "test-theme"
        assert report.theme_name == "测试主题"
        assert report.total_checks > 0

    def test_nonexistent_file(self):
        report = audit_theme_file(self._TMP_BASE / "nonexistent.json")
        assert report.overall_status == "error"

    def test_with_css_file(self):
        theme_dir = self._TMP_BASE / "css-theme"
        theme_dir.mkdir(exist_ok=True)
        manifest = {
            "id": "css-theme",
            "name": "CSS主题",
            "entry": "theme.css",
        }
        (theme_dir / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
        css = ":root { --text: #1a1a2e; --bg: #f5f7fb; --accent: #4a90d9; }"
        (theme_dir / "theme.css").write_text(css, encoding="utf-8")
        report = audit_theme_file(theme_dir / "theme.json")
        assert report.total_checks > 0

    def test_invalid_json(self):
        bad = self._TMP_BASE / "bad.json"
        bad.write_text("{invalid json", encoding="utf-8")
        report = audit_theme_file(bad)
        assert report.overall_status == "error"


# ============================================================
# 报告格式化测试
# ============================================================


class TestFormatReport:
    def test_basic_format(self):
        tokens = {
            "--text": "#1a1a2e",
            "--bg": "#f5f7fb",
            "--card": "#ffffff",
            "--panel": "#ffffff",
            "--text2": "#5a6a7e",
            "--text3": "#999999",
            "--border": "#e2e8f0",
            "--accent": "#4a90d9",
            "--accent2": "#7c5ce0",
            "--green": "#22c55e",
            "--red": "#ef4444",
            "--yellow": "#f59e0b",
        }
        report = audit_theme(tokens, theme_id="fmt", theme_name="格式化测试")
        text = format_report(report)
        assert "无障碍审计报告" in text
        assert "格式化测试" in text
        assert "对比度检查" in text
        assert "色盲友好性" in text

    def test_missing_tokens_in_report(self):
        report = AuditReport(
            theme_id="x",
            missing_tokens=["--card", "--panel"],
        )
        text = format_report(report)
        assert "缺失 Token" in text


# ============================================================
# 常量与数据结构测试
# ============================================================


class TestConstants:
    def test_wcag_thresholds(self):
        assert WCAG_AA_NORMAL == 4.5
        assert WCAG_AA_LARGE == 3.0
        assert WCAG_AA_UI == 3.0

    def test_default_pairs_not_empty(self):
        assert len(DEFAULT_CONTRAST_PAIRS) > 0
        assert all(isinstance(p, ContrastPair) for p in DEFAULT_CONTRAST_PAIRS)

    def test_colorblind_matrices_shape(self):
        for matrix in (PROTANOPIA_MATRIX, DEUTERANOPIA_MATRIX, TRITANOPIA_MATRIX):
            assert len(matrix) == 3
            for row in matrix:
                assert len(row) == 3

    def test_check_result_dataclass(self):
        r = CheckResult(
            check_type="contrast",
            label="test",
            status="pass",
            value=4.5,
            threshold=4.5,
        )
        assert r.check_type == "contrast"
        assert r.status == "pass"

    def test_audit_report_dataclass(self):
        r = AuditReport(theme_id="test")
        assert r.theme_id == "test"
        assert r.results == []
        assert r.overall_status == "pass"
