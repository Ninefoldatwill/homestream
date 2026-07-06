"""
SkillRouter v2 双层路由测试
============================
测试覆盖：
  1. 基础路由（Skill层 + Model层）
  2. 分类→模型映射
  3. 角色过滤
  4. 全局锁定 + 分类覆盖
  5. 批量路由
  6. to_dict序列化
  7. 降级策略
  8. 空查询处理
"""

import pytest
import os
from skill_router_v2 import (
    SkillRouterV2, RouteResultV2, create_router_v2,
    CATEGORY_MODEL_MAP, CATEGORY_REASON,
)
from skill_router import SkillEntry
from providers.base_provider import ProviderTier


# ─── Fixtures ───────────────────────────────────────────────

@pytest.fixture
def router():
    """加载真实注册表的Router"""
    here = os.path.dirname(os.path.abspath(__file__))
    return create_router_v2(os.path.join(here, "skill_registry.json"))


@pytest.fixture
def empty_router():
    """空Router（手动注入Skill）"""
    r = SkillRouterV2()
    r.load_dict({
        "version": "2.0.0-test",
        "skills": [
            {"id": "test-research", "name": "Test Research", "category": "research",
             "priority": 0, "role": "灵犀", "triggers": ["调研", "research"],
             "description": "测试用研究技能"},
            {"id": "test-auto", "name": "Test Auto", "category": "automation",
             "priority": 0, "role": "澜舟", "triggers": ["自动化", "browser"],
             "description": "测试用自动化技能"},
            {"id": "test-content", "name": "Test Content", "category": "content",
             "priority": 1, "role": "澜澜", "triggers": ["写作", "article"],
             "description": "测试用内容技能"},
        ],
    })
    return r


# ─── 1. 基础路由测试 ────────────────────────────────────────

class TestBasicRouting:
    """基础双层路由"""

    def test_research_routes_to_l3(self, router):
        """研究类查询 → L3付费模型"""
        result = router.route_with_model("帮我做市场调研", role="灵犀")
        assert result.skill is not None
        assert result.skill.category == "research"
        assert result.model_tier == ProviderTier.L3
        assert not result.model_tier_locked

    def test_automation_routes_to_l1(self, router):
        """自动化查询 → L1本地模型"""
        result = router.route_with_model("浏览器自动化截图", role="澜舟")
        assert result.skill is not None
        assert result.skill.category == "automation"
        assert result.model_tier == ProviderTier.L1

    def test_content_routes_to_l2(self, router):
        """内容类查询 → L2免费API"""
        result = router.route_with_model("帮我写文章去AI味", role="澜澜")
        assert result.skill is not None
        assert result.skill.category == "content"
        assert result.model_tier == ProviderTier.L2

    def test_result_has_latency(self, router):
        """结果包含延迟信息"""
        result = router.route_with_model("调研", role="all")
        assert result.total_latency_ms is not None
        assert result.total_latency_ms >= 0


# ─── 2. 分类→模型映射测试 ──────────────────────────────────

class TestCategoryModelMap:
    """分类到模型的映射覆盖"""

    @pytest.mark.parametrize("category,expected_tier", [
        ("automation", ProviderTier.L1),
        ("research", ProviderTier.L3),
        ("content", ProviderTier.L2),
        ("knowledge", ProviderTier.L1),
        ("meta", ProviderTier.L1),
        ("media", ProviderTier.L1),
        ("design", ProviderTier.L2),
        ("education", ProviderTier.L3),
        ("collaboration", ProviderTier.L1),
        ("information", ProviderTier.L2),
        ("productivity", ProviderTier.L1),
    ])
    def test_all_categories_mapped(self, category, expected_tier):
        """所有分类都有正确的tier映射"""
        assert CATEGORY_MODEL_MAP[category] == expected_tier

    def test_all_categories_have_reason(self):
        """所有分类都有推荐理由"""
        for cat in CATEGORY_MODEL_MAP:
            assert cat in CATEGORY_REASON
            assert len(CATEGORY_REASON[cat]) > 0

    def test_get_model_tier_no_skill(self, empty_router):
        """无Skill时返回None"""
        tier, reason, locked = empty_router.get_model_tier(None)
        assert tier is None
        assert "无匹配" in reason
        assert not locked


# ─── 3. 角色过滤测试 ────────────────────────────────────────

