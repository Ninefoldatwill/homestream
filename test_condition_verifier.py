"""
test_condition_verifier.py — ConditionVerifier 单元测试

覆盖:
  TestKappaCondition    — 连续 N 次相同输出 → KAPPA 停止
  TestPhiCondition      — DONE + 无 pending → PHI 停止
  TestEmptyCondition    — 超时无新事件 → EMPTY 停止
  TestErrorCondition    — 连续 N 次错误 → ERROR 停止
  TestMaxIterCondition  — 达到最大迭代 → MAX_ITER 停止
  TestContinueOnFail    — 未命中条件 → should_stop=False + CONTINUE
  TestVerifierSubscriber — DONE → 验证 → STOPPED/CONTINUE Observation
  TestWALIntegration    — dump_state / load_state 持久化
  TestWorktreeConfig    — 不同配置不同行为
  TestFactoryFunctions  — 便捷工厂函数
"""

import time
import unittest
import uuid
from typing import Optional

from event_stream import (
    Action,
    EventSource,
    EventStream,
    EventType,
    Observation,
    _gen_event_id,
    create_action,
    create_done_action,
)
from condition_verifier import (
    ConditionVerifier,
    VerifierConfig,
    VerifierSubscriber,
    VerificationResult,
    StopCondition,
    WALVerifierMixin,
    attach_verifier_to_stream,
    create_default_verifier,
    create_lenient_verifier,
    create_strict_verifier,
)


# ==================== 辅助函数 ====================

def _mk_stream(session_id: Optional[str] = None) -> EventStream:
    return EventStream(session_id or f"test-{uuid.uuid4().hex[:6]}")


def _mk_done_event(sender: str = "澜舟", recipient: str = "九重") -> Action:
    return Action(
        event_id=_gen_event_id("act"),
        event_type=EventType.DONE,
        sender=sender,
        recipient=recipient,
        content="[DONE] 任务完成",
        source=EventSource.AGENT,
    )


def _mk_task_event(sender: str = "九重", recipient: str = "澜舟") -> Action:
    return Action(
        event_id=_gen_event_id("act"),
        event_type=EventType.TASK,
        sender=sender,
        recipient=recipient,
        content="[TASK] 请完成分析",
        source=EventSource.USER,
    )


# ==================== 测试用例 ====================

class TestKappaCondition(unittest.TestCase):
    """KAPPA — 输出收敛检测"""

    def test_no_convergence_when_outputs_vary(self):
        """不同输出不触发 KAPPA"""
        stream = _mk_stream()
        cfg = VerifierConfig(kappa_window=3, kappa_threshold=0.95)
        v = ConditionVerifier(cfg)
        v.notify_action("INFO", "苹果 橙子 香蕉")
        v.notify_action("INFO", "cat dog bird fish")
        v.notify_action("INFO", "数学 物理 化学")
        result = v.check(stream)
        self.assertFalse(result.should_stop or result.condition == StopCondition.KAPPA,
                         f"不应触发 KAPPA，但得到: {result}")

    def test_kappa_triggers_on_convergent_outputs(self):
        """相同输出连续 3 次 → KAPPA 停止"""
        stream = _mk_stream()
        cfg = VerifierConfig(kappa_window=3, kappa_threshold=0.95,
                             max_iterations=999, empty_timeout=0,
                             phi_required=False, enable_deep_check=False)
        v = ConditionVerifier(cfg)
        same = "这是一段完全相同的输出内容用于测试收敛"
        for _ in range(3):
            v.notify_action("INFO", same)
        result = v.check(stream)
        # KAPPA 在 deep_check 中检测，需开启 enable_deep_check
        # 重新用 enable_deep_check=True 测试
        cfg2 = VerifierConfig(kappa_window=3, kappa_threshold=0.95,
                              max_iterations=999, empty_timeout=0)
        v2 = ConditionVerifier(cfg2)
        for _ in range(3):
            v2.notify_action("INFO", same)
        result2 = v2.check(stream)
        self.assertTrue(result2.should_stop)
        self.assertEqual(result2.condition, StopCondition.KAPPA)

    def test_jaccard_similarity_identical(self):
        """完全相同文本 → 相似度 1.0"""
        from condition_verifier import ConditionVerifier
        sim = ConditionVerifier._jaccard_similarity("hello world", "hello world")
        self.assertAlmostEqual(sim, 1.0)

    def test_jaccard_similarity_disjoint(self):
        """完全不同文本 → 相似度 0.0"""
        sim = ConditionVerifier._jaccard_similarity("apple banana", "cat dog")
        self.assertAlmostEqual(sim, 0.0)

    def test_jaccard_similarity_partial(self):
        """部分重叠 → 0 < 相似度 < 1"""
        sim = ConditionVerifier._jaccard_similarity("apple banana cherry", "banana cherry durian")
        self.assertGreater(sim, 0.0)
        self.assertLess(sim, 1.0)


