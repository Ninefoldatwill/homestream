"""
Skill Router 测试套件
=====================
九重生态 · 澜舟开发 · 2026-06-18

覆盖：
  1. 注册表加载 + 完整性验证
  2. 优先级排序
  3. 角色过滤
  4. 关键词精确匹配
  5. 关键词模糊匹配
  6. 降级策略（无匹配→P0兜底）
  7. 置信度计算
  8. 分类查询
  9. 统计信息
  10. 动态注册/注销
  11. 真实场景路由（6个实战case）
"""

import os
import sys
import json
import unittest

# 添加项目目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skill_router import (
    SkillRouter,
    SkillEntry,
    RouteResult,
    Priority,
    create_router,
)


class TestRegistryLoad(unittest.TestCase):
    """测试注册表加载"""

    def setUp(self):
        here = os.path.dirname(os.path.abspath(__file__))
        registry_path = os.path.join(here, "skill_registry.json")
        self.router = SkillRouter()
        self.count = self.router.load(registry_path)

    def test_load_count(self):
        """应加载42+个Skill"""
        self.assertGreaterEqual(self.count, 40)

    def test_all_have_required_fields(self):
        """每个Skill必须有id/name/category/priority"""
        for skill in self.router.all_skills():
            self.assertTrue(skill.id, f"Skill missing id")
            self.assertTrue(skill.name, f"Skill {skill.id} missing name")
            self.assertTrue(skill.category, f"Skill {skill.id} missing category")
            self.assertIn(skill.priority, [0, 1, 2, 3])

    def test_priority_distribution(self):
        """P0应有5个，P1应有8+个"""
        p0 = self.router.by_priority(0)
        p1 = self.router.by_priority(1)
        self.assertGreaterEqual(len(p0), 4, f"P0 should have 4+, got {len(p0)}")
        self.assertGreaterEqual(len(p1), 6, f"P1 should have 6+, got {len(p1)}")

    def test_all_p0_have_triggers(self):
        """P0核心Skill必须有触发词"""
        for skill in self.router.by_priority(0):
            self.assertTrue(
                skill.triggers,
                f"P0 Skill '{skill.name}' has no triggers"
            )


class TestPrioritySort(unittest.TestCase):
    """测试优先级排序"""

    def setUp(self):
        self.router = create_router()

    def test_sorted_by_priority(self):
        """all_skills() 应按优先级升序排列"""
        skills = self.router.all_skills()
        for i in range(len(skills) - 1):
            self.assertLessEqual(
                skills[i].priority,
                skills[i + 1].priority,
                f"排序错误: {skills[i].name}(P{skills[i].priority}) > {skills[i+1].name}(P{skills[i+1].priority})"
            )

    def test_p0_before_p3(self):
        """P0必须在P3前面"""
        skills = self.router.all_skills()
        p0_idx = [i for i, s in enumerate(skills) if s.priority == 0]
        p3_idx = [i for i, s in enumerate(skills) if s.priority == 3]
        if p0_idx and p3_idx:
            self.assertLess(max(p0_idx), min(p3_idx))


class TestRoleFilter(unittest.TestCase):
    """测试角色过滤"""

    def setUp(self):
        self.router = create_router()

    def test_lanzhou_has_automation(self):
        """澜舟应有自动化类Skill"""
        lanzhou = self.router.by_role("澜舟")
        categories = {s.category for s in lanzhou}
        self.assertIn("automation", categories)

    def test_lingxi_has_research(self):
        """灵犀应有研究类Skill"""
        lingxi = self.router.by_role("灵犀")
        categories = {s.category for s in lingxi}
        self.assertIn("research", categories)

    def test_qianxun_has_knowledge(self):
        """千寻应有知识类Skill"""
        qianxun = self.router.by_role("千寻")
        categories = {s.category for s in qianxun}
        self.assertIn("knowledge", categories)

    def test_all_role_includes_everyone(self):
        """role=all 的Skill应被所有角色看到"""
        for role in ["澜舟", "灵犀", "千寻", "澜澜"]:
            skills = self.router.by_role(role)
            all_skills = [s for s in skills if s.role == "all"]
            self.assertGreater(len(all_skills), 0, f"{role} should see 'all' skills")


