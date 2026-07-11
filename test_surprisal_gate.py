"""
surprisal_gate.py — 信息密度过滤器单元测试

覆盖范围：
- 分词（中英文混合）
- Surprisal 计算（内容/上下文/新词）
- 观察期（warmup）
- 阈值过滤
- 自适应阈值
- 频率模型更新
- 统计信息
- 批量处理
- 边界情况
- 线程安全
"""

import math
import threading
from collections import Counter

import pytest

from event_stream import (
    Action,
    EventSource,
    EventType,
    Observation,
    create_action,
    create_observation,
)
from surprisal_gate import (
    DEFAULT_ALPHA,
    DEFAULT_BETA,
    DEFAULT_GAMMA,
    DEFAULT_THRESHOLD,
    DEFAULT_WARMUP,
    MAX_VOCAB_SIZE,
    SurprisalGate,
    SurprisalResult,
    GateStats,
    create_gate,
    create_relaxed_gate,
    create_strict_gate,
    tokenize,
)


# ==================== 辅助函数 ====================


def _make_event(
    content: str = "测试消息",
    sender: str = "澜舟",
    recipient: str = "澜澜",
    etype: EventType = EventType.INFO,
) -> Action:
    return create_action(sender=sender, recipient=recipient, event_type=etype, content=content)


# ==================== 1. 分词测试 ====================


class TestTokenize:
    def test_english_only(self):
        tokens = tokenize("hello world test")
        assert tokens == ["hello", "world", "test"]

    def test_chinese_only(self):
        tokens = tokenize("你好世界")
        assert tokens == ["你", "好", "世", "界"]

    def test_mixed(self):
        tokens = tokenize("Hello世界test")
        assert "hello" in tokens
        assert "世" in tokens
        assert "界" in tokens
        assert "test" in tokens

    def test_lowercase(self):
        tokens = tokenize("HELLO World")
        assert tokens == ["hello", "world"]

    def test_numbers(self):
        tokens = tokenize("task 123 test456")
        assert "123" in tokens
        assert "test456" in tokens

    def test_empty(self):
        assert tokenize("") == []
        assert tokenize(None) == []  # type: ignore

    def test_punctuation_only(self):
        assert tokenize("!!!???") == []

    def test_chinese_punctuation(self):
        tokens = tokenize("完成。任务！")
        assert "完" in tokens
        assert "成" in tokens
        assert "任" in tokens
        assert "务" in tokens

    def test_underscore(self):
        tokens = tokenize("hello_world test_case")
        assert "hello_world" in tokens
        assert "test_case" in tokens


# ==================== 2. Surprisal 计算 ====================