class TestPhiCondition(unittest.TestCase):
    """PHI — 目标达成检测"""

    def test_phi_triggers_when_done_no_pending(self):
        """有 DONE 事件且无 pending TASK → PHI 停止"""
        stream = _mk_stream()
        done = _mk_done_event()
        stream.publish(done)

        cfg = VerifierConfig(max_iterations=999, empty_timeout=0,
                             enable_deep_check=True)
        v = ConditionVerifier(cfg)
        result = v.check(stream)
        self.assertTrue(result.should_stop)
        self.assertEqual(result.condition, StopCondition.PHI)

    def test_phi_does_not_trigger_with_pending_tasks(self):
        """有 DONE 也有随后的 TASK（新任务已分配）→ 不触发 PHI"""
        import time as _time

        stream = _mk_stream()
        # 先发 DONE
        done = _mk_done_event()
        stream.publish(done)

        # 稍等确保时间戳差异
        _time.sleep(0.01)

        # 再发新 TASK（时间戳晚于 DONE → 有 pending after done）
        new_task = Action(
            event_id=_gen_event_id("act"),
            event_type=EventType.TASK,
            sender="九重",
            recipient="澜舟",
            content="[TASK] 继续做下一个任务",
            source=EventSource.USER,
        )
        stream.publish(new_task)

        cfg = VerifierConfig(max_iterations=999, empty_timeout=0,
                             enable_deep_check=True)
        v = ConditionVerifier(cfg)
        result = v.check(stream)
        # DONE 之后有新 TASK → pending_after_done 非空 → 不应触发 PHI
        self.assertFalse(
            result.should_stop and result.condition == StopCondition.PHI,
            f"不应触发 PHI（有后续新任务），但得到: {result}"
        )


class TestEmptyCondition(unittest.TestCase):
    """EMPTY — 空闲超时检测"""

    def test_empty_triggers_after_timeout(self):
        """超时 0.1s 无新 Action → EMPTY 停止"""
        stream = _mk_stream()
        cfg = VerifierConfig(empty_timeout=0.05,  # 50ms
                             max_iterations=999, enable_deep_check=False)
        v = ConditionVerifier(cfg)
        # 等待超时
        time.sleep(0.1)
        result = v.check(stream)
        self.assertTrue(result.should_stop)
        self.assertEqual(result.condition, StopCondition.EMPTY)

    def test_empty_does_not_trigger_immediately(self):
        """刚刚重置的验证器不应立即触发 EMPTY"""
        stream = _mk_stream()
        cfg = VerifierConfig(empty_timeout=30.0,  # 30 秒
                             max_iterations=999, enable_deep_check=False)
        v = ConditionVerifier(cfg)
        result = v.check(stream)
        self.assertFalse(
            result.condition == StopCondition.EMPTY,
            "不应立即触发 EMPTY"
        )

    def test_empty_disabled_when_zero(self):
        """empty_timeout=0 → 不启用 EMPTY 检测"""
        stream = _mk_stream()
        cfg = VerifierConfig(empty_timeout=0, max_iterations=999,
                             enable_deep_check=False)
        v = ConditionVerifier(cfg)
        time.sleep(0.05)
        result = v.check(stream)
        self.assertNotEqual(result.condition, StopCondition.EMPTY)


