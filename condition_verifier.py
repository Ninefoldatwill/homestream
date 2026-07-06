"""
桥v7 ConditionVerifier — Stop Hook 条件验证器

融优主义分级: C类 — 自己造
灵感来源: Claude Code Stop Hook 概念
实现方式: 完全自研，深度集成 EventStream + WAL

核心设计：
1. 5种停止条件 (Kappa/Phi/Empty/Error/MaxIter)
2. 三步验证链 (快速→深度→自定义)
3. Per-Worktree 独立配置
4. WAL 持久化支持（重启恢复）
5. EventStream 订阅集成

日期: 2026-06-18
作者: 澜舟
"""

from __future__ import annotations

import time
import uuid
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from event_stream import (
    Event,
    EventStream,
    EventType,
    EventSource,
    Observation,
    _gen_event_id,
)


# ==================== 枚举 & 数据类 ====================

class StopCondition(Enum):
    """Agent 循环停止条件 — 对标 Claude Code Stop Hook

    六种条件覆盖 Agent 循环所有终止场景：
    - KAPPA: 输出收敛（连续 N 次无实质变化）
    - PHI:   目标达成（任务被标记为 DONE）
    - EMPTY: 空闲超时（EventStream 长时间无新事件）
    - ERROR: 错误兜底（连续 N 次 Action 执行失败）
    - MAX_ITER: 循环上限（防止无限循环）
    - EXPERIMENT: 实验完成（Ratchet Loop实验目标达成，v7.3新增）
    """
    KAPPA = "kappa"
    PHI = "phi"
    EMPTY = "empty"
    ERROR = "error"
    MAX_ITER = "max_iter"
    EXPERIMENT = "experiment"  # v7.3: Ratchet Loop实验模式


@dataclass
class VerifierConfig:
    """验证器配置 — Per-Worktree 独立，可通过 API 热更新"""

    # Kappa — 输出收敛
    kappa_window: int = 3             # 连续 N 次输出无变化则停止
    kappa_threshold: float = 0.95     # Jaccard 相似度阈值 (0-1)

    # Phi — 目标达成
    phi_required: bool = True         # 是否要求目标显式达成

    # Error — 错误兜底
    max_consecutive_errors: int = 3   # 连续 N 次错误则停止

    # MaxIter — 循环上限
    max_iterations: int = 50          # 最大迭代次数 (0=不限)

    # Empty — 空闲超时
    empty_timeout: float = 30.0       # 无新 Action 超时秒数 (0=不启用)

    # 验证链开关
    enable_deep_check: bool = True    # 是否执行深度检查
    require_human_confirm: bool = False   # 高危操作需要人工确认

    # v7.3: Ratchet Loop实验模式
    experiment_mode: bool = False              # 是否为实验模式
    experiment_success_keywords: List[str] = field(default_factory=list)  # 实验成功关键词
    experiment_fail_keywords: List[str] = field(default_factory=lambda: ["error", "failed", "exception"])  # 实验失败关键词


@dataclass
class VerificationResult:
    """单次验证结果"""
    condition: StopCondition
    should_stop: bool               # True → 允许停止，False → 强制继续
    confidence: float               # 停止置信度 (0-1)
    reason: str                     # 人类可读说明
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        decision = "STOP" if self.should_stop else "CONTINUE"
        return (
            f"VerificationResult({decision} | {self.condition.value} | "
            f"confidence={self.confidence:.2f} | {self.reason})"
        )


# ==================== 核心验证器 ====================

