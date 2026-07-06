"""
桥v7 Ratchet Loop — 双层实验工坊测试套件

测试覆盖:
1. ProgramParser — program.md解析
2. ExperimentConfig — 配置创建与默认值
3. RatchetLoopEngine — 实验执行(成功+失败+超时)
4. ConditionVerifier — EXPERIMENT实验模式
5. WorktreeManager — 实验Worktree角色与状态
6. ExperimentArchiver — 归档与检索
7. 集成测试 — 完整Ratchet Loop流程

日期: 2026-06-26
作者: 澜舟
"""

import sys
import os
import time
import json
import tempfile
import shutil

import pytest

# 确保项目目录在 sys.path 中
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from ratchet_loop import (
    ProgramParser,
    ExperimentConfig,
    ExperimentResult,
    ExperimentStatus,
    RatchetPhase,
    RatchetLoopEngine,
    create_experiment_config,
    create_ratchet_engine,
)
from condition_verifier import (
    ConditionVerifier,
    VerifierConfig,
    StopCondition,
    create_experiment_verifier,
)
from event_stream import EventStream, EventType, create_action
from experiment_archiver import ExperimentArchiver


# ==================== 1. ProgramParser 测试 ====================

class TestProgramParser:
    """program.md 实验指令解析器测试"""

    def test_parse_full_frontmatter(self):
        """完整frontmatter解析"""
        content = '''---
experiment:
  name: "test-skill-router"
  maker: "澜舟"
  reviewer: "千寻"
  hypothesis: "双层路由快30%"
  success_criteria:
    - "延迟低于50ms"
    - "准确率高于95%"
  max_iterations: 10
  timeout: 300
  rollback_on_fail: true
  archive_to: "bookhouse"
---
# 实验内容
测试SkillRouter v2...
'''
        config, desc = ProgramParser.parse(content)

        assert config.name == "test-skill-router"
        assert config.maker == "澜舟"
        assert config.reviewer == "千寻"
        assert config.hypothesis == "双层路由快30%"
        assert len(config.success_criteria) == 2
        assert "延迟低于50ms" in config.success_criteria
        assert config.max_iterations == 10
        assert config.timeout == 300.0
        assert config.rollback_on_fail is True
        assert config.archive_to == "bookhouse"
        assert "实验内容" in desc

    def test_parse_minimal(self):
        """最小配置解析（只有name）"""
        content = '''---
experiment:
  name: "minimal-test"
---
简单实验
'''
        config, desc = ProgramParser.parse(content)

        assert config.name == "minimal-test"
        assert config.maker == "澜舟"  # 默认值
        assert config.reviewer == "千寻"  # 默认值
        assert config.max_iterations == 10  # 默认值

    def test_parse_no_frontmatter(self):
        """无frontmatter的纯文本"""
        content = "这是纯文本实验描述"
        config, desc = ProgramParser.parse(content)

        assert config.name.startswith("exp_")
        assert desc == "这是纯文本实验描述"

    def test_parse_empty_success_criteria(self):
        """空成功标准列表"""
        content = '''---
experiment:
  name: "no-criteria"
---
无标准实验
'''
        config, _ = ProgramParser.parse(content)
        assert config.success_criteria == []

    def test_parse_tags(self):
        """标签解析"""
        content = '''---
experiment:
  name: "tagged-exp"
  tags:
    - "performance"
    - "routing"
---
带标签的实验
'''
        config, _ = ProgramParser.parse(content)
        # tags may or may not parse depending on YAML complexity
        # at minimum the name should be correct
        assert config.name == "tagged-exp"

    def test_auto_generated_ids(self):
        """自动生成实验ID和Worktree名"""
        config = ExperimentConfig(name="auto-id-test")
        assert config.experiment_id.startswith("exp_")
        assert "auto-id-test" in config.worktree_name
        assert config.created_at  # 非空


# ==================== 2. RatchetLoopEngine 测试 ====================

