"""
soul_config.py 测试 — 四层记忆系统验证

覆盖范围：
- 四层加载链（SOUL→USER→TOOLS→Session）
- 人格模板系统（pydantic模型 + 预置模板库）
- Compaction 压缩引擎（触发·压缩·重置）
- LayeredContext 组装与裁剪
- SOUL.md/USER.md 文件解析
- 便捷API
"""

import os
import tempfile

import pytest

from soul_config import (
    BUILTIN_TEMPLATES,
    DEFAULT_TEMPLATE,
    CompactionEngine,
    LayeredContext,
    MemoryLayer,
    PersonalityTemplate,
    SoulConfigLoader,
    ToolsProfile,
    UserProfile,
    get_agent_context,
    get_system_prompt,
    list_available_templates,
)

# ============================================================
# 人格模板系统测试
# ============================================================


class TestPersonalityTemplate:
    """人格模板 pydantic 模型测试。"""

    def test_default_template_creation(self):
        """默认模板能正确创建。"""
        tmpl = PersonalityTemplate()
        assert tmpl.role == "assistant"
        assert len(tmpl.core_traits) >= 1
        assert tmpl.language == "zh-CN"
        assert len(tmpl.behavior_rules) >= 1
        assert len(tmpl.boundaries) >= 1

    def test_custom_template(self):
        """自定义模板字段覆盖。"""
        tmpl = PersonalityTemplate(
            role="测试工程师",
            core_traits=["严谨", "边界测试"],
            communication_style="简洁·错误优先",
            behavior_rules=["必须覆盖边界", "不跳过测试"],
            boundaries=["不忽略失败测试"],
        )
        assert tmpl.role == "测试工程师"
        assert "严谨" in tmpl.core_traits
        assert tmpl.communication_style == "简洁·错误优先"

    def test_extra_fields_allowed(self):
        """pydantic ConfigDict(extra='allow') 允许扩展字段。"""
        tmpl = PersonalityTemplate(
            role="扩展Agent",
            custom_field="自定义值",
        )
        assert tmpl.custom_field == "自定义值"

    def test_builtin_templates_exist(self):
        """预置模板库包含核心团队。"""
        assert "leader" in BUILTIN_TEMPLATES
        assert "developer" in BUILTIN_TEMPLATES
        assert "coordinator" in BUILTIN_TEMPLATES
        assert "researcher" in BUILTIN_TEMPLATES
        assert "archivist" in BUILTIN_TEMPLATES

    def test_builtin_template_content(self):
        """核心团队模板内容完整性。"""
        jc = BUILTIN_TEMPLATES["leader"]
        assert jc.role == "Team leader · strategy"
        assert "warm" in jc.core_traits

        lz = BUILTIN_TEMPLATES["developer"]
        assert lz.role == "Developer · system architect"
        assert "pragmatic" in lz.core_traits

    def test_list_available_templates(self):
        """便捷API列出模板。"""
        templates = list_available_templates()
        assert "leader" in templates
        assert "developer" in templates
        assert "default" in templates
        assert "Team leader" in templates["leader"]


# ============================================================
# 用户画像与工具画像测试
# ============================================================


class TestUserProfile:
    """用户画像模型测试。"""

    def test_default_profile(self):
        """默认用户画像。"""
        profile = UserProfile()
        assert profile.identity == "用户"
        assert profile.scenario == "通用"
        assert len(profile.preferences) == 0
        assert len(profile.taboos) == 0

    def test_custom_profile(self):
        """自定义用户画像。"""
        profile = UserProfile(
            identity="九重·项目负责人",
            scenario="OpenBridge V8开发",
            preferences={"language": "zh-CN", "style": "表格化"},
            taboos=["不执行未经调研的决策"],
        )
        assert profile.identity == "九重·项目负责人"
        assert profile.preferences["style"] == "表格化"


class TestToolsProfile:
    """工具技能画像模型测试。"""

    def test_default_tools(self):
        """默认工具画像。"""
        tools = ToolsProfile()
        assert tools.permission_level == "L1_PUBLIC"
        assert len(tools.available_skills) == 0

    def test_custom_tools(self):
        """自定义工具画像。"""
        tools = ToolsProfile(
            available_skills=["research", "code_gen", "archiver"],
            permission_level="L3_CORE",
            collaborators={"researcher": "信息咨询", "archivist": "书记归档"},
            workflow_sequence=["调研", "开发", "归档"],
        )
        assert "research" in tools.available_skills
        assert tools.permission_level == "L3_CORE"
        assert "researcher" in tools.collaborators