class ConditionVerifier:
    """
    条件验证器 — 桥 v7 的 Stop Hook

    工作流程：
      DONE Action 发布
        → EventStream 触发 VerifierSubscriber._on_done()
        → ConditionVerifier.check()
        → 三步验证链 (快速 → 深度 → 自定义)
        → should_stop=True  → 发布 STOPPED Observation
        → should_stop=False → 发布 CONTINUE Observation → Agent 继续

    独立状态（Per-Session）：
      _state["iteration"]           # 当前迭代次数
      _state["consecutive_errors"]  # 连续错误次数
      _state["last_action_time"]    # 最近一次 Action 时间戳
      _state["last_outputs"]        # 最近 N 次内容（Kappa 检测）
    """

    def __init__(self, config: Optional[VerifierConfig] = None):
        self.config = config or VerifierConfig()
        self._state: Dict[str, Any] = self._initial_state()
        self._custom_checkers: List[Callable] = []
        self._lock = threading.Lock()

    # ---------- 公开 API ----------

    def register_checker(self, checker: Callable) -> None:
        """注册自定义验证器（可扩展钩子）
        
        签名: checker(stream: EventStream, state: dict) -> Optional[VerificationResult]
        返回 None 表示该检查器未命中，继续下一个
        """
        with self._lock:
            self._custom_checkers.append(checker)

    def check(
        self,
        stream: EventStream,
        worktree_name: Optional[str] = None,
    ) -> VerificationResult:
        """执行完整验证链 — 每次 DONE Action 后调用
        
        Returns:
            VerificationResult:
              - should_stop=True  → Agent 可以停止
              - should_stop=False → Agent 必须继续循环
        """
        with self._lock:
            self._state["iteration"] += 1
            iteration = self._state["iteration"]

        # === Step 1: 快速检查 O(1) ===
        result = self._quick_check()
        if result is not None:
            return result

        # === Step 2: 深度检查 O(n) ===
        if self.config.enable_deep_check:
            result = self._deep_check(stream)
            if result is not None:
                return result

        # === Step 3: 自定义检查器 ===
        with self._lock:
            checkers = list(self._custom_checkers)
            state_snapshot = dict(self._state)
        for checker in checkers:
            try:
                result = checker(stream, state_snapshot)
                if result is not None:
                    return result
            except Exception as exc:  # noqa: BLE001
                print(f"[ConditionVerifier] Custom checker error: {exc}")

        # === 未命中任何停止条件 → 继续循环 ===
        return VerificationResult(
            condition=StopCondition.PHI,
            should_stop=False,
            confidence=0.0,
            reason=f"第 {iteration} 次迭代：未满足任何停止条件，继续循环",
        )

    def notify_action(self, action_type: str, content: str = "") -> None:
        """Agent 循环中每次 Action 执行后调用 — 更新内部状态
        
        - action_type="ERROR"  → 连续错误计数 +1
        - 其他                 → 清零连续错误计数
        - content 非空         → 追加到最近输出窗口（Kappa 检测用）
        """
        with self._lock:
            self._state["last_action_time"] = time.time()
            if action_type == EventType.WARN.value or "error" in action_type.lower():
                self._state["consecutive_errors"] += 1
            else:
                self._state["consecutive_errors"] = 0
            if content:
                self._state["last_outputs"].append(content)
                # 只保留最近 20 条，Kappa 只用最后 kappa_window 条
                self._state["last_outputs"] = self._state["last_outputs"][-20:]

    def reset(self) -> None:
        """重置验证器状态 — 新任务开始时调用"""
        with self._lock:
            self._state = self._initial_state()

    def dump_state(self) -> Dict[str, Any]:
        """导出可序列化的状态快照（用于 WAL 持久化）"""
        with self._lock:
            return {
                "iteration": self._state["iteration"],
                "consecutive_errors": self._state["consecutive_errors"],
                "last_outputs_count": len(self._state["last_outputs"]),
                "last_action_time": self._state["last_action_time"],
            }

    def load_state(self, snapshot: Dict[str, Any]) -> None:
        """从 WAL 快照恢复状态（重启后调用）"""
        with self._lock:
            self._state["iteration"] = snapshot.get("iteration", 0)
            self._state["consecutive_errors"] = snapshot.get("consecutive_errors", 0)
            self._state["last_action_time"] = snapshot.get(
                "last_action_time", time.time()
            )
            # last_outputs 不持久化（内容较长，仅恢复计数即可）

    # ---------- 内部方法 ----------

    @staticmethod
    def _initial_state() -> Dict[str, Any]:
        return {
            "iteration": 0,
            "consecutive_errors": 0,
            "last_action_time": time.time(),
            "last_outputs": [],
        }

    def _quick_check(self) -> Optional[VerificationResult]:
        """快速检查 — O(1)，检查迭代计数 / 错误计数 / 空闲超时"""
        with self._lock:
            iteration = self._state["iteration"]
            consecutive_errors = self._state["consecutive_errors"]
            idle_seconds = time.time() - self._state["last_action_time"]

        # MAX_ITER
        if self.config.max_iterations > 0 and iteration >= self.config.max_iterations:
            return VerificationResult(
                condition=StopCondition.MAX_ITER,
                should_stop=True,
                confidence=1.0,
                reason=f"已达最大迭代次数 {self.config.max_iterations}",
                metadata={"iteration": iteration},
            )

        # ERROR
        if consecutive_errors >= self.config.max_consecutive_errors:
            return VerificationResult(
                condition=StopCondition.ERROR,
                should_stop=True,
                confidence=0.9,
                reason=f"连续 {consecutive_errors} 次 Action 执行失败",
                metadata={"consecutive_errors": consecutive_errors},
            )

        # EMPTY
        if self.config.empty_timeout > 0 and idle_seconds > self.config.empty_timeout:
            return VerificationResult(
                condition=StopCondition.EMPTY,
                should_stop=True,
                confidence=0.7,
                reason=f"EventStream 已 {idle_seconds:.1f}s 无新 Action",
                metadata={"idle_seconds": idle_seconds},
            )

        return None

    def _deep_check(self, stream: EventStream) -> Optional[VerificationResult]:
        """深度检查 — O(n)，分析 EventStream 历史"""
        recent = stream.events[-20:] if stream.events else []

        # PHI — 目标达成：最近事件中有 DONE 且无未完成 TASK/ASK
        done_events = [e for e in recent if e.event_type == EventType.DONE]
        pending_tasks = [
            e for e in recent
            if e.event_type in (EventType.TASK, EventType.ASK)
        ]
        # 检查 DONE 后是否有新 TASK（即新任务已分配）
        done_after_task = False
        if done_events and pending_tasks:
            last_done_ts = max(e.timestamp for e in done_events)
            # 如果所有 TASK 都早于最后一个 DONE，说明均已被 DONE 覆盖
            pending_after_done = [
                e for e in pending_tasks if e.timestamp > last_done_ts
            ]
            done_after_task = len(pending_after_done) == 0

        if done_events and (not pending_tasks or done_after_task):
            return VerificationResult(
                condition=StopCondition.PHI,
                should_stop=True,
                confidence=0.85,
                reason="目标已达成：检测到 DONE 且无待处理任务",
                metadata={"done_count": len(done_events)},
            )

        # KAPPA — 输出收敛
        with self._lock:
            last_outputs = list(self._state["last_outputs"])
        if len(last_outputs) >= self.config.kappa_window:
            window = last_outputs[-self.config.kappa_window:]
            if self._check_convergence(window):
                return VerificationResult(
                    condition=StopCondition.KAPPA,
                    should_stop=True,
                    confidence=0.8,
                    reason=(
                        f"输出收敛：连续 {self.config.kappa_window} 次内容"
                        f"相似度 ≥ {self.config.kappa_threshold:.0%}"
                    ),
                    metadata={"window_size": self.config.kappa_window},
                )

        # v7.3: EXPERIMENT — 实验完成检测
        if self.config.experiment_mode:
            result = self._check_experiment(last_outputs)
            if result is not None:
                return result

        return None

    def _check_experiment(self, outputs: List[str]) -> Optional[VerificationResult]:
        """实验模式检查 — 检测实验成功/失败关键词

        v7.3新增：Ratchet Loop实验工坊专用
        - 检测success_keywords → 实验成功，停止循环
        - 检测fail_keywords → 实验失败，停止循环
        """
        if not outputs:
            return None

        latest = outputs[-1].lower() if outputs else ""

        # 检查成功关键词
        for keyword in self.config.experiment_success_keywords:
            if keyword.lower() in latest:
                return VerificationResult(
                    condition=StopCondition.EXPERIMENT,
                    should_stop=True,
                    confidence=0.9,
                    reason=f"实验成功：检测到关键词 '{keyword}'",
                    metadata={"keyword": keyword, "mode": "success"},
                )

        # 检查失败关键词
        for keyword in self.config.experiment_fail_keywords:
            if keyword.lower() in latest:
                return VerificationResult(
                    condition=StopCondition.EXPERIMENT,
                    should_stop=True,
                    confidence=0.85,
                    reason=f"实验失败：检测到关键词 '{keyword}'",
                    metadata={"keyword": keyword, "mode": "fail"},
                )

        return None

    def _check_convergence(self, outputs: List[str]) -> bool:
        """检测输出列表是否已收敛（相邻两条相似度均高于阈值）"""
        if len(outputs) < 2:
            return False
        for i in range(1, len(outputs)):
            sim = self._jaccard_similarity(outputs[i - 1], outputs[i])
            if sim < self.config.kappa_threshold:
                return False
        return True

    @staticmethod
    def _jaccard_similarity(a: str, b: str) -> float:
        """Jaccard 词级相似度（轻量，无外部依赖）"""
        set_a = set(a.split())
        set_b = set(b.split())
        if not set_a and not set_b:
            return 1.0
        union = set_a | set_b
        if not union:
            return 0.0
        return len(set_a & set_b) / len(union)