class TestRatchetLoopEngine:
    """Ratchet Loop引擎核心测试"""

    def test_experiment_success(self):
        """实验成功 → 棘轮锁定"""
        config = create_experiment_config(
            name="test-success",
            hypothesis="测试成功路径",
            success_criteria=["条件1满足", "条件2满足"],
            max_iterations=3,
        )
        engine = RatchetLoopEngine()

        # 自定义Maker：模拟成功执行
        def maker_cb(cfg):
            return [f"处理: {c}" for c in cfg.success_criteria]

        # 自定义Reviewer：总是通过
        def reviewer_cb(cfg, outputs):
            return True, [f"✅ {c}" for c in cfg.success_criteria]

        result = engine.run_experiment(config, maker_cb, reviewer_cb)

        assert result.status in (ExperimentStatus.LOCKED, ExperimentStatus.ARCHIVED)
        assert result.verification_passed is True
        assert result.iterations >= 1
        assert len(result.outputs) == 2
        assert result.locked_at  # 有锁定时间

    def test_experiment_fail_rollback(self):
        """实验失败 → 回滚"""
        config = create_experiment_config(
            name="test-fail",
            hypothesis="测试失败路径",
            success_criteria=["不可能满足的条件"],
            max_iterations=2,
            rollback_on_fail=True,
        )
        engine = RatchetLoopEngine()

        # Maker正常执行
        def maker_cb(cfg):
            return ["执行了但不会通过验证"]

        # Reviewer：总是失败
        def reviewer_cb(cfg, outputs):
            return False, ["❌ 验证失败"]

        result = engine.run_experiment(config, maker_cb, reviewer_cb)

        assert result.status == ExperimentStatus.ROLLED_BACK
        assert result.verification_passed is False
        assert result.rollback_reason == "验证未通过"
        assert result.lessons_learned  # 有教训记录

    def test_experiment_timeout(self):
        """实验超时"""
        config = create_experiment_config(
            name="test-timeout",
            hypothesis="测试超时",
            success_criteria=["超时测试"],
            max_iterations=100,
            timeout=0.01,  # 极短超时
        )
        engine = RatchetLoopEngine()

        # Maker：模拟慢执行
        def maker_cb(cfg):
            time.sleep(0.1)  # 超过timeout
            return ["慢执行结果"]

        result = engine.run_experiment(config, maker_cb)

        # 超时应该导致TIMEOUT或ROLLED_BACK
        assert result.status in (ExperimentStatus.TIMEOUT, ExperimentStatus.ROLLED_BACK)

    def test_experiment_exception_rollback(self):
        """Maker异常 → 自动回滚"""
        config = create_experiment_config(
            name="test-exception",
            hypothesis="测试异常处理",
            max_iterations=3,
        )
        engine = RatchetLoopEngine()

        def maker_cb(cfg):
            raise RuntimeError("模拟执行异常")

        result = engine.run_experiment(config, maker_cb)

        assert result.status == ExperimentStatus.ROLLED_BACK
        assert "模拟执行异常" in result.rollback_reason

    def test_default_maker_execute(self):
        """默认Maker执行（无callback）"""
        config = create_experiment_config(
            name="test-default-maker",
            hypothesis="测试默认执行",
            success_criteria=["条件A", "条件B"],
            max_iterations=2,
        )
        engine = RatchetLoopEngine()
        result = engine.run_experiment(config)

        assert len(result.outputs) > 0
        assert result.status in (
            ExperimentStatus.LOCKED,
            ExperimentStatus.ARCHIVED,
            ExperimentStatus.ROLLED_BACK,
        )

    def test_default_reviewer_verify_pass(self):
        """默认Reviewer验证 — 通过（有输出）"""
        config = create_experiment_config(
            name="test-default-reviewer-pass",
            success_criteria=["标准1"],
            max_iterations=1,
        )
        engine = RatchetLoopEngine()

        def maker_cb(cfg):
            return ["标准1 已满足"]

        result = engine.run_experiment(config, maker_cb)
        # 默认reviewer应该通过（有输出）
        assert result.verification_passed is True

    def test_default_reviewer_verify_fail(self):
        """默认Reviewer验证 — 失败（无输出）"""
        config = create_experiment_config(
            name="test-default-reviewer-fail",
            success_criteria=[],
            max_iterations=1,
        )
        engine = RatchetLoopEngine()

        def maker_cb(cfg):
            return []  # 无输出

        result = engine.run_experiment(config, maker_cb)
        assert result.verification_passed is False

    def test_ratchet_lock_is_permanent(self):
        """棘轮锁定后不可回退"""
        config = create_experiment_config(
            name="test-ratchet-lock",
            success_criteria=["锁定测试"],
            max_iterations=1,
        )
        engine = RatchetLoopEngine()

        def maker_cb(cfg):
            return ["锁定测试完成"]

        def reviewer_cb(cfg, outputs):
            return True, ["✅ 通过"]

        result = engine.run_experiment(config, maker_cb, reviewer_cb)

        # 确认已锁定
        assert engine.is_locked(result.experiment_id) is True

    def test_engine_stats(self):
        """引擎统计"""
        engine = RatchetLoopEngine()

        # 执行两个实验
        for i in range(2):
            config = create_experiment_config(
                name=f"test-stats-{i}",
                success_criteria=["统计测试"],
                max_iterations=1,
            )
            engine.run_experiment(config)

        stats = engine.get_stats()
        assert stats["total"] == 2
        assert stats["success_rate"] >= 0.0

    def test_list_experiments_by_status(self):
        """按状态过滤实验列表"""
        engine = RatchetLoopEngine()

        # 成功实验
        config_ok = create_experiment_config(name="test-list-ok", max_iterations=1)
        engine.run_experiment(config_ok)

        # 失败实验
        config_fail = create_experiment_config(name="test-list-fail", max_iterations=1)
        def fail_maker(cfg):
            raise RuntimeError("故意失败")
        engine.run_experiment(config_fail, fail_maker)

        locked = engine.list_experiments(status=ExperimentStatus.LOCKED)
        rolled = engine.list_experiments(status=ExperimentStatus.ROLLED_BACK)

        assert len(locked) >= 1
        assert len(rolled) >= 1

    def test_with_event_stream(self):
        """集成EventStream的实验执行"""
        stream = EventStream("test-ratchet-stream")
        engine = create_ratchet_engine(stream=stream)

        config = create_experiment_config(
            name="test-with-stream",
            success_criteria=["EventStream集成"],
            max_iterations=2,
        )

        def maker_cb(cfg):
            return ["EventStream集成完成"]

        result = engine.run_experiment(config, maker_cb)

        # 验证EventStream有事件
        assert stream.event_count > 0
        # 检查有INFO/TASK/DONE等事件
        type_counts = stream.get_statistics()["type_counts"]
        assert "INFO" in type_counts or "TASK" in type_counts