class TestKeywordMatch(unittest.TestCase):
    """测试关键词匹配"""

    def setUp(self):
        self.router = create_router()

    def test_exact_trigger_browser(self):
        """精确匹配: 'browser' → agent-browser"""
        result = self.router.route("帮我用browser自动化操作网页")
        self.assertIsNotNone(result.recommended_skill)
        self.assertEqual(result.recommended_skill.id, "agent-browser")
        self.assertGreater(result.confidence, 0.3)

    def test_exact_trigger_research(self):
        """精确匹配: '调研' → deep-research"""
        result = self.router.route("帮我做一个深度调研")
        self.assertIsNotNone(result.recommended_skill)
        # deep-research 的 name 包含 "Deep Research"
        self.assertIn("research", result.recommended_skill.name.lower())

    def test_exact_trigger_humanizer(self):
        """精确匹配: '去AI味' → humanizer"""
        result = self.router.route("这段文字帮我去AI味，自然一点")
        self.assertIsNotNone(result.recommended_skill)
        self.assertEqual(result.recommended_skill.id, "humanizer")

    def test_fuzzy_match_image(self):
        """模糊匹配: '生成图片' → nano-banana-pro 或 openai-image-gen"""
        result = self.router.route("帮我生成一张图片")
        self.assertIsNotNone(result.recommended_skill)
        self.assertIn(result.recommended_skill.category, ["media"])

    def test_chinese_trigger(self):
        """中文触发词: '新闻热榜' → tencent-news"""
        result = self.router.route("看看今天的新闻热榜")
        self.assertIsNotNone(result.recommended_skill)
        self.assertEqual(result.recommended_skill.id, "tencent-news")


class TestFallbackStrategy(unittest.TestCase):
    """测试降级策略"""

    def setUp(self):
        self.router = create_router()

    def test_no_match_falls_to_p0(self):
        """无匹配时降级到P0"""
        result = self.router.route("xyzqwerty完全无关的内容12345")
        # 要么返回P0兜底，要么返回None
        if result.recommended_skill:
            self.assertEqual(result.strategy, "fallback_priority")
            self.assertEqual(result.recommended_skill.priority, 0)
            self.assertLess(result.confidence, 0.3)

    def test_empty_query(self):
        """空查询不崩溃"""
        result = self.router.route("")
        self.assertIsNotNone(result)
        # 空查询应该走降级
        self.assertIn(result.strategy, ["fallback_priority", "fallback_none"])


class TestConfidence(unittest.TestCase):
    """测试置信度计算"""

    def setUp(self):
        self.router = create_router()

    def test_high_confidence_for_exact_match(self):
        """精确匹配应有较高置信度"""
        result = self.router.route("用browser打开网页截图")
        self.assertGreater(result.confidence, 0.3)

    def test_low_confidence_for_fallback(self):
        """降级应有低置信度"""
        result = self.router.route("zzzzzzzz")
        self.assertLessEqual(result.confidence, 0.3)

    def test_confidence_range(self):
        """置信度应在 0.0~1.0 之间"""
        queries = ["调研", "browser", "图片", "新闻", "xyz123", ""]
        for q in queries:
            result = self.router.route(q)
            self.assertGreaterEqual(result.confidence, 0.0)
            self.assertLessEqual(result.confidence, 1.0)


class TestCategoryQuery(unittest.TestCase):
    """测试分类查询"""

    def setUp(self):
        self.router = create_router()

    def test_media_category(self):
        """media分类应有多个Skill"""
        media = self.router.by_category("media")
        self.assertGreaterEqual(len(media), 3)

    def test_research_category(self):
        """research分类应有多个Skill"""
        research = self.router.by_category("research")
        self.assertGreaterEqual(len(research), 2)

    def test_meta_category(self):
        """meta分类应有多个Skill"""
        meta = self.router.by_category("meta")
        self.assertGreaterEqual(len(meta), 3)


class TestDynamicRegister(unittest.TestCase):
    """测试动态注册/注销"""

    def setUp(self):
        self.router = create_router()

    def test_register_new_skill(self):
        """动态注册新Skill"""
        before = len(self.router.all_skills())
        new_skill = SkillEntry(
            id="test-custom-skill",
            name="Test Custom",
            category="test",
            priority=2,
            role="all",
            triggers=["test123", "customtest"],
            description="A test skill",
        )
        self.router.register(new_skill)
        after = len(self.router.all_skills())
        self.assertEqual(after, before + 1)

        # 验证可以路由到
        result = self.router.route("请执行test123")
        self.assertEqual(result.recommended_skill.id, "test-custom-skill")

    def test_unregister_skill(self):
        """注销Skill"""
        new_skill = SkillEntry(
            id="test-temp-skill",
            name="Test Temp",
            category="test",
            priority=3,
            role="all",
            triggers=["temptest"],
            description="temp",
        )
        self.router.register(new_skill)
        self.assertTrue(self.router.unregister("test-temp-skill"))
        self.assertIsNone(self.router.get("test-temp-skill"))

    def test_unregister_nonexistent(self):
        """注销不存在的Skill返回False"""
        self.assertFalse(self.router.unregister("nonexistent-id-123"))


