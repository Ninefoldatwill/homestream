"""
桥v7 ExperimentArchiver — 实验结果千寻归档适配器

融优主义分级: B类 — 融合改造
灵感来源: 千寻书阁知识管理 + Ratchet Loop实验成果归档

核心功能:
1. 将实验结果格式化为结构化文档（JSON + Markdown）
2. 归档到千寻书阁本地目录（可选远程API）
3. 建立实验知识索引（可检索、可追溯）
4. 失败实验的教训也归档（避免重复踩坑）

归档结构:
  bookhouse/experiments/
    ├── {experiment_id}.json     # 结构化数据
    ├── {experiment_id}.md       # 人类可读报告
    └── index.json               # 索引（实验名→ID→状态）

日期: 2026-06-26
作者: 澜舟
"""

from __future__ import annotations

import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path

from ratchet_loop import ExperimentResult, ExperimentStatus, RatchetPhase


# ==================== 归档配置 ====================

# 默认归档根目录（可通过环境变量 EXPERIMENT_ARCHIVE_DIR 配置）
DEFAULT_ARCHIVE_BASE = os.environ.get(
    "EXPERIMENT_ARCHIVE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments_archive"),
)

# 归档文件名模板
ARCHIVE_JSON_TEMPLATE = "{experiment_id}.json"
ARCHIVE_MD_TEMPLATE = "{experiment_id}.md"
ARCHIVE_INDEX = "index.json"


# ==================== 归档适配器 ====================