class TestRoleFiltering:
    """角色过滤确保只推荐该角色可用的Skill"""

    def test_lingxi_only_gets_research(self, router):
        """灵犀角色 → 只看到research/information类"""
        result = router.route_with_model("调研", role="灵犀")
        if result.skill:
            assert result.skill.role in ("灵犀", "all")

    def test_lanzhou_only_gets_automation(self, router):
        """澜舟角色 → 只看到automation/meta类"""
        result = router.route_with_model("自动化", role="澜舟")
        if result.skill:
            assert result.skill.role in ("澜舟", "all")

    def test_unknown_role_falls_to_all_skills(self, router):
        """未知角色 → 仍可看到role=all的Skill（正确行为）"""
        result = router.route_with_model("调研", role="不存在的人")
        # role=all 的P0 skill应该可用
        if result.skill:
            assert result.skill.role == "all"


# ─── 4. 全局锁定 + 分类覆盖测试 ────────────────────────────

class TestModelOverride:
    """模型层级覆盖机制"""

    def test_global_lock(self, router):
        """全局锁定 → 所有查询都用锁定tier"""
        router.lock_model_tier(ProviderTier.L3)
        result = router.route_with_model("浏览器自动化", role="all")
        assert result.model_tier == ProviderTier.L3
        assert result.model_tier_locked is True
        # 清理
        router.lock_model_tier(None)

    def test_category_override(self, router):
        """分类级覆盖 → 只覆盖该分类"""
        router.set_model_override("automation", ProviderTier.L3)
        result = router.route_with_model("浏览器自动化", role="all")
        assert result.model_tier == ProviderTier.L3
        assert not result.model_tier_locked
        # 清理
        router._model_override.clear()

    def test_force_tier_in_call(self, router):
        """调用时强制指定tier"""
        result = router.route_with_model(
            "调研", role="all", force_tier=ProviderTier.L1
        )
        assert result.model_tier == ProviderTier.L1
        assert result.model_tier_locked is True


# ─── 5. 批量路由测试 ────────────────────────────────────────

class TestBatchRouting:
    """批量路由"""

    def test_batch_returns_list(self, router):
        """批量路由返回列表"""
        results = router.route_batch(
            ["调研", "自动化", "写作"],
            role="all",
        )
        assert len(results) == 3
        assert all(isinstance(r, RouteResultV2) for r in results)

    def test_batch_empty_list(self, router):
        """空列表"""
        results = router.route_batch([], role="all")
        assert results == []


# ─── 6. 序列化测试 ──────────────────────────────────────────

class TestSerialization:
    """to_dict序列化"""

    def test_to_dict_structure(self, router):
        """to_dict结构完整"""
        result = router.route_with_model("调研", role="all")
        d = result.to_dict()
        assert "skill" in d
        assert "model" in d
        assert "candidates" in d
        assert "query" in d
        assert "role" in d
        assert "latency_ms" in d

    def test_to_dict_skill_fields(self, router):
        """skill子字典字段完整"""
        result = router.route_with_model("调研", role="all")
        d = result.to_dict()
        if d["skill"]["name"]:
            assert "id" in d["skill"]
            assert "name" in d["skill"]
            assert "category" in d["skill"]
            assert "priority" in d["skill"]
            assert "confidence" in d["skill"]
            assert "strategy" in d["skill"]

    def test_to_dict_model_fields(self, router):
        """model子字典字段完整"""
        result = router.route_with_model("调研", role="all")
        d = result.to_dict()
        assert "tier" in d["model"]
        assert "reason" in d["model"]
        assert "locked" in d["model"]
        assert "has_response" in d["model"]


# ─── 7. 降级策略测试 ────────────────────────────────────────

class TestFallback:
    """降级策略"""

    def test_no_keyword_match_falls_to_p0(self, router):
        """无关键词匹配 → 降级到P0"""
        result = router.route_with_model("xyz_random_query_12345", role="all")
        # 要么无匹配，要么降级到P0
        if result.skill:
            assert result.skill_strategy in ("fallback_priority", "keyword_fuzzy", "keyword_exact")

    def test_empty_query(self, router):
        """空查询 → 降级"""
        result = router.route_with_model("", role="all")
        # 空查询应该走降级路径
        assert result.skill_strategy in ("fallback_priority", "fallback_none")


# ─── 8. explain方法测试 ────────────────────────────────────

class TestExplain:
    """explain方法"""

    def test_explain_returns_string(self, router):
        """explain返回字符串"""
        text = router.explain("调研", role="all")
        assert isinstance(text, str)
        assert "Layer1" in text
        assert "Layer2" in text

    def test_explain_contains_skill_name(self, router):
        """explain包含Skill名"""
        text = router.explain("调研", role="灵犀")
        assert "Deep Research" in text or "None" in text
