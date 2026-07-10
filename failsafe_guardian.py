from __future__ import annotations

"""
致命三要素应对模块 — RPM→TPM · 监管者模式 · 五级优雅降级 · Canary升级。

三要素定义：
  ① 速率失控：RPM/TPM 无限制增长 → 线程级速率控制
  ② 无监督自循环：AI 无外部审核 → 监管者审计线程
  ③ 版本回滚缺失：升级失败无退路 → 五级降级 + Canary灰度

运行模式：asyncio 后台守护线程 · 不阻塞主请求路径
"""

import asyncio
import json
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from enum import IntEnum
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger("bridge_v7.failsafe")


# ===========================================================================
# Degradation levels (increasing severity)
# ===========================================================================


class DegradationLevel(IntEnum):
    """五级优雅降级。数字越大降级越深。"""

    L0_FULL = 0  # 全功能运行
    L1_THROTTLE = 1  # 限速：非核心endpoint降低QPS
    L2_REDUCED = 2  # 削减：关闭实验性功能(多模态/工作树)
    L3_ESSENTIAL = 3  # 核心：仅L1模型+ICP+事件流
    L4_SAFE_MODE = 4  # 安全模式：只读·拒绝写入·等待人工介入


DEGRADATION_TRIGGERS = {
    DegradationLevel.L1_THROTTLE: {
        "rpm_spike_ratio": 2.0,  # RPM突增2倍 → L1
        "consecutive_errors": 10,
        "memory_mb": 2048,
    },
    DegradationLevel.L2_REDUCED: {
        "rpm_spike_ratio": 5.0,
        "consecutive_errors": 50,
        "memory_mb": 3072,
    },
    DegradationLevel.L3_ESSENTIAL: {
        "rpm_spike_ratio": 10.0,
        "consecutive_errors": 100,
        "memory_mb": 5120,
    },
    DegradationLevel.L4_SAFE_MODE: {
        "consecutive_errors": 500,
        "crash_loop_count": 3,  # 连续崩溃3次 → L4
        "memory_mb": 8192,
    },
}


# ===========================================================================
# RPM → TPM rate control
# ===========================================================================


@dataclass
class TokenBudget:
    """滑动窗口token预算。"""

    max_tpm: int = 100000  # 每分钟最大token
    window_sec: float = 60.0
    _spent: deque = field(default_factory=lambda: deque(maxlen=1000))
    _total_tokens: int = 0

    def consume(self, tokens: int, now: float | None = None) -> bool:
        """尝试消费token。返回是否允许。"""
        now = now or time.time()
        cutoff = now - self.window_sec

        while self._spent and self._spent[0][0] < cutoff:
            _, spent_t = self._spent.popleft()
            self._total_tokens -= spent_t

        if self._total_tokens + tokens > self.max_tpm:
            return False

        self._spent.append((now, tokens))
        self._total_tokens += tokens
        return True

    @property
    def current_tpm(self) -> int:
        return self._total_tokens

    @property
    def utilization(self) -> float:
        return self._total_tokens / self.max_tpm if self.max_tpm > 0 else 0.0


@dataclass
class RateMonitor:
    """请求速率监控器。"""

    window_sec: float = 60.0
    _requests: deque = field(default_factory=deque)
    _error_count: int = 0
    _consecutive_errors: int = 0
    _crash_loop: int = 0

    def record_request(self, success: bool = True):
        now = time.time()
        cutoff = now - self.window_sec
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()
        self._requests.append(now)

        if success:
            self._consecutive_errors = 0
        else:
            self._error_count += 1
            self._consecutive_errors += 1

    def record_crash_loop(self):
        self._crash_loop += 1

    def reset_crash_loop(self):
        self._crash_loop = 0

    @property
    def current_rpm(self) -> int:
        return len(self._requests)

    @property
    def baseline_rpm(self) -> float:
        """30秒平均RPM。"""
        now = time.time()
        recent = [r for r in self._requests if r > now - 30]
        return len(recent) * 2  # 换算成每分钟

    @property
    def spike_ratio(self) -> float:
        baseline = self.baseline_rpm
        if baseline < 1:
            return 1.0
        return self.current_rpm / baseline

    @property
    def is_crash_looping(self) -> bool:
        return self._crash_loop >= 3