class TestSurprisalCalculation:
    def test_basic_calculation(self):
        gate = SurprisalGate(warmup=0)
        event = _make_event("全新独特消息内容")
        result = gate.calculate_surprisal(event)
        assert result.total > 0
        assert result.content > 0
        assert result.context > 0

    def test_novelty_bonus_for_new_words(self):
        gate = SurprisalGate(warmup=0, gamma=0.1)
        # 全新事件 → 高新词奖励
        event = _make_event("全新的独特内容词汇")
        result = gate.calculate_surprisal(event)
        assert result.novelty > 0

    def test_no_novelty_for_seen_words(self):
        gate = SurprisalGate(warmup=0)
        event = _make_event("重复内容")
        gate.update(event)
        gate.update(event)
        gate.update(event)

        result = gate.calculate_surprisal(event)
        # 全部词都已见过 → novelty 应为 0
        assert result.novelty == 0.0

    def test_content_surprisal_decreases_with_repetition(self):
        """重复内容的 surprisal 应递减"""
        gate = SurprisalGate(warmup=0)
        event = _make_event("重复测试内容")

        # 第一次（全新）
        r1 = gate.calculate_surprisal(event)
        gate.update(event)

        # 第二次（已见）
        r2 = gate.calculate_surprisal(event)
        gate.update(event)

        # 第三次（更常见）
        r3 = gate.calculate_surprisal(event)

        assert r1.content > r2.content > r3.content

    def test_context_surprisal_decreases_with_repetition(self):
        """重复上下文的 surprisal 应递减"""
        gate = SurprisalGate(warmup=0)
        event = _make_event("内容", sender="A", recipient="B", etype=EventType.INFO)

        r1 = gate.calculate_surprisal(event)
        gate.update(event)
        r2 = gate.calculate_surprisal(event)
        gate.update(event)
        r3 = gate.calculate_surprisal(event)

        assert r1.context > r2.context > r3.context

    def test_different_context_higher_surprisal(self):
        """不同上下文的 surprisal 应高于重复上下文"""
        gate = SurprisalGate(warmup=0)
        e1 = _make_event("内容", sender="A", recipient="B")
        gate.update(e1)
        gate.update(e1)
        gate.update(e1)

        # 同上下文
        r_same = gate.calculate_surprisal(e1)

        # 不同上下文
        e2 = _make_event("内容", sender="X", recipient="Y", etype=EventType.WARN)
        r_diff = gate.calculate_surprisal(e2)

        assert r_diff.context > r_same.context

    def test_empty_content(self):
        gate = SurprisalGate(warmup=0)
        event = _make_event("")
        result = gate.calculate_surprisal(event)
        assert result.content == 0.0
        assert result.novelty == 0.0
        assert result.total >= 0  # 仍有上下文信息量

    def test_result_to_dict(self):
        gate = SurprisalGate(warmup=0)
        result = gate.calculate_surprisal(_make_event("测试"))
        d = result.to_dict()
        assert "total" in d
        assert "content" in d
        assert "context" in d
        assert "novelty" in d
        assert "passed" in d
        assert "reason" in d

    def test_weighted_sum(self):
        """验证加权求和公式"""
        gate = SurprisalGate(warmup=0, alpha=0.5, beta=0.3, gamma=0.2)
        event = _make_event("独特新内容")
        result = gate.calculate_surprisal(event)

        expected = 0.5 * result.content + 0.3 * result.context + 0.2 * result.novelty
        assert abs(result.total - expected) < 0.001


# ==================== 3. 观察期 ====================


class TestWarmup:
    def test_warmup_all_pass(self):
        """观察期内所有事件都通过"""
        gate = SurprisalGate(threshold=100.0, warmup=5)  # 极高阈值
        for i in range(5):
            result = gate.process(_make_event(f"warmup-{i}"))
            assert result.passed
            assert result.reason == "warmup"

    def test_warmup_expired_filters(self):
        """观察期结束后开始过滤"""
        gate = SurprisalGate(threshold=100.0, warmup=3)  # 极高阈值
        # 先更新频率模型让 "ok" 变得非常常见
        for _ in range(10):
            gate.update(_make_event("ok"))

        # warmup 期间
        for i in range(3):
            r = gate.process(_make_event("ok"))
            assert r.passed

        # warmup 结束后
        r = gate.process(_make_event("ok"))
        assert not r.passed
        assert r.reason == "low_density"

    def test_warmup_zero(self):
        """warmup=0 → 立即过滤"""
        gate = SurprisalGate(threshold=100.0, warmup=0)
        gate.update(_make_event("ok"))
        gate.update(_make_event("ok"))
        r = gate.process(_make_event("ok"))
        assert not r.passed

    def test_warmup_remaining(self):
        gate = SurprisalGate(warmup=10)
        stats = gate.get_stats()
        assert stats["warmup_remaining"] == 10

        gate.process(_make_event("test"))
        stats = gate.get_stats()
        assert stats["warmup_remaining"] == 9


# ==================== 4. 阈值过滤 ====================