class TestErrorCondition(unittest.TestCase):
    """ERROR — 连续错误兜底"""

    def test_error_triggers_after_consecutive_errors(self):
        """连续 3 次 ERROR → ERROR 停止"""
        stream = _mk_stream()
        cfg = VerifierConfig(max_consecutive_errors=3,
                             max_iterations=999, empty_timeout=0,
                             enable_deep_check=False)
        v = ConditionVerifier(cfg)
        for _ in range(3):
            v.notify_action("ERROR")
        result = v.check(stream)
        self.assertTrue(result.should_stop)
        self.assertEqual(result.condition, StopCondition.ERROR)

    def test_error_resets_on_success(self):
        """错误后成功 → 连续错误计数清零"""
        stream = _mk_stream()
        cfg = VerifierConfig(max_consecutive_errors=3,
                             max_iterations=999, empty_timeout=0,
                             enable_deep_check=False)
        v = ConditionVerifier(cfg)
        v.notify_action("ERROR")
        v.notify_action("ERROR")
        v.notify_action("INFO", "success")  # 清零
        result = v.check(stream)
        self.assertFalse(result.condition == StopCondition.ERROR,
                         "清零后不应触发 ERROR")

    def test_warn_counted_as_error(self):
        """WARN 类型应被计入错误"""
        stream = _mk_stream()
        cfg = VerifierConfig(max_consecutive_errors=3,
                             max_iterations=999, empty_timeout=0,
                             enable_deep_check=False)
        v = ConditionVerifier(cfg)
        # notify_action 中 WARN → is_error=True
        v.notify_action(EventType.WARN.value)
        v.notify_action(EventType.WARN.value)
        v.notify_action(EventType.WARN.value)
        result = v.check(stream)
        self.assertTrue(result.should_stop)
        self.assertEqual(result.condition, StopCondition.ERROR)


class TestMaxIterCondition(unittest.TestCase):
    """MAX_ITER — 循环上限"""

    def test_max_iter_triggers_exactly_at_limit(self):
        """第 N 次 check() 时触发 MAX_ITER"""
        stream = _mk_stream()
        cfg = VerifierConfig(max_iterations=5,
                             empty_timeout=0, enable_deep_check=False)
        v = ConditionVerifier(cfg)
        for i in range(5):
            result = v.check(stream)
        self.assertTrue(result.should_stop)
        self.assertEqual(result.condition, StopCondition.MAX_ITER)

    def test_max_iter_disabled_when_zero(self):
        """max_iterations=0 → 不限制迭代次数"""
        stream = _mk_stream()
        cfg = VerifierConfig(max_iterations=0,
                             empty_timeout=0, enable_deep_check=False)
        v = ConditionVerifier(cfg)
        for _ in range(100):
            result = v.check(stream)
        self.assertNotEqual(result.condition, StopCondition.MAX_ITER)


class TestContinueOnFail(unittest.TestCase):
    """未命中任何停止条件 → should_stop=False"""

    def test_no_stop_condition_returns_continue(self):
        """空流 + 未超时 + 无错误 → CONTINUE"""
        stream = _mk_stream()
        cfg = VerifierConfig(max_iterations=999, empty_timeout=999,
                             enable_deep_check=True)
        v = ConditionVerifier(cfg)
        result = v.check(stream)
        self.assertFalse(result.should_stop)

    def test_custom_checker_can_force_stop(self):
        """自定义检查器可以触发停止"""
        stream = _mk_stream()
        v = create_default_verifier()

        def always_stop(s, state):
            return VerificationResult(
                condition=StopCondition.PHI,
                should_stop=True,
                confidence=1.0,
                reason="自定义检查器强制停止",
            )

        v.register_checker(always_stop)
        result = v.check(stream)
        self.assertTrue(result.should_stop)
        self.assertEqual(result.reason, "自定义检查器强制停止")

    def test_custom_checker_none_falls_through(self):
        """自定义检查器返回 None → 不阻断后续流程"""
        stream = _mk_stream()
        cfg = VerifierConfig(max_iterations=999, empty_timeout=999,
                             enable_deep_check=False)
        v = ConditionVerifier(cfg)
        v.register_checker(lambda s, state: None)
        result = v.check(stream)
        self.assertFalse(result.should_stop)