# ===========================================================================
# Supervisor watchdog
# ===========================================================================


class WatchdogSupervisor:
    """监管者模式：外部审核线程，独立于AI执行路径。"""

    def __init__(self, check_interval: float = 5.0, max_error_rate: float = 0.3):
        self.interval = check_interval
        self.max_error_rate = max_error_rate
        self.monitor = RateMonitor()
        self.token_budget = TokenBudget()
        self._current_level = DegradationLevel.L0_FULL
        self._running = False
        self._thread: threading.Thread | None = None
        self._callbacks: dict[DegradationLevel, list[Callable]] = {
            level: [] for level in DegradationLevel
        }
        self._lock = threading.Lock()

        # 断路器：按关键依赖分舱
        self.circuit_breakers: dict[str, CircuitBreaker] = {
            "llm": CircuitBreaker("llm", failure_threshold=5),
            "search": CircuitBreaker("search", failure_threshold=3),
            "database": CircuitBreaker("database", failure_threshold=5),
            "external_api": CircuitBreaker("external_api", failure_threshold=3),
        }
        # 隔舱执行器
        self.bulkhead = BulkheadExecutor()

    @property
    def current_level(self) -> DegradationLevel:
        return self._current_level

    def register_callback(self, level: DegradationLevel, cb: Callable):
        """注册降级回调。"""
        self._callbacks[level].append(cb)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watchdog")
        self._thread.start()
        logger.info("failsafe.watchdog_started", interval=self.interval)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("failsafe.watchdog_stopped")

    def record(self, success: bool, tokens: int = 0):
        """记录一次请求（从主路径调用）。"""
        self.monitor.record_request(success)
        if tokens > 0:
            self.token_budget.consume(tokens)

    def check_token_budget(self, tokens: int) -> bool:
        """前置检查token预算。"""
        return self.token_budget.consume(tokens)

    def _loop(self):
        """审计线程：独立于AI路径，永不阻塞模型推理。"""
        while self._running:
            try:
                new_level = self._evaluate()
                if new_level != self._current_level:
                    self._transition(new_level)
                self._current_level = new_level
            except Exception:
                logger.exception("failsafe.watchdog_error")
            time.sleep(self.interval)

    def _evaluate(self) -> DegradationLevel:
        m = self.monitor
        mem_mb = self._get_memory_usage()

        # 检查触发条件（从高到低）
        if (
            m._consecutive_errors
            >= DEGRADATION_TRIGGERS[DegradationLevel.L4_SAFE_MODE]["consecutive_errors"]
            or m.is_crash_looping
            or mem_mb > DEGRADATION_TRIGGERS[DegradationLevel.L4_SAFE_MODE]["memory_mb"]
        ):
            return DegradationLevel.L4_SAFE_MODE

        triggers_l3 = DEGRADATION_TRIGGERS[DegradationLevel.L3_ESSENTIAL]
        if (
            m.spike_ratio > triggers_l3["rpm_spike_ratio"]
            or m._consecutive_errors >= triggers_l3["consecutive_errors"]
            or mem_mb > triggers_l3["memory_mb"]
        ):
            return DegradationLevel.L3_ESSENTIAL

        triggers_l2 = DEGRADATION_TRIGGERS[DegradationLevel.L2_REDUCED]
        if (
            m.spike_ratio > triggers_l2["rpm_spike_ratio"]
            or m._consecutive_errors >= triggers_l2["consecutive_errors"]
            or mem_mb > triggers_l2["memory_mb"]
        ):
            return DegradationLevel.L2_REDUCED

        triggers_l1 = DEGRADATION_TRIGGERS[DegradationLevel.L1_THROTTLE]
        if (
            m.spike_ratio > triggers_l1["rpm_spike_ratio"]
            or m._consecutive_errors >= triggers_l1["consecutive_errors"]
            or mem_mb > triggers_l1["memory_mb"]
        ):
            return DegradationLevel.L1_THROTTLE

        # 恢复检查：降级后条件消失则逐步恢复
        if self._current_level > DegradationLevel.L1_THROTTLE:
            if (
                m.spike_ratio <= triggers_l1["rpm_spike_ratio"] * 0.5
                and m._consecutive_errors < triggers_l1["consecutive_errors"] * 0.3
                and mem_mb < triggers_l1["memory_mb"]
            ):
                return DegradationLevel(max(0, self._current_level - 1))

        return DegradationLevel.L0_FULL

    def _transition(self, new_level: DegradationLevel):
        direction = "DEGRADE" if new_level > self._current_level else "RECOVER"
        logger.warning(
            "failsafe.level_transition",
            from_level=self._current_level.name,
            to_level=new_level.name,
            direction=direction,
            rpm=self.monitor.current_rpm,
            errors=self.monitor._consecutive_errors,
            tpm=self.token_budget.current_tpm,
        )

        # 执行回调
        for cb in self._callbacks[new_level]:
            try:
                cb(new_level, direction)
            except Exception:
                logger.exception("failsafe.callback_error", level=new_level.name)

    @staticmethod
    def _get_memory_usage() -> float:
        """获取进程内存使用量(MB)。"""
        try:
            import psutil

            proc = psutil.Process()
            return proc.memory_info().rss / (1024 * 1024)
        except ImportError:
            return 0.0

    def get_circuit_breaker(self, name: str) -> CircuitBreaker:
        """获取或创建指定名称的断路器。"""
        if name not in self.circuit_breakers:
            self.circuit_breakers[name] = CircuitBreaker(name)
        return self.circuit_breakers[name]

    def record_circuit_result(self, name: str, success: bool):
        """记录断路器结果。"""
        cb = self.get_circuit_breaker(name)
        if success:
            cb.record_success()
        else:
            cb.record_failure()

    def get_status(self) -> dict:
        return {
            "level": self._current_level.name,
            "level_code": int(self._current_level),
            "rpm": self.monitor.current_rpm,
            "rpm_spike": round(self.monitor.spike_ratio, 2),
            "consecutive_errors": self.monitor._consecutive_errors,
            "crash_loop": self.monitor._crash_loop,
            "tpm": self.token_budget.current_tpm,
            "tpm_utilization": round(self.token_budget.utilization * 100, 1),
            "memory_mb": round(self._get_memory_usage(), 1),
            "circuits": {
                name: {
                    "state": cb.state.name,
                    "failures": cb._failures,
                }
                for name, cb in self.circuit_breakers.items()
            },
            "bulkhead": self.bulkhead.get_status(),
        }


