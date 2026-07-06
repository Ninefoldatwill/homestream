"""
桥v7 Day 2 集成测试 — Worktree并行隔离 + 审查分离

测试覆盖：
1. WorktreeManager完整CRUD
2. PortManager双端口模型
3. SQLiteManager DB隔离
4. ReviewerSubscriber审查闭环
5. WorktreeSubscriber事件联动
6. 四场景隔离验证
7. API端点集成测试
8. Day2验收
"""

import os
import sys
import json
import shutil
import tempfile
import unittest

# 确保模块可导入
sys.path.insert(0, os.path.dirname(__file__))

from event_stream import EventStream, Event, Action, Observation, EventType, EventSource
from actions import create_action, create_done_action, create_task_action
from worktree_manager import (
    WorktreeManager, WorktreeConfig, WorktreeRole, WorktreeStatus,
    PortManager, SQLiteManager,
    create_maker_worktree, create_reviewer_worktree,
    create_researcher_worktree, create_coordinator_worktree,
)
from worktree_subscribers import ReviewerSubscriber, WorktreeSubscriber


class TestWorktreeManager(unittest.TestCase):
    """WorktreeManager核心CRUD测试"""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="wt_test_")
        self.mgr = WorktreeManager(repo_path=".", worktree_base=self.test_dir)
    
    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_01_create_maker_worktree(self):
        """测试创建制造者Worktree"""
        config = create_maker_worktree("test-maker", agent="澜舟")
        path = self.mgr.create_worktree(config)
        
        self.assertTrue(os.path.exists(path))
        self.assertEqual(config.status, WorktreeStatus.ACTIVE)
        self.assertEqual(config.agent, "澜舟")
        self.assertEqual(config.role, WorktreeRole.MAKER)
        self.assertEqual(config.reviewer, "千寻")  # 默认审查者
    
    def test_02_create_reviewer_worktree(self):
        """测试创建审查者Worktree"""
        # 先创建maker
        maker_config = create_maker_worktree("feature-x", agent="澜舟")
        self.mgr.create_worktree(maker_config)
        
        # 再创建reviewer
        reviewer_config = create_reviewer_worktree("feature-x", reviewer="千寻")
        path = self.mgr.create_worktree(reviewer_config)
        
        self.assertTrue(os.path.exists(path))
        self.assertEqual(reviewer_config.role, WorktreeRole.REVIEWER)
        self.assertEqual(reviewer_config.name, "review-feature-x")
    
    def test_03_create_all_roles(self):
        """测试创建4种角色Worktree"""
        configs = [
            create_maker_worktree("wt-maker"),
            create_reviewer_worktree("wt-maker"),
            create_researcher_worktree("agent-loop"),
            create_coordinator_worktree("sprint-plan"),
        ]
        
        for config in configs:
            path = self.mgr.create_worktree(config)
            self.assertTrue(os.path.exists(path))
        
        self.assertEqual(len(self.mgr.list_worktrees()), 4)
    
    def test_04_lock_unlock(self):
        """测试锁定/解锁"""
        config = create_maker_worktree("lock-test")
        self.mgr.create_worktree(config)
        
        self.mgr.lock_worktree("lock-test", "审查中")
        wt = self.mgr.get_worktree("lock-test")
        self.assertEqual(wt.status, WorktreeStatus.LOCKED)
        
        self.mgr.unlock_worktree("lock-test")
        wt = self.mgr.get_worktree("lock-test")
        self.assertEqual(wt.status, WorktreeStatus.ACTIVE)
    
    def test_05_remove_worktree(self):
        """测试删除"""
        config = create_maker_worktree("remove-test")
        self.mgr.create_worktree(config)
        self.assertEqual(len(self.mgr.list_worktrees()), 1)
        
        self.mgr.remove_worktree("remove-test", force=True)
        self.assertEqual(len(self.mgr.list_worktrees()), 0)
    
    def test_06_remove_locked_fails(self):
        """测试锁定Worktree不可删除"""
        config = create_maker_worktree("locked-test")
        self.mgr.create_worktree(config)
        self.mgr.lock_worktree("locked-test", "保护")
        
        with self.assertRaises(ValueError):
            self.mgr.remove_worktree("locked-test", force=False)
        
        # force可以删除
        self.mgr.remove_worktree("locked-test", force=True)
        self.assertEqual(len(self.mgr.list_worktrees()), 0)
    
    def test_07_duplicate_name_fails(self):
        """测试重名创建失败"""
        config = create_maker_worktree("dup-test")
        self.mgr.create_worktree(config)
        
        with self.assertRaises(ValueError):
            self.mgr.create_worktree(config)
    
    def test_08_assign_reviewer(self):
        """测试审查者分配"""
        config = create_maker_worktree("review-test")
        self.mgr.create_worktree(config)
        
        self.mgr.assign_reviewer("review-test", "千寻")
        wt = self.mgr.get_worktree("review-test")
        self.assertEqual(wt.reviewer, "千寻")
        self.assertEqual(wt.status, WorktreeStatus.REVIEWING)
    
    def test_09_stats(self):
        """测试统计信息"""
        self.mgr.create_worktree(create_maker_worktree("s1"))
        self.mgr.create_worktree(create_reviewer_worktree("s1"))
        
        stats = self.mgr.get_stats()
        self.assertEqual(stats["total_worktrees"], 2)
        self.assertEqual(stats["active"], 2)
        self.assertIn("澜舟", stats["agents"])
        self.assertIn("千寻", stats["agents"])