class ExperimentArchiver:
    """实验结果千寻归档适配器

    将Ratchet Loop实验结果归档到千寻书阁，建立可检索的实验知识库。

    归档内容：
    1. JSON结构化数据 — 供程序检索
    2. Markdown人类可读报告 — 供千寻查阅
    3. 索引更新 — 供批量查询

    使用方式：
        archiver = ExperimentArchiver()
        path = archiver.archive(experiment_result)
    """

    def __init__(
        self,
        archive_base: str = DEFAULT_ARCHIVE_BASE,
        bookhouse_api: Optional[str] = None,
    ):
        """
        Args:
            archive_base: 归档根目录（默认千寻书阁数据库下）
            bookhouse_api: 千寻书阁API地址（可选，用于远程归档）
        """
        self.archive_base = Path(archive_base)
        self.bookhouse_api = bookhouse_api
        self._lock_dirs()

    def archive(self, result: ExperimentResult) -> str:
        """归档实验结果

        Args:
            result: 实验结果对象

        Returns:
            归档文件路径（JSON文件）
        """
        # 确保目录存在
        self.archive_base.mkdir(parents=True, exist_ok=True)

        # 1. 写入JSON结构化数据
        json_path = self.archive_base / ARCHIVE_JSON_TEMPLATE.format(
            experiment_id=result.experiment_id
        )
        json_data = result.to_dict()
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        # 2. 写入Markdown人类可读报告
        md_path = self.archive_base / ARCHIVE_MD_TEMPLATE.format(
            experiment_id=result.experiment_id
        )
        md_content = self._format_markdown(result)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # 3. 更新索引
        self._update_index(result)

        return str(json_path)

    def get_archive(self, experiment_id: str) -> Optional[Dict[str, Any]]:
        """获取已归档的实验结果

        Args:
            experiment_id: 实验ID

        Returns:
            实验结果字典，不存在则返回None
        """
        json_path = self.archive_base / ARCHIVE_JSON_TEMPLATE.format(
            experiment_id=experiment_id
        )
        if not json_path.exists():
            return None

        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_archives(
        self,
        status: Optional[ExperimentStatus] = None,
    ) -> List[Dict[str, Any]]:
        """列出已归档的实验

        Args:
            status: 可选状态过滤

        Returns:
            实验摘要列表
        """
        index = self._load_index()
        archives = index.get("experiments", [])
        if status:
            archives = [a for a in archives if a.get("status") == status.value]
        return archives

    def search_archives(self, keyword: str) -> List[Dict[str, Any]]:
        """搜索归档实验（关键词匹配名称/假设/标签）

        Args:
            keyword: 搜索关键词

        Returns:
            匹配的实验摘要列表
        """
        index = self._load_index()
        keyword_lower = keyword.lower()
        results = []
        for exp in index.get("experiments", []):
            if (
                keyword_lower in exp.get("name", "").lower()
                or keyword_lower in exp.get("hypothesis", "").lower()
                or any(keyword_lower in tag.lower() for tag in exp.get("tags", []))
            ):
                results.append(exp)
        return results

    def get_stats(self) -> Dict[str, Any]:
        """获取归档统计"""
        index = self._load_index()
        experiments = index.get("experiments", [])
        total = len(experiments)

        status_counts = {}
        for exp in experiments:
            s = exp.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

        return {
            "total_archives": total,
            "status_counts": status_counts,
            "locked": status_counts.get("locked", 0) + status_counts.get("archived", 0),
            "rolled_back": status_counts.get("rolled_back", 0),
            "archive_base": str(self.archive_base),
        }

    # ========== 内部方法 ==========

    def _format_markdown(self, result: ExperimentResult) -> str:
        """将实验结果格式化为Markdown报告"""
        config = result.config
        status_emoji = {
            ExperimentStatus.LOCKED: "🔒",
            ExperimentStatus.ARCHIVED: "📂",
            ExperimentStatus.ROLLED_BACK: "↩️",
            ExperimentStatus.TIMEOUT: "⏱️",
            ExperimentStatus.RUNNING: "🔄",
            ExperimentStatus.VERIFYING: "🔍",
            ExperimentStatus.PENDING: "⏳",
        }.get(result.status, "❓")

        lines = [
            f"# {status_emoji} 实验报告: {config.name}",
            "",
            f"**实验ID**: `{result.experiment_id}`  ",
            f"**状态**: {result.status.value}  ",
            f"**阶段**: {result.phase.value}  ",
            f"**创建时间**: {config.created_at}  ",
            f"**执行Agent**: {config.maker}  ",
            f"**验证Agent**: {config.reviewer}  ",
            "",
            "---",
            "",
            "## 假设",
            "",
            f"> {config.hypothesis or '（未声明）'}",
            "",
            "## 成功标准",
            "",
        ]

        if config.success_criteria:
            for i, criterion in enumerate(config.success_criteria, 1):
                lines.append(f"{i}. {criterion}")
        else:
            lines.append("（未定义明确标准）")

        lines.extend([
            "",
            "## 执行结果",
            "",
            f"- **迭代次数**: {result.iterations}",
            f"- **执行时长**: {result.duration:.2f}s",
            f"- **停止条件**: {result.stop_condition or 'N/A'}",
            f"- **停止原因**: {result.stop_reason or 'N/A'}",
            "",
            "### Maker输出",
            "",
        ])

        if result.outputs:
            for i, output in enumerate(result.outputs, 1):
                lines.append(f"{i}. {output}")
        else:
            lines.append("（无输出）")

        lines.extend([
            "",
            "## 验证结果",
            "",
            f"**验证{'通过 ✅' if result.verification_passed else '未通过 ❌'}**",
            "",
        ])

        if result.verification_details:
            for detail in result.verification_details:
                lines.append(f"- {detail}")

        if result.reviewer_notes:
            lines.extend(["", f"**审查备注**: {result.reviewer_notes}"])

        # 棘轮锁定信息
        if result.status in (ExperimentStatus.LOCKED, ExperimentStatus.ARCHIVED):
            lines.extend([
                "",
                "## 棘轮锁定",
                "",
                f"- **锁定时间**: {result.locked_at}",
                f"- **Commit**: `{result.locked_commit or 'N/A'}`",
                f"- **Tag**: `{result.locked_tag or 'N/A'}`",
            ])

        # 回滚信息
        if result.status == ExperimentStatus.ROLLED_BACK:
            lines.extend([
                "",
                "## 回滚信息",
                "",
                f"- **回滚原因**: {result.rollback_reason}",
                f"- **经验教训**: {result.lessons_learned}",
            ])

        # 归档信息
        if result.archived_at:
            lines.extend([
                "",
                "## 归档信息",
                "",
                f"- **归档时间**: {result.archived_at}",
                f"- **归档路径**: `{result.archive_path}`",
            ])

        lines.extend([
            "",
            "---",
            f"*归档者: 千寻 | 归档时间: {datetime.now().isoformat()}*",
        ])

        return "\n".join(lines)

    def _update_index(self, result: ExperimentResult) -> None:
        """更新归档索引"""
        index = self._load_index()

        # 实验摘要
        summary = {
            "experiment_id": result.experiment_id,
            "name": result.config.name,
            "status": result.status.value,
            "hypothesis": result.config.hypothesis,
            "maker": result.config.maker,
            "reviewer": result.config.reviewer,
            "tags": result.config.tags,
            "iterations": result.iterations,
            "duration": round(result.duration, 2),
            "verification_passed": result.verification_passed,
            "created_at": result.config.created_at,
            "locked_at": result.locked_at,
            "archived_at": result.archived_at,
        }

        # 去重更新（同ID覆盖）
        experiments = index.get("experiments", [])
        experiments = [e for e in experiments if e.get("experiment_id") != result.experiment_id]
        experiments.append(summary)
        experiments.sort(key=lambda e: e.get("created_at", ""), reverse=True)

        index["experiments"] = experiments
        index["last_updated"] = datetime.now().isoformat()

        index_path = self.archive_base / ARCHIVE_INDEX
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

    def _load_index(self) -> Dict[str, Any]:
        """加载归档索引"""
        index_path = self.archive_base / ARCHIVE_INDEX
        if not index_path.exists():
            return {"experiments": [], "last_updated": datetime.now().isoformat()}
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _lock_dirs(self) -> None:
        """确保归档目录结构存在"""
        self.archive_base.mkdir(parents=True, exist_ok=True)


