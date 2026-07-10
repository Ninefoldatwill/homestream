"""
桥v7 知识防腐引擎 — 记忆演化机制（v7.3+P1生态健康）

融优来源：6/29六维生态健康冲浪 — arXiv 2512.13564 记忆演化
演化公式：L = I(M_t; M_{t-1}) - λ·|M_t|
三步法：遗忘（移除过时）+ 合并（碎片→规律）+ 重构（修正错误）

设计决策：
- 不自动修改MEMORY.md（人机协作·需人工确认）
- 提供分析报告：哪些条目可以遗忘/合并/修正
- 支持归档：删除前备份到.pruned/目录
- 定期检查：每30天触发一次审查提醒
"""

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger("bridge_v7.knowledge_pruner")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class MemoryEntry:
    """MEMORY.md中的知识点条目"""

    line_start: int
    line_end: int
    content: str
    category: str  # 分类标签
    is_outdated: bool = False  # 是否过时
    is_redundant: bool = False  # 是否与其他条目重复
    is_correctable: bool = False  # 是否有可修正的错误
    suggestion: str = ""  # 操作建议


@dataclass
class PruneReport:
    """知识防腐分析报告"""

    timestamp: str
    total_lines: int
    total_entries: int
    outdated_entries: list[MemoryEntry] = field(default_factory=list)
    redundant_pairs: list[tuple[MemoryEntry, MemoryEntry]] = field(default_factory=list)
    correctable_entries: list[MemoryEntry] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# ============================================================
# 过时检测规则
# ============================================================

# 已完成的日期/任务引用模式（超过30天视为可归档）
OUTDATED_PATTERNS = [
    r"✅.*\d{1,2}/\d{1,2}",  # ✅已完成的日期标记
    r"已完成.*\d{4}-\d{2}-\d{2}",  # 已完成的日期引用
    r"之前已\S*(完成|交付|上线)",  # 之前已完成
    r"旧版\S*(已废弃|已下线|已迁移)",  # 旧版废弃标记
    r"v[0-4]\.\d+\.\d+",  # 旧版本号（v4及以下）
]

# 冗余检测关键词（同类概念可能重复）
REDUNDANCY_KEYWORDS = {
    "端口": ["port", "端口", "3458", "3459", "8643", "3460", "9119", "28790"],
    "协议": ["ICP", "icp", "协议"],
    "安全": ["安全", "注入", "security", "防护"],
    "版本": ["版本", "version", "v7.", "v8."],
    "测试": ["测试", "test", "全通过"],
    "模型": ["模型", "model", "路由", "router", "Qwen", "GLM", "DeepSeek"],
    "商标": ["商标", "trademark", "法律"],
}


def detect_outdated(content: str, reference_date: datetime | None = None) -> bool:
    """检测知识条目是否过时"""
    if reference_date is None:
        reference_date = datetime.now(UTC)

    for pattern in OUTDATED_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            # 检查引用日期的旧度
            dates = re.findall(r"(\d{1,2})/(\d{1,2})", content)
            for m, d in dates:
                try:
                    entry_date = datetime(reference_date.year, int(m), int(d), tzinfo=UTC)
                    if entry_date > reference_date:
                        # 可能是明年，视为去年
                        entry_date = entry_date.replace(year=reference_date.year - 1)
                    if (reference_date - entry_date).days > 30:
                        return True
                except ValueError:
                    pass
    return False


def find_redundant(entries: list[MemoryEntry]) -> list[tuple[MemoryEntry, MemoryEntry]]:
    """发现同一分类下的冗余条目对"""
    redundant_pairs = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            if entries[i].category == entries[j].category:
                # 计算重叠度（共享关键词占比）
                words_i = set(re.findall(r"\w+", entries[i].content.lower()))
                words_j = set(re.findall(r"\w+", entries[j].content.lower()))
                if len(words_i) > 0 and len(words_j) > 0:
                    overlap = len(words_i & words_j) / min(len(words_i), len(words_j))
                    if overlap > 0.6:
                        redundant_pairs.append((entries[i], entries[j]))
    return redundant_pairs


# ============================================================
# MEMORY.md 解析器
# ============================================================


def categorize_line(line: str) -> str:
    """根据行内容推断分类"""
    line_lower = line.lower()
    for category, keywords in REDUNDANCY_KEYWORDS.items():
        if any(kw.lower() in line_lower for kw in keywords):
            return category

    # 标题推断
    if line.startswith("## ") and "安全" in line:
        return "安全"
    if line.startswith("## ") and "版本" in line:
        return "版本"
    if line.startswith("## ") and "路由" in line:
        return "路由"
    if line.startswith("## ") and "测试" in line:
        return "测试"
    if line.startswith("## "):
        return "通用"

    return "通用"