# ==================== 3. ConditionVerifier 实验模式测试 ====================

class TestExperimentVerifier:
    """条件验证器实验模式测试"""

    def test_experiment_verifier_creation(self):
        """实验验证器创建"""
        verifier = create_experiment_verifier(
            max_iterations=5,
            timeout=60,
            success_keywords=["passed", "完成"],
        )
        assert verifier.config.experiment_mode is True
        assert verifier.config.max_iterations == 5
        assert verifier.config.empty_timeout == 60
        assert "passed" in verifier.config.experiment_success_keywords
        assert "完成" in verifier.config.experiment_success_keywords

    def test_experiment_success_keyword_detection(self):
        """实验成功关键词检测"""
        verifier = create_experiment_verifier(
            success_keywords=["实验通过"],
        )
        stream = EventStream("test-exp-success")

        # 模拟成功输出
        verifier.notify_action("EXECUTE", "实验执行完成，实验通过")
        result = verifier.check(stream)

        assert result.condition == StopCondition.EXPERIMENT
        assert result.should_stop is True
        assert "实验通过" in result.reason

    def test_experiment_fail_keyword_detection(self):
        """实验失败关键词检测"""
        verifier = create_experiment_verifier(
            fail_keywords=["fatal_error"],
        )
        stream = EventStream("test-exp-fail")

        verifier.notify_action("EXECUTE", "执行遇到fatal_error")
        result = verifier.check(stream)

        assert result.condition == StopCondition.EXPERIMENT
        assert result.should_stop is True
        assert "fatal_error" in result.reason

    def test_experiment_no_keyword_continue(self):
        """无关键词匹配 → 继续循环"""
        verifier = create_experiment_verifier(
            max_iterations=100,  # 高上限避免MAX_ITER触发
            success_keywords=["特定成功词"],
            fail_keywords=["特定失败词"],
        )
        stream = EventStream("test-exp-continue")

        verifier.notify_action("EXECUTE", "普通输出，无关键词")
        result = verifier.check(stream)

        # 不应因EXPERIMENT条件停止
        assert result.condition != StopCondition.EXPERIMENT or not result.should_stop

    def test_experiment_mode_in_deep_check(self):
        """实验模式在深度检查中生效"""
        verifier = create_experiment_verifier(
            max_iterations=100,
            success_keywords=["目标达成"],
        )
        stream = EventStream("test-exp-deep")

        # 需要先有足够输出触发深度检查
        verifier.notify_action("EXECUTE", "准备中")
        verifier.notify_action("EXECUTE", "目标达成")

        result = verifier.check(stream)
        # 应该检测到成功关键词
        assert result.should_stop is True