# ===========================================================================
# Circuit Breaker (5-state with hysteresis)
# ===========================================================================


class CircuitState(IntEnum):
    """断路器五状态。"""

    CLOSED = 0  # 正常闭合
    OPEN = 1  # 断开
    HALF_OPEN = 2  # 探测中
    OPEN_EXTENDED = 3  # 扩展断开（防抖动）
    PERMANENT = 4  # 永久断开（需人工恢复）


@dataclass
class CircuitBreaker:
    """
    断路器：防止级联故障。
    CLOSED → (连续失败≥threshold) → OPEN → (冷却时间到) → HALF_OPEN
      ↑                                     ↓
      └─────────(探测成功)─────────────────┘  (探测失败) → OPEN_EXTENDED
    """

    name: str
    failure_threshold: int = 5
    open_cooldown_sec: float = 30.0
    extended_cooldown_sec: float = 300.0
    half_open_max_calls: int = 3
    permanent_threshold: int = 3  # OPEN_EXTENDED 连续失败次数达到则永久断开

    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _successes: int = 0
    _last_failure_time: float = 0.0
    _half_open_calls: int = 0
    _extended_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def state(self) -> CircuitState:
        return self._state

    def allow(self) -> bool:
        """当前是否允许通过。"""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.open_cooldown_sec:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._successes = 0
                    logger.info("failsafe.circuit_half_open", breaker=self.name)
                    return True
                return False

            if self._state == CircuitState.OPEN_EXTENDED:
                if time.time() - self._last_failure_time >= self.extended_cooldown_sec:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._successes = 0
                    logger.info("failsafe.circuit_half_open_extended", breaker=self.name)
                    return True
                return False

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False

            return False  # PERMANENT

    def record_success(self):
        """记录成功。"""
        with self._lock:
            self._failures = 0
            if self._state == CircuitState.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.half_open_max_calls:
                    self._state = CircuitState.CLOSED
                    self._extended_count = 0
                    self._half_open_calls = 0
                    logger.info("failsafe.circuit_closed", breaker=self.name)
            elif self._state == CircuitState.CLOSED:
                self._successes = min(self._successes + 1, self.failure_threshold)

    def record_failure(self):
        """记录失败。"""
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN_EXTENDED
                self._extended_count += 1
                if self._extended_count >= self.permanent_threshold:
                    self._state = CircuitState.PERMANENT
                    logger.error("failsafe.circuit_permanent", breaker=self.name)
                else:
                    logger.warning(
                        "failsafe.circuit_open_extended",
                        breaker=self.name,
                        extended_count=self._extended_count,
                    )
                return

            if self._state in (CircuitState.CLOSED,):
                if self._failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(
                        "failsafe.circuit_open", breaker=self.name, failures=self._failures
                    )


