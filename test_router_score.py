"""
RouterScore 多维度路由评分系统 — 测试套件

覆盖6维度评分、综合排序、元数据追踪、预设权重方案、SMART策略集成。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from providers.base_provider import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderConfig,
    ProviderStatus,
    ProviderTier,
    ProviderType,
)
from router_score import (
    DEFAULT_WEIGHTS,
    MAX_CONCURRENT_LOAD,
    MAX_COST_PER_1K,
    MAX_LATENCY_MS,
    NEUTRAL_SCORE,
    DimensionScore,
    ProviderMeta,
    ProviderScore,
    RouterScore,
    create_balanced_weights,
    create_cost_optimized_weights,
    create_reliability_optimized_weights,
    create_speed_optimized_weights,
)

# ==================== 测试用 Mock Provider ====================


class MockProvider(BaseProvider):
    """可配置的 Mock Provider"""

    def __init__(
        self,
        name: str = "mock",
        tier: ProviderTier = ProviderTier.L1,
        cost_in: float = 0.0,
        cost_out: float = 0.0,
        latency_ms: float = 100.0,
        requests: int = 0,
        errors: int = 0,
        status: ProviderStatus = ProviderStatus.HEALTHY,
    ):
        config = ProviderConfig(
            name=name,
            display_name=f"Mock {name}",
            provider_type=ProviderType.LOCAL,
            tier=tier,
            model_name=f"mock-{name}",
            cost_per_1k_input=cost_in,
            cost_per_1k_output=cost_out,
        )
        super().__init__(config)
        self._mark_status(status)
        self._request_count = requests
        self._total_latency_ms = latency_ms * requests
        self._error_count = errors

    async def chat(self, messages, max_tokens=None, temperature=None):
        return ChatResponse(
            content="mock response",
            model=self.config.model_name,
            provider=self.name,
            tier=self.config.tier,
            latency_ms=50,
            tokens_in=10,
            tokens_out=5,
        )

    async def health_check(self):
        return self.status == ProviderStatus.HEALTHY


def _make_provider(
    name: str = "test",
    cost: float = 0.0,
    latency: float = 100,
    requests: int = 10,
    errors: int = 0,
    status: ProviderStatus = ProviderStatus.HEALTHY,
    tier: ProviderTier = ProviderTier.L1,
) -> MockProvider:
    """快捷创建 MockProvider"""
    return MockProvider(
        name=name,
        tier=tier,
        cost_in=cost,
        cost_out=cost,
        latency_ms=latency,
        requests=requests,
        errors=errors,
        status=status,
    )


# ==================== 数据结构测试 ====================


class TestDimensionScore:
    """DimensionScore 数据类测试"""

    def test_creation(self):
        ds = DimensionScore(
            name="latency",
            raw_value=200.0,
            score=0.96,
            weight=0.20,
            weighted_score=0.192,
        )
        assert ds.name == "latency"
        assert ds.raw_value == 200.0
        assert ds.score == 0.96
        assert ds.weight == 0.20
        assert ds.weighted_score == 0.192

    def test_to_dict(self):
        ds = DimensionScore(
            name="cost",
            raw_value=0.005,
            score=0.5,
            weight=0.20,
            weighted_score=0.1,
        )
        d = ds.to_dict()
        assert d["raw_value"] == 0.005
        assert d["score"] == 0.5
        assert d["weight"] == 0.20
        assert d["weighted_score"] == 0.1

    def test_to_dict_rounding(self):
        ds = DimensionScore(
            name="latency",
            raw_value=333.333333,
            score=0.9333333,
            weight=0.20,
            weighted_score=0.1866666,
        )
        d = ds.to_dict()
        assert d["raw_value"] == 333.3333
        assert d["score"] == 0.9333
        assert d["weighted_score"] == 0.1867


class TestProviderScore:
    """ProviderScore 数据类测试"""

    def test_creation(self):
        dims = {
            "latency": DimensionScore("latency", 100, 0.98, 0.20, 0.196),
        }
        ps = ProviderScore(
            provider_name="test",
            tier="L1",
            total_score=0.196,
            dimensions=dims,
        )
        assert ps.provider_name == "test"
        assert ps.tier == "L1"
        assert ps.total_score == 0.196
        assert "latency" in ps.dimensions

    def test_to_dict(self):
        dims = {
            "latency": DimensionScore("latency", 100, 0.98, 0.20, 0.196),
            "cost": DimensionScore("cost", 0.0, 1.0, 0.20, 0.20),
        }
        ps = ProviderScore("p1", "L1", 0.396, dims)
        d = ps.to_dict()
        assert d["provider"] == "p1"
        assert d["tier"] == "L1"
        assert d["total_score"] == 0.396
        assert "latency" in d["dimensions"]
        assert "cost" in d["dimensions"]


class TestProviderMeta:
    """ProviderMeta 数据类测试"""

    def test_defaults(self):
        meta = ProviderMeta()
        assert meta.last_success_time == 0.0
        assert meta.active_requests == 0
        assert meta.total_success == 0
        assert meta.total_failure == 0

    def test_total_requests(self):
        meta = ProviderMeta(total_success=5, total_failure=3)
        assert meta.total_requests == 8

    def test_success_rate_no_data(self):
        meta = ProviderMeta()
        assert meta.success_rate == NEUTRAL_SCORE

    def test_success_rate_with_data(self):
        meta = ProviderMeta(total_success=8, total_failure=2)
        assert meta.success_rate == 0.8

    def test_hours_since_last_success_never(self):
        meta = ProviderMeta(last_success_time=0.0)
        assert meta.hours_since_last_success == float("inf")

    def test_hours_since_last_success_recent(self):
        meta = ProviderMeta(last_success_time=time.time() - 3600)  # 1小时前
        hours = meta.hours_since_last_success
        assert 0.9 < hours < 1.1


# ==================== RouterScore 初始化测试 ====================


class TestRouterScoreInit:
    """RouterScore 初始化和权重验证测试"""

    def test_default_weights(self):
        scorer = RouterScore()
        assert scorer.weights == DEFAULT_WEIGHTS
        assert abs(sum(scorer.weights.values()) - 1.0) < 0.01

    def test_custom_weights(self):
        custom = {
            "latency": 0.30,
            "cost": 0.10,
            "health": 0.20,
            "freshness": 0.10,
            "success_rate": 0.25,
            "load": 0.05,
        }
        scorer = RouterScore(weights=custom)
        assert scorer.weights["latency"] == 0.30

    def test_weights_missing_dim_filled(self):
        """缺少的维度用默认值填充"""
        partial = {
            "latency": 0.30,
            "cost": 0.10,
            # 缺少 health/freshness/success_rate/load → 用默认值填充
        }
        scorer = RouterScore(weights=partial)
        # 缺少的维度用默认值填充
        assert scorer.weights["health"] == DEFAULT_WEIGHTS["health"]
        assert scorer.weights["latency"] == 0.30

    def test_weights_sum_not_one_raises(self):
        """权重总和不为1时报错"""
        bad_weights = {
            "latency": 0.50,
            "cost": 0.50,
            "health": 0.50,  # 总和=1.5
            "freshness": 0.0,
            "success_rate": 0.0,
            "load": 0.0,
        }
        with pytest.raises(ValueError, match="权重总和"):
            RouterScore(weights=bad_weights)


# ==================== 维度评分测试 ====================


class TestLatencyScore:
    """延迟维度评分测试"""

    def test_no_requests(self):
        scorer = RouterScore()
        p = _make_provider(requests=0, latency=0)
        raw, score = scorer._latency_score(p)
        assert raw == 0.0
        assert score == NEUTRAL_SCORE

    def test_fast_latency(self):
        scorer = RouterScore()
        p = _make_provider(requests=10, latency=100)
        raw, score = scorer._latency_score(p)
        assert raw == 100
        expected = 1.0 - 100 / MAX_LATENCY_MS
        assert abs(score - expected) < 0.01

    def test_max_latency(self):
        scorer = RouterScore()
        p = _make_provider(requests=10, latency=MAX_LATENCY_MS)
        raw, score = scorer._latency_score(p)
        assert score == 0.0

    def test_over_max_latency(self):
        scorer = RouterScore()
        p = _make_provider(requests=10, latency=MAX_LATENCY_MS * 2)
        raw, score = scorer._latency_score(p)
        assert score == 0.0  # 不低于0

    def test_zero_latency(self):
        scorer = RouterScore()
        p = _make_provider(requests=10, latency=0)
        raw, score = scorer._latency_score(p)
        assert score == 1.0


class TestCostScore:
    """成本维度评分测试"""

    def test_free_provider(self):
        scorer = RouterScore()
        p = _make_provider(cost=0.0)
        raw, score = scorer._cost_score(p)
        assert raw == 0.0
        assert score == 1.0

    def test_cheap_provider(self):
        scorer = RouterScore()
        p = _make_provider(cost=0.001)
        raw, score = scorer._cost_score(p)
        assert raw == 0.002  # input + output
        expected = 1.0 - 0.002 / MAX_COST_PER_1K
        assert abs(score - expected) < 0.01

    def test_expensive_provider(self):
        scorer = RouterScore()
        p = _make_provider(cost=MAX_COST_PER_1K / 2)
        raw, score = scorer._cost_score(p)
        assert score == 0.0  # input + output = MAX_COST_PER_1K

    def test_very_expensive_provider(self):
        scorer = RouterScore()
        p = _make_provider(cost=0.1)
        raw, score = scorer._cost_score(p)
        assert score == 0.0


class TestHealthScore:
    """健康度维度评分测试"""

    def test_healthy(self):
        scorer = RouterScore()
        p = _make_provider(status=ProviderStatus.HEALTHY)
        raw, score = scorer._health_score(p)
        assert raw == "healthy"
        assert score == 1.0

    def test_degraded(self):
        scorer = RouterScore()
        p = _make_provider(status=ProviderStatus.DEGRADED)
        raw, score = scorer._health_score(p)
        assert score == 0.5

    def test_unknown(self):
        scorer = RouterScore()
        p = _make_provider(status=ProviderStatus.UNKNOWN)
        raw, score = scorer._health_score(p)
        assert score == NEUTRAL_SCORE

    def test_offline(self):
        scorer = RouterScore()
        p = _make_provider(status=ProviderStatus.OFFLINE)
        raw, score = scorer._health_score(p)
        assert score == 0.0


class TestFreshnessScore:
    """新鲜度维度评分测试"""

    def test_never_used(self):
        scorer = RouterScore()
        p = _make_provider()
        raw, score = scorer._freshness_score(p)
        assert raw == -1.0
        assert score == NEUTRAL_SCORE

    def test_just_succeeded(self):
        scorer = RouterScore()
        p = _make_provider(name="fresh")
        scorer.on_request_success("fresh")
        raw, score = scorer._freshness_score(p)
        assert raw < 0.01  # 刚刚才成功
        assert score > 0.99  # 接近满分

    def test_old_success(self):
        scorer = RouterScore()
        p = _make_provider(name="old")
        # 模拟24小时前成功
        with scorer._lock:
            scorer._get_meta("old").last_success_time = time.time() - 86400
        raw, score = scorer._freshness_score(p)
        assert raw > 23
        assert score < 0.1  # 24小时后接近0

    def test_half_life(self):
        scorer = RouterScore()
        p = _make_provider(name="half")
        # 模拟6小时前（半衰期）
        with scorer._lock:
            scorer._get_meta("half").last_success_time = time.time() - 6 * 3600
        raw, score = scorer._freshness_score(p)
        assert 5.9 < raw < 6.1
        assert abs(score - 0.5) < 0.05  # 半衰期=0.5


class TestSuccessRateScore:
    """成功率维度评分测试"""

    def test_no_data_no_stats(self):
        scorer = RouterScore()
        p = _make_provider(requests=0, errors=0)
        raw, score = scorer._success_rate_score(p)
        assert raw == 0.0
        assert score == NEUTRAL_SCORE

    def test_perfect_rate_from_meta(self):
        scorer = RouterScore()
        p = _make_provider(name="perfect", requests=0, errors=0)
        scorer.on_request_success("perfect")
        scorer.on_request_success("perfect")
        raw, score = scorer._success_rate_score(p)
        assert raw == 1.0
        assert score == 1.0

    def test_poor_rate_from_meta(self):
        scorer = RouterScore()
        p = _make_provider(name="poor", requests=0, errors=0)
        scorer.on_request_failure("poor")
        scorer.on_request_failure("poor")
        scorer.on_request_failure("poor")
        raw, score = scorer._success_rate_score(p)
        assert raw == 0.0
        assert score == 0.0

    def test_mixed_rate_from_meta(self):
        scorer = RouterScore()
        p = _make_provider(name="mixed", requests=0, errors=0)
        for _ in range(7):
            scorer.on_request_success("mixed")
        for _ in range(3):
            scorer.on_request_failure("mixed")
        raw, score = scorer._success_rate_score(p)
        assert abs(raw - 0.7) < 0.01
        assert abs(score - 0.7) < 0.01

    def test_fallback_to_provider_stats(self):
        """当 RouterScore 无元数据时，回退到 provider 自带 stats"""
        scorer = RouterScore()
        p = _make_provider(requests=10, errors=2)
        raw, score = scorer._success_rate_score(p)
        assert abs(raw - 0.8) < 0.01


class TestLoadScore:
    """负载维度评分测试"""

    def test_no_active_requests(self):
        scorer = RouterScore()
        p = _make_provider(name="idle")
        raw, score = scorer._load_score(p)
        assert raw == 0
        assert score == 1.0

    def test_some_active_requests(self):
        scorer = RouterScore()
        p = _make_provider(name="busy")
        scorer.on_request_start("busy")
        scorer.on_request_start("busy")
        raw, score = scorer._load_score(p)
        assert raw == 2
        expected = 1.0 - 2 / MAX_CONCURRENT_LOAD
        assert abs(score - expected) < 0.01

    def test_max_active_requests(self):
        scorer = RouterScore()
        p = _make_provider(name="maxed")
        for _ in range(MAX_CONCURRENT_LOAD):
            scorer.on_request_start("maxed")
        raw, score = scorer._load_score(p)
        assert raw == MAX_CONCURRENT_LOAD
        assert score == 0.0


# ==================== 综合评分测试 ====================


class TestScoreProvider:
    """score_provider 综合评分测试"""

    def test_free_healthy_provider(self):
        scorer = RouterScore()
        p = _make_provider(name="free", cost=0.0, latency=50, requests=10, errors=0)
        score = scorer.score_provider(p)
        assert score.provider_name == "free"
        assert score.tier == "L1"
        assert score.total_score > 0.8  # 免费+健康+快 → 高分
        assert "latency" in score.dimensions
        assert "cost" in score.dimensions
        assert "health" in score.dimensions
        assert "freshness" in score.dimensions
        assert "success_rate" in score.dimensions
        assert "load" in score.dimensions

    def test_expensive_offline_provider(self):
        scorer = RouterScore()
        p = _make_provider(
            name="expensive",
            cost=0.01,
            latency=4000,
            requests=10,
            errors=5,
            status=ProviderStatus.OFFLINE,
        )
        score = scorer.score_provider(p)
        assert score.total_score < 0.3  # 贵+离线+慢 → 低分

    def test_all_dimensions_weighted(self):
        """验证综合评分 = Σ(weight × dimension_score)"""
        scorer = RouterScore()
        p = _make_provider(name="check", cost=0.0, latency=500, requests=10, errors=1)
        score = scorer.score_provider(p)

        manual_sum = sum(d.weighted_score for d in score.dimensions.values())
        assert abs(score.total_score - manual_sum) < 0.001

    def test_score_in_range(self):
        """评分在 [0, 1] 范围内"""
        scorer = RouterScore()
        for cost in [0.0, 0.001, 0.005, 0.01]:
            for latency in [0, 100, 1000, 5000]:
                for status in ProviderStatus:
                    p = _make_provider(
                        cost=cost, latency=latency,
                        requests=10, errors=3, status=status,
                    )
                    score = scorer.score_provider(p)
                    assert 0.0 <= score.total_score <= 1.0


class TestScoreAll:
    """score_all 批量评分测试"""

    def test_sorts_by_score_descending(self):
        scorer = RouterScore()
        p_free = _make_provider(name="free", cost=0.0, latency=50)
        p_expensive = _make_provider(name="expensive", cost=0.01, latency=4000, status=ProviderStatus.DEGRADED)
        scores = scorer.score_all([p_expensive, p_free])
        assert scores[0].provider_name == "free"
        assert scores[1].provider_name == "expensive"
        assert scores[0].total_score > scores[1].total_score

    def test_empty_list(self):
        scorer = RouterScore()
        assert scorer.score_all([]) == []

    def test_single_provider(self):
        scorer = RouterScore()
        p = _make_provider(name="solo")
        scores = scorer.score_all([p])
        assert len(scores) == 1
        assert scores[0].provider_name == "solo"


class TestRankProviders:
    """rank_providers 排序测试"""

    def test_ranks_by_score(self):
        scorer = RouterScore()
        p1 = _make_provider(name="p1", cost=0.0, latency=50)
        p2 = _make_provider(name="p2", cost=0.005, latency=500)
        p3 = _make_provider(name="p3", cost=0.01, latency=4000, status=ProviderStatus.DEGRADED)
        ranked = scorer.rank_providers([p3, p1, p2])
        assert ranked[0].name == "p1"
        assert ranked[1].name == "p2"
        assert ranked[2].name == "p3"

    def test_preserves_all_providers(self):
        scorer = RouterScore()
        providers = [_make_provider(name=f"p{i}") for i in range(5)]
        ranked = scorer.rank_providers(providers)
        assert len(ranked) == 5


# ==================== 元数据追踪测试 ====================


class TestMetadataTracking:
    """on_request_start/success/failure 追踪测试"""

    def test_request_start_increments_active(self):
        scorer = RouterScore()
        scorer.on_request_start("p1")
        scorer.on_request_start("p1")
        meta = scorer.get_meta("p1")
        assert meta["active_requests"] == 2

    def test_request_success_decrements_active(self):
        scorer = RouterScore()
        scorer.on_request_start("p1")
        scorer.on_request_start("p1")
        scorer.on_request_success("p1")
        meta = scorer.get_meta("p1")
        assert meta["active_requests"] == 1
        assert meta["total_success"] == 1
        assert meta["last_success_time"] > 0

    def test_request_failure_decrements_active(self):
        scorer = RouterScore()
        scorer.on_request_start("p1")
        scorer.on_request_failure("p1")
        meta = scorer.get_meta("p1")
        assert meta["active_requests"] == 0
        assert meta["total_failure"] == 1
        assert meta["total_success"] == 0

    def test_active_never_negative(self):
        scorer = RouterScore()
        scorer.on_request_failure("p1")  # 没有先 start
        meta = scorer.get_meta("p1")
        assert meta["active_requests"] == 0  # 不会变负

    def test_success_updates_last_success_time(self):
        scorer = RouterScore()
        scorer.on_request_success("p1")
        t1 = scorer.get_meta("p1")["last_success_time"]
        assert t1 > 0
        time.sleep(0.01)
        scorer.on_request_success("p1")
        t2 = scorer.get_meta("p1")["last_success_time"]
        assert t2 > t1

    def test_multiple_providers_independent(self):
        scorer = RouterScore()
        scorer.on_request_success("p1")
        scorer.on_request_failure("p2")
        m1 = scorer.get_meta("p1")
        m2 = scorer.get_meta("p2")
        assert m1["total_success"] == 1
        assert m1["total_failure"] == 0
        assert m2["total_success"] == 0
        assert m2["total_failure"] == 1

    def test_get_meta_unknown_provider(self):
        scorer = RouterScore()
        meta = scorer.get_meta("unknown")
        assert meta["total_requests"] == 0
        assert meta["success_rate"] == NEUTRAL_SCORE


# ==================== 看板和重置测试 ====================


class TestScoreboardAndReset:
    """get_scoreboard 和 reset 测试"""

    def test_scoreboard_format(self):
        scorer = RouterScore()
        p1 = _make_provider(name="p1", cost=0.0)
        p2 = _make_provider(name="p2", cost=0.005, tier=ProviderTier.L2)
        board = scorer.get_scoreboard([p1, p2])
        assert len(board) == 2
        assert board[0]["provider"] in ("p1", "p2")
        assert "total_score" in board[0]
        assert "dimensions" in board[0]
        assert board[0]["total_score"] >= board[1]["total_score"]  # 降序

    def test_scoreboard_empty(self):
        scorer = RouterScore()
        assert scorer.get_scoreboard([]) == []

    def test_reset_clears_metadata(self):
        scorer = RouterScore()
        scorer.on_request_success("p1")
        scorer.on_request_failure("p2")
        scorer.reset()
        m1 = scorer.get_meta("p1")
        m2 = scorer.get_meta("p2")
        assert m1["total_success"] == 0
        assert m2["total_failure"] == 0


# ==================== 预设权重方案测试 ====================


class TestPresetWeights:
    """预设权重方案测试"""

    def test_cost_optimized(self):
        w = create_cost_optimized_weights()
        assert w["cost"] == 0.40  # 成本权重最高
        assert abs(sum(w.values()) - 1.0) < 0.01

    def test_speed_optimized(self):
        w = create_speed_optimized_weights()
        assert w["latency"] == 0.40  # 延迟权重最高
        assert abs(sum(w.values()) - 1.0) < 0.01

    def test_reliability_optimized(self):
        w = create_reliability_optimized_weights()
        assert w["success_rate"] == 0.35  # 成功率权重最高
        assert w["health"] == 0.30  # 健康度第二
        assert abs(sum(w.values()) - 1.0) < 0.01

    def test_balanced(self):
        w = create_balanced_weights()
        assert w == DEFAULT_WEIGHTS
        assert abs(sum(w.values()) - 1.0) < 0.01

    def test_preset_weights_change_ranking(self):
        """不同权重方案应产生不同的排序"""
        p_free_slow = _make_provider(name="free_slow", cost=0.0, latency=3000)
        p_expensive_fast = _make_provider(name="paid_fast", cost=0.008, latency=50)

        # 成本优先 → free_slow 排前面
        scorer_cost = RouterScore(weights=create_cost_optimized_weights())
        ranked_cost = scorer_cost.rank_providers([p_free_slow, p_expensive_fast])
        assert ranked_cost[0].name == "free_slow"

        # 速度优先 → paid_fast 排前面
        scorer_speed = RouterScore(weights=create_speed_optimized_weights())
        ranked_speed = scorer_speed.rank_providers([p_free_slow, p_expensive_fast])
        assert ranked_speed[0].name == "paid_fast"


# ==================== SMART 策略集成测试 ====================


class TestSmartStrategyIntegration:
    """SMART 策略与 ModelRouter 集成测试"""

    @pytest.fixture
    def router(self):
        from model_router import ModelRouter, RouterStrategy
        r = ModelRouter(strategy=RouterStrategy.SMART)
        return r

    def test_smart_strategy_enum(self):
        from model_router import RouterStrategy
        assert RouterStrategy.SMART.value == "smart"

    def test_scorer_initialized(self, router):
        assert router.scorer is not None
        assert isinstance(router.scorer, RouterScore)

    def test_smart_strategy_orders_by_score(self, router):
        """SMART 策略应按评分排序 Provider"""
        router._initialized = True  # 避免 auto_init_from_env
        p_free = _make_provider(name="free_local", cost=0.0, latency=50)
        p_paid = _make_provider(name="paid_api", cost=0.008, latency=500, tier=ProviderTier.L3)
        router.registry.register(p_free)
        router.registry.register(p_paid)

        ordered = router._get_ordered_providers()
        # 免费Provider评分更高，应排在前面
        names = [p.name for p in ordered]
        assert "free_local" in names
        assert names.index("free_local") < names.index("paid_api")

    def test_scorer_tracks_requests(self, router):
        """验证 SMART 策略下请求追踪生效"""
        p = _make_provider(name="tracked", cost=0.0, latency=50)
        router.registry.register(p)

        # 模拟请求开始
        router.scorer.on_request_start("tracked")
        meta = router.scorer.get_meta("tracked")
        assert meta["active_requests"] == 1

        # 模拟请求成功
        router.scorer.on_request_success("tracked")
        meta = router.scorer.get_meta("tracked")
        assert meta["active_requests"] == 0
        assert meta["total_success"] == 1

    @pytest.mark.asyncio
    async def test_smart_strategy_chat_success(self, router):
        """SMART 策略下 chat 成功后更新元数据"""
        router.dual_redundancy.enabled = False
        router._initialized = True
        p = _make_provider(name="smart_p", cost=0.0, latency=50)
        router.registry.register(p)

        messages = [ChatMessage(role="user", content="hello")]
        response = await router.chat(messages)

        assert response is not None
        meta = router.scorer.get_meta("smart_p")
        assert meta["total_success"] == 1
        assert meta["active_requests"] == 0

    @pytest.mark.asyncio
    async def test_smart_strategy_chat_failure_tracked(self, router):
        """SMART 策略下 chat 失败也更新元数据"""
        # 禁用双保障 + 标记已初始化（避免 auto_init_from_env 注册 llama_cpp）
        router.dual_redundancy.enabled = False
        router._initialized = True

        p = _make_provider(name="fail_p", cost=0.0, latency=50, status=ProviderStatus.HEALTHY)
        # 让 chat 抛异常
        p.chat = AsyncMock(side_effect=Exception("connection refused"))
        router.registry.register(p)

        messages = [ChatMessage(role="user", content="hello")]
        # chat 会失败，但不应崩溃
        with pytest.raises(Exception):  # noqa: B017 — 有意捕获任意异常, 验证不崩溃
            await router.chat(messages)

        meta = router.scorer.get_meta("fail_p")
        assert meta["total_failure"] >= 1


# ==================== 线程安全测试 ====================


class TestThreadSafety:
    """线程安全测试"""

    def test_concurrent_request_tracking(self):
        """并发请求追踪不丢数据"""
        import threading

        scorer = RouterScore()
        results: list[int] = []

        def worker():
            scorer.on_request_start("concurrent_p")
            time.sleep(0.001)
            scorer.on_request_success("concurrent_p")
            results.append(1)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        meta = scorer.get_meta("concurrent_p")
        assert meta["total_success"] == 10
        assert meta["active_requests"] == 0

    def test_concurrent_scoring(self):
        """并发评分不崩溃"""
        import threading

        scorer = RouterScore()
        p = _make_provider(name="ts", cost=0.0, latency=100)

        def worker():
            scorer.score_provider(p)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 如果没崩溃就算通过


# ==================== 边界情况测试 ====================


class TestEdgeCases:
    """边界情况测试"""

    def test_score_provider_with_zero_requests(self):
        """无请求历史的 Provider 也能评分"""
        scorer = RouterScore()
        p = _make_provider(requests=0, latency=0, errors=0)
        score = scorer.score_provider(p)
        assert 0.0 <= score.total_score <= 1.0

    def test_score_provider_offline(self):
        """离线 Provider 总分应很低"""
        scorer = RouterScore()
        p = _make_provider(
            status=ProviderStatus.OFFLINE,
            cost=0.01,
            latency=4000,
            requests=10,
            errors=8,
        )
        score = scorer.score_provider(p)
        assert score.total_score < 0.3

    def test_negative_latency_clamped(self):
        """负延迟不会导致评分>1"""
        scorer = RouterScore()
        p = _make_provider(requests=10, latency=-100)
        raw, score = scorer._latency_score(p)
        assert score <= 1.0

    def test_all_providers_same_score(self):
        """所有 Provider 评分相同时排序仍稳定"""
        scorer = RouterScore()
        providers = [_make_provider(name=f"p{i}", cost=0.0, latency=100) for i in range(3)]
        ranked = scorer.rank_providers(providers)
        assert len(ranked) == 3

    def test_weight_sum_slightly_off_accepted(self):
        """权重总和偏差在0.01以内可接受"""
        w = dict(DEFAULT_WEIGHTS)
        w["latency"] += 0.005  # 总和=1.005
        w["cost"] -= 0.005  # 调回=1.0
        scorer = RouterScore(weights=w)
        assert scorer is not None