# ==================== EventStream 订阅集成 ====================

class VerifierSubscriber:
    """
    验证器订阅者 — 监听 EventStream 中的 DONE 事件，触发条件验证

    工作流程：
      DONE Action → _on_done() → verifier.check()
        → should_stop=True  → 发布 STOPPED Observation（[DONE] 类型）
        → should_stop=False → 发布 CONTINUE Observation（[UPD] 类型）

    发布的 Observation 会被 Agent 订阅者接收，决定是否继续循环。
    """

    VERIFIER_AGENT_NAME = "verifier"  # 验证器作为虚拟 Agent 参与通信

    def __init__(
        self,
        stream: EventStream,
        verifier: Optional[ConditionVerifier] = None,
        config: Optional[VerifierConfig] = None,
    ):
        self.stream = stream
        self.verifier = verifier or ConditionVerifier(config)

        # 订阅 DONE 事件（触发条件验证）
        stream.subscribe_by_type(EventType.DONE, self._on_done)
        # 订阅所有 Action 类事件（维护内部状态）
        for etype in (EventType.TASK, EventType.UPD, EventType.WARN, EventType.LOG):
            stream.subscribe_by_type(etype, self._on_any_action)

    def _on_any_action(self, event: Event) -> None:
        """维护验证器内部状态（错误计数 / 最近输出 / 活跃时间）"""
        is_error = event.event_type == EventType.WARN
        self.verifier.notify_action(
            action_type=("ERROR" if is_error else event.event_type.value),
            content=event.content,
        )

    def _on_done(self, event: Event) -> None:
        """DONE 事件到达 → 触发条件验证链"""
        # 防止递归：忽略验证器自身发出的 DONE（STOPPED Observation）
        if event.sender == self.VERIFIER_AGENT_NAME:
            return

        # 更新状态
        self.verifier.notify_action(
            action_type=EventType.DONE.value,
            content=event.content,
        )

        # 执行验证
        result = self.verifier.check(self.stream)

        if result.should_stop:
            # ——— 允许停止 → 发布 STOPPED Observation ———
            obs = Observation(
                event_id=_gen_event_id("obs"),
                event_type=EventType.DONE,
                sender=self.VERIFIER_AGENT_NAME,
                recipient=event.sender,
                source=EventSource.ENVIRONMENT,
                content=(
                    f"[STOPPED][{result.condition.value.upper()}] "
                    f"{result.reason} "
                    f"(置信度:{result.confidence:.0%})"
                ),
                cause=event.event_id,
                confidence=result.confidence,
                wal_entry={
                    "type": "VERIFIER_STOP",
                    "condition": result.condition.value,
                    "iteration": self.verifier._state.get("iteration", 0),
                    "metadata": result.metadata,
                },
            )
        else:
            # ——— 强制继续 → 发布 CONTINUE Observation ———
            obs = Observation(
                event_id=_gen_event_id("obs"),
                event_type=EventType.UPD,
                sender=self.VERIFIER_AGENT_NAME,
                recipient=event.sender,
                source=EventSource.ENVIRONMENT,
                content=(
                    f"[CONTINUE] {result.reason}，请继续执行"
                ),
                cause=event.event_id,
                confidence=1.0 - result.confidence,
            )

        self.stream.publish(obs)