class TestThresholdFiltering:
    def test_high_threshold_filters_more(self):
        """高阈值过滤更多"""
        gate_high = SurprisalGate(threshold=5.0, warmup=0, adaptive=False)
        gate_low = SurprisalGate(threshold=0.1, warmup=0, adaptive=False)

        events = [_make_event(f"消息内容{i}") for i in range(20)]
        for e in events:
            gate_high.process(e)
            gate_high.update(e)
            gate_low.process(e)
            gate_low.update(e)

        high_stats = gate_high.get_stats()
        low_stats = gate_low.get_stats()
        assert high_stats["filtered_events"] >= low_stats["filtered_events"]

    def test_zero_threshold_passes_all(self):
        """阈值为 0 → 全部通过"""
        gate = SurprisalGate(threshold=0.0, warmup=0, adaptive=False)
        for i in range(10):
            gate.update(_make_event("重复"))
        r = gate.process(_make_event("重复"))
        assert r.passed

    def test_should_pass_method(self):
        gate = SurprisalGate(threshold=0.0, warmup=0, adaptive=False)
        assert gate.should_pass(_make_event("test"))

    def test_filter_rate_increases_with_repetition(self):
        """重复率越高，过滤率越高"""
        gate = SurprisalGate(threshold=1.0, warmup=5, adaptive=False)

        # 先 warmup
        for i in range(5):
            gate.process(_make_event(f"warmup-{i}"))
            gate.update(_make_event(f"warmup-{i}"))

        # 然后大量重复 "ok"
        for _ in range(20):
            gate.process(_make_event("ok"))
            gate.update(_make_event("ok"))

        stats = gate.get_stats()
        assert stats["filter_rate"] > 0


# ==================== 5. 自适应阈值 ====================


class TestAdaptiveThreshold:
    def test_adaptive_disabled(self):
        gate = SurprisalGate(adaptive=False, threshold=1.0)
        assert gate.get_threshold() == 1.0

    def test_adaptive_with_few_samples(self):
        """样本不足时使用固定阈值"""
        gate = SurprisalGate(adaptive=True, threshold=1.0, adaptive_window=100)
        gate.process(_make_event("test"))
        assert gate.get_threshold() == 1.0

    def test_adaptive_adjusts(self):
        """足够样本后自适应调整"""
        gate = SurprisalGate(adaptive=True, threshold=1.0, adaptive_window=50, warmup=0)

        # 处理足够多事件
        for i in range(20):
            gate.process(_make_event(f"unique-event-{i}-content"))
            gate.update(_make_event(f"unique-event-{i}-content"))

        # 自适应阈值应与固定阈值不同（可能更高或更低，取决于分布）
        adaptive = gate.get_threshold()
        assert adaptive > 0

    def test_adaptive_bounded(self):
        """自适应阈值有上下界"""
        gate = SurprisalGate(
            adaptive=True, threshold=1.0, adaptive_window=20, warmup=0
        )

        for i in range(30):
            gate.process(_make_event(f"event-{i}"))
            gate.update(_make_event(f"event-{i}"))

        threshold = gate.get_threshold()
        # 不低于固定阈值的 50%
        assert threshold >= 0.5
        # 不超过固定阈值的 2 倍
        assert threshold <= 2.0


# ==================== 6. 频率模型更新 ====================


class TestFrequencyModel:
    def test_update_increases_token_freq(self):
        gate = SurprisalGate()
        gate.update(_make_event("测试内容"))
        assert gate._token_freq["测"] >= 1
        assert gate._token_freq["试"] >= 1

    def test_update_increases_context_freq(self):
        gate = SurprisalGate()
        e = _make_event("x", sender="A", recipient="B", etype=EventType.INFO)
        gate.update(e)
        key = ("A", "B", "INFO")
        assert gate._context_freq[key] == 1

    def test_vocab_size_grows(self):
        gate = SurprisalGate()
        gate.update(_make_event("苹果"))
        gate.update(_make_event("香蕉"))
        gate.update(_make_event("橙子"))
        stats = gate.get_stats()
        assert stats["vocab_size"] >= 5  # 至少 5 个不同的字

    def test_vocab_size_capped(self):
        """词汇表大小有上限"""
        gate = SurprisalGate()
        # 生成超过 MAX_VOCAB_SIZE 个不同 token
        for i in range(MAX_VOCAB_SIZE + 500):
            gate.update(_make_event(f"token{i}"))
        assert len(gate._token_freq) <= MAX_VOCAB_SIZE

    def test_reset_clears_model(self):
        gate = SurprisalGate()
        gate.update(_make_event("测试"))
        gate.update(_make_event("内容"))
        gate.reset()
        assert len(gate._token_freq) == 0
        assert gate._total_tokens == 0
        stats = gate.get_stats()
        assert stats["total_events"] == 0