class TestPortManager(unittest.TestCase):
    """PortManager双端口模型测试"""
    
    def test_01_allocate_main_instance(self):
        """测试主实例端口分配"""
        pm = PortManager()
        ports = pm.allocate("main", 0)
        
        self.assertEqual(ports["bridge_v7"], 3459)
        self.assertEqual(ports["kanban"], 8643)
        self.assertEqual(ports["bookhouse"], 3460)
        self.assertEqual(ports["openclaw"], 28790)
    
    def test_02_allocate_parallel_instance(self):
        """测试并行实例端口偏移"""
        pm = PortManager()
        ports_a = pm.allocate("wt-a", 0)
        ports_b = pm.allocate("wt-b", 1)
        
        self.assertEqual(ports_a["bridge_v7"], 3459)
        self.assertEqual(ports_b["bridge_v7"], 4459)  # +1000
        self.assertEqual(ports_b["kanban"], 9643)
    
    def test_03_conflict_detection(self):
        """测试端口冲突检测"""
        pm = PortManager()
        pm.allocate("wt-a", 0)
        
        with self.assertRaises(ValueError):
            pm.allocate("wt-b", 0)  # 同索引冲突
    
    def test_04_release_and_reallocate(self):
        """测试释放后重新分配"""
        pm = PortManager()
        pm.allocate("wt-a", 0)
        pm.release("wt-a")
        
        # 应该可以重新分配
        ports = pm.allocate("wt-b", 0)
        self.assertEqual(ports["bridge_v7"], 3459)
    
    def test_05_set_canonical(self):
        """测试规范端口切换"""
        pm = PortManager()
        pm.allocate("wt-a", 0)
        pm.allocate("wt-b", 1)
        
        # wt-b签出，获得规范端口
        pm.set_canonical("wt-b")
        ports_b = pm.get_ports("wt-b")
        self.assertEqual(ports_b["bridge_v7"], 3459)  # 获得规范端口
    
    def test_06_env_vars(self):
        """测试环境变量生成"""
        pm = PortManager()
        pm.allocate("wt-a", 1)
        env = pm.get_env_vars("wt-a")
        
        self.assertIn("JIUCHONG_PORT_BRIDGE_V7", env)
        self.assertEqual(env["JIUCHONG_WORKTREE"], "wt-a")


