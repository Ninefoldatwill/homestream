"""
SurprisalGate — 信息密度过滤器（v5.1.0 新增）

基于预测编码理论（Predictive Coding），在 EventStream 写入时评估
每条事件的信息密度（Surprisal），过滤低信息输入，减少噪声。

理论基础（干净室实现，基于公开学术理论从零编写）：
  - 香农信息熵：H(X) = -Σ P(x) * log2(P(x))
  - 自信息量（Surprisal）：I(x) = -log2(P(x))
  - 预测编码：大脑只处理"意外"（高 Surprisal）的输入，
    可预测的输入被自动过滤（Rao & Ballard, 1999）

自研信息熵计算公式：
  surprisal = α * content_surprisal + β * context_surprisal + γ * novelty_bonus

  其中：
  - content_surprisal = avg(-log2(P(token)))  按内容词频计算
  - context_surprisal = -log2(P(type|sender,recipient))  按上下文模式计算
  - novelty_bonus = 新词比例 × 2.0  首次出现的词给予信息增益
  - α=0.6, β=0.3, γ=0.1（可配置）

工作方式：
  1. 观察阶段：gate 观察 N 条事件建立频率模型
  2. 过滤阶段：低于 threshold 的事件被标记为 low_density
  3. 自适应：threshold 根据近期信息密度分布动态调整

集成方式：
  from surprisal_gate import SurprisalGate
  gate = SurprisalGate(threshold=1.0)
  # 在 EventStream.publish() 前调用
  should_pass = gate.should_pass(event)
  gate.update(event)  # 更新频率模型
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from event_stream import Event, EventType

# ==================== 常量 ====================

# 默认权重
DEFAULT_ALPHA = 0.6  # 内容信息量权重
DEFAULT_BETA = 0.3  # 上下文信息量权重
DEFAULT_GAMMA = 0.1  # 新词奖励权重

# 默认阈值
DEFAULT_THRESHOLD = 1.0  # bits

# 观察期：前 N 条事件不做过滤，只学习
DEFAULT_WARMUP = 20

# 平滑因子（防止 log2(0)）
SMOOTHING = 1e-10

# 最大频率表大小（防止内存无限增长）
MAX_VOCAB_SIZE = 10000

# CJK 字符范围（中日韩统一表意文字）
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
# 分词正则：匹配 CJK 单字 或 拉丁文词
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]|[a-zA-Z0-9_]+")


# ==================== 数据结构 ====================


@dataclass
class SurprisalResult:
    """单条事件的 Surprisal 计算结果"""

    total: float  # 总信息量（bits）
    content: float  # 内容信息量
    context: float  # 上下文信息量
    novelty: float  # 新词奖励
    passed: bool  # 是否通过门控
    reason: str  # 判定原因

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 4),
            "content": round(self.content, 4),
            "context": round(self.context, 4),
            "novelty": round(self.novelty, 4),
            "passed": self.passed,
            "reason": self.reason,
        }


@dataclass
class GateStats:
    """门控统计信息"""

    total_events: int = 0
    passed_events: int = 0
    filtered_events: int = 0
    total_surprisal: float = 0.0
    min_surprisal: float = float("inf")
    max_surprisal: float = 0.0
    vocab_size: int = 0
    context_patterns: int = 0
    warmup_remaining: int = 0

    @property
    def avg_surprisal(self) -> float:
        if self.total_events == 0:
            return 0.0
        return self.total_surprisal / self.total_events

    @property
    def filter_rate(self) -> float:
        if self.total_events == 0:
            return 0.0
        return self.filtered_events / self.total_events

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "passed_events": self.passed_events,
            "filtered_events": self.filtered_events,
            "avg_surprisal": round(self.avg_surprisal, 4),
            "min_surprisal": round(self.min_surprisal, 4)
            if self.min_surprisal != float("inf")
            else 0.0,
            "max_surprisal": round(self.max_surprisal, 4),
            "filter_rate": round(self.filter_rate, 4),
            "vocab_size": self.vocab_size,
            "context_patterns": self.context_patterns,
            "warmup_remaining": self.warmup_remaining,
        }


# ==================== 分词 ====================


def tokenize(text: str) -> list[str]:
    """将文本分词（支持中英文混合）

    策略：
    - CJK 字符 → 每个字单独成词（适合中文信息密度计算）
    - 拉丁文 → 连续字母数字下划线组成一个词
    - 全部小写化（拉丁文部分）

    示例：
      "Hello世界test" → ["hello", "世", "界", "test"]
      "完成任务T001" → ["完成", "任", "务", "t001"]
    """
    if not text:
        return []
    return _TOKEN_PATTERN.findall(text.lower())


# ==================== SurprisalGate 核心类 ====================


class SurprisalGate:
    """信息密度过滤器

    基于预测编码理论，在事件流中过滤低信息密度的输入。
    采用频率统计模型 + 香农自信息公式计算每条事件的 Surprisal。

    使用方式：
        gate = SurprisalGate(threshold=1.0)

        # 处理事件
        result = gate.process(event)
        if result.passed:
            stream.publish(event)  # 通过门控，写入流
        gate.update(event)  # 无论是否通过都更新频率模型

    线程安全：内部使用 RLock 保护频率表操作。
    """

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        warmup: int = DEFAULT_WARMUP,
        alpha: float = DEFAULT_ALPHA,
        beta: float = DEFAULT_BETA,
        gamma: float = DEFAULT_GAMMA,
        adaptive: bool = True,
        adaptive_window: int = 100,
    ):
        """初始化 SurprisalGate

        Args:
            threshold: 过滤阈值（bits），低于此值的事件被过滤
            warmup: 观察期事件数，前 N 条不过滤只学习
            alpha: 内容信息量权重
            beta: 上下文信息量权重
            gamma: 新词奖励权重
            adaptive: 是否启用自适应阈值
            adaptive_window: 自适应窗口大小（最近 N 条事件的统计）
        """
        self.threshold = threshold
        self.warmup = warmup
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.adaptive = adaptive
        self.adaptive_window = adaptive_window

        self._lock = threading.RLock()

        # 频率模型
        self._token_freq: Counter = Counter()  # token → count
        self._total_tokens: int = 0
        self._type_freq: Counter = Counter()  # EventType → count
        self._total_events: int = 0
        self._context_freq: Counter = Counter()  # (sender, recipient, type) → count
        self._total_contexts: int = 0

        # 自适应：滑动窗口记录最近的 surprisal 值
        self._recent_surprisals: list[float] = []

        # 统计
        self._stats = GateStats(warmup_remaining=warmup)

    # ==================== 核心计算 ====================

    def _calc_content_surprisal(self, content: str) -> tuple[float, float]:
        """计算内容信息量和新词比例

        Returns:
            (content_surprisal, novelty_ratio)
        """
        tokens = tokenize(content)
        if not tokens:
            return 0.0, 0.0

        # 冷启动：无历史数据时，所有 token 都是全新的，给予最高 surprisal
        if self._total_tokens == 0:
            # log2(1/SMOOTHING) 是理论最大值，实际用 token 数的对数更合理
            max_surprisal = -math.log2(SMOOTHING)
            # 但为可读性，用一个合理上限（比如每个 token 10 bits）
            per_token = min(max_surprisal, 10.0)
            return per_token, 1.0  # 全新 → novelty_ratio = 1.0

        total_surprisal = 0.0
        novel_count = 0

        for token in tokens:
            freq = self._token_freq.get(token, 0)
            # P(token) = (freq + 1) / (total + vocab + 1)
            # 使用加一平滑（拉普拉斯平滑）
            prob = (freq + 1) / (self._total_tokens + len(self._token_freq) + 1)
            token_surprisal = -math.log2(max(prob, SMOOTHING))
            total_surprisal += token_surprisal

            if freq == 0:
                novel_count += 1

        avg_surprisal = total_surprisal / len(tokens)
        novelty_ratio = novel_count / len(tokens)

        return avg_surprisal, novelty_ratio

    def _calc_context_surprisal(self, event: Event) -> float:
        """计算上下文信息量

        基于 (sender, recipient, event_type) 三元组的频率。
        """
        context_key = (event.sender, event.recipient, event.event_type.value)

        # 冷启动：无历史数据时，上下文是全新的，给予高 surprisal
        if self._total_contexts == 0:
            return 5.0  # 新上下文给予 5 bits 的基础信息量

        freq = self._context_freq.get(context_key, 0)

        # P(context) = (freq + 1) / (total + unique_contexts + 1)
        prob = (freq + 1) / (self._total_contexts + len(self._context_freq) + 1)
        return -math.log2(max(prob, SMOOTHING))

    def calculate_surprisal(self, event: Event) -> SurprisalResult:
        """计算单条事件的信息密度

        Args:
            event: 要评估的事件

        Returns:
            SurprisalResult: 包含各维度信息量和判定结果
        """
        with self._lock:
            # 1. 内容信息量
            content_s, novelty_ratio = self._calc_content_surprisal(event.content)

            # 2. 上下文信息量
            context_s = self._calc_context_surprisal(event)

            # 3. 新词奖励
            novelty_bonus = novelty_ratio * 2.0  # 新词比例 × 2.0 bits

            # 4. 加权合成
            total = self.alpha * content_s + self.beta * context_s + self.gamma * novelty_bonus

            # 5. 判定
            in_warmup = self._stats.total_events < self.warmup
            if in_warmup:
                passed = True
                reason = "warmup"
            elif total >= self._effective_threshold():
                passed = True
                reason = "pass"
            else:
                passed = False
                reason = "low_density"

            return SurprisalResult(
                total=total,
                content=content_s,
                context=context_s,
                novelty=novelty_bonus,
                passed=passed,
                reason=reason,
            )

    def _effective_threshold(self) -> float:
        """计算当前有效阈值（自适应）"""
        if not self.adaptive or not self._recent_surprisals:
            return self.threshold

        # 自适应：使用最近窗口的均值 - 0.5 * 标准差
        # 这样阈值会随信息密度分布动态调整
        recent = self._recent_surprisals[-self.adaptive_window :]
        if len(recent) < 5:
            return self.threshold

        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        std = math.sqrt(variance)

        # 阈值 = max(固定阈值, 均值 - 0.5*标准差)
        # 确保不低于固定阈值的 50%
        adaptive_threshold = max(self.threshold * 0.5, mean - 0.5 * std)
        return min(adaptive_threshold, self.threshold * 2.0)  # 也不超过固定阈值的2倍

    # ==================== 门控接口 ====================

    def should_pass(self, event: Event) -> bool:
        """判断事件是否应通过门控

        Args:
            event: 要评估的事件

        Returns:
            True = 通过（高信息密度），False = 过滤（低信息密度）
        """
        result = self.calculate_surprisal(event)
        return result.passed

    def process(self, event: Event) -> SurprisalResult:
        """处理事件：计算 surprisal + 判定 + 更新统计

        注意：此方法不调用 update()。如需更新频率模型，需手动调用。

        Args:
            event: 要处理的事件

        Returns:
            SurprisalResult: 完整的计算结果
        """
        result = self.calculate_surprisal(event)

        with self._lock:
            self._stats.total_events += 1
            self._stats.total_surprisal += result.total
            if result.total < self._stats.min_surprisal:
                self._stats.min_surprisal = result.total
            if result.total > self._stats.max_surprisal:
                self._stats.max_surprisal = result.total

            if result.passed:
                self._stats.passed_events += 1
            else:
                self._stats.filtered_events += 1

            # 更新自适应窗口
            self._recent_surprisals.append(result.total)
            if len(self._recent_surprisals) > self.adaptive_window * 2:
                self._recent_surprisals = self._recent_surprisals[-self.adaptive_window :]

            # 更新 warmup 计数
            if self._stats.warmup_remaining > 0:
                self._stats.warmup_remaining -= 1

        return result

    def update(self, event: Event) -> None:
        """更新频率模型（无论事件是否通过门控都应调用）

        Args:
            event: 已处理的事件
        """
        with self._lock:
            # 1. 更新词频
            tokens = tokenize(event.content)
            for token in tokens:
                self._token_freq[token] += 1
                self._total_tokens += 1

            # 词汇表大小限制（LRU 式淘汰：保留频率最高的）
            if len(self._token_freq) > MAX_VOCAB_SIZE:
                # 保留频率前 MAX_VOCAB_SIZE 的词
                top = self._token_freq.most_common(MAX_VOCAB_SIZE)
                self._token_freq = Counter(dict(top))

            # 2. 更新事件类型频率
            self._type_freq[event.event_type.value] += 1
            self._total_events += 1

            # 3. 更新上下文频率
            context_key = (event.sender, event.recipient, event.event_type.value)
            self._context_freq[context_key] += 1
            self._total_contexts += 1

            # 更新统计
            self._stats.vocab_size = len(self._token_freq)
            self._stats.context_patterns = len(self._context_freq)

    # ==================== 查询接口 ====================

    def get_stats(self) -> dict[str, Any]:
        """获取门控统计信息"""
        with self._lock:
            return self._stats.to_dict()

    def get_threshold(self) -> float:
        """获取当前有效阈值"""
        return self._effective_threshold()

    def reset(self) -> None:
        """重置门控状态（频率模型 + 统计）"""
        with self._lock:
            self._token_freq.clear()
            self._total_tokens = 0
            self._type_freq.clear()
            self._total_events = 0
            self._context_freq.clear()
            self._total_contexts = 0
            self._recent_surprisals.clear()
            self._stats = GateStats(warmup_remaining=self.warmup)

    # ==================== 批量处理 ====================

    def process_batch(self, events: list[Event]) -> list[tuple[Event, SurprisalResult]]:
        """批量处理事件

        Args:
            events: 事件列表

        Returns:
            [(event, result), ...] 每个事件及其计算结果
        """
        results = []
        for event in events:
            result = self.process(event)
            self.update(event)
            results.append((event, result))
        return results

    def filter_batch(self, events: list[Event]) -> list[Event]:
        """批量过滤：只返回通过门控的事件

        Args:
            events: 事件列表

        Returns:
            通过门控的事件列表
        """
        passed = []
        for event in events:
            result = self.process(event)
            self.update(event)
            if result.passed:
                passed.append(event)
        return passed


# ==================== 便捷工厂 ====================


def create_gate(
    threshold: float = DEFAULT_THRESHOLD,
    warmup: int = DEFAULT_WARMUP,
    adaptive: bool = True,
) -> SurprisalGate:
    """创建 SurprisalGate 实例

    示例：
        gate = create_gate(threshold=0.5)  # 低阈值，过滤更少
        gate = create_gate(threshold=2.0)  # 高阈值，过滤更多
    """
    return SurprisalGate(
        threshold=threshold,
        warmup=warmup,
        adaptive=adaptive,
    )


def create_strict_gate() -> SurprisalGate:
    """创建严格门控（高阈值，只通过高信息密度事件）"""
    return SurprisalGate(threshold=2.0, warmup=10, adaptive=False)


def create_relaxed_gate() -> SurprisalGate:
    """创建宽松门控（低阈值，几乎不过滤）"""
    return SurprisalGate(threshold=0.3, warmup=5, adaptive=False)


# ==================== 自检 ====================


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    from event_stream import create_action, create_observation

    print("=" * 60)
    print("SurprisalGate 信息密度过滤器 — 自检")
    print("=" * 60)

    gate = SurprisalGate(threshold=1.0, warmup=5)

    # 1. 分词测试
    print("\n① 分词测试")
    tokens = tokenize("Hello世界test123")
    print(f"   'Hello世界test123' → {tokens}")
    assert "hello" in tokens
    assert "世" in tokens
    assert "界" in tokens

    # 2. 观察期事件
    print("\n② 观察期事件（前5条不过滤）")
    for i in range(5):
        e = create_action("澜舟", "澜澜", EventType.INFO, f"观察期消息 #{i}")
        result = gate.process(e)
        gate.update(e)
        print(
            f"   [{i}] surprisal={result.total:.3f} passed={result.passed} reason={result.reason}"
        )
        assert result.passed  # warmup 期间全部通过

    # 3. 重复低信息事件
    print("\n③ 重复低信息事件（应被过滤）")
    for i in range(10):
        e = create_action("澜舟", "澜澜", EventType.INFO, "ok")
        result = gate.process(e)
        gate.update(e)
        if i >= 3:
            print(
                f"   [{i}] surprisal={result.total:.3f} passed={result.passed} reason={result.reason}"
            )

    # 4. 高信息新事件
    print("\n④ 高信息新事件（应通过）")
    e = create_action("九重", "澜舟", EventType.TASK, "全新任务：调研量子计算与AI结合的前沿方向")
    result = gate.process(e)
    gate.update(e)
    print(f"   surprisal={result.total:.3f} passed={result.passed} reason={result.reason}")

    # 5. 统计
    print("\n⑤ 统计信息")
    stats = gate.get_stats()
    print(f"   {stats}")

    print("\n" + "=" * 60)
    print("✅ SurprisalGate 自检通过！")
    print("=" * 60)