# ===========================================================================
# Bulkhead isolation
# ===========================================================================


@dataclass
class BulkheadExecutor:
    """
    隔舱模式：按资源类型隔离并发，防止一个服务拖垮全部。
    """

    limits: dict[str, int] = field(
        default_factory=lambda: {
            "llm": 10,
            "search": 5,
            "database": 20,
            "external_api": 3,
            "memory": 8,
        }
    )

    _semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _get_semaphore(self, resource: str) -> asyncio.Semaphore:
        with self._lock:
            if resource not in self._semaphores:
                limit = self.limits.get(resource, 5)
                self._semaphores[resource] = asyncio.Semaphore(limit)
            return self._semaphores[resource]

    async def execute(self, resource: str, coro):
        """在指定资源池内执行协程。"""
        sem = self._get_semaphore(resource)
        async with sem:
            return await coro

    def get_status(self) -> dict[str, dict[str, Any]]:
        """返回各资源池占用情况。"""
        status = {}
        for resource, sem in self._semaphores.items():
            status[resource] = {
                "limit": sem._value,  # type: ignore
                "available": sem._value,  # type: ignore
            }
        return status


# ===========================================================================
# Error classification
# ===========================================================================


def is_retriable_error(exception: Exception, status_code: int | None = None) -> bool:
    """
    判断错误是否属于可重试的瞬态错误。
    可重试：连接超时、Connection Refused、HTTP 502/503/504
    不可重试：400、401、验证失败等业务逻辑错误
    """
    if status_code is not None:
        return status_code in (408, 429, 500, 502, 503, 504)

    msg = str(exception).lower()
    retriable_signals = [
        "timeout",
        "timed out",
        "connection refused",
        "connection reset",
        "no route to host",
        "temporary failure",
        "dns resolution",
        "ssl handshake",
        "broken pipe",
    ]
    non_retriable_signals = [
        "invalid",
        "unauthorized",
        "forbidden",
        "not found",
        "bad request",
        "validation",
        "authentication",
    ]

    if any(s in msg for s in non_retriable_signals):
        return False
    return any(s in msg for s in retriable_signals)


# ===========================================================================
# Canary upgrade
# ===========================================================================