# ==================== 便捷函数 ====================

def create_archiver(
    archive_base: str = DEFAULT_ARCHIVE_BASE,
) -> ExperimentArchiver:
    """创建千寻归档适配器"""
    return ExperimentArchiver(archive_base=archive_base)


# ==================== 验证入口 ====================

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("=" * 60)
    print("桥v7 ExperimentArchiver — 千寻归档适配器验证")
    print("=" * 60)

    from ratchet_loop import (
        ExperimentConfig,
        ExperimentResult,
        RatchetLoopEngine,
    )

    # 1. 创建实验
    config = ExperimentConfig(
        name="test-archive-demo",
        hypothesis="归档适配器能正确存储实验结果",
        success_criteria=["JSON文件生成", "Markdown报告生成", "索引更新"],
        maker="澜舟",
        reviewer="千寻",
        max_iterations=3,
        tags=["archiver", "test"],
    )

    # 2. 执行实验
    engine = RatchetLoopEngine()
    result = engine.run_experiment(config)

    # 3. 归档
    archiver = ExperimentArchiver()
    archive_path = archiver.archive(result)

    print(f"\n① 归档路径: {archive_path}")

    # 4. 查询
    retrieved = archiver.get_archive(result.experiment_id)
    print(f"② 检索结果: {retrieved['experiment_id'] if retrieved else 'NOT FOUND'}")

    # 5. 搜索
    search_results = archiver.search_archives("archive")
    print(f"③ 搜索 'archive': {len(search_results)} 条结果")

    # 6. 统计
    stats = archiver.get_stats()
    print(f"④ 统计: {stats}")

    print("\n" + "=" * 60)
    print("✅ ExperimentArchiver 验证完成！")
    print("=" * 60)