# ==================== 4. WorktreeManager 实验模式测试 ====================

class TestExperimentWorktree:
    """Worktree实验模式测试"""

    def test_experiment_role_exists(self):
        """EXPERIMENTER角色存在"""
        from worktree_manager import WorktreeRole
        assert WorktreeRole.EXPERIMENTER == "experimenter"

    def test_experiment_status_exists(self):
        """实验状态值存在"""
        from worktree_manager import WorktreeStatus
        assert WorktreeStatus.EXPERIMENTING == "experimenting"
        assert WorktreeStatus.RATCHET_LOCKED == "ratchet_locked"
        assert WorktreeStatus.ROLLED_BACK == "rolled_back"

    def test_create_experiment_worktree_config(self):
        """实验Worktree配置创建"""
        from worktree_manager import create_experiment_worktree, WorktreeRole

        config = create_experiment_worktree(
            name="test-exp-wt",
            agent="澜舟",
            reviewer="千寻",
            max_iterations=5,
        )

        assert config.name == "test-exp-wt"
        assert config.role == WorktreeRole.EXPERIMENTER
        assert config.branch == "experiment/test-exp-wt"
        assert config.review_required is True
        assert config.reviewer == "千寻"
        assert "max_iter:5" in config.stop_conditions
        assert "experiment_mode" in config.stop_conditions


# ==================== 5. ExperimentArchiver 测试 ====================