# ==================== WAL 集成混入 ====================

class WALVerifierMixin:
    """WAL 验证器混入 — 为持有 WAL 的类提供验证器状态持久化能力

    使用方式：
        class MyService(WALVerifierMixin):
            def save(self, session_id, verifier):
                self.save_verifier_state(session_id, verifier)
    """

    def save_verifier_state(
        self,
        session_id: str,
        verifier: ConditionVerifier,
        update_fn: Callable[[str, str, Any], None],
    ) -> None:
        """将验证器状态写入 WAL session_state 字段
        
        Args:
            session_id: WAL Session ID
            verifier:   ConditionVerifier 实例
            update_fn:  (session_id, field_name, value) → None，调用方提供
        """
        update_fn(session_id, "verifier_state", verifier.dump_state())

    def restore_verifier_state(
        self,
        session_id: str,
        verifier: ConditionVerifier,
        get_fn: Callable[[str, str], Optional[Dict]],
    ) -> bool:
        """从 WAL 恢复验证器状态
        
        Args:
            session_id: WAL Session ID
            verifier:   ConditionVerifier 实例（原地修改）
            get_fn:     (session_id, field_name) → Any，调用方提供
        
        Returns:
            True 如果成功恢复，False 如果无记录
        """
        snapshot = get_fn(session_id, "verifier_state")
        if snapshot:
            verifier.load_state(snapshot)
            return True
        return False