# ============================================================
# 四层加载链测试
# ============================================================


class TestSoulConfigLoader:
    """四层加载器测试。"""

    def test_load_all_default(self):
        """默认加载四层上下文（无文件·无Agent）。"""
        loader = SoulConfigLoader()
        ctx = loader.load_all()
        assert ctx.soul.role == DEFAULT_TEMPLATE.role
        assert isinstance(ctx.user, UserProfile)
        assert isinstance(ctx.tools, ToolsProfile)

    def test_load_all_with_agent_id(self):
        """指定Agent加载对应人格模板。"""
        loader = SoulConfigLoader()
        ctx = loader.load_all(agent_id="developer")
        assert ctx.soul.role == "Developer · system architect"
        assert "pragmatic" in ctx.soul.core_traits

    def test_load_all_unknown_agent(self):
        """未知Agent使用默认模板。"""
        loader = SoulConfigLoader()
        ctx = loader.load_all(agent_id="unknown_agent")
        assert ctx.soul.role == DEFAULT_TEMPLATE.role

    def test_load_with_session_summary(self):
        """注入对话摘要。"""
        loader = SoulConfigLoader()
        ctx = loader.load_all(
            agent_id="leader",
            session_summary="正在讨论V8架构优化方案",
        )
        assert "V8架构优化" in ctx.session

    def test_load_with_soul_md_file(self):
        """SOUL.md 文件覆盖人格模板。"""
        soul_content = """# SOUL.md

## 角色定位
You are a Developer, pragmatic and rigorous.

## 交流风格
Code speaks, document pitfalls, small steps.

## 行为原则
- 增量切片小步交付
- 踩坑必记入MEMORY.md

## 行为边界
- 不跳过测试验证
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(soul_content)
            tmp_path = f.name

        try:
            # 创建包含SOUL.md的临时工作区
            workspace = os.path.dirname(tmp_path)
            loader = SoulConfigLoader(workspace_root=workspace)
            # 因为文件名是随机的，需要手动设置路径
            loader._find_layer_file = lambda fn: tmp_path if fn == "SOUL.md" else None

            ctx = loader.load_all(agent_id="developer")
            # SOUL.md覆盖后，角色定位应更新
            assert "Developer" in ctx.soul.role or "developer" in ctx.soul.role
            # 行为原则应包含SOUL.md中的规则
            assert any("增量" in r for r in ctx.soul.behavior_rules)
        finally:
            os.unlink(tmp_path)

    def test_load_with_user_md_file(self):
        """USER.md 文件加载用户画像。"""
        user_content = """## 身份
九重·项目负责人

## 场景
OpenBridge V8架构决策

## 偏好
- language: zh-CN
- style: 表格化分析