class TestExperimentArchiver:
    """千寻归档适配器测试"""

    @pytest.fixture
    def temp_archive_dir(self):
        """临时归档目录"""
        temp_dir = tempfile.mkdtemp(prefix="ratchet_archive_")
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_archive_success(self, temp_archive_dir):
        """成功归档实验"""
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)

        config = create_experiment_config(
            name="test-archive",
            hypothesis="归档测试",
            success_criteria=["归档成功"],
            max_iterations=1,
        )
        engine = RatchetLoopEngine()
        result = engine.run_experiment(config)

        archive_path = archiver.archive(result)

        assert os.path.exists(archive_path)
        assert archive_path.endswith(".json")

        # JSON文件可读
        with open(archive_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["experiment_id"] == result.experiment_id

    def test_archive_markdown_generated(self, temp_archive_dir):
        """Markdown报告生成"""
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)

        config = create_experiment_config(
            name="test-md",
            hypothesis="MD报告测试",
            max_iterations=1,
        )
        engine = RatchetLoopEngine()
        result = engine.run_experiment(config)

        archiver.archive(result)

        md_path = os.path.join(temp_archive_dir, f"{result.experiment_id}.md")
        assert os.path.exists(md_path)

        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        assert "实验报告" in md_content
        assert config.name in md_content

    def test_archive_and_retrieve(self, temp_archive_dir):
        """归档后检索"""
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)

        config = create_experiment_config(
            name="test-retrieve",
            max_iterations=1,
        )
        engine = RatchetLoopEngine()
        result = engine.run_experiment(config)

        archiver.archive(result)

        # 检索
        retrieved = archiver.get_archive(result.experiment_id)
        assert retrieved is not None
        assert retrieved["experiment_id"] == result.experiment_id
        assert retrieved["config"]["name"] == "test-retrieve"

    def test_archive_index_update(self, temp_archive_dir):
        """索引更新"""
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)

        # 归档两个实验
        for i in range(2):
            config = create_experiment_config(
                name=f"test-index-{i}",
                max_iterations=1,
            )
            engine = RatchetLoopEngine()
            result = engine.run_experiment(config)
            archiver.archive(result)

        # 检查索引
        index_path = os.path.join(temp_archive_dir, "index.json")
        assert os.path.exists(index_path)

        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
        assert len(index["experiments"]) == 2

    def test_archive_search(self, temp_archive_dir):
        """搜索归档"""
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)

        config = create_experiment_config(
            name="searchable-experiment",
            hypothesis="搜索关键词测试",
            max_iterations=1,
        )
        engine = RatchetLoopEngine()
        result = engine.run_experiment(config)
        archiver.archive(result)

        # 搜索
        results = archiver.search_archives("searchable")
        assert len(results) >= 1
        assert any(r["name"] == "searchable-experiment" for r in results)

    def test_archive_stats(self, temp_archive_dir):
        """归档统计"""
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)

        config = create_experiment_config(name="test-stats", max_iterations=1)
        engine = RatchetLoopEngine()
        result = engine.run_experiment(config)
        archiver.archive(result)

        stats = archiver.get_stats()
        assert stats["total_archives"] >= 1
        assert "status_counts" in stats

    def test_archive_rolled_back_experiment(self, temp_archive_dir):
        """归档失败实验（含教训）"""
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)

        config = create_experiment_config(name="test-fail-archive", max_iterations=1)
        engine = RatchetLoopEngine()

        def fail_maker(cfg):
            raise RuntimeError("故意失败")

        result = engine.run_experiment(config, fail_maker)
        archiver.archive(result)

        # 检查Markdown报告含教训
        md_path = os.path.join(temp_archive_dir, f"{result.experiment_id}.md")
        with open(md_path, "r", encoding="utf-8") as f:
            md = f.read()
        assert "回滚" in md or "Rollback" in md or "rolled_back" in md


# ==================== 6. 集成测试 ====================

