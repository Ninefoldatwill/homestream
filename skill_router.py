"""
Skill Router v1.0 — 轻量级技能调度中枢
=========================================
九重生态 · 澜舟开发 · 2026-06-18

设计理念（融优主义三级决策）：
  A类-直接融: CSDN方案的 priority 字段 + 置信度 + 降级策略
  B类-融合改造: agentregistry 的生命周期管理思路（简化版）
  C类-自己造: 针对九重生态44个Skill的轻量实现

核心能力：
  1. 注册表加载 (JSON)
  2. 优先级排序 (P0→P3)
  3. 关键词匹配 (多策略)
  4. 路由推荐 (置信度+降级)
  5. 角色过滤
  6. 状态统计

使用方式：
  router = SkillRouter()
  router.load("skill_registry.json")
  result = router.route("帮我做市场调研", role="灵犀")
  print(result.recommended_skill, result.confidence)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional

# ─── 优先级枚举 ────────────────────────────────────────────


class Priority(IntEnum):
    P0_CORE = 0  # 核心：必选加载
    P1_ENHANCE = 1  # 增强：常备技能
    P2_SCENE = 2  # 场景：按需加载
    P3_TOOL = 3  # 工具：低频使用


# ─── 数据模型 ──────────────────────────────────────────────


@dataclass
class SkillEntry:
    """单个Skill的注册信息"""

    id: str
    name: str
    category: str
    priority: int  # 0-3, 对应 Priority 枚举
    role: str  # 澜舟/灵犀/千寻/澜澜/all
    triggers: list[str]  # 触发关键词
    description: str
    source: str = "user"  # user/market/builtin
    status: str = "active"  # active/disabled/deprecated
    hit_count: int = 0  # 调用次数（运行时统计）
    last_used: str | None = None  # 最后调用时间


@dataclass
class RouteResult:
    """路由推荐结果"""

    recommended_skill: SkillEntry | None
    candidates: list[SkillEntry]
    confidence: float  # 0.0 ~ 1.0
    strategy: str  # keyword/fallback/none
    reason: str

    def summary(self) -> str:
        if self.recommended_skill is None:
            return f"[RouteResult] 无匹配 (strategy={self.strategy}, reason={self.reason})"
        s = self.recommended_skill
        return (
            f"[RouteResult] {s.name} (P{s.priority}) "
            f"confidence={self.confidence:.2f} "
            f"strategy={self.strategy} "
            f"reason={self.reason}"
        )


# ─── 核心路由器 ────────────────────────────────────────────


class SkillRouter:
    """
    轻量级技能调度中枢

    不依赖外部服务，纯Python实现，<300行核心逻辑。
    支持关键词匹配 + 优先级排序 + 角色过滤 + 降级策略。
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillEntry] = {}
        self._version: str = "0.0.0"
        self._role_mapping: dict[str, list[str]] = {}

    # ── 注册表管理 ──────────────────────────────────

    def load(self, json_path: str) -> int:
        """从JSON文件加载注册表，返回加载数量"""
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        self._version = data.get("version", "0.0.0")
        self._role_mapping = data.get("role_mapping", {})

        count = 0
        for s in data.get("skills", []):
            entry = SkillEntry(
                id=s["id"],
                name=s.get("name", s["id"]),
                category=s.get("category", "unknown"),
                priority=s.get("priority", 3),
                role=s.get("role", "all"),
                triggers=s.get("triggers", []),
                description=s.get("description", ""),
                source=s.get("source", "user"),
                status=s.get("status", "active"),
            )
            self._skills[entry.id] = entry
            count += 1

        return count

    def load_dict(self, data: dict) -> int:
        """从字典加载注册表（用于测试）"""
        self._version = data.get("version", "0.0.0")
        self._role_mapping = data.get("role_mapping", {})
        count = 0
        for s in data.get("skills", []):
            entry = SkillEntry(
                id=s["id"],
                name=s.get("name", s["id"]),
                category=s.get("category", "unknown"),
                priority=s.get("priority", 3),
                role=s.get("role", "all"),
                triggers=s.get("triggers", []),
                description=s.get("description", ""),
                source=s.get("source", "user"),
                status=s.get("status", "active"),
            )
            self._skills[entry.id] = entry
            count += 1
        return count

    def register(self, skill: SkillEntry) -> None:
        """注册单个Skill"""
        self._skills[skill.id] = skill

    def unregister(self, skill_id: str) -> bool:
        """注销Skill"""
        return self._skills.pop(skill_id, None) is not None

    def get(self, skill_id: str) -> SkillEntry | None:
        """按ID获取Skill"""
        return self._skills.get(skill_id)

    # ── 查询接口 ────────────────────────────────────

    def all_skills(self, active_only: bool = True) -> list[SkillEntry]:
        """获取全部Skill，按优先级排序"""
        skills = list(self._skills.values())
        if active_only:
            skills = [s for s in skills if s.status == "active"]
        return sorted(skills, key=lambda s: (s.priority, s.id))

    def by_priority(self, level: int) -> list[SkillEntry]:
        """获取指定优先级的Skill"""
        return [s for s in self.all_skills() if s.priority == level]

    def by_category(self, category: str) -> list[SkillEntry]:
        """获取指定分类的Skill"""
        return [s for s in self.all_skills() if s.category == category]

    def by_role(self, role: str) -> list[SkillEntry]:
        """获取角色可用的Skill（含 role=all 的）"""
        if role == "all":
            return self.all_skills()
        return [s for s in self.all_skills() if s.role == role or s.role == "all"]

    # ── 核心路由 ────────────────────────────────────

    def route(
        self,
        query: str,
        role: str = "all",
        top_k: int = 5,
    ) -> RouteResult:
        """
        根据自然语言查询路由到最合适的Skill

        策略链（降级）：
          1. 关键词精确匹配 → 高置信度
          2. 关键词模糊匹配 → 中置信度
          3. 优先级兜底 → 低置信度

        Args:
            query: 用户的自然语言请求
            role: 当前角色（澜舟/灵犀/千寻/澜澜/all）
            top_k: 返回候选数量

        Returns:
            RouteResult 包含推荐Skill+候选列表+置信度
        """
        # 第一步：角色过滤
        candidates = self.by_role(role)
        if not candidates:
            return RouteResult(
                recommended_skill=None,
                candidates=[],
                confidence=0.0,
                strategy="none",
                reason=f"角色 '{role}' 无可用Skill",
            )

        # 第二步：关键词匹配（空查询跳过）
        scored = self._score_candidates(query, candidates) if query.strip() else []

        if scored and scored[0][1] > 0:
            best_skill, best_score = scored[0]

            # 置信度：sqrt(score / 3.0)，平方根缩放使单次精确匹配即达高置信
            confidence = min((best_score / 3.0) ** 0.5, 1.0)

            # 策略判定
            if confidence >= 0.6:
                strategy = "keyword_exact"
                reason = f"触发词高匹配: {best_skill.triggers[:3]}"
            else:
                strategy = "keyword_fuzzy"
                reason = f"部分匹配: {best_skill.triggers[:2]}"

            top_candidates = [s for s, _ in scored[:top_k]]
            return RouteResult(
                recommended_skill=best_skill,
                candidates=top_candidates,
                confidence=confidence,
                strategy=strategy,
                reason=reason,
            )

        # 第三步：降级 — 无关键词匹配，返回P0核心
        p0_skills = [s for s in candidates if s.priority == 0]
        if p0_skills:
            return RouteResult(
                recommended_skill=p0_skills[0],
                candidates=p0_skills[:top_k],
                confidence=0.2,
                strategy="fallback_priority",
                reason="无关键词匹配，降级到P0核心Skill",
            )

        # 最终兜底
        return RouteResult(
            recommended_skill=None,
            candidates=candidates[:top_k],
            confidence=0.0,
            strategy="fallback_none",
            reason="无匹配且无P0兜底",
        )

    def recommend(self, query: str, role: str = "all") -> str:
        """便捷方法：返回推荐Skill的名称"""
        result = self.route(query, role)
        if result.recommended_skill:
            return result.recommended_skill.name
        return "无匹配Skill"

    # ── 内部评分 ────────────────────────────────────

    def _score_candidates(
        self,
        query: str,
        candidates: list[SkillEntry],
    ) -> list[tuple[SkillEntry, float]]:
        """
        对候选Skill评分

        评分公式（匹配分 + 优先级仅作排序权重）:
          match_score = trigger_hits * 2.0 + description_overlap * 0.3
          最终排序: match_score DESC, priority ASC

        注意：优先级不加分，只作同分时的排序权重。
        这样保证低优先级但精确匹配的Skill不会被高优先级但无关的Skill挤掉。
        """
        query_lower = query.lower()
        query_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query_lower))

        scored: list[tuple[SkillEntry, float]] = []

        for skill in candidates:
            score = 0.0

            # 1. 触发词匹配（权重最高）
            for trigger in skill.triggers:
                trigger_lower = trigger.lower()
                if trigger_lower in query_lower:
                    score += 2.0  # 精确包含
                else:
                    # 模糊：触发词的token是否在query中
                    trigger_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", trigger_lower))
                    overlap = trigger_tokens & query_tokens
                    if overlap:
                        score += len(overlap) * 0.5

                    # 中文字符级匹配（解决分词问题）
                    # "图片生成" vs "生成图片" → 字符重叠但token不同
                    trigger_cn_chars = set(c for c in trigger_lower if "\u4e00" <= c <= "\u9fff")
                    query_cn_chars = set(c for c in query_lower if "\u4e00" <= c <= "\u9fff")
                    if trigger_cn_chars and query_cn_chars:
                        char_overlap = trigger_cn_chars & query_cn_chars
                        if len(char_overlap) >= 3:  # 至少3个汉字重叠才算匹配
                            ratio = len(char_overlap) / len(trigger_cn_chars)
                            if ratio >= 0.6:
                                score += 1.5 * ratio  # 字符级匹配加分

            # 2. 描述重叠
            desc_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", skill.description.lower()))
            desc_overlap = desc_tokens & query_tokens
            score += len(desc_overlap) * 0.3

            if score > 0:
                scored.append((skill, score))

        # 按匹配分降序，同分按优先级升序
        scored.sort(key=lambda x: (-x[1], x[0].priority))
        return scored

    def _max_possible_score(self, skill: SkillEntry) -> float:
        """计算Skill的理论最高分（用于置信度归一化）"""
        # 触发词精确匹配 + 字符级匹配 + 描述重叠
        trigger_max = len(skill.triggers) * 3.5  # 2.0精确 + 1.5字符级
        return trigger_max + 3.0  # +3.0描述上限（合理估值）

    # ── 统计 ────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """返回注册表统计"""
        skills = list(self._skills.values())
        by_priority = {}
        for s in skills:
            key = f"P{s.priority}"
            by_priority[key] = by_priority.get(key, 0) + 1

        by_category = {}
        for s in skills:
            by_category[s.category] = by_category.get(s.category, 0) + 1

        by_source = {}
        for s in skills:
            by_source[s.source] = by_source.get(s.source, 0) + 1

        by_role = {}
        for s in skills:
            by_role[s.role] = by_role.get(s.role, 0) + 1

        return {
            "total": len(skills),
            "active": len([s for s in skills if s.status == "active"]),
            "by_priority": by_priority,
            "by_category": by_category,
            "by_source": by_source,
            "by_role": by_role,
            "version": self._version,
        }

    def summary_table(self) -> str:
        """返回文本格式摘要表"""
        s = self.stats()
        lines = [
            f"Skill Router v{self._version} | 总计 {s['total']} 个 ({s['active']} active)",
            "",
            "按优先级:",
        ]
        for k in sorted(s["by_priority"].keys()):
            lines.append(f"  {k}: {s['by_priority'][k]}")

        lines.append("\n按分类:")
        for k, v in sorted(s["by_category"].items(), key=lambda x: -x[1]):
            lines.append(f"  {k}: {v}")

        lines.append("\n按来源:")
        for k, v in s["by_source"].items():
            lines.append(f"  {k}: {v}")

        return "\n".join(lines)


# ─── 工厂函数 ──────────────────────────────────────────────


def create_router(registry_path: str | None = None) -> SkillRouter:
    """
    创建并加载Router

    Args:
        registry_path: JSON注册表路径，默认使用桥v7目录下的 skill_registry.json

    Returns:
        已加载的 SkillRouter 实例
    """
    router = SkillRouter()
    if registry_path is None:
        # 默认路径：桥v7项目目录
        here = os.path.dirname(os.path.abspath(__file__))
        registry_path = os.path.join(here, "skill_registry.json")

    if os.path.exists(registry_path):
        router.load(registry_path)
    else:
        raise FileNotFoundError(f"注册表文件不存在: {registry_path}")

    return router


# ─── CLI 入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    router = create_router()
    print(router.summary_table())
    print()

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        role = "all"
        result = router.route(query, role=role)
        print(f"查询: {query}")
        print(f"角色: {role}")
        print(result.summary())
        print()
        print("候选列表:")
        for i, s in enumerate(result.candidates, 1):
            print(f"  {i}. [{s.name}] P{s.priority} | {s.category} | {s.description[:60]}")