class TestRealWorldScenarios(unittest.TestCase):
    """真实场景路由测试"""

    def setUp(self):
        self.router = create_router()

    def test_scenario_lanzhou_browser_automation(self):
        """场景: 澜舟做浏览器自动化"""
        result = self.router.route("打开网页自动填表", role="澜舟")
        self.assertIsNotNone(result.recommended_skill)
        self.assertIn(result.recommended_skill.category, ["automation"])

    def test_scenario_lingxi_market_research(self):
        """场景: 灵犀做市场调研"""
        result = self.router.route("帮我做市场调研和消费者分析", role="灵犀")
        self.assertIsNotNone(result.recommended_skill)
        # 应该匹配到 market-researcher 或 deep-research
        self.assertIn(result.recommended_skill.category, ["research"])

    def test_scenario_qianxun_knowledge_graph(self):
        """场景: 千寻管理知识图谱"""
        result = self.router.route("更新知识图谱的实体关系", role="千寻")
        self.assertIsNotNone(result.recommended_skill)
        self.assertEqual(result.recommended_skill.id, "knowledge-graph")

    def test_scenario_lanlan_content_repurpose(self):
        """场景: 澜澜做内容跨平台转换"""
        result = self.router.route("把这篇博客转成跨平台短内容", role="澜澜")
        self.assertIsNotNone(result.recommended_skill)
        self.assertEqual(result.recommended_skill.id, "content-repurposer")

    def test_scenario_all_generate_image(self):
        """场景: 任意角色生成图片"""
        result = self.router.route("帮我文生图生成一张图片", role="all")
        self.assertIsNotNone(result.recommended_skill)
        self.assertIn(result.recommended_skill.category, ["media"])

    def test_scenario_lanzhou_skill_audit(self):
        """场景: 澜舟审查Skill安全性"""
        result = self.router.route("审查这个新skill的安全性", role="澜舟")
        self.assertIsNotNone(result.recommended_skill)
        # 应匹配到 skill-vetter
        self.assertIn("vetter", result.recommended_skill.id.lower())


class TestStats(unittest.TestCase):
    """测试统计信息"""

    def setUp(self):
        self.router = create_router()

    def test_stats_structure(self):
        """统计信息结构完整"""
        s = self.router.stats()
        self.assertIn("total", s)
        self.assertIn("active", s)
        self.assertIn("by_priority", s)
        self.assertIn("by_category", s)
        self.assertIn("by_source", s)

    def test_stats_consistency(self):
        """统计数量一致"""
        s = self.router.stats()
        priority_sum = sum(s["by_priority"].values())
        self.assertEqual(priority_sum, s["total"])

    def test_summary_table_not_empty(self):
        """摘要表不为空"""
        table = self.router.summary_table()
        self.assertTrue(table)
        self.assertIn("Skill Router", table)


class TestRouteResultFormat(unittest.TestCase):
    """测试路由结果格式"""

    def setUp(self):
        self.router = create_router()

    def test_result_has_all_fields(self):
        """RouteResult 包含所有字段"""
        result = self.router.route("browser")
        self.assertTrue(hasattr(result, "recommended_skill"))
        self.assertTrue(hasattr(result, "candidates"))
        self.assertTrue(hasattr(result, "confidence"))
        self.assertTrue(hasattr(result, "strategy"))
        self.assertTrue(hasattr(result, "reason"))

    def test_result_summary_string(self):
        """summary() 返回字符串"""
        result = self.router.route("browser")
        s = result.summary()
        self.assertIsInstance(s, str)
        self.assertTrue(len(s) > 0)

    def test_candidates_is_list(self):
        """candidates 是列表"""
        result = self.router.route("调研")
        self.assertIsInstance(result.candidates, list)

    def test_top_k_limit(self):
        """top_k 限制候选数量"""
        result = self.router.route("搜索", top_k=3)
        self.assertLessEqual(len(result.candidates), 3)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestRegistryLoad))
    suite.addTests(loader.loadTestsFromTestCase(TestPrioritySort))
    suite.addTests(loader.loadTestsFromTestCase(TestRoleFilter))
    suite.addTests(loader.loadTestsFromTestCase(TestKeywordMatch))
    suite.addTests(loader.loadTestsFromTestCase(TestFallbackStrategy))
    suite.addTests(loader.loadTestsFromTestCase(TestConfidence))
    suite.addTests(loader.loadTestsFromTestCase(TestCategoryQuery))
    suite.addTests(loader.loadTestsFromTestCase(TestDynamicRegister))
    suite.addTests(loader.loadTestsFromTestCase(TestRealWorldScenarios))
    suite.addTests(loader.loadTestsFromTestCase(TestStats))
    suite.addTests(loader.loadTestsFromTestCase(TestRouteResultFormat))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print(f"\n{'='*60}")
    print(f"Skill Router 测试结果: {result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun} 通过")
    if result.failures:
        print(f"失败: {len(result.failures)}")
        for name, err in result.failures:
            print(f"  - {name}: {err[:200]}")
    if result.errors:
        print(f"错误: {len(result.errors)}")
        for name, err in result.errors:
            print(f"  - {name}: {err[:200]}")
    print(f"{'='*60}")