# ==================== 7. 统计信息 ====================


class TestStats:
    def test_initial_stats(self):
        gate = SurprisalGate(warmup=10)
        stats = gate.get_stats()
        assert stats["total_events"] == 0
        assert stats["passed_events"] == 0
        assert stats["filtered_events"] == 0
        assert stats["warmup_remaining"] == 10

    def test_stats_after_processing(self):
        gate = SurprisalGate(threshold=0.0, warmup=0, adaptive=False)
        for i in range(10):
            gate.process(_make_event(f"event-{i}"))

        stats = gate.get_stats()
        assert stats["total_events"] == 10
        assert stats["passed_events"] == 10
        assert stats["filtered_events"] == 0

    def test_stats_avg_surprisal(self):
        gate = SurprisalGate(warmup=0, adaptive=False)
        gate.process(_make_event("test content here"))
        stats = gate.get_stats()
        assert stats["avg_surprisal"] > 0

    def test_stats_min_max(self):
        gate = SurprisalGate(warmup=0, adaptive=False)
        r1 = gate.process(_make_event("a"))
        r2 = gate.process(_make_event("completely different unique content"))
        stats = gate.get_stats()
        assert stats["min_surprisal"] <= stats["max_surprisal"]

    def test_stats_filter_rate(self):
        gate = SurprisalGate(threshold=0.0, warmup=0, adaptive=False)
        for _ in range(5):
            gate.process(_make_event("test"))
        stats = gate.get_stats()
        assert stats["filter_rate"] == 0.0  # 阈值为 0 → 全通过

    def test_gate_stats_dataclass(self):
        """测试 GateStats 数据类属性"""
        stats = GateStats(total_events=10, passed_events=8, filtered_events=2)
        assert stats.avg_surprisal == 0.0  # total_surprisal=0
        assert stats.filter_rate == 0.2


# ==================== 8. 批量处理 ====================


class TestBatchProcessing:
    def test_process_batch(self):
        gate = SurprisalGate(warmup=0, adaptive=False)
        events = [_make_event(f"batch-{i}") for i in range(10)]
        results = gate.process_batch(events)
        assert len(results) == 10
        for event, result in results:
            assert isinstance(result, SurprisalResult)

    def test_filter_batch(self):
        gate = SurprisalGate(threshold=0.0, warmup=0, adaptive=False)
        events = [_make_event(f"batch-{i}") for i in range(10)]
        passed = gate.filter_batch(events)
        assert len(passed) == 10  # 阈值为 0 → 全通过

    def test_filter_batch_with_filtering(self):
        gate = SurprisalGate(threshold=100.0, warmup=2, adaptive=False)
        # 先 warmup
        gate.process_batch([_make_event("w1"), _make_event("w2")])
        # 然后过滤
        events = [_make_event("repeat") for _ in range(5)]
        passed = gate.filter_batch(events)
        assert len(passed) < 5  # 部分被过滤


# ==================== 9. 边界情况 ====================


class TestEdgeCases:
    def test_none_content(self):
        """content 为 None 时不崩溃"""
        gate = SurprisalGate(warmup=0)
        event = _make_event("")
        result = gate.calculate_surprisal(event)
        assert result.total >= 0

    def test_very_long_content(self):
        """超长内容不崩溃"""
        gate = SurprisalGate(warmup=0)
        content = "测试" * 1000
        event = _make_event(content)
        result = gate.calculate_surprisal(event)
        assert result.total > 0

    def test_special_characters(self):
        """特殊字符处理"""
        gate = SurprisalGate(warmup=0)
        event = _make_event("!@#$%^&*()_+-=[]{}|;':\",./<>?`~")
        result = gate.calculate_surprisal(event)
        assert result.total >= 0

    def test_unicode_emoji(self):
        """emoji 处理"""
        gate = SurprisalGate(warmup=0)
        event = _make_event("测试emoji内容🎉🎊")
        result = gate.calculate_surprisal(event)
        assert result.total > 0

    def test_repeated_reset(self):
        """多次 reset 不崩溃"""
        gate = SurprisalGate()
        gate.update(_make_event("test"))
        gate.reset()
        gate.reset()
        gate.reset()
        assert gate._total_tokens == 0

    def test_process_without_update(self):
        """process 不调用 update → 频率模型不变"""
        gate = SurprisalGate(warmup=0)
        gate.process(_make_event("test content"))
        assert gate._total_tokens == 0  # 未 update

    def test_update_without_process(self):
        """update 不调用 process → 统计不变"""
        gate = SurprisalGate(warmup=0)
        gate.update(_make_event("test content"))
        stats = gate.get_stats()
        assert stats["total_events"] == 0  # 未 process