class CanaryUpgrader:
    """Canary灰度升级：新版本先在1%流量验证，逐步扩大到100%。"""

    def __init__(self, new_version: str, current_version: str, watchdog: WatchdogSupervisor):
        self.new_version = new_version
        self.current_version = current_version
        self.watchdog = watchdog
        # Canary stages: (ratio, min_seconds, error_rate_threshold)
        self.stages = [
            (0.01, 300, 0.05),  # 1%流量 5分钟 错误率<5%
            (0.05, 600, 0.08),  # 5%流量 10分钟 <8%
            (0.25, 1200, 0.10),  # 25%流量 20分钟 <10%
            (0.50, 1800, 0.15),  # 50%流量 30分钟 <15%
            (1.00, 3600, 0.20),  # 100%流量 1小时 <20%
        ]
        self.current_stage_idx: int = 0
        self.stage_started: float = 0.0
        self.stage_error_count: int = 0
        self.stage_request_count: int = 0
        self._active = False
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._active

    @property
    def canary_ratio(self) -> float:
        if not self._active or self.current_stage_idx >= len(self.stages):
            return 1.0 if self._active else 0.0
        return self.stages[self.current_stage_idx][0]

    def start(self):
        with self._lock:
            self._active = True
            self.current_stage_idx = 0
            self.stage_started = time.time()
            self.stage_error_count = 0
            self.stage_request_count = 0
        logger.info(
            "failsafe.canary_started",
            from_version=self.current_version,
            to_version=self.new_version,
            stage=1,
        )

    def rollback(self):
        """紧急回滚到旧版本。"""
        with self._lock:
            self._active = False
        logger.warning(
            "failsafe.canary_rolled_back",
            from_version=self.new_version,
            to_version=self.current_version,
        )

    def should_use_new_version(self) -> bool:
        """当前请求是否应该路由到新版本。"""
        if not self._active:
            return False
        import random

        return random.random() < self.canary_ratio

    def record_result(self, success: bool):
        """记录新版本处理结果。"""
        with self._lock:
            self.stage_request_count += 1
            if not success:
                self.stage_error_count += 1
            self._check_stage_progression()

    def _check_stage_progression(self):
        if self.current_stage_idx >= len(self.stages):
            return

        ratio, min_sec, error_max = self.stages[self.current_stage_idx]
        elapsed = time.time() - self.stage_started

        if elapsed < min_sec:
            return

        error_rate = (
            self.stage_error_count / self.stage_request_count
            if self.stage_request_count > 10
            else 0.0
        )

        if error_rate > error_max:
            logger.warning(
                "failsafe.canary_error_rate_exceeded",
                stage=self.current_stage_idx + 1,
                error_rate=round(error_rate, 3),
                threshold=error_max,
            )
            self.rollback()
            return

        # 晋级
        self.current_stage_idx += 1
        self.stage_started = time.time()
        self.stage_error_count = 0
        self.stage_request_count = 0

        if self.current_stage_idx >= len(self.stages):
            logger.info("failsafe.canary_completed", version=self.new_version)
            self._active = False
        else:
            next_ratio = self.stages[self.current_stage_idx][0]
            logger.info(
                "failsafe.canary_advanced", stage=self.current_stage_idx + 1, ratio=next_ratio
            )

    def get_status(self) -> dict:
        return {
            "active": self._active,
            "from_version": self.current_version,
            "to_version": self.new_version,
            "stage": self.current_stage_idx + 1,
            "total_stages": len(self.stages),
            "canary_ratio": self.canary_ratio,
            "stage_elapsed": round(time.time() - self.stage_started, 1) if self._active else 0,
            "stage_requests": self.stage_request_count,
            "stage_errors": self.stage_error_count,
        }


# ===========================================================================
# Health status aggregator
# ===========================================================================


def get_full_health(watchdog: WatchdogSupervisor, canary: CanaryUpgrader | None = None) -> dict:
    """全量健康报告。"""
    status = {
        "failsafe": watchdog.get_status(),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if canary:
        status["canary"] = canary.get_status()
    return status
