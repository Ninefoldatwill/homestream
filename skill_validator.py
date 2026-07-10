"""
skill_validator.py — SKILL.md 标准校验器
============================================
对齐 agentskills.io 规范，支持 YAML frontmatter 解析、
渐进式加载和团队共享目录校验。

九重生态 · 澜舟开发 · 2026-07-02
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ─── 常量 ─────────────────────────────────────────────────────

RE_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME_LEN = 64
MAX_DESC_LEN = 1024
MAX_COMPAT_LEN = 500
MAX_BODY_TOKENS_ESTIMATE = 5000  # 用于渐进式加载警告，非强制

REQUIRED_FIELDS = {"name", "description"}
OPTIONAL_FIELDS = {"license", "compatibility", "metadata", "allowed-tools"}


# ─── 数据模型 ──────────────────────────────────────────────────


@dataclass
class SkillValidationIssue:
    level: str  # error | warning | info
    field: str
    message: str


@dataclass
class SkillManifest:
    """SKILL.md 的 YAML frontmatter 元数据"""

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] | None = None
    body: str = ""  # YAML 之后的 markdown 正文
    path: Path | None = None

    @property
    def estimated_tokens(self) -> int:
        """粗略估算正文 token 数（中文≈1字/1token，英文≈1词/1.3token）"""
        if not self.body:
            return 0
        # 简单启发式：总字符数 / 1.5
        return int(len(self.body) / 1.5)


class SkillValidator:
    """
    校验单个 SKILL.md 是否符合 agentskills.io 规范。
    """

    def __init__(self, strict: bool = False):
        self.strict = strict
        self.issues: list[SkillValidationIssue] = []

    def validate(
        self, skill_path: Path | str
    ) -> tuple[SkillManifest | None, list[SkillValidationIssue]]:
        """校验一个 SKILL.md 文件，返回 (manifest, issues)"""
        self.issues = []
        path = Path(skill_path)

        if not path.exists():
            self.issues.append(SkillValidationIssue("error", "file", f"文件不存在: {path}"))
            return None, self.issues

        raw = path.read_text(encoding="utf-8")
        manifest = self._parse_frontmatter(raw, path)
        if manifest is None:
            return None, self.issues

        self._validate_fields(manifest)
        self._validate_body(manifest)
        self._validate_directory_structure(path, manifest)

        return manifest, self.issues

    def _parse_frontmatter(self, raw: str, path: Path) -> SkillManifest | None:
        """解析 YAML frontmatter + markdown body"""
        raw = raw.strip()
        if not raw.startswith("---"):
            self.issues.append(SkillValidationIssue("error", "frontmatter", "必须以 '---' 开头"))
            return None

        parts = raw.split("---", 2)
        if len(parts) < 3:
            self.issues.append(
                SkillValidationIssue("error", "frontmatter", "缺少 YAML frontmatter 结束标记")
            )
            return None

        yaml_text = parts[1].strip()
        body = parts[2].strip()

        try:
            data = yaml.safe_load(yaml_text) or {}
        except yaml.YAMLError as e:
            self.issues.append(SkillValidationIssue("error", "frontmatter", f"YAML 解析失败: {e}"))
            return None

        if not isinstance(data, dict):
            self.issues.append(SkillValidationIssue("error", "frontmatter", "YAML 根必须是对象"))
            return None

        name = data.get("name", "")
        description = data.get("description", "")
        manifest = SkillManifest(
            name=name,
            description=description,
            license=data.get("license"),
            compatibility=data.get("compatibility"),
            metadata=data.get("metadata", {}),
            allowed_tools=data.get("allowed-tools"),
            body=body,
            path=path,
        )
        return manifest

    def _validate_fields(self, manifest: SkillManifest) -> None:
        """校验必填/可选字段"""
        # name
        if not manifest.name:
            self.issues.append(SkillValidationIssue("error", "name", "name 必填"))
        elif not RE_NAME.match(manifest.name):
            self.issues.append(
                SkillValidationIssue(
                    "error",
                    "name",
                    f"name '{manifest.name}' 必须是小写字母/数字/连字符，且不能首尾连字符",
                )
            )
        elif len(manifest.name) > MAX_NAME_LEN:
            self.issues.append(
                SkillValidationIssue("error", "name", f"name 长度超过 {MAX_NAME_LEN}")
            )

        # description
        if not manifest.description:
            self.issues.append(SkillValidationIssue("error", "description", "description 必填"))
        elif len(manifest.description) > MAX_DESC_LEN:
            self.issues.append(
                SkillValidationIssue("error", "description", f"description 长度超过 {MAX_DESC_LEN}")
            )
        elif "做什么" not in manifest.description and "when" not in manifest.description.lower():
            self.issues.append(
                SkillValidationIssue(
                    "warning", "description", "description 建议包含'做什么'和'何时使用'"
                )
            )

        # license
        if manifest.license and len(manifest.license) > 64:
            self.issues.append(SkillValidationIssue("warning", "license", "license 字段过长"))

        # compatibility
        if manifest.compatibility and len(manifest.compatibility) > MAX_COMPAT_LEN:
            self.issues.append(
                SkillValidationIssue(
                    "error", "compatibility", f"compatibility 超过 {MAX_COMPAT_LEN}"
                )
            )

        # metadata
        if not isinstance(manifest.metadata, dict):
            self.issues.append(
                SkillValidationIssue("error", "metadata", "metadata 必须是 key-value 对象")
            )

        # allowed-tools
        if manifest.allowed_tools is not None and not isinstance(manifest.allowed_tools, list):
            self.issues.append(
                SkillValidationIssue("error", "allowed-tools", "allowed-tools 必须是列表")
            )

        # 未知字段警告
        known = REQUIRED_FIELDS | OPTIONAL_FIELDS
        for key in manifest.metadata.keys() if isinstance(manifest.metadata, dict) else []:
            pass  # metadata 内任意
        # 这里只校验 frontmatter 顶层，已由 yaml 解析为 dict

    def _validate_body(self, manifest: SkillManifest) -> None:
        """校验 markdown 正文"""
        if not manifest.body.strip():
            self.issues.append(
                SkillValidationIssue("warning", "body", "正文为空，建议补充 Instructions")
            )
            return

        if "# " not in manifest.body and "## " not in manifest.body:
            self.issues.append(
                SkillValidationIssue("warning", "body", "正文缺少 Markdown 标题层级")
            )

        tokens = manifest.estimated_tokens
        if tokens > MAX_BODY_TOKENS_ESTIMATE:
            self.issues.append(
                SkillValidationIssue(
                    "warning",
                    "body",
                    f"正文估算约 {tokens} tokens，建议拆分到 references/ 子目录（主文件 <5000 tokens）",
                )
            )

    def _validate_directory_structure(self, path: Path, manifest: SkillManifest) -> None:
        """校验目录结构是否符合 agentskills.io 标准"""
        skill_dir = path.parent
        expected_name = skill_dir.name
        if manifest.name != expected_name:
            self.issues.append(
                SkillValidationIssue(
                    "error", "directory", f"name '{manifest.name}' 必须匹配目录名 '{expected_name}'"
                )
            )

        # 建议存在 scripts/ 或 references/ 或 assets/ 中的一个，不强求
        has_subdir = any((skill_dir / d).is_dir() for d in ("scripts", "references", "assets"))
        if self.strict and not has_subdir:
            self.issues.append(
                SkillValidationIssue(
                    "warning",
                    "directory",
                    "严格模式下建议包含 scripts/、references/ 或 assets/ 子目录",
                )
            )


class SkillRegistryScanner:
    """
    扫描 skills/ 目录，批量校验所有 SKILL.md。
    """

    def __init__(self, skills_root: Path | str, strict: bool = False):
        self.skills_root = Path(skills_root)
        self.validator = SkillValidator(strict=strict)
        self.results: dict[str, tuple[SkillManifest | None, list[SkillValidationIssue]]] = {}

    def scan(self) -> dict[str, tuple[SkillManifest | None, list[SkillValidationIssue]]]:
        """扫描并返回所有结果"""
        self.results = {}
        if not self.skills_root.exists():
            return self.results

        for skill_dir in sorted(self.skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            manifest, issues = self.validator.validate(skill_md)
            self.results[skill_dir.name] = (manifest, issues)

        return self.results

    def summary(self) -> dict[str, int]:
        """返回统计信息"""
        error_count = sum(
            1 for _, issues in self.results.values() for issue in issues if issue.level == "error"
        )
        warning_count = sum(
            1 for _, issues in self.results.values() for issue in issues if issue.level == "warning"
        )
        valid_count = sum(
            1 for _, issues in self.results.values() if not any(i.level == "error" for i in issues)
        )
        return {
            "total": len(self.results),
            "valid": valid_count,
            "errors": error_count,
            "warnings": warning_count,
        }


# ─── 便捷函数 ──────────────────────────────────────────────────


def validate_skill(
    skill_path: Path | str, strict: bool = False
) -> tuple[SkillManifest | None, list[SkillValidationIssue]]:
    """便捷函数：校验单个 SKILL.md"""
    return SkillValidator(strict=strict).validate(skill_path)


def scan_skills(
    skills_root: Path | str, strict: bool = False
) -> dict[str, tuple[SkillManifest | None, list[SkillValidationIssue]]]:
    """便捷函数：扫描 skills 目录"""
    return SkillRegistryScanner(skills_root, strict=strict).scan()


# ─── 最小 CLI ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python skill_validator.py <skills_root>")
        sys.exit(1)

    root = Path(sys.argv[1])
    scanner = SkillRegistryScanner(root)
    scanner.scan()
    summary = scanner.summary()

    print(f"扫描完成: {summary['total']} 个 Skill")
    print(f"  有效: {summary['valid']}")
    print(f"  错误: {summary['errors']}")
    print(f"  警告: {summary['warnings']}")

    for name, (manifest, issues) in scanner.results.items():
        errors = [i for i in issues if i.level == "error"]
        if errors:
            print(f"\n❌ {name}")
            for issue in errors:
                print(f"   [{issue.level}] {issue.field}: {issue.message}")
        elif issues:
            print(f"\n⚠️  {name}")
            for issue in issues[:3]:
                print(f"   [{issue.level}] {issue.field}: {issue.message}")