# ==================== 10. 工厂函数 ====================


class TestFactoryFunctions:
    def test_create_gate_default(self):
        gate = create_gate()
        assert isinstance(gate, SurprisalGate)
        assert gate.threshold == DEFAULT_THRESHOLD

    def test_create_gate_custom(self):
        gate = create_gate(threshold=2.0, warmup=5, adaptive=False)
        assert gate.threshold == 2.0
        assert gate.warmup == 5
        assert gate.adaptive is False

    def test_create_strict_gate(self):
        gate = create_strict_gate()
        assert gate.threshold == 2.0
        assert gate.adaptive is False

    def test_create_relaxed_gate(self):
        gate = create_relaxed_gate()
        assert gate.threshold == 0.3
        assert gate.adaptive is False


# ==================== 11. 线程安全 ====================


class TestThreadSafety:
    def test_concurrent_update(self):
        """并发 update 不崩溃"""
        gate = SurprisalGate()
        errors = []

        def worker(start: int):
            try:
                for i in range(start, start + 50):
                    gate.update(_make_event(f"thread-{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i * 50,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert gate._total_tokens > 0

    def test_concurrent_process(self):
        """并发 process 不崩溃"""
        gate = SurprisalGate(warmup=0, adaptive=False)
        results = []

        def worker(start: int):
            for i in range(start, start + 20):
                r = gate.process(_make_event(f"concurrent-{i}"))
                results.append(r)

        threads = [threading.Thread(target=worker, args=(i * 20,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 80
        stats = gate.get_stats()
        assert stats["total_events"] == 80


# ==================== 12. 集成场景 ====================


class TestIntegration:
    def test_realistic_workflow(self):
        """模拟真实工作流：混合高低信息密度事件"""
        gate = SurprisalGate(threshold=0.8, warmup=10, adaptive=True)

        # warmup：多样化的初始事件
        warmup_events = [
            _make_event(f"初始化模块{i}", sender="系统", recipient="澜舟")
            for i in range(10)
        ]
        for e in warmup_events:
            gate.process(e)
            gate.update(e)

        # 低信息：大量重复 ACK
        ack_count = 0
        for _ in range(20):
            e = _make_event("ok", sender="澜舟", recipient="澜澜", etype=EventType.ACK)
            r = gate.process(e)
            gate.update(e)
            if not r.passed:
                ack_count += 1

        # 高信息：新任务
        task_passed = 0
        for i in range(10):
            e = _make_event(
                f"新任务#{i}：调研方向{i}的可行性分析报告",
                sender="九重",
                recipient="澜舟",
                etype=EventType.TASK,
            )
            r = gate.process(e)
            gate.update(e)
            if r.passed:
                task_passed += 1

        stats = gate.get_stats()
        # ACK 应有较高过滤率
        assert ack_count > 0
        # 新任务应大部分通过
        assert task_passed > 5
        # 总过滤率合理
        assert 0 < stats["filter_rate"] < 1

    def test_gate_with_different_event_types(self):
        """不同事件类型的 surprisal 差异"""
        gate = SurprisalGate(warmup=0, adaptive=False)
        types = [EventType.INFO, EventType.TASK, EventType.WARN, EventType.DONE, EventType.ASK]

        results = {}
        for t in types:
            e = _make_event("测试内容", etype=t)
            r = gate.calculate_surprisal(e)
            results[t.value] = r.total
            gate.update(e)

        # 所有类型都应产生有效 surprisal
        for val in results.values():
            assert val > 0


# ==================== 入口 ====================


if __name__ == "__main__":
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=sys.path[0] or ".",
    )
    sys.exit(result.returncode)