class TestVerifierSubscriber(unittest.TestCase):
    """VerifierSubscriber — DONE 事件 → STOPPED/CONTINUE Observation"""

    def _setup(self, cfg: Optional[VerifierConfig] = None):
        stream = _mk_stream()
        sub = VerifierSubscriber(stream=stream, config=cfg)
        return stream, sub

    def test_done_with_no_pending_triggers_stopped(self):
        """DONE 后无 TASK → 验证通过 → STOPPED Observation"""
        stream, sub = self._setup(
            VerifierConfig(max_iterations=999, empty_timeout=0)
        )

        received_obs = []
        stream.subscribe(VerifierSubscriber.VERIFIER_AGENT_NAME, lambda e: None)
        # 订阅 DONE 类型（STOPPED obs 用 DONE 类型）
        stream.subscribe_by_type(EventType.DONE, lambda e: received_obs.append(e)
                                 if e.sender == VerifierSubscriber.VERIFIER_AGENT_NAME
                                 else None)

        done = _mk_done_event(sender="澜舟", recipient="九重")
        stream.publish(done)

        # 等待订阅者回调
        time.sleep(0.05)
        stopped = [e for e in received_obs
                   if e.sender == VerifierSubscriber.VERIFIER_AGENT_NAME
                   and "[STOPPED]" in e.content]
        self.assertTrue(len(stopped) > 0,
                        f"期望收到 STOPPED，但 received_obs={[e.content for e in received_obs]}")

    def test_attach_verifier_to_stream(self):
        """attach_verifier_to_stream 工厂函数正常工作"""
        stream = _mk_stream()
        sub = attach_verifier_to_stream(stream)
        self.assertIsInstance(sub, VerifierSubscriber)
        self.assertIsNotNone(sub.verifier)


class TestWALIntegration(unittest.TestCase):
    """WALVerifierMixin — dump_state / load_state 持久化"""

    def test_dump_and_load_roundtrip(self):
        """dump → load 后状态一致"""
        v = create_default_verifier()
        for i in range(7):
            v.notify_action("INFO", f"output {i}")
        v.notify_action("ERROR")
        v.notify_action("ERROR")

        snapshot = v.dump_state()
        self.assertEqual(snapshot["iteration"], 0)  # check() 未调用
        self.assertEqual(snapshot["consecutive_errors"], 2)

        v2 = create_default_verifier()
        v2.load_state(snapshot)
        self.assertEqual(v2._state["consecutive_errors"], 2)

    def test_wal_mixin_save_restore(self):
        """WALVerifierMixin save/restore 流程"""
        class MockService(WALVerifierMixin):
            def __init__(self):
                self._store = {}

            def _update(self, sid, field, val):
                self._store[(sid, field)] = val

            def _get(self, sid, field):
                return self._store.get((sid, field))

        svc = MockService()
        v = create_default_verifier()
        v.notify_action("ERROR")
        v.notify_action("ERROR")

        svc.save_verifier_state("sess-1", v, svc._update)

        v2 = create_default_verifier()
        ok = svc.restore_verifier_state("sess-1", v2, svc._get)
        self.assertTrue(ok)
        self.assertEqual(v2._state["consecutive_errors"], 2)

    def test_restore_nonexistent_session_returns_false(self):
        """不存在的 session → 恢复失败返回 False"""
        class MockService(WALVerifierMixin):
            def _get(self, sid, field):
                return None

        svc = MockService()
        v = create_default_verifier()
        ok = svc.restore_verifier_state("nonexistent", v, svc._get)
        self.assertFalse(ok)