# ==================== 便捷工厂函数 ====================

def create_default_verifier() -> ConditionVerifier:
    """创建默认配置的条件验证器"""
    return ConditionVerifier(VerifierConfig())


def create_strict_verifier() -> ConditionVerifier:
    """创建严格配置的条件验证器（适合生产环境/高危 Worktree）"""
    return ConditionVerifier(
        VerifierConfig(
            max_iterations=20,
            max_consecutive_errors=2,
            empty_timeout=15.0,
            kappa_window=2,
            kappa_threshold=0.98,
            require_human_confirm=True,
        )
    )


def create_lenient_verifier() -> ConditionVerifier:
    """创建宽松配置的条件验证器（适合实验/调试场景）"""
    return ConditionVerifier(
        VerifierConfig(
            max_iterations=200,
            max_consecutive_errors=10,
            empty_timeout=120.0,
            kappa_window=5,
            kappa_threshold=0.99,
            enable_deep_check=False,
        )
    )


def create_experiment_verifier(
    max_iterations: int = 10,
    timeout: float = 300.0,
    success_keywords: Optional[List[str]] = None,
    fail_keywords: Optional[List[str]] = None,
) -> ConditionVerifier:
    """创建Ratchet Loop实验模式验证器（v7.3新增）

    实验模式特点：
    - 启用experiment_mode
    - 宽松的迭代限制（实验需要更多尝试空间）
    - 关键词检测自动判断实验成功/失败
    - 较长的空闲超时（实验可能需要长时间计算）

    Args:
        max_iterations: 最大迭代次数
        timeout: 空闲超时秒数
        success_keywords: 实验成功关键词列表
        fail_keywords: 实验失败关键词列表
    """
    return ConditionVerifier(
        VerifierConfig(
            max_iterations=max_iterations,
            max_consecutive_errors=5,
            empty_timeout=timeout,
            kappa_window=4,
            kappa_threshold=0.97,
            enable_deep_check=True,
            experiment_mode=True,
            experiment_success_keywords=success_keywords or ["passed", "success", "完成", "通过"],
            experiment_fail_keywords=fail_keywords or ["error", "failed", "exception", "失败"],
        )
    )


def attach_verifier_to_stream(
    stream: EventStream,
    config: Optional[VerifierConfig] = None,
) -> VerifierSubscriber:
    """将验证器绑定到 EventStream（一行搞定集成）
    
    用法:
        stream = EventStream("session-001")
        subscriber = attach_verifier_to_stream(stream)
        # 之后 stream.publish(DONE_event) 会自动触发验证
    """
    return VerifierSubscriber(stream=stream, config=config)