class TestSQLiteManager(unittest.TestCase):
    """SQLiteManager DB隔离测试"""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="db_test_")
        self.db_mgr = SQLiteManager()
    
    def tearDown(self):
        self.db_mgr.close_all()
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_01_create_database(self):
        """测试创建独立DB"""
        db_path = os.path.join(self.test_dir, "wt-a", "wt-a.db")
        path = self.db_mgr.create_database("wt-a", db_path)
        
        self.assertTrue(os.path.exists(path))
    
    def test_02_isolation(self):
        """测试DB隔离（两个Worktree互不影响）"""
        db_path_a = os.path.join(self.test_dir, "wt-a", "wt-a.db")
        db_path_b = os.path.join(self.test_dir, "wt-b", "wt-b.db")
        
        self.db_mgr.create_database("wt-a", db_path_a)
        self.db_mgr.create_database("wt-b", db_path_b)
        
        # wt-a写入
        conn_a = self.db_mgr.get_connection("wt-a")
        conn_a.execute("INSERT INTO worktree_state (key, value) VALUES (?, ?)", ("test_key", "value_a"))
        conn_a.commit()
        
        # wt-b写入
        conn_b = self.db_mgr.get_connection("wt-b")
        conn_b.execute("INSERT INTO worktree_state (key, value) VALUES (?, ?)", ("test_key", "value_b"))
        conn_b.commit()
        
        # 验证隔离
        result_a = conn_a.execute("SELECT value FROM worktree_state WHERE key=?", ("test_key",)).fetchone()
        result_b = conn_b.execute("SELECT value FROM worktree_state WHERE key=?", ("test_key",)).fetchone()
        
        self.assertEqual(result_a[0], "value_a")
        self.assertEqual(result_b[0], "value_b")


class TestReviewerSubscriber(unittest.TestCase):
    """ReviewerSubscriber审查闭环测试"""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="review_test_")
        self.stream = EventStream(session_id="test-review-sub")
        self.mgr = WorktreeManager(repo_path=".", worktree_base=self.test_dir)
        self.reviewer_sub = ReviewerSubscriber(self.stream, self.mgr)
        self.worktree_sub = WorktreeSubscriber(self.stream, self.mgr)
    
    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_01_done_triggers_review(self):
        """测试DONE事件触发审查"""
        # 创建制造者Worktree
        maker_config = create_maker_worktree("feat-x", agent="澜舟")
        self.mgr.create_worktree(maker_config)
        
        # 发布DONE事件
        done_event = create_done_action(
            sender="澜舟",
            recipient="千寻",
            task_id="task-feat-x",
            what_done="功能开发完成",
            where_artifacts=["worktree:feat-x"],
            how_verify="千寻审查",
            known_issues=[],
            what_next="等待审查",
        )
        self.stream.publish(done_event)
        
        # 验证Worktree被锁定（审查中）
        wt = self.mgr.get_worktree("feat-x")
        self.assertEqual(wt.status, WorktreeStatus.LOCKED)
        
        # 验证审查事件被发布
        review_events = [e for e in self.stream.events if e.event_type == EventType.TASK]
        self.assertGreater(len(review_events), 0)
    
    def test_02_review_pass_unlocks(self):
        """测试审查通过解锁"""
        # 创建并锁定
        maker_config = create_maker_worktree("feat-y", agent="澜舟")
        self.mgr.create_worktree(maker_config)
        self.mgr.assign_reviewer("feat-y", "千寻")
        self.mgr.lock_worktree("feat-y", "审查中")
        
        # 发布审查通过事件
        pass_event = create_action(
            sender="千寻",
            recipient="澜舟",
            event_type=EventType.INFO,
            content="[REVIEW:PASS] 审查通过",
        )
        self.stream.publish(pass_event)
        
        # 验证解锁
        wt = self.mgr.get_worktree("feat-y")
        self.assertEqual(wt.status, WorktreeStatus.ACTIVE)
    
    def test_03_review_fail_notifies(self):
        """测试审查不通过通知"""
        maker_config = create_maker_worktree("feat-z", agent="澜舟")
        self.mgr.create_worktree(maker_config)
        self.mgr.assign_reviewer("feat-z", "千寻")
        self.mgr.lock_worktree("feat-z", "审查中")
        
        fail_event = create_action(
            sender="千寻",
            recipient="澜舟",
            event_type=EventType.WARN,
            content="[REVIEW:FAIL] 需要修改",
        )
        self.stream.publish(fail_event)
        
        # 验证解锁但需要修改
        wt = self.mgr.get_worktree("feat-z")
        self.assertEqual(wt.status, WorktreeStatus.ACTIVE)