class TestWorktreeConfig(unittest.TestCase):
    """不同 Worktree 配置 → 不同验证行为"""

    def test_strict_verifier_stops_sooner(self):
        """严格验证器在 max_iterations=20 时比宽松 50 次更快停止"""
        strict_v = create_strict_verifier()
        lenient_v = create_lenient_verifier()
        stream = _mk_stream()

        strict_stop_iter = None
        for i in range(30):
            r = strict_v.check(stream)
            if r.condition == StopCondition.MAX_ITER and r.should_stop:
                strict_stop_iter = i + 1
                break

        self.assertIsNotNone(strict_stop_iter)
        self.assertLessEqual(strict_stop_iter, 20)

    def test_lenient_verifier_tolerates_more_errors(self):
        """宽松验证器允许更多连续错误"""
        v = create_lenient_verifier()
        stream = _mk_stream()
        # max_consecutive_errors=10，先触发 5 次
        for _ in range(5):
            v.notify_action("ERROR")
        result = v.check(stream)
        self.assertFalse(result.condition == StopCondition.ERROR and result.should_stop,
                         "宽松模式不应在第 5 次错误时停止")


class TestFactoryFunctions(unittest.TestCase):
    """便捷工厂函数"""

    def test_create_default_verifier(self):
        v = create_default_verifier()
        self.assertEqual(v.config.max_iterations, 50)
        self.assertEqual(v.config.max_consecutive_errors, 3)

    def test_create_strict_verifier(self):
        v = create_strict_verifier()
        self.assertEqual(v.config.max_iterations, 20)
        self.assertTrue(v.config.require_human_confirm)

    def test_create_lenient_verifier(self):
        v = create_lenient_verifier()
        self.assertEqual(v.config.max_iterations, 200)
        self.assertFalse(v.config.enable_deep_check)

    def test_reset_clears_state(self):
        """reset() 后状态归零"""
        v = create_default_verifier()
        for _ in range(10):
            v.notify_action("ERROR")
        v.reset()
        self.assertEqual(v._state["consecutive_errors"], 0)
        self.assertEqual(v._state["iteration"], 0)


# ==================== 集成测试：完整循环 ====================

class TestFullLoopIntegration(unittest.TestCase):
    """模拟完整 Agent 循环 — EventStream + VerifierSubscriber"""

    def test_agent_loop_terminates_on_done(self):
        """模拟 Agent 循环：发 DONE → 验证通过 → 循环终止"""
        stream = _mk_stream()
        cfg = VerifierConfig(max_iterations=999, empty_timeout=0)
        sub = VerifierSubscriber(stream=stream, config=cfg)

        stopped_events = []
        stream.subscribe_by_type(
            EventType.DONE,
            lambda e: stopped_events.append(e)
            if e.sender == VerifierSubscriber.VERIFIER_AGENT_NAME else None
        )

        # 模拟 Agent 发布 DONE（无 pending TASK）
        done = _mk_done_event("澜舟", "九重")
        stream.publish(done)
        time.sleep(0.05)

        self.assertTrue(
            any("[STOPPED]" in e.content for e in stopped_events),
            f"期望收到 STOPPED，实际: {[e.content for e in stopped_events]}"
        )

    def test_agent_loop_continues_before_done(self):
        """未发 DONE 时不应停止"""
        stream = _mk_stream()
        cfg = VerifierConfig(max_iterations=999, empty_timeout=999)
        v = ConditionVerifier(cfg)
        # 发几条非 DONE 事件
        task = _mk_task_event()
        stream.publish(task)
        result = v.check(stream)
        self.assertFalse(result.should_stop,
                         f"未发 DONE 不应停止，得到: {result}")


# ==================== 运行 ====================

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestKappaCondition,
        TestPhiCondition,
        TestEmptyCondition,
        TestErrorCondition,
        TestMaxIterCondition,
        TestContinueOnFail,
        TestVerifierSubscriber,
        TestWALIntegration,
        TestWorktreeConfig,
        TestFactoryFunctions,
        TestFullLoopIntegration,
    ]

    for tc in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(tc))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