def parse_memory_md(filepath: str) -> tuple[list[MemoryEntry], int]:
    """解析MEMORY.md为知识条目列表"""
    if not os.path.exists(filepath):
        return [], 0

    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    entries = []
    current_entry = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 标题行=新条目起点
        if stripped.startswith("## ") or stripped.startswith("### "):
            if current_entry and current_entry.content.strip():
                current_entry.line_end = i - 1
                entries.append(current_entry)
            current_entry = MemoryEntry(
                line_start=i,
                line_end=i,
                content=stripped,
                category=categorize_line(stripped),
            )
        elif current_entry and stripped:
            # 非空行追加内容
            current_entry.content += "\n" + stripped

    # 最后一个条目
    if current_entry and current_entry.content.strip():
        current_entry.line_end = len(lines) - 1
        entries.append(current_entry)

    return entries, len(lines)


# ============================================================
# 精简分析主函数
# ============================================================


def analyze_memory(filepath: str, reference_date: datetime | None = None) -> PruneReport:
    """分析MEMORY.md，生成精简建议报告。

    返回PruneReport包含:
    - outdated_entries: 可遗忘的过时条目
    - redundant_pairs: 可合并的冗余对
    - correctable_entries: 需修正的条目
    - suggestions: 自然语言建议
    """
    entries, total_lines = parse_memory_md(filepath)
    report = PruneReport(
        timestamp=datetime.now(UTC).isoformat(),
        total_lines=total_lines,
        total_entries=len(entries),
    )

    # 1. 过时检测
    for entry in entries:
        if detect_outdated(entry.content, reference_date):
            entry.is_outdated = True
            report.outdated_entries.append(entry)

    # 2. 冗余检测
    report.redundant_pairs = find_redundant(entries)

    # 3. 修正建议（检测可能的错误）
    for entry in entries:
        # 检查是否有冲突的版本号
        versions = re.findall(r"v(\d)\.(\d+)", entry.content)
        if versions:
            # 同一段落有多个不同版本引用
            unique_versions = set(versions)
            if len(unique_versions) > 1:
                entry.is_correctable = True
                report.correctable_entries.append(entry)
                entry.suggestion = "同一段落引用多个不同版本号，建议统一"

    # 4. 生成自然语言建议
    if report.outdated_entries:
        report.suggestions.append(
            f"发现 {len(report.outdated_entries)} 条过时条目（超过30天未更新），建议归档或删除"
        )
    if report.redundant_pairs:
        report.suggestions.append(f"发现 {len(report.redundant_pairs)} 对冗余条目，建议合并为一条")
    if report.correctable_entries:
        report.suggestions.append(
            f"发现 {len(report.correctable_entries)} 条可修正条目（版本冲突等），建议核查修正"
        )

    # 健康度评分（0-100）
    health = 100
    health -= len(report.outdated_entries) * 5  # 每条过时扣5分
    health -= len(report.redundant_pairs) * 3  # 每对冗余扣3分
    health -= len(report.correctable_entries) * 2  # 每条需修正扣2分
    report.suggestions.insert(0, f"知识健康度: {max(0, health)}/100")

    return report


def should_trigger_review(filepath: str, max_age_days: int = 30) -> bool:
    """检查是否需要触发知识复审。

    满足以下任一条件触发:
    1. MEMORY.md超过30天未复审
    2. MEMORY.md超过3000字符（接近限制）
    """
    if not os.path.exists(filepath):
        return False

    # 检查最后修改时间
    mtime = datetime.fromtimestamp(os.path.getmtime(filepath), tz=UTC)
    if (datetime.now(UTC) - mtime).days > max_age_days:
        return True

    # 检查文件大小
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    if len(content) > 2500:  # 接近3000字符限制
        return True

    return False


# ============================================================
# 命令行入口
# ============================================================

if __name__ == "__main__":
    import sys

    memory_path = sys.argv[1] if len(sys.argv) > 1 else ".workbuddy/memory/MEMORY.md"

    print(f"知识防腐分析: {memory_path}")
    print("=" * 60)

    report = analyze_memory(memory_path)

    print(f"总行数: {report.total_lines}")
    print(f"条目数: {report.total_entries}")
    print()

    for suggestion in report.suggestions:
        print(f"  {suggestion}")

    if report.outdated_entries:
        print("\n📦 可遗忘条目:")
        for entry in report.outdated_entries[:5]:
            preview = entry.content[:80].replace("\n", " ")
            print(f"  L{entry.line_start + 1}-L{entry.line_end + 1}: {preview}...")

    if report.redundant_pairs:
        print(f"\n🔗 冗余条目对 ({len(report.redundant_pairs)}):")
        for a, b in report.redundant_pairs[:3]:
            print(f"  [{a.category}] L{a.line_start + 1} ↔ L{b.line_start + 1}")

    print("\n" + "=" * 60)
    print("知识防腐完成。以上为分析建议，请人工确认后执行精简。")