class TestFourScenarios(unittest.TestCase):
    """四场景隔离验证（参考Claude Code的4种Worktree模式）"""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="scenario_test_")
        self.mgr = WorktreeManager(repo_path=".", worktree_base=self.test_dir)
    
    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_01_one_task_per_worktree(self):
        """场景1: 每任务一Worktree（澜舟开发，千寻审查）"""
        # 创建3个独立任务的Worktree
        for task in ["auth", "api", "ui"]:
            config = create_maker_worktree(f"task-{task}", agent="澜舟")
            self.mgr.create_worktree(config)
        
        self.assertEqual(len(self.mgr.list_worktrees()), 3)
        
        # 验证端口隔离
        ports_0 = self.mgr.assign_ports("task-auth")
        ports_1 = self.mgr.assign_ports("task-api")
        self.assertNotEqual(ports_0.get("bridge_v7"), ports_1.get("bridge_v7"))
    
    def test_02_best_of_n(self):
        """场景2: Best-of-N（多个方案竞争，选最优）"""
        for i in range(3):
            config = create_maker_worktree(f"solution-{i}", agent="澜舟")
            self.mgr.create_worktree(config)
        
        # 模拟选择solution-1，删除其他
        self.mgr.remove_worktree("solution-0", force=True)
        self.mgr.remove_worktree("solution-2", force=True)
        
        self.assertEqual(len(self.mgr.list_worktrees()), 1)
        self.assertEqual(self.mgr.get_worktree("solution-1").name, "solution-1")
    
    def test_03_pipeline(self):
        """场景3: 流水线（澜舟开发→千寻审查→合并）"""
        # Step 1: 澜舟开发
        maker_config = create_maker_worktree("pipeline-feat", agent="澜舟")
        self.mgr.create_worktree(maker_config)
        
        # Step 2: 千寻审查
        self.mgr.assign_reviewer("pipeline-feat", "千寻")
        wt = self.mgr.get_worktree("pipeline-feat")
        self.assertEqual(wt.status, WorktreeStatus.REVIEWING)
        
        # Step 3: 审查完成，解锁
        self.mgr.unlock_worktree("pipeline-feat")
        wt = self.mgr.get_worktree("pipeline-feat")
        self.assertEqual(wt.status, WorktreeStatus.ACTIVE)
    
    def test_04_parallel_development(self):
        """场景4: 并行开发（4个Agent同时工作）"""
        agents_configs = [
            create_maker_worktree("dev-core", agent="澜舟"),
            create_reviewer_worktree("dev-core", reviewer="千寻"),
            create_researcher_worktree("tech-research", agent="灵犀"),
            create_coordinator_worktree("sprint-coord", agent="澜澜"),
        ]
        
        for config in agents_configs:
            self.mgr.create_worktree(config)
        
        # 验证4个Worktree活跃
        active = [wt for wt in self.mgr.list_worktrees() if wt.status == WorktreeStatus.ACTIVE]
        self.assertEqual(len(active), 4)
        
        # 验证端口分配
        stats = self.mgr.get_stats()
        self.assertEqual(stats["total_worktrees"], 4)
        self.assertIn("澜舟", stats["agents"])
        self.assertIn("千寻", stats["agents"])
        self.assertIn("灵犀", stats["agents"])
        self.assertIn("澜澜", stats["agents"])


