"""
Skill Router v2.0 — 双层路由中枢（Skill + Model）
=====================================================
九重生态 · 澜舟开发 · 2026-06-24

升级亮点（vs v1）：
  v1: 单层 — 只做Skill关键词匹配+优先级排序
  v2: 双层 — Skill路由 + Model路由

设计理念：
  不同Skill分类对模型能力要求不同：
    research  → L3 付费高质量模型（需要深度推理）
    content   → L2 免费API（足够好的生成质量）
    automation→ L1 本地模型（快速响应，低成本）
    knowledge → L1 本地模型（查询为主，不需推理）
    ...

  v2在v1的Skill路由结果基础上，根据Skill分类自动推荐
  最优ModelTier，实现"对的技能用对的模型"。

核心能力：
  1. 继承v1全部能力（关键词匹配/优先级/角色过滤/降级）
  2. Category → ModelTier 映射（10+分类）
  3. 与ModelRouter集成（可选，传入即用）
  4. 统一 route_with_model() 接口
  5. 延迟评估（不传ModelRouter也能返回推荐）
  6. 策略覆盖（用户可手动指定tier）

使用方式：
  # 基础用法（不传ModelRouter）
  router = SkillRouterV2()
  router.load("skill_registry.json")
  result = router.route_with_model("帮我做市场调研", role="灵犀")
  print(result.skill.name, result.model_tier)

  # 高级用法（传入ModelRouter，实际调模型）
  from model_router import ModelRouter
  mr = ModelRouter()
  mr.auto_init_from_env()
  result = router.route_with_model("帮我做市场调研", role="灵犀", model_router=mr)
  reply = result.model_response  # 实际模型回复
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional, Any, List

from skill_router import (
    SkillRouter, SkillEntry, RouteResult, Priority,
    create_router as create_router_v1,
)
from providers.base_provider import ProviderTier, ChatMessage
from model_router import ModelRouter, RouterStrategy


# ─── 分类 → 模型层级映射 ────────────────────────────────────

CATEGORY_MODEL_MAP: dict[str, ProviderTier] = {
    "automation":    ProviderTier.L1,   # 自动化：本地快速响应
    "research":      ProviderTier.L3,   # 研究：付费高质量推理
    "content":       ProviderTier.L2,   # 内容：免费API足够
    "knowledge":     ProviderTier.L1,   # 知识：查询为主
    "meta":          ProviderTier.L1,   # 元技能：简单逻辑
    "media":         ProviderTier.L1,   # 媒体：专用工具，不需LLM推理
    "design":        ProviderTier.L2,   # 设计：需要一定创意
    "education":     ProviderTier.L3,   # 教育：需要深度推理
    "collaboration": ProviderTier.L1,   # 协作：协调为主
    "information":   ProviderTier.L2,   # 信息：检索+总结
    "productivity":  ProviderTier.L1,   # 生产力：工具操作
}

# 分类 → 推荐理由（用于RouteResultV2的model_reason字段）
CATEGORY_REASON: dict[str, str] = {
    "automation":    "自动化任务，本地L1模型快速响应即可",
    "research":      "研究分析需要深度推理，推荐L3付费模型",
    "content":       "内容生成，L2免费API质量足够",
    "knowledge":     "知识查询，本地L1模型即可处理",
    "meta":          "元技能操作，本地L1足够",
    "media":         "媒体处理，专用工具优先，模型辅助用L1",
    "design":        "设计创作，L2免费API提供足够创意",
    "education":     "教育辅导需要深度推理，推荐L3付费模型",
    "collaboration": "团队协作，本地L1模型处理协调逻辑",
    "information":   "信息检索总结，L2免费API性价比最优",
    "productivity":  "生产力工具，本地L1即可",
}


# ─── v2 路由结果 ─────────────────────────────────────────────

@dataclass
class RouteResultV2:
    """双层路由结果（Skill + Model）"""
    # Layer 1: Skill路由
    skill: Optional[SkillEntry]
    skill_candidates: list[SkillEntry]
    skill_confidence: float
    skill_strategy: str
    skill_reason: str

    # Layer 2: Model路由
    model_tier: Optional[ProviderTier]
    model_reason: str
    model_tier_locked: bool          # 是否被用户强制指定
    model_response: Optional[str]    # 实际模型回复（如果传了model_router）

    # 元信息
    query: str
    role: str
    total_latency_ms: Optional[float] = None

    def summary(self) -> str:
        lines = [
            f"[RouteResultV2] query=\"{self.query[:50]}\" role={self.role}",
            f"  Layer1 Skill: {self.skill.name if self.skill else 'None'} "
            f"(confidence={self.skill_confidence:.2f}, strategy={self.skill_strategy})",
            f"  Layer2 Model: {self.model_tier.value if self.model_tier else 'None'}"
            f"{' [locked]' if self.model_tier_locked else ''} — {self.model_reason}",
        ]
        if self.model_response:
            preview = self.model_response[:80].replace("\n", " ")
            lines.append(f"  Response: {preview}...")
        if self.total_latency_ms is not None:
            lines.append(f"  Latency: {self.total_latency_ms:.0f}ms")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": {
                "id": self.skill.id if self.skill else None,
                "name": self.skill.name if self.skill else None,
                "category": self.skill.category if self.skill else None,
                "priority": self.skill.priority if self.skill else None,
                "confidence": round(self.skill_confidence, 3),
                "strategy": self.skill_strategy,
                "reason": self.skill_reason,
            },
            "model": {
                "tier": self.model_tier.value if self.model_tier else None,
                "reason": self.model_reason,
                "locked": self.model_tier_locked,
                "has_response": self.model_response is not None,
            },
            "candidates": [
                {"id": s.id, "name": s.name, "priority": s.priority}
                for s in self.skill_candidates
            ],
            "query": self.query,
            "role": self.role,
            "latency_ms": round(self.total_latency_ms, 1) if self.total_latency_ms else None,
        }


# ─── v2 核心路由器 ───────────────────────────────────────────

class SkillRouterV2(SkillRouter):
    """
    双层路由器：Skill路由 + Model路由

    继承SkillRouter v1全部能力，新增：
    - route_with_model(): 双层路由统一接口
    - get_model_tier():   根据Skill分类推荐ModelTier
    - execute():          路由+执行（可选，传入ModelRouter时）
    """

    def __init__(self) -> None:
        super().__init__()
        # 用户可覆盖默认的category→tier映射
        self._model_override: dict[str, ProviderTier] = {}
        # 全局tier锁定（用户强制指定）
        self._locked_tier: Optional[ProviderTier] = None

    # ── Model层配置 ──────────────────────────────────

    def set_model_override(self, category: str, tier: ProviderTier) -> None:
        """覆盖某个分类的默认ModelTier推荐"""
        self._model_override[category] = tier

    def lock_model_tier(self, tier: Optional[ProviderTier]) -> None:
        """全局锁定ModelTier（所有请求都用这个tier）"""
        self._locked_tier = tier

    def get_model_tier(self, skill: Optional[SkillEntry]) -> tuple[Optional[ProviderTier], str, bool]:
        """
        根据Skill推荐ModelTier

        Returns:
            (tier, reason, locked)
            - tier: 推荐的ProviderTier
            - reason: 推荐理由
            - locked: 是否为用户强制锁定
        """
        # 优先级1: 全局锁定
        if self._locked_tier:
            return (
                self._locked_tier,
                f"用户全局锁定为 {self._locked_tier.value}",
                True,
            )

        # 无Skill → 无推荐
        if skill is None:
            return (None, "无匹配Skill，不推荐模型", False)

        # 优先级2: 分类级覆盖
        if skill.category in self._model_override:
            tier = self._model_override[skill.category]
            return (tier, f"用户覆盖: {skill.category} → {tier.value}", False)

        # 优先级3: 默认映射
        tier = CATEGORY_MODEL_MAP.get(skill.category, ProviderTier.L1)
        reason = CATEGORY_REASON.get(
            skill.category,
            f"分类 '{skill.category}' 默认L1本地模型",
        )
        return (tier, reason, False)

    # ── 双层路由核心 ─────────────────────────────────

    def route_with_model(
        self,
        query: str,
        role: str = "all",
        top_k: int = 5,
        model_router: Optional[ModelRouter] = None,
        force_tier: Optional[ProviderTier] = None,
        system_prompt: str = "",
        max_tokens: int = 512,
    ) -> RouteResultV2:
        """
        双层路由：Skill路由 + Model路由（+可选执行）

        Args:
            query:        自然语言请求
            role:         当前角色
            top_k:        候选数量
            model_router: ModelRouter实例（传入则实际调模型）
            force_tier:   强制指定ModelTier（覆盖分类映射）
            system_prompt:系统提示词（传给模型）
            max_tokens:   最大输出token

        Returns:
            RouteResultV2: 双层路由结果
        """
        import time
        t0 = time.perf_counter()

        # ── Layer 1: Skill路由（复用v1逻辑）──
        if force_tier:
            self.lock_model_tier(force_tier)

        skill_result: RouteResult = self.route(query, role=role, top_k=top_k)

        # ── Layer 2: Model路由 ──
        model_tier, model_reason, locked = self.get_model_tier(
            skill_result.recommended_skill
        )

        # ── 可选：实际执行模型调用 ──
        model_response: Optional[str] = None
        if model_router is not None and model_tier is not None:
            try:
                # 构建消息
                messages: list[ChatMessage] = []
                if system_prompt:
                    messages.append(ChatMessage(role="system", content=system_prompt))
                messages.append(ChatMessage(role="user", content=query))

                # 调用模型（指定tier）
                response = await_if_needed(
                    model_router.chat(
                        messages,
                        max_tokens=max_tokens,
                        prefer_tier=model_tier,
                    )
                )
                model_response = response.content
            except Exception as e:
                model_reason = f"模型调用失败: {e}"
                model_response = None

        # 清除临时锁定
        if force_tier:
            self.lock_model_tier(None)

        t1 = time.perf_counter()

        return RouteResultV2(
            skill=skill_result.recommended_skill,
            skill_candidates=skill_result.candidates,
            skill_confidence=skill_result.confidence,
            skill_strategy=skill_result.strategy,
            skill_reason=skill_result.reason,
            model_tier=model_tier,
            model_reason=model_reason,
            model_tier_locked=locked,
            model_response=model_response,
            query=query,
            role=role,
            total_latency_ms=(t1 - t0) * 1000,
        )

    # ── 批量路由 ─────────────────────────────────────

    def route_batch(
        self,
        queries: list[str],
        role: str = "all",
    ) -> list[RouteResultV2]:
        """批量路由（不含模型执行）"""
        return [
            self.route_with_model(q, role=role)
            for q in queries
        ]

    # ── 路由解释 ─────────────────────────────────────

    def explain(self, query: str, role: str = "all") -> str:
        """返回人类可读的路由解释（用于调试/日志）"""
        result = self.route_with_model(query, role=role)
        return result.summary()


# ─── 辅助函数 ───────────────────────────────────────────────

def await_if_needed(coro_or_result: Any) -> Any:
    """处理同步/异步返回值"""
    import asyncio
    if asyncio.iscoroutine(coro_or_result):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在运行中的事件循环里，用ensure_future
                future = asyncio.ensure_future(coro_or_result)
                return loop.run_until_complete(future)
            else:
                return loop.run_until_complete(coro_or_result)
        except RuntimeError:
            # 没有事件循环，创建新的
            return asyncio.run(coro_or_result)
    return coro_or_result


# ─── 工厂函数 ───────────────────────────────────────────────

def create_router_v2(
    registry_path: str | None = None,
    model_router: Optional[ModelRouter] = None,
) -> SkillRouterV2:
    """
    创建并加载 SkillRouterV2

    Args:
        registry_path: JSON注册表路径
        model_router:  ModelRouter实例（可选）

    Returns:
        已加载的 SkillRouterV2 实例
    """
    router = SkillRouterV2()
    if registry_path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        registry_path = os.path.join(here, "skill_registry.json")

    if os.path.exists(registry_path):
        router.load(registry_path)
    else:
        raise FileNotFoundError(f"注册表文件不存在: {registry_path}")

    return router


# ─── CLI 入口 ───────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    router = create_router_v2()
    print(router.summary_table())
    print()

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        result = router.route_with_model(query, role="all")
        print(result.summary())
        print()
        print("路由详情:")
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
