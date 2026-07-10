"""
skill_quality.py — SkillsBench 12维质量评分系统
================================================
融优来源：
  Stanford + CMU + Berkeley SkillsBench (arXiv 2026)
  12分制质量评估框架，含安全审计子维度。

设计原则：
  只融优高分技能 · 全塞文档反降性能 · 安全审计优先 · 质量>数量

质量分类：
  Elite(精英)   10.0-12.0  可直接纳入核心生态
  High(优秀)     7.0-9.9   推荐安装
  Medium(可用)   4.0-6.9   需人工审核
  Low(低质)      0.0-3.9   不推荐/需重写

12维度：
  1. clarity         清晰度    — 描述是否"做什么+何时用"
  2. completeness    完整度    — 必填字段+推荐段齐全
  3. correctness     正确性    — YAML/JSON/指令无错误
  4. security        安全性    — 注入风险/危险操作检测(子维度5项)
  5. efficiency      效率      — token 用量/kg 级描述
  6. robustness      鲁棒性    — 错误处理/边缘案例
  7. maintainability 可维护性  — 版本号/作者/更新日志
  8. usability       可用性    — 示例/快速入门
  9. modularity      模块化    — 文件结构/子目录
  10. documentation  文档质量  — 注释/引用/参考
  11. compatibility  兼容性    — Agent/平台声明
  12. testability    可测试性  — 测试脚本/验证逻辑
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("bridge_v7.skill_quality")


# ===========================================================================
# 质量等级
# ===========================================================================


class QualityTier(str, Enum):
    ELITE = "elite"  # 10.0-12.0 精英
    HIGH = "high"  # 7.0-9.9   优秀
    MEDIUM = "medium"  # 4.0-6.9   可用
    LOW = "low"  # 0.0-3.9   低质

    @classmethod
    def from_score(cls, total: float) -> QualityTier:
        if total >= 10.0:
            return cls.ELITE
        if total >= 7.0:
            return cls.HIGH
        if total >= 4.0:
            return cls.MEDIUM
        return cls.LOW

    @property
    def emoji(self) -> str:
        return {"elite": "💎", "high": "⭐", "medium": "✅", "low": "⚠️"}[self.value]

    @property
    def label_cn(self) -> str:
        return {"elite": "精英", "high": "优秀", "medium": "可用", "low": "低质"}[self.value]


# ===========================================================================
# 维度定义
# ===========================================================================


@dataclass
class DimensionScore:
    """单维度评分。"""

    name: str  # 维度标识
    label_cn: str  # 中文名
    score: float  # 0.0-1.0
    max_score: float  # 满分
    weight: float  # 权重
    findings: list[str]  # 发现列表（加分/扣分原因）

    @property
    def percentage(self) -> int:
        return int(self.score * 100)

    @property
    def level(self) -> str:
        if self.score >= 0.8:
            return "excellent"
        if self.score >= 0.6:
            return "good"
        if self.score >= 0.4:
            return "fair"
        return "poor"


@dataclass
class QualityReport:
    """完整质量报告。"""

    skill_name: str
    skill_path: str
    total_score: float  # 0.0-12.0
    tier: QualityTier
    dimensions: list[DimensionScore]
    security_details: SecurityAudit | None = None
    recommendations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    scored_at: str = ""

    @property
    def score_percent(self) -> int:
        return int(self.total_score / 12.0 * 100)

    @property
    def pass_threshold(self) -> bool:
        """是否达到推荐安装阈值 (≥4.0)"""
        return self.total_score >= 4.0

    @property
    def elite_threshold(self) -> bool:
        """是否达到精英阈值 (≥10.0)"""
        return self.total_score >= 10.0


# ===========================================================================
# 安全审计子维度（SkillsBench 安全审计扩展）
# ===========================================================================


@dataclass
class SecurityAudit:
    """5项安全子维度审计。"""

    injection_risk: float  # 注入风险 (0=安全, 1=高危)
    dangerous_ops: float  # 危险操作 (exec/eval/shell)
    network_access: float  # 网络访问风险
    file_system_access: float  # 文件系统访问风险
    credential_leak: float  # 凭据泄露风险
    findings: list[str] = field(default_factory=list)

    @property
    def overall_security(self) -> float:
        """综合安全分 (0=完全安全, 1=极高风险)。"""
        weights = [0.30, 0.25, 0.15, 0.15, 0.15]
        scores = [
            self.injection_risk,
            self.dangerous_ops,
            self.network_access,
            self.file_system_access,
            self.credential_leak,
        ]
        return sum(w * s for w, s in zip(weights, scores))

    @property
    def risk_level(self) -> str:
        s = self.overall_security
        if s <= 0.1:
            return "low"
        if s <= 0.3:
            return "medium"
        if s <= 0.6:
            return "high"
        return "critical"


# ===========================================================================
# 注入检测模式（融优 prompt_security.py + indirect_injection_guard.py）
# ===========================================================================

# 危险代码执行模式
DANGEROUS_CODE_PATTERNS = [
    (r"\bexec\s*\(", 0.8, "exec() 调用"),
    (r"\beval\s*\(", 0.8, "eval() 调用"),
    (r"\b__import__\s*\(", 0.6, "__import__() 动态导入"),
    (r"\bcompile\s*\(", 0.5, "compile() 动态编译"),
    (r"\bos\.system\s*\(", 0.7, "os.system() 系统命令"),
    (r"\bsubprocess\.", 0.6, "subprocess 调用"),
    (r"\bopen\s*\(.+['\"]w", 0.4, "文件写入操作"),
    (r"\bopen\s*\(.+['\"]a", 0.3, "文件追加操作"),
    (r"\brequests\.(get|post|put|delete)\s*\(", 0.3, "HTTP 网络请求"),
    (r"\bsocket\.", 0.5, "socket 原始网络访问"),
    (r"\bshutil\.rmtree\s*\(", 0.9, "shutil.rmtree() 递归删除"),
    (r"\bos\.remove\s*\(", 0.6, "os.remove() 文件删除"),
    (r"\bos\.rmdir\s*\(", 0.6, "os.rmdir() 目录删除"),
]

# 凭据泄露模式
CREDENTIAL_PATTERNS = [
    (r"(?i)(api[_-]?key|apikey|secret|password|token|auth)\s*[:=]\s*['\"][^'\"]{8,}['\"]", 0.9),
    (r"(?i)sk-[A-Za-z0-9]{20,}", 0.95),
    (r"(?i)ghp_[A-Za-z0-9]{20,}", 0.95),
    (r"(?i)(BEGIN\s+(RSA|EC)\s+PRIVATE\s+KEY)", 1.0),
    (r"(?i)(bearer|authorization)\s+['\"][^'\"]{8,}['\"]", 0.8),
    (r"(?i)eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{0,}", 0.7),  # JWT
]

# 注入提示模式（中文+英文）
INJECTION_PATTERNS = [
    (
        r"(忽略|忘记|无视|跳过|覆盖|override|ignore|disregard|bypass|skip)\s*(上述|以上|之前|前面|所有|all|above|previous|prior)\s*(指令|规则|限制|约束|规定|instruction|rule|constraint|restriction)",
        0.9,
        "忽略指令模式",
    ),
    (
        r"(你|you)\s*(现在|now)\s*(是|are)\s*(一个|a|an)\s*(不同|新|new|different)\s*(的|角色|身份|人格|role|identity|persona)",
        0.7,
        "身份重定义",
    ),
    (
        r"(假装|扮演|模拟|角色扮演|act as|pretend|roleplay|simulate)\s*(你是|你是|you are)",
        0.6,
        "角色扮演注入",
    ),
    (
        r"(不要|别再|停止|don't|stop|never)\s*(说|回复|输出|say|reply|output)\s*(你是|你是|you are|as an?)\s*(AI|助手|assistant|agent)",
        0.5,
        "抑制身份声明",
    ),
    (r"(system\s*prompt|系统提示|SYSTEM|INSTRUCTION)\s*[:=]", 0.8, "系统提示提取"),
    (r"\[INST\]|<<SYS>>|<\|\s*im_start\s*\|>|<\|\s*im_end\s*\|>", 0.9, "特殊token注入"),
    (r"DAN\s*mode|jailbreak|越狱|developer\s*mode", 0.95, "越狱/越权模式"),
    (
        r"(输出|打印|显示|复述|重复|output|print|display|repeat|echo)\s*(你的|your)\s*(系统|system)\s*(提示|prompt)",
        0.85,
        "提示词提取",
    ),
]


# ===========================================================================
# SkillsBench 12维评估引擎
# ===========================================================================


class SkillsBenchScorer:
    """SkillsBench 12维质量评估器。

    使用方式:
        scorer = SkillsBenchScorer()
        report = scorer.score_file("path/to/SKILL.md")
        # 或批量
        reports = scorer.score_directory("skills/")
        summary = scorer.summary(reports)
    """

    def __init__(self, strict: bool = False):
        self.strict = strict
        self._reset()

    def _reset(self):
        self._raw_content = ""
        self._frontmatter: dict[str, Any] = {}
        self._body = ""
        self._skill_dir: Path | None = None

    # ── 主入口 ──────────────────────────────────────────────────

    def score_file(self, skill_path: Path | str) -> QualityReport:
        """对单个 SKILL.md 进行12维度评分。"""
        from datetime import datetime

        path = Path(skill_path)
        self._reset()
        self._skill_dir = path.parent

        if not path.exists():
            return self._empty_report(str(path), f"文件不存在: {path}")

        self._raw_content = path.read_text(encoding="utf-8")
        self._parse_content()

        skill_name = self._frontmatter.get("name", path.parent.name)

        # 逐维度评分
        dims = [
            self._score_clarity(),
            self._score_completeness(),
            self._score_correctness(),
            self._score_efficiency(),
            self._score_robustness(),
            self._score_maintainability(),
            self._score_usability(),
            self._score_modularity(),
            self._score_documentation(),
            self._score_compatibility(),
            self._score_testability(),
        ]

        # 安全性单独审计（计入总分但独立展示）
        security = self._audit_security()
        security_score = 1.0 - security.overall_security  # 安全→高分
        dims.append(
            DimensionScore(
                name="security",
                label_cn="安全性",
                score=security_score,
                max_score=1.0,
                weight=1.0,
                findings=security.findings if security.findings else ["无明显安全风险"],
            )
        )

        total = sum(d.score for d in dims)
        tier = QualityTier.from_score(total)

        # 生成改进建议
        recs = [f"{d.label_cn}: {f}" for d in dims if d.score < 0.6 for f in d.findings[:1]]

        return QualityReport(
            skill_name=skill_name,
            skill_path=str(path),
            total_score=round(total, 1),
            tier=tier,
            dimensions=dims,
            security_details=security,
            recommendations=recs,
            metadata={
                "frontmatter_keys": list(self._frontmatter.keys()),
                "body_chars": len(self._body),
                "has_examples": "## 示例" in self._raw_content
                or "## Examples" in self._raw_content,
                "has_tests": self._has_test_files(),
            },
            scored_at=datetime.now(UTC).isoformat(),
        )

    def score_directory(self, dir_path: Path | str) -> list[QualityReport]:
        """批量评分目录中所有 SKILL.md。"""
        root = Path(dir_path)
        reports = []

        if not root.exists():
            return reports

        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                try:
                    report = self.score_file(skill_md)
                    reports.append(report)
                except Exception as e:
                    logger.warning("skill_quality.score_error", path=str(skill_md), error=str(e))
                    reports.append(self._empty_report(str(skill_md), str(e)))

        return reports

    def summary(self, reports: list[QualityReport]) -> dict[str, Any]:
        """生成批量评分汇总。"""
        if not reports:
            return {"total": 0, "avg_score": 0.0, "tiers": {}, "top_3": [], "worst_3": []}

        scores = [r.total_score for r in reports]
        avg = sum(scores) / len(scores)

        tiers = {}
        for r in reports:
            t = r.tier.value
            tiers[t] = tiers.get(t, 0) + 1

        sorted_reports = sorted(reports, key=lambda r: r.total_score, reverse=True)
        top_3 = [(r.skill_name, r.total_score, r.tier.emoji) for r in sorted_reports[:3]]
        worst_3 = [(r.skill_name, r.total_score, r.tier.emoji) for r in sorted_reports[-3:]]

        return {
            "total": len(reports),
            "avg_score": round(avg, 1),
            "max_score": round(max(scores), 1),
            "min_score": round(min(scores), 1),
            "tiers": tiers,
            "elite_count": tiers.get("elite", 0),
            "high_count": tiers.get("high", 0),
            "pass_rate": round(sum(1 for r in reports if r.pass_threshold) / len(reports) * 100, 1),
            "top_3": top_3,
            "worst_3": worst_3,
        }

    # ── 内容解析 ───────────────────────────────────────────────

    def _parse_content(self):
        """解析 YAML frontmatter + body。"""
        raw = self._raw_content.strip()
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                try:
                    import yaml

                    self._frontmatter = yaml.safe_load(parts[1].strip()) or {}
                except Exception:
                    self._frontmatter = {}
                self._body = parts[2].strip()
                return

        self._body = raw
        self._frontmatter = {}

    # ── 12维度评分（每维度0.0-1.0）─────────────────────────────

    def _score_clarity(self) -> DimensionScore:
        """1. 清晰度：描述是否明确"做什么+何时使用"。"""
        score = 0.5  # 基准分
        findings = []

        desc = self._frontmatter.get("description", "")
        if not desc:
            findings.append("缺少 description 字段")
            score -= 0.3

        # 中英文触发词检查
        triggers_cn = ["做什么", "何时用", "触发", "用于", "当"]
        triggers_en = ["when", "trigger", "use case", "purpose", "scenario"]
        all_triggers = triggers_cn + triggers_en

        matched = sum(1 for t in all_triggers if t.lower() in (desc + self._body).lower())
        if matched >= 3:
            score += 0.3
            findings.append("触发场景描述充分")
        elif matched >= 1:
            score += 0.15
        else:
            findings.append("缺少触发场景描述（建议含'做什么'和'何时使用'）")
            score -= 0.15

        # body 第一部分应说清楚用途
        first_para = self._body.split("\n\n")[0] if self._body else ""
        if len(first_para) >= 20:
            score += 0.1
        if len(first_para) < 10:
            score -= 0.1
            findings.append("正文第一段过短（建议≥20字符）")

        return DimensionScore(
            name="clarity",
            label_cn="清晰度",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_completeness(self) -> DimensionScore:
        """2. 完整度：必填+推荐字段齐全。"""
        score = 0.5
        findings = []

        required = ["name", "description"]
        recommended = ["license", "compatibility", "metadata", "allowed-tools"]
        bonus = ["version", "author", "tags", "homepage"]

        for field in required:
            if field in self._frontmatter and self._frontmatter[field]:
                score += 0.1
            else:
                findings.append(f"缺少必填字段: {field}")
                score -= 0.15

        present_rec = sum(1 for f in recommended if f in self._frontmatter)
        if present_rec >= 3:
            score += 0.2
            findings.append("推荐字段齐全")
        elif present_rec >= 1:
            score += 0.1
        else:
            findings.append("缺少推荐字段（如 license/compatibility）")

        present_bonus = sum(1 for f in bonus if f in self._frontmatter)
        score += present_bonus * 0.03

        # body 完整性
        if len(self._body) >= 200:
            score += 0.1
        if len(self._body) < 50:
            score -= 0.1
            findings.append("正文过短（<50字符）")

        return DimensionScore(
            name="completeness",
            label_cn="完整度",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_correctness(self) -> DimensionScore:
        """3. 正确性：无语法错误/格式问题。"""
        score = 0.8
        findings = []

        # YAML 解析
        if self._frontmatter:
            score += 0.1
        else:
            if self._raw_content.startswith("---"):
                findings.append("YAML frontmatter 解析失败")
                score -= 0.3

        # name 格式
        name = self._frontmatter.get("name", "")
        if name and not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
            findings.append(f"name 格式不符规范: {name}")
            score -= 0.15

        # 检查 JSON 嵌入
        json_errors = 0
        for match in re.finditer(r"\{[^}]*\}", self._body):
            try:
                json.loads(match.group())
            except json.JSONDecodeError:
                json_errors += 1
        if json_errors > 0:
            findings.append(f"发现 {json_errors} 处 JSON 格式错误")
            score -= json_errors * 0.05

        # Markdown 链接完整性
        broken_links = len(re.findall(r"\[[^\]]+\]\(\s*\)", self._body))
        if broken_links:
            findings.append(f"发现 {broken_links} 个空链接")
            score -= broken_links * 0.05

        return DimensionScore(
            name="correctness",
            label_cn="正确性",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_efficiency(self) -> DimensionScore:
        """5. 效率：token 用量/描述精炼度。"""
        score = 0.6
        findings = []

        body_len = len(self._body)
        estimated_tokens = body_len // 2  # 粗略估算

        if estimated_tokens <= 1000:
            score += 0.2
            findings.append("token 用量优（≤1000 est.）")
        elif estimated_tokens <= 3000:
            score += 0.1
            findings.append("token 用量适中（≤3000 est.）")
        elif estimated_tokens <= 5000:
            pass
        else:
            score -= 0.2
            findings.append(f"token 用量偏高（~{estimated_tokens} est.，建议拆分到 references/）")

        # 检测"全塞一个文档"反模式（SkillsBench: -2.9pp）
        section_count = len(re.findall(r"^#{1,3}\s", self._body, re.MULTILINE))
        if section_count > 15:
            score -= 0.25
            findings.append(f"检测到全塞文档反模式（{section_count}个章节）→ 建议拆分")

        # 代码块比例
        code_blocks = len(re.findall(r"```", self._body))
        if code_blocks > 10:
            score -= 0.1
            findings.append("代码块过多（建议精简为关键示例）")

        return DimensionScore(
            name="efficiency",
            label_cn="效率",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_robustness(self) -> DimensionScore:
        """6. 鲁棒性：错误处理/边缘案例。"""
        score = 0.4
        findings = []

        content = self._body.lower()
        patterns = [
            (r"(error|错误|异常|exception).*(handl|处理|捕获|catch)", "包含错误处理"),
            (r"(fallback|降级|backup|备选)", "包含降级策略"),
            (r"(edge\s*case|边缘|边界|boundary)", "包含边缘案例"),
            (r"(retry|重试|backoff)", "包含重试机制"),
            (r"(timeout|超时)", "包含超时处理"),
        ]

        matched = 0
        for pattern, label in patterns:
            if re.search(pattern, content):
                matched += 1
                findings.append(label)

        if matched >= 3:
            score += 0.4
        elif matched >= 1:
            score += 0.2
        else:
            findings.append("缺少错误处理/降级策略")
            score -= 0.1

        return DimensionScore(
            name="robustness",
            label_cn="鲁棒性",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_maintainability(self) -> DimensionScore:
        """7. 可维护性：版本/作者/更新。"""
        score = 0.4
        findings = []

        if "version" in self._frontmatter:
            ver = self._frontmatter["version"]
            if re.match(r"^\d+\.\d+\.\d+", str(ver)):
                score += 0.2
                findings.append(f"版本号规范: {ver}")
            else:
                score += 0.1

        if "author" in self._frontmatter:
            score += 0.1
            findings.append("包含作者信息")

        if "license" in self._frontmatter:
            score += 0.15
            findings.append(f"许可证: {self._frontmatter['license']}")

        # changelog / history
        if re.search(r"(changelog|更新日志|history|变更)", self._body, re.IGNORECASE):
            score += 0.1
            findings.append("包含更新历史")

        return DimensionScore(
            name="maintainability",
            label_cn="可维护性",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_usability(self) -> DimensionScore:
        """8. 可用性：示例/快速入门。"""
        score = 0.3
        findings = []

        content_lower = self._body.lower()

        # 示例检查
        example_indicators = [
            (r"##\s*(示例|example|usage|用法|quick\s*start|快速开始)", "有示例/用法章节"),
            (r"```", "包含代码示例"),
            (r"(curl|npm|pip|npx|docker)\s", "包含安装命令"),
            (r"(截图|screenshot|demo|演示)", "包含截图/演示"),
            (r"(faq|常见问题|q&a)", "包含 FAQ"),
        ]

        matched = 0
        for pattern, label in example_indicators:
            if re.search(pattern, content_lower):
                matched += 1
                if matched <= 3:  # 只记录前3个
                    findings.append(label)

        if matched >= 4:
            score += 0.5
        elif matched >= 2:
            score += 0.3
        elif matched >= 1:
            score += 0.15
        else:
            findings.append("缺少示例/快速入门（建议含代码示例+安装命令）")
            score -= 0.1

        return DimensionScore(
            name="usability",
            label_cn="可用性",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_modularity(self) -> DimensionScore:
        """9. 模块化：文件结构。"""
        score = 0.4
        findings = []

        if self._skill_dir and self._skill_dir.exists():
            subdirs = [d.name for d in self._skill_dir.iterdir() if d.is_dir()]
            std_dirs = {"scripts", "references", "assets", "tests"}

            has_std = std_dirs & set(subdirs)
            if len(has_std) >= 2:
                score += 0.4
                findings.append(f"标准子目录齐全: {', '.join(sorted(has_std))}")
            elif len(has_std) >= 1:
                score += 0.2
                findings.append(f"有标准子目录: {list(has_std)[0]}")
            else:
                findings.append("无标准子目录（建议含 scripts/ references/ assets/）")

            # 文件数合理
            py_files = list(self._skill_dir.glob("*.py"))
            md_files = list(self._skill_dir.glob("*.md"))
            if 2 <= len(py_files) + len(md_files) <= 15:
                score += 0.1
            if len(py_files) + len(md_files) > 20:
                score -= 0.1
                findings.append("文件过多（>20），建议精简")

        return DimensionScore(
            name="modularity",
            label_cn="模块化",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_documentation(self) -> DimensionScore:
        """10. 文档质量：注释/引用。"""
        score = 0.4
        findings = []

        content = self._body
        # inline 注释
        inline_comments = len(re.findall(r"# .+", content))
        if inline_comments >= 5:
            score += 0.15
            findings.append(f"{inline_comments} 处内联注释")
        if inline_comments < 2:
            findings.append("内联注释不足（建议≥5处）")

        # 引用/参考
        refs = re.findall(r"(https?://|arxiv|doi|参考文献|reference)", content, re.IGNORECASE)
        if refs:
            score += 0.15
            findings.append(f"包含 {len(refs)} 处参考引用")
        else:
            findings.append("缺少参考引用")

        # Markdown 结构化
        headings = len(re.findall(r"^#{1,4}\s", content, re.MULTILINE))
        if headings >= 5:
            score += 0.15
        if headings >= 3:
            score += 0.1
        else:
            findings.append("Markdown 结构不足（建议≥3级标题）")

        # 表格
        if "|---|---|" in content or "| --- |" in content:
            score += 0.05

        return DimensionScore(
            name="documentation",
            label_cn="文档质量",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_compatibility(self) -> DimensionScore:
        """11. 兼容性：Agent/平台声明。"""
        score = 0.3
        findings = []

        compat = self._frontmatter.get("compatibility", "")
        if compat:
            score += 0.3
            # 检查是否声明了具体平台
            platforms = re.findall(
                r"(openbridge|homebridge|claude|codex|gemini|copilot|workbuddy)",
                compat,
                re.IGNORECASE,
            )
            if platforms:
                score += 0.2
                findings.append(f"兼容平台: {', '.join(set(platforms))}")
        else:
            findings.append("缺少 compatibility 声明")

        # 检查 body 中是否有兼容性说明
        if re.search(
            r"(兼容|compatible|supported)\s*(agent|平台|platform)", self._body, re.IGNORECASE
        ):
            score += 0.1
            findings.append("正文含兼容性说明")

        # metadata 中声明
        if self._frontmatter.get("metadata", {}).get("platform"):
            score += 0.1

        return DimensionScore(
            name="compatibility",
            label_cn="兼容性",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    def _score_testability(self) -> DimensionScore:
        """12. 可测试性：测试脚本/验证。"""
        score = 0.3
        findings = []

        # 检测测试文件
        if self._skill_dir and self._has_test_files():
            score += 0.4
            findings.append("包含测试文件")

        # 检测验证逻辑
        if re.search(r"(test|验证|assert|expect)\s", self._body, re.IGNORECASE):
            score += 0.15
            findings.append("包含验证逻辑")

        # 检测 curl/CLI 测试命令
        if re.search(r"(curl|pytest|npm test)", self._body, re.IGNORECASE):
            score += 0.1

        return DimensionScore(
            name="testability",
            label_cn="可测试性",
            score=round(max(0.0, min(1.0, score)), 2),
            max_score=1.0,
            weight=1.0,
            findings=findings,
        )

    # ── 安全审计 ───────────────────────────────────────────────

    def _audit_security(self) -> SecurityAudit:
        """5项安全子维度审计。"""
        full_text = self._raw_content
        findings = []

        # 1. 注入风险
        inj_score = 0.0
        for pattern, weight, label in INJECTION_PATTERNS:
            matches = re.findall(pattern, full_text)
            if matches:
                inj_score = max(inj_score, weight)
                findings.append(f"[注入风险] {label}: {len(matches)} 处匹配")
        if inj_score == 0.0:
            findings.append("[注入风险] 未检测到已知注入模式")

        # 2. 危险操作
        danger_score = 0.0
        for pattern, weight, label in DANGEROUS_CODE_PATTERNS:
            matches = re.findall(pattern, full_text)
            if matches:
                danger_score = max(danger_score, weight)
                if weight >= 0.7:
                    findings.append(f"[高危操作] {label}: {len(matches)} 处匹配")
        if danger_score == 0.0:
            findings.append("[危险操作] 未检测到系统级危险调用")

        # 3. 网络访问
        network_score = 0.0
        network_patterns = [
            (r"requests\.(get|post|put|delete|patch)", 0.5),
            (r"urllib\.(request|open)", 0.4),
            (r"fetch\s*\(\s*['\"]https?://", 0.4),
            (r"socket\.(connect|send)", 0.6),
            (r"websocket|ws\.connect", 0.5),
            (r"http\.client|httpx", 0.3),
        ]
        for pattern, weight in network_patterns:
            matches = re.findall(pattern, full_text)
            if matches:
                network_score = max(network_score, weight)
        if network_score > 0:
            findings.append(f"[网络访问] 风险等级: {network_score:.2f}")

        # 4. 文件系统访问
        fs_score = 0.0
        fs_patterns = [
            (r"open\s*\(.*['\"]w", 0.4),
            (r"os\.(remove|unlink|rmdir|rename|chmod|chown)", 0.5),
            (r"shutil\.(rmtree|copytree|move)", 0.7),
            (r"read_file|write_file|Read|Write", 0.3),
            (r"pathlib.*(unlink|write_text|write_bytes)", 0.4),
        ]
        for pattern, weight in fs_patterns:
            matches = re.findall(pattern, full_text)
            if matches:
                fs_score = max(fs_score, weight)
        if fs_score > 0:
            findings.append(f"[文件系统] 风险等级: {fs_score:.2f}")

        # 5. 凭据泄露
        cred_score = 0.0
        for pattern, weight in CREDENTIAL_PATTERNS:
            matches = re.findall(pattern, full_text)
            if matches:
                cred_score = max(cred_score, weight)
                findings.append(f"[凭据泄露] 检测到敏感信息模式 ({weight:.0%} 置信度)")
                break  # 一个就够了
        if cred_score == 0.0:
            findings.append("[凭据泄露] 未检测到敏感信息")

        return SecurityAudit(
            injection_risk=inj_score,
            dangerous_ops=danger_score,
            network_access=network_score,
            file_system_access=fs_score,
            credential_leak=cred_score,
            findings=findings,
        )

    # ── 辅助 ───────────────────────────────────────────────────

    def _has_test_files(self) -> bool:
        """检查技能目录是否含测试文件。"""
        if not self._skill_dir:
            return False
        test_indicators = ["test_", "_test.", "spec.", "__tests__"]
        for f in self._skill_dir.rglob("*"):
            if f.is_file():
                name = f.name.lower()
                if any(t in name for t in test_indicators):
                    return True
        return False

    def _empty_report(self, path: str, error: str) -> QualityReport:
        return QualityReport(
            skill_name="unknown",
            skill_path=path,
            total_score=0.0,
            tier=QualityTier.LOW,
            dimensions=[],
            recommendations=[error],
        )


# ===========================================================================
# Rich 格式化输出（CLI集成用）
# ===========================================================================


def format_report_rich(report: QualityReport) -> str:
    """生成 Rich 格式的质量报告（彩色终端输出）。

    不依赖 Rich 库，纯 ANSI 转义，零依赖显示。
    """
    lines = []

    # 标题
    tier_emoji = report.tier.emoji
    tier_label = report.tier.label_cn
    score_bar = _score_bar(report.total_score, 12.0)
    lines.append(f"\n{'=' * 60}")
    lines.append(
        f"  {tier_emoji} {report.skill_name} — {report.total_score}/12.0 {score_bar} [{tier_label}]"
    )
    lines.append(f"{'=' * 60}")

    # 12维详情表
    lines.append(f"\n{'维度':<12} {'得分':<8} {'等级':<12} {'加权':<6} {'关键发现'}")
    lines.append("-" * 60)

    for dim in report.dimensions:
        pct = dim.percentage
        bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
        level_map = {"excellent": "优秀 ✓", "good": "良好 ○", "fair": "一般 △", "poor": "较差 ✗"}
        lines.append(
            f"{dim.label_cn:<12} {bar:<10} {level_map.get(dim.level, '?'):<12} "
            f"x{dim.weight:.1f}  {dim.findings[0] if dim.findings else '—'}"
        )

    # 安全审计
    if report.security_details:
        sec = report.security_details
        lines.append(f"\n{'─' * 60}")
        lines.append(
            f"  🛡️  安全审计 | 综合风险: {sec.overall_security:.2f} | 等级: {sec.risk_level}"
        )
        for f in sec.findings:
            lines.append(f"     {f}")

    # 改进建议
    if report.recommendations:
        lines.append(f"\n{'─' * 60}")
        lines.append(f"  💡 改进建议 ({len(report.recommendations)}条):")
        for i, rec in enumerate(report.recommendations[:5], 1):
            lines.append(f"     {i}. {rec}")

    # 底线
    lines.append(f"\n  Scored at: {report.scored_at}")
    lines.append(
        f"  Pass: {'✅' if report.pass_threshold else '❌'} | Elite: {'💎' if report.elite_threshold else '—'}"
    )

    return "\n".join(lines)


def format_summary_rich(summary: dict[str, Any]) -> str:
    """Rich 格式的批量评分汇总。"""
    lines = []

    lines.append(f"\n{'=' * 60}")
    lines.append("  SkillsBench 批量评分汇总")
    lines.append(f"{'=' * 60}")

    lines.append(f"  总计: {summary['total']} 个技能")
    lines.append(
        f"  平均分: {summary['avg_score']}/12.0 | 最高: {summary['max_score']} | 最低: {summary['min_score']}"
    )
    lines.append(f"  通过率 (≥4.0): {summary['pass_rate']}%")

    tiers = summary.get("tiers", {})
    lines.append(
        f"  等级分布: 💎精英={tiers.get('elite', 0)} ⭐优秀={tiers.get('high', 0)} ✅可用={tiers.get('medium', 0)} ⚠️低质={tiers.get('low', 0)}"
    )

    if summary.get("top_3"):
        lines.append("\n  Top 3:")
        for name, score, emoji in summary["top_3"]:
            lines.append(f"    {emoji} {name}: {score}/12.0")

    if summary.get("worst_3"):
        lines.append("\n  Worst 3:")
        for name, score, emoji in summary["worst_3"]:
            lines.append(f"    {emoji} {name}: {score}/12.0")

    return "\n".join(lines)


def _score_bar(score: float, max_score: float, width: int = 20) -> str:
    """生成得分进度条。"""
    filled = int(score / max_score * width)
    if score / max_score >= 0.83:
        fill_char = "▓"
    elif score / max_score >= 0.5:
        fill_char = "▓"
    else:
        fill_char = "▓"
    return "[" + fill_char * filled + "░" * (width - filled) + "]"


# ===========================================================================
# 便捷函数
# ===========================================================================


def score_skill(skill_path: Path | str, strict: bool = False) -> QualityReport:
    """便捷函数：对单个技能评分。"""
    return SkillsBenchScorer(strict=strict).score_file(skill_path)


def score_directory(dir_path: Path | str, strict: bool = False) -> dict[str, Any]:
    """便捷函数：批量评分目录并返回汇总。"""
    scorer = SkillsBenchScorer(strict=strict)
    reports = scorer.score_directory(dir_path)
    summary = scorer.summary(reports)
    return {
        "reports": reports,
        "summary": summary,
    }


# ===========================================================================
# 最小 CLI — 直接运行看效果
# ===========================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("SkillsBench 12维质量评分系统")
        print("Usage: python skill_quality.py <SKILL.md路径|skills目录>")
        print("示例:")
        print("  python skill_quality.py skills/my-skill/SKILL.md  # 单文件评分")
        print("  python skill_quality.py skills/                    # 批量目录评分")
        sys.exit(0)

    path = Path(sys.argv[1])
    strict = "--strict" in sys.argv

    if path.is_file():
        scorer = SkillsBenchScorer(strict=strict)
        report = scorer.score_file(path)
        print(format_report_rich(report))
    elif path.is_dir():
        scorer = SkillsBenchScorer(strict=strict)
        reports = scorer.score_directory(path)
        summary = scorer.summary(reports)
        print(format_summary_rich(summary))
        for report in reports:
            # 简要展示每个技能的核心数据
            bar = "▓" * int(report.total_score) + "░" * (12 - int(report.total_score))
            print(
                f"  {report.tier.emoji} {report.skill_name:<30} {report.total_score:>4.1f}/12.0 [{bar}]"
            )
    else:
        print(f"路径不存在: {path}")
        sys.exit(1)