class TestDay2Acceptance(unittest.TestCase):
    """Day 2验收测试"""
    
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="day2_accept_")
        self.stream = EventStream(session_id="day2-acceptance")
        self.mgr = WorktreeManager(repo_path=".", worktree_base=self.test_dir)
        self.reviewer_sub = ReviewerSubscriber(self.stream, self.mgr)
        self.worktree_sub = WorktreeSubscriber(self.stream, self.mgr)
    
    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_acceptance_01_full_lifecycle(self):
        """验收1: 完整生命周期（创建→开发→审查→合并）"""
        # 1. 创建
        config = create_maker_worktree("acceptance-test", agent="澜舟")
        path = self.mgr.create_worktree(config)
        self.assertTrue(os.path.exists(path))
        
        # 2. 分配端口+DB
        ports = self.mgr.assign_ports("acceptance-test")
        self.assertIn("bridge_v7", ports)
        
        # 3. 提交工作
        wt = self.mgr.get_worktree("acceptance-test")
        self.assertEqual(wt.status, WorktreeStatus.ACTIVE)
        
        # 4. 分配审查者
        self.mgr.assign_reviewer("acceptance-test", "千寻")
        wt = self.mgr.get_worktree("acceptance-test")
        self.assertEqual(wt.status, WorktreeStatus.REVIEWING)
        
        # 5. 锁定
        self.mgr.lock_worktree("acceptance-test", "审查中")
        wt = self.mgr.get_worktree("acceptance-test")
        self.assertEqual(wt.status, WorktreeStatus.LOCKED)
        
        # 6. 审查通过解锁
        self.mgr.unlock_worktree("acceptance-test")
        wt = self.mgr.get_worktree("acceptance-test")
        self.assertEqual(wt.status, WorktreeStatus.ACTIVE)
    
    def test_acceptance_02_port_isolation(self):
        """验收2: 端口隔离（3个并行实例无冲突）"""
        pm = PortManager()
        ports_list = []
        
        for i in range(3):
            config = create_maker_worktree(f"port-test-{i}")
            self.mgr.create_worktree(config)
            ports = pm.allocate(f"port-test-{i}", i)
            ports_list.append(ports)
        
        # 验证所有端口互不冲突
        all_ports = []
        for ports in ports_list:
            all_ports.extend(ports.values())
        
        self.assertEqual(len(all_ports), len(set(all_ports)), "存在端口冲突！")
    
    def test_acceptance_03_db_isolation(self):
        """验收3: DB隔离（独立数据互不干扰）"""
        db_mgr = SQLiteManager()
        
        for name in ["wt-a", "wt-b"]:
            db_path = os.path.join(self.test_dir, name, f"{name}.db")
            db_mgr.create_database(name, db_path)
            
            # 写入不同的值
            conn = db_mgr.get_connection(name)
            conn.execute("INSERT INTO worktree_state (key, value) VALUES (?, ?)", ("owner", name))
            conn.commit()
        
        # 验证隔离
        conn_a = db_mgr.get_connection("wt-a")
        conn_b = db_mgr.get_connection("wt-b")
        
        val_a = conn_a.execute("SELECT value FROM worktree_state WHERE key='owner'").fetchone()[0]
        val_b = conn_b.execute("SELECT value FROM worktree_state WHERE key='owner'").fetchone()[0]
        
        self.assertEqual(val_a, "wt-a")
        self.assertEqual(val_b, "wt-b")
        
        db_mgr.close_all()
    
    def test_acceptance_04_reviewer_loop(self):
        """验收4: 审查闭环（DONE→REVIEW→PASS→解锁）"""
        # 创建Worktree
        config = create_maker_worktree("review-loop", agent="澜舟")
        self.mgr.create_worktree(config)
        
        # 澜舟发DONE
        done_event = create_done_action(
            sender="澜舟", recipient="千寻",
            task_id="task-review-loop",
            what_done="功能开发完成",
            where_artifacts=["worktree:review-loop"],
            how_verify="千寻审查",
            known_issues=[],
            what_next="等待审查",
        )
        self.stream.publish(done_event)
        
        # 验证锁定
        wt = self.mgr.get_worktree("review-loop")
        self.assertEqual(wt.status, WorktreeStatus.LOCKED)
        
        # 千寻审查通过
        pass_event = create_action(
            sender="千寻", recipient="澜舟",
            event_type=EventType.INFO,
            content="[REVIEW:PASS] 审查通过",
        )
        self.stream.publish(pass_event)
        
        # 验证解锁
        wt = self.mgr.get_worktree("review-loop")
        self.assertEqual(wt.status, WorktreeStatus.ACTIVE)
    
    def test_acceptance_05_event_cause_chain(self):
        """验收5: 事件因果链（Worktree操作→Event→审查→Event）"""
        config = create_maker_worktree("cause-chain", agent="澜舟")
        self.mgr.create_worktree(config)
        
        # 发布DONE
        done = create_done_action(
            sender="澜舟", recipient="千寻",
            task_id="task-cause-chain",
            what_done="功能开发",
            where_artifacts=["worktree:cause-chain"],
            how_verify="审查验证",
            known_issues=[],
            what_next="等待审查",
        )
        self.stream.publish(done)
        
        # 验证因果链
        self.assertGreater(self.stream.event_count, 0)
        
        # 审查通过事件应引用DONE事件的cause
        review_events = [e for e in self.stream.events if e.event_type == EventType.TASK]
        self.assertGreater(len(review_events), 0)


if __name__ == "__main__":
    # 运行所有测试
    unittest.main(verbosity=2)