## 禁忌
- 不执行未经调研的决策
- 不做政治敏感内容
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(user_content)
            tmp_path = f.name

        try:
            workspace = os.path.dirname(tmp_path)
            loader = SoulConfigLoader(workspace_root=workspace)
            loader._find_layer_file = lambda fn: tmp_path if fn == "USER.md" else None

            ctx = loader.load_all()
            assert "九重" in ctx.user.identity or "项目负责人" in ctx.user.identity
            assert ctx.user.preferences.get("style") == "表格化分析"
        finally:
            os.unlink(tmp_path)

    def test_parse_markdown_layers(self):
        """Markdown段解析。"""
        md_content = """## 角色定位
测试角色

## 交流风格
简洁明了

## 行为原则
- 先测试再上线
- 覆盖边界条件
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(md_content)
            tmp_path = f.name

        try:
            loader = SoulConfigLoader()
            result = loader._parse_markdown_layers(tmp_path)
            assert "角色定位" in result
            assert "交流风格" in result
            assert "行为原则" in result
            assert "测试角色" in result["角色定位"]
        finally:
            os.unlink(tmp_path)


# ============================================================
# LayeredContext 组装测试
# ============================================================


class TestLayeredContext:
    """四层上下文组装与裁剪测试。"""

    def test_to_prompt_sections(self):
        """四层转为prompt段落。"""
        ctx = LayeredContext(
            soul=BUILTIN_TEMPLATES["leader"],
            user=UserProfile(identity="九重", preferences={"style": "表格"}),
            tools=ToolsProfile(available_skills=["research", "code_gen"]),
            session="正在讨论V8优化",
        )
        sections = ctx.to_prompt_sections()
        assert "soul" in sections
        assert "user" in sections
        assert "tools" in sections
        assert "session" in sections
        assert "leader" in sections["soul"] or "Team leader" in sections["soul"]

    def test_assemble_system_prompt(self):
        """组装系统提示词。"""
        ctx = LayeredContext(
            soul=BUILTIN_TEMPLATES["developer"],
            user=UserProfile(identity="测试用户"),
            tools=ToolsProfile(available_skills=["test_skill"]),
            session="测试对话",
        )
        prompt = ctx.assemble_system_prompt(max_chars=2000)
        assert len(prompt) <= 2500  # 给一定余量
        assert "Developer" in prompt or "developer" in prompt

    def test_assemble_with_char_limit(self):
        """字符限制裁剪：SOUL必留·SESSION截断。"""
        ctx = LayeredContext(
            soul=BUILTIN_TEMPLATES["leader"],
            user=UserProfile(identity="九重", preferences={"k1": "v1", "k2": "v2"}),
            tools=ToolsProfile(available_skills=["s1", "s2", "s3"]),
            session="这是一段很长的对话摘要" * 50,
        )
        prompt = ctx.assemble_system_prompt(max_chars=500)
        # SOUL段应完整保留
        assert "leader" in prompt or "Team leader" in prompt
        # 总长度应控制在限制附近
        assert len(prompt) <= 700  # 给余量

    def test_memory_layer_enum(self):
        """四层枚举完整性。"""
        assert MemoryLayer.SOUL.value == "soul"
        assert MemoryLayer.USER.value == "user"
        assert MemoryLayer.TOOLS.value == "tools"
        assert MemoryLayer.SESSION.value == "session"
        assert len(MemoryLayer) == 4


# ============================================================
# Compaction 压缩引擎测试
# ============================================================


class TestCompactionEngine:
    """对话压缩引擎测试。"""

    def test_observe_no_trigger(self):
        """观察消息但不触发压缩。"""
        engine = CompactionEngine()
        result = engine.observe("普通消息")
        assert result is None
        assert engine.turn_count == 1

    def test_observe_critical_info(self):
        """识别关键信息模式。"""
        engine = CompactionEngine()
        engine.observe("我决定使用FastAPI框架", is_user=True)
        assert len(engine._critical_buffer) == 1
        assert "FastAPI" in engine._critical_buffer[0]

    def test_observe_non_critical(self):
        """非关键消息不暂存。"""
        engine = CompactionEngine()
        engine.observe("请问天气如何？", is_user=True)
        assert len(engine._critical_buffer) == 0

    def test_compact_on_turn_threshold(self):
        """轮次阈值触发压缩。"""
        engine = CompactionEngine()
        for i in range(engine.TURN_THRESHOLD):
            result = engine.observe(f"消息{i}")
        # 第20轮应触发压缩
        assert result is not None
        assert len(result) > 0

    def test_compact_on_token_threshold(self):
        """token阈值触发压缩。"""
        engine = CompactionEngine()
        # 发送一条足够长的消息触发token阈值
        long_msg = "这是一条很长的消息" * 300  # ~300*15*1.5 ≈ 6750 tokens
        result = engine.observe(long_msg)
        assert result is not None

    def test_compact_result_content(self):
        """压缩结果包含关键信息和主题摘要。"""
        engine = CompactionEngine()
        engine.observe("我决定使用Python开发")
        engine.observe("偏好表格化输出")
        # 手动触发压缩
        result = engine.compact()
        assert "关键" in result or "决定" in result or "偏好" in result

    def test_should_compact(self):
        """压缩判断。"""
        engine = CompactionEngine()
        assert not engine.should_compact()
        engine.turn_count = engine.TURN_THRESHOLD
        assert engine.should_compact()

    def test_reset(self):
        """重置计数器。"""
        engine = CompactionEngine()
        engine.observe("消息1")
        engine.observe("消息2")
        assert engine.turn_count == 2
        engine.reset()
        assert engine.turn_count == 0
        assert engine.token_count == 0
        assert len(engine._critical_buffer) == 0


# ============================================================
# 便捷API测试
# ============================================================


class TestConvenienceAPI:
    """便捷API测试。"""

    def test_get_agent_context(self):
        """快捷获取上下文。"""
        ctx = get_agent_context(agent_id="leader")
        assert ctx.soul.role == "Team leader · strategy"

    def test_get_system_prompt(self):
        """快捷获取系统提示词。"""
        prompt = get_system_prompt(agent_id="developer", max_chars=1000)
        assert "Developer" in prompt or "architect" in prompt

    def test_get_context_default_agent(self):
        """默认Agent上下文。"""
        ctx = get_agent_context()
        assert ctx.soul.role == DEFAULT_TEMPLATE.role
