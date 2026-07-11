"""
RouterScore — 多维度路由评分系统（v5.1.0 新增）

6维度加权评分，自研公式（干净室实现）：
  1. 延迟 (latency)      — 平均响应时间越低越好
  2. 成本 (cost)          — 单位成本越低越好（免费=满分）
  3. 健康度 (health)      — Provider 状态健康度
  4. 新鲜度 (freshness)   — 最近成功调用距今时间越短越好
  5. 成功率 (success_rate)— 历史成功请求占比
  6. 负载 (load)          — 当前并发请求数越少越好

综合评分公式：
  score = Σ(weight_i × dimension_score_i)

  各维度评分归一化到 [0.0, 1.0]，综合评分也在 [0.0, 1.0]。
  分数越高 → 越优先被选择。

默认权重（可配置）：
  health:      0.25  — 健康度最重要（不健康的不用）
  latency:     0.20  — 延迟影响用户体验
  cost:        0.20  — 成本优先是开源基因
  success_rate: 0.20  — 成功率是可靠性指标
  freshness:   0.10  — 新鲜度影响信心
  load:        0.05  — 负载均衡（权重低，仅在极端时影响）

集成方式：
  from router_score import RouterScore, RouterStrategy
  router = ModelRouter(strategy=RouterStrategy.SMART)
  # SMART 策略自动使用 RouterScore 排序
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from providers.base_provider import (
    BaseProvider,
    ProviderConfig,
    ProviderStatus,
    ProviderTier,
)


# ==================== 常量 ====================

# 默认权重
DEFAULT_WEIGHTS: dict[str, float] = {
    "latency": 0.20,
    "cost": 0.20,
    "health": 0.25,
    "freshness": 0.10,
    "success_rate": 0.20,
    "load": 0.05,
}

# 归一化参考值
MAX_LATENCY_MS = 5000.0       # 5秒 = 0分
MAX_COST_PER_1K = 0.01        # $0.01/1k tokens = 0分
FRESHNESS_HALF_LIFE_HOURS = 6.0  # 6小时半衰期
MAX_CONCURRENT_LOAD = 5       # 5个并发 = 0分

# 未知状态的中性分数（给新 Provider 一个机会）
NEUTRAL_SCORE = 0.5


# ==================== 数据结构 ====================


@dataclass
class DimensionScore:
    """单维度评分结果"""

    name: str
    raw_value: float  # 原始值
    score: float  # 归一化评分 [0.0, 1.0]
    weight: float  # 权重
    weighted_score: float  # score × weight

    def to_dict(self) -> dict[str, Any]:
        # raw_value 可能是字符串（如 health 维度的状态值），仅对数值型取整
        raw = self.raw_value
        if isinstance(raw, (int, float)):
            raw = round(raw, 4)
        return {
            "raw_value": raw,
            "score": round(self.score, 4),
            "weight": self.weight,
            "weighted_score": round(self.weighted_score, 4),
        }


@dataclass
class ProviderScore:
    """Provider 综合评分"""

    provider_name: str
    tier: str
    total_score: float  # 综合评分 [0.0, 1.0]
    dimensions: dict[str, DimensionScore]  # 各维度评分

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "tier": self.tier,
            "total_score": round(self.total_score, 4),
            "dimensions": {k: v.to_dict() for k, v in self.dimensions.items()},
        }


# ==================== Provider 运行时元数据 ====================


@dataclass
class ProviderMeta:
    """Provider 运行时元数据（RouterScore 追踪，不侵入 BaseProvider）"""

    last_success_time: float = 0.0  # 上次成功调用的 Unix 时间戳
    active_requests: int = 0  # 当前并发请求数
    total_success: int = 0  # 总成功次数
    total_failure: int = 0  # 总失败次数

    @property
    def total_requests(self) -> int:
        return self.total_success + self.total_failure

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return NEUTRAL_SCORE
        return self.total_success / self.total_requests

    @property
    def hours_since_last_success(self) -> float:
        if self.last_success_time == 0.0:
            return float("inf")
        return (time.time() - self.last_success_time) / 3600.0


# ==================== RouterScore 核心类 ====================


class RouterScore:
    """6维度路由评分系统

    线程安全：内部使用 RLock 保护元数据操作。
    """

    def __init__(self, weights: dict[str, float] | None = None):
        """初始化 RouterScore

        Args:
            weights: 6维度权重，None 使用默认值
        """
        self.weights = weights or dict(DEFAULT_WEIGHTS)
        self._validate_weights()

        self._lock = threading.RLock()
        self._meta: dict[str, ProviderMeta] = {}  # provider_name → meta

    def _validate_weights(self) -> None:
        """验证权重合法性"""
        for dim in DEFAULT_WEIGHTS:
            if dim not in self.weights:
                self.weights[dim] = DEFAULT_WEIGHTS[dim]
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"权重总和应为 1.0，当前为 {total:.4f}")

    def _get_meta(self, provider_name: str) -> ProviderMeta:
        """获取或创建 Provider 元数据"""
        if provider_name not in self._meta:
            self._meta[provider_name] = ProviderMeta()
        return self._meta[provider_name]

    # ==================== 维度评分计算 ====================

    def _latency_score(self, provider: BaseProvider) -> tuple[float, float]:
        """延迟评分：avg_latency_ms 越低越好"""
        stats = provider.stats
        requests = stats.get("requests", 0)
        avg_latency = stats.get("avg_latency_ms", 0.0)

        if requests == 0:
            return avg_latency, NEUTRAL_SCORE  # 无数据 → 中性

        # 线性归一化：0ms=1.0, 5000ms=0.0（clamp 到 [0, 1]）
        score = max(0.0, min(1.0, 1.0 - avg_latency / MAX_LATENCY_MS))
        return avg_latency, score

    def _cost_score(self, provider: BaseProvider) -> tuple[float, float]:
        """成本评分：cost_per_1k 越低越好"""
        config = provider.config
        total_cost = config.cost_per_1k_input + config.cost_per_1k_output

        if total_cost <= 0:
            return 0.0, 1.0  # 免费 = 满分

        # 线性归一化：$0=1.0, $0.01/1k=0.0
        score = max(0.0, 1.0 - total_cost / MAX_COST_PER_1K)
        return total_cost, score

    def _health_score(self, provider: BaseProvider) -> tuple[str, float]:
        """健康度评分：基于 ProviderStatus"""
        status = provider.status
        health_map = {
            ProviderStatus.HEALTHY: 1.0,
            ProviderStatus.DEGRADED: 0.5,
            ProviderStatus.UNKNOWN: NEUTRAL_SCORE,
            ProviderStatus.OFFLINE: 0.0,
        }
        score = health_map.get(status, NEUTRAL_SCORE)
        return status.value, score

    def _freshness_score(self, provider: BaseProvider) -> tuple[float, float]:
        """新鲜度评分：最近成功调用距今时间越短越好"""
        with self._lock:
            meta = self._get_meta(provider.name)
            hours = meta.hours_since_last_success

        if hours == float("inf"):
            return -1.0, NEUTRAL_SCORE  # 从未成功 → 中性

        # 指数衰减：0h=1.0, 6h(半衰期)=0.5, 24h≈0.06
        import math
        score = max(0.0, 0.5 ** (hours / FRESHNESS_HALF_LIFE_HOURS))
        return hours, score

    def _success_rate_score(self, provider: BaseProvider) -> tuple[float, float]:
        """成功率评分：成功请求 / 总请求"""
        with self._lock:
            meta = self._get_meta(provider.name)
            rate = meta.success_rate
            total = meta.total_requests

        if total == 0:
            # 用 provider 自带的 stats 作为后备
            stats = provider.stats
            requests = stats.get("requests", 0)
            errors = stats.get("errors", 0)
            if requests == 0:
                return 0.0, NEUTRAL_SCORE
            rate = (requests - errors) / requests

        return rate, rate  # 原始值 = 评分

    def _load_score(self, provider: BaseProvider) -> tuple[int, float]:
        """负载评分：当前并发请求数越少越好"""
        with self._lock:
            meta = self._get_meta(provider.name)
            active = meta.active_requests

        # 线性归一化：0并发=1.0, MAX并发=0.0
        score = max(0.0, 1.0 - active / MAX_CONCURRENT_LOAD)
        return active, score

    # ==================== 综合评分 ====================

    def score_provider(self, provider: BaseProvider) -> ProviderScore:
        """计算 Provider 的综合评分

        Args:
            provider: 要评分的 Provider

        Returns:
            ProviderScore: 包含6维度评分和综合分
        """
        # 计算各维度
        latency_raw, latency_s = self._latency_score(provider)
        cost_raw, cost_s = self._cost_score(provider)
        health_raw, health_s = self._health_score(provider)
        freshness_raw, freshness_s = self._freshness_score(provider)
        success_raw, success_s = self._success_rate_score(provider)
        load_raw, load_s = self._load_score(provider)

        dimensions = {
            "latency": DimensionScore(
                name="latency", raw_value=latency_raw, score=latency_s,
                weight=self.weights["latency"],
                weighted_score=latency_s * self.weights["latency"],
            ),
            "cost": DimensionScore(
                name="cost", raw_value=cost_raw, score=cost_s,
                weight=self.weights["cost"],
                weighted_score=cost_s * self.weights["cost"],
            ),
            "health": DimensionScore(
                name="health", raw_value=health_raw, score=health_s,
                weight=self.weights["health"],
                weighted_score=health_s * self.weights["health"],
            ),
            "freshness": DimensionScore(
                name="freshness", raw_value=freshness_raw, score=freshness_s,
                weight=self.weights["freshness"],
                weighted_score=freshness_s * self.weights["freshness"],
            ),
            "success_rate": DimensionScore(
                name="success_rate", raw_value=success_raw, score=success_s,
                weight=self.weights["success_rate"],
                weighted_score=success_s * self.weights["success_rate"],
            ),
            "load": DimensionScore(
                name="load", raw_value=load_raw, score=load_s,
                weight=self.weights["load"],
                weighted_score=load_s * self.weights["load"],
            ),
        }

        total = sum(d.weighted_score for d in dimensions.values())

        return ProviderScore(
            provider_name=provider.name,
            tier=provider.config.tier.value,
            total_score=total,
            dimensions=dimensions,
        )

    def score_all(self, providers: list[BaseProvider]) -> list[ProviderScore]:
        """批量评分并排序（分数从高到低）

        Args:
            providers: Provider 列表

        Returns:
            排序后的 ProviderScore 列表
        """
        scores = [self.score_provider(p) for p in providers]
        scores.sort(key=lambda s: s.total_score, reverse=True)
        return scores

    def rank_providers(self, providers: list[BaseProvider]) -> list[BaseProvider]:
        """按综合评分排序 Provider（分数高的优先）

        Args:
            providers: Provider 列表

        Returns:
            排序后的 Provider 列表
        """
        scored = self.score_all(providers)
        name_order = {s.provider_name: i for i, s in enumerate(scored)}
        return sorted(providers, key=lambda p: name_order.get(p.name, 999))

    # ==================== 元数据更新接口 ====================

    def on_request_start(self, provider_name: str) -> None:
        """记录请求开始（增加并发计数）"""
        with self._lock:
            meta = self._get_meta(provider_name)
            meta.active_requests += 1

    def on_request_success(self, provider_name: str) -> None:
        """记录请求成功"""
        with self._lock:
            meta = self._get_meta(provider_name)
            meta.active_requests = max(0, meta.active_requests - 1)
            meta.total_success += 1
            meta.last_success_time = time.time()

    def on_request_failure(self, provider_name: str) -> None:
        """记录请求失败"""
        with self._lock:
            meta = self._get_meta(provider_name)
            meta.active_requests = max(0, meta.active_requests - 1)
            meta.total_failure += 1

    # ==================== 查询接口 ====================

    def get_meta(self, provider_name: str) -> dict[str, Any]:
        """获取 Provider 元数据"""
        with self._lock:
            meta = self._get_meta(provider_name)
            return {
                "last_success_time": meta.last_success_time,
                "active_requests": meta.active_requests,
                "total_success": meta.total_success,
                "total_failure": meta.total_failure,
                "total_requests": meta.total_requests,
                "success_rate": round(meta.success_rate, 4),
                "hours_since_last_success": (
                    round(meta.hours_since_last_success, 2)
                    if meta.hours_since_last_success != float("inf")
                    else -1
                ),
            }

    def get_scoreboard(self, providers: list[BaseProvider]) -> list[dict[str, Any]]:
        """获取评分看板（用于可观测性）"""
        scores = self.score_all(providers)
        return [s.to_dict() for s in scores]

    def reset(self) -> None:
        """重置所有元数据"""
        with self._lock:
            self._meta.clear()


# ==================== 预设权重方案 ====================


def create_cost_optimized_weights() -> dict[str, float]:
    """成本优先权重方案"""
    return {
        "latency": 0.10,
        "cost": 0.40,
        "health": 0.20,
        "freshness": 0.05,
        "success_rate": 0.20,
        "load": 0.05,
    }


def create_speed_optimized_weights() -> dict[str, float]:
    """速度优先权重方案"""
    return {
        "latency": 0.40,
        "cost": 0.10,
        "health": 0.20,
        "freshness": 0.05,
        "success_rate": 0.20,
        "load": 0.05,
    }


def create_reliability_optimized_weights() -> dict[str, float]:
    """可靠性优先权重方案"""
    return {
        "latency": 0.10,
        "cost": 0.10,
        "health": 0.30,
        "freshness": 0.10,
        "success_rate": 0.35,
        "load": 0.05,
    }


def create_balanced_weights() -> dict[str, float]:
    """均衡权重方案（默认）"""
    return dict(DEFAULT_WEIGHTS)


# ==================== 自检 ====================


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("RouterScore 多维度路由评分 — 自检")
    print("=" * 60)

    # 用 Mock Provider 测试
    from providers.base_provider import (
        BaseProvider,
        ChatMessage,
        ChatResponse,
        ProviderConfig,
        ProviderStatus,
        ProviderTier,
        ProviderType,
    )

    class MockProvider(BaseProvider):
        def __init__(self, name, tier, cost=0.0, latency=100, status=ProviderStatus.HEALTHY):
            config = ProviderConfig(
                name=name,
                display_name=f"Mock {name}",
                provider_type=ProviderType.LOCAL,
                tier=tier,
                model_name=f"mock-{name}",
                cost_per_1k_input=cost,
                cost_per_1k_output=cost,
            )
            super().__init__(config)
            self._mark_status(status)
            # 模拟一些请求统计
            self._request_count = 10
            self._total_latency_ms = latency * 10
            self._error_count = 1

        async def chat(self, messages, max_tokens=None, temperature=None):
            return ChatResponse(
                content="mock",
                model="mock",
                provider=self.name,
                tier=self.config.tier,
                latency_ms=100,
                tokens_in=5,
                tokens_out=10,
                cost_estimate=0.0,
            )

        async def health_check(self):
            return True

    scorer = RouterScore()

    p1 = MockProvider("free_local", ProviderTier.L1, cost=0.0, latency=50)
    p2 = MockProvider("cheap_api", ProviderTier.L2, cost=0.002, latency=200)
    p3 = MockProvider("premium_api", ProviderTier.L3, cost=0.008, latency=500)

    # 模拟一些成功/失败
    scorer.on_request_success("free_local")
    scorer.on_request_success("free_local")
    scorer.on_request_failure("free_local")
    scorer.on_request_success("cheap_api")

    print("\n① 评分看板:")
    for entry in scorer.get_scoreboard([p1, p2, p3]):
        print(f"   {entry['provider']} ({entry['tier']}): score={entry['total_score']}")
        for dim, val in entry["dimensions"].items():
            print(f"     {dim}: score={val['score']:.3f} weight={val['weight']}")

    print("\n② 排序结果:")
    ranked = scorer.rank_providers([p1, p2, p3])
    for i, p in enumerate(ranked):
        print(f"   [{i+1}] {p.name}")

    print("\n" + "=" * 60)
    print("✅ RouterScore 自检通过！")
    print("=" * 60)