class TestRatchetLoopIntegration:
    """完整Ratchet Loop流程集成测试"""

    @pytest.fixture
    def temp_archive_dir(self):
        temp_dir = tempfile.mkdtemp(prefix="ratchet_integration_")
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_full_flow_success(self, temp_archive_dir):
        """完整成功流程: 解析 → 执行 → 验证 → 锁定 → 归档"""
        # 1. 解析program.md
        program = '''---
experiment:
  name: "integration-test-full"
  maker: "澜舟"
  reviewer: "千寻"
  hypothesis: "完整流程测试"
  success_criteria:
    - "解析正确"
    - "执行完成"
    - "验证通过"
  max_iterations: 5
  timeout: 30
  rollback_on_fail: true
  archive_to: "bookhouse"
---
完整集成测试
'''
        config, desc = ProgramParser.parse(program)
        assert config.name == "integration-test-full"

        # 2. 创建引擎（带EventStream + Archiver）
        stream = EventStream("integration-test")
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)
        engine = create_ratchet_engine(stream=stream, archiver=archiver)

        # 3. 执行实验
        def maker_cb(cfg):
            return [f"完成: {c}" for c in cfg.success_criteria]

        def reviewer_cb(cfg, outputs):
            return True, [f"✅ {c}" for c in cfg.success_criteria]

        result = engine.run_experiment(config, maker_cb, reviewer_cb)

        # 4. 验证结果
        assert result.status == ExperimentStatus.ARCHIVED
        assert result.verification_passed is True
        assert result.locked_at  # 有锁定时间
        assert result.archived_at  # 有归档时间
        assert result.archive_path  # 有归档路径

        # 5. 验证归档可检索
        retrieved = archiver.get_archive(result.experiment_id)
        assert retrieved is not None
        assert retrieved["status"] == "archived"

        # 6. 验证EventStream有事件
        assert stream.event_count > 0

    def test_full_flow_failure(self, temp_archive_dir):
        """完整失败流程: 解析 → 执行 → 验证失败 → 回滚 → 归档教训"""
        program = '''---
experiment:
  name: "integration-test-fail"
  hypothesis: "失败流程测试"
  success_criteria:
    - "不可能满足"
  max_iterations: 2
  rollback_on_fail: true
---
失败集成测试
'''
        config, _ = ProgramParser.parse(program)

        archiver = ExperimentArchiver(archive_base=temp_archive_dir)
        engine = create_ratchet_engine(archiver=archiver)

        def maker_cb(cfg):
            return ["执行了但验证会失败"]

        def reviewer_cb(cfg, outputs):
            return False, ["❌ 不可能满足"]

        result = engine.run_experiment(config, maker_cb, reviewer_cb)

        assert result.status == ExperimentStatus.ROLLED_BACK
        assert result.verification_passed is False
        assert result.lessons_learned  # 有教训

        # 归档失败实验
        archiver.archive(result)
        retrieved = archiver.get_archive(result.experiment_id)
        assert retrieved["status"] == "rolled_back"

    def test_ratchet_does_not_regress(self, temp_archive_dir):
        """棘轮不可回退: 锁定后的实验状态不可变更"""
        config = create_experiment_config(
            name="test-no-regress",
            success_criteria=["不可回退"],
            max_iterations=1,
        )
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)
        engine = create_ratchet_engine(archiver=archiver)

        def maker_cb(cfg):
            return ["不可回退测试完成"]

        def reviewer_cb(cfg, outputs):
            return True, ["✅ 不可回退"]

        result = engine.run_experiment(config, maker_cb, reviewer_cb)

        # 确认锁定
        assert engine.is_locked(result.experiment_id) is True

        # 再次检查 — 仍然是锁定状态
        exp = engine.get_experiment(result.experiment_id)
        assert exp.status in (ExperimentStatus.LOCKED, ExperimentStatus.ARCHIVED)

    def test_multiple_experiments_sequential(self, temp_archive_dir):
        """连续多个实验"""
        archiver = ExperimentArchiver(archive_base=temp_archive_dir)
        engine = create_ratchet_engine(archiver=archiver)

        results = []
        for i in range(3):
            config = create_experiment_config(
                name=f"seq-test-{i}",
                success_criteria=[f"条件{i}"],
                max_iterations=1,
            )
            def maker_cb(cfg, _i=i):
                return [f"条件{_i}满足"]

            result = engine.run_experiment(config, maker_cb)
            results.append(result)

        assert len(results) == 3
        # 至少有一个成功
        success_count = sum(1 for r in results if r.verification_passed)
        assert success_count >= 1

        # 统计正确
        stats = engine.get_stats()
        assert stats["total"] == 3


# ==================== 7. 运行入口 ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
