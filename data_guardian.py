"""
HomeStream 事件数据质量守卫

对 EventStore 中的事件数据做四维质量校验，输出结构化质量报告。
完全原创实现，基于 HomeStream 自有的 EventStore 和 EventType 枚举设计。

四维校验：
  1. 因果链完整性 — cause 引用的 event_id 是否存在
  2. 时间戳连续性 — 事件时间是否单调递增（允许并发，不允许倒流）
  3. 事件类型合法性 — event_type 是否在 EventType 枚举中
  4. Agent 身份有效性 — sender/recipient 非空、长度合理、无注入风险

设计原则：
  - 只读校验：不修改 EventStore 中的任何数据
  - 降级安全：EventStore 未初始化时返回空报告
  - 分级报告：每项校验有 pass/warn/error 三级状态
  - 铸钥匠精神：数据质量是信任的基石

灵感来源：受数据质量校验方法论启发，但完全基于 EventStore 架构实现。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from event_store import EventStore

logger = logging.getLogger(__name__)

# ==================== 常量 ====================

# EventType 合法值集合
VALID_EVENT_TYPES = frozenset(
    {
        "INFO",
        "ASK",
        "TASK",
        "UPD",
        "DONE",
        "WARN",
        "ACK",
        "PING",
        "LOG",
    }
)

# Agent 名称长度限制
MAX_AGENT_NAME_LEN = 64
MIN_AGENT_NAME_LEN = 1

# 内容字段长度限制（用于检测异常）
MAX_CONTENT_LEN = 50000

# 单次校验最大事件数
MAX_EVENTS_TO_CHECK = 500


# ==================== 校验结果数据结构 ====================


def _empty_result(name: str) -> dict[str, Any]:
    """创建空的校验结果"""
    return {
        "name": name,
        "status": "pass",
        "total_checked": 0,
        "issues": [],
        "score": 1.0,
    }


def _add_issue(
    result: dict[str, Any],
    severity: str,
    message: str,
    event_id: str = "",
    detail: str = "",
) -> None:
    """添加一个校验问题"""
    result["issues"].append(
        {
            "severity": severity,
            "message": message,
            "event_id": event_id,
            "detail": detail,
        }
    )
    if severity == "error":
        result["status"] = "error"
    elif severity == "warn" and result["status"] != "error":
        result["status"] = "warn"


def _calc_score(result: dict[str, Any]) -> float:
    """根据问题数量和严重程度计算分数 (0.0-1.0)"""
    total = result["total_checked"]
    if total == 0:
        return 1.0
    errors = sum(1 for i in result["issues"] if i["severity"] == "error")
    warns = sum(1 for i in result["issues"] if i["severity"] == "warn")
    penalty = (errors * 10 + warns * 2) / total
    return round(max(0.0, 1.0 - penalty), 4)


# ==================== 1. 因果链完整性校验 ====================


def validate_causal_chain(
    events: list[Any],
    event_store: EventStore | None = None,
) -> dict[str, Any]:
    """校验事件因果链完整性

    检查每个事件的 cause 字段（如果非空）所引用的事件是否存在。
    支持 EventStore 回查和事件列表内查两种模式。
    """
    result = _empty_result("\u56e0\u679c\u94fe\u5b8c\u6574\u6027")

    # 构建 event_id 集合（从事件列表）
    event_ids = set()
    for ev in events:
        ev_id = getattr(ev, "event_id", None)
        if ev_id:
            event_ids.add(ev_id)

    for ev in events:
        result["total_checked"] += 1
        cause = getattr(ev, "cause", None)
        ev_id = getattr(ev, "event_id", "?")

        if cause is None or cause == "":
            # 无因果链是正常的（根事件）
            continue

        # 先在本地集合中查
        if cause in event_ids:
            continue

        # 尝试从 EventStore 回查
        found = False
        if event_store:
            try:
                ref = event_store.get_event_by_id(cause)
                if ref is not None:
                    found = True
            except Exception:
                pass

        if not found:
            _add_issue(
                result,
                "error",
                "\u56e0\u679c\u94fe\u65ad\u88c2: cause \u5f15\u7528\u7684\u4e8b\u4ef6\u4e0d\u5b58\u5728",
                event_id=ev_id,
                detail=f"cause={cause[:32]}",
            )

    result["score"] = _calc_score(result)
    return result


# ==================== 2. 时间戳连续性校验 ====================


def validate_timestamps(events: list[Any]) -> dict[str, Any]:
    """校验时间戳连续性

    检查事件时间戳是否大致单调递增。
    允许小幅度的时间跳跃（并发事件），但检测明显的时间倒流。

    同时检查：
    - 未来时间戳（timestamp > now）
    - None 或无效时间戳
    """
    result = _empty_result("\u65f6\u95f4\u6233\u8fde\u7eed\u6027")

    now = datetime.now()
    prev_ts: datetime | None = None

    for ev in events:
        result["total_checked"] += 1
        ev_id = getattr(ev, "event_id", "?")
        ts = getattr(ev, "timestamp", None)

        if ts is None:
            _add_issue(
                result,
                "error",
                "\u65f6\u95f4\u6233\u4e3a\u7a7a",
                event_id=ev_id,
            )
            continue

        # 转换为 datetime 对象
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                _add_issue(
                    result,
                    "error",
                    "\u65f6\u95f4\u6233\u683c\u5f0f\u65e0\u6548",
                    event_id=ev_id,
                    detail=f"ts={str(ts)[:32]}",
                )
                continue

        if not isinstance(ts, datetime):
            _add_issue(
                result,
                "warn",
                "\u65f6\u95f4\u6233\u7c7b\u578b\u5f02\u5e38",
                event_id=ev_id,
                detail=type(ts).__name__,
            )
            continue

        # 检查未来时间戳（允许5秒时钟偏差）
        if ts > now:
            delta = (ts - now).total_seconds()
            if delta > 5:
                _add_issue(
                    result,
                    "warn",
                    "\u672a\u6765\u65f6\u95f4\u6233",
                    event_id=ev_id,
                    detail=f"\u8d85\u524d{delta:.0f}s",
                )

        # 检查时间倒流（与前一个事件比较）
        if prev_ts is not None:
            delta = (prev_ts - ts).total_seconds()
            if delta > 60:
                _add_issue(
                    result,
                    "warn",
                    "\u65f6\u95f4\u5012\u6d41",
                    event_id=ev_id,
                    detail=f"\u5012\u9000{delta:.0f}s",
                )

        prev_ts = ts

    result["score"] = _calc_score(result)
    return result


# ==================== 3. 事件类型合法性校验 ====================


def validate_event_types(events: list[Any]) -> dict[str, Any]:
    """校验事件类型合法性

    检查每个事件的 event_type 是否在 EventType 枚举定义的9种合法值中。
    """
    result = _empty_result("\u4e8b\u4ef6\u7c7b\u578b\u5408\u6cd5\u6027")

    type_counter: dict[str, int] = {}

    for ev in events:
        result["total_checked"] += 1
        ev_id = getattr(ev, "event_id", "?")
        ev_type = getattr(ev, "event_type", None)

        # EventType 是 str Enum，取其字符串值
        if hasattr(ev_type, "value"):
            ev_type_str = str(ev_type.value)
        elif ev_type is not None:
            ev_type_str = str(ev_type)
        else:
            ev_type_str = ""

        type_counter[ev_type_str] = type_counter.get(ev_type_str, 0) + 1

        if not ev_type_str:
            _add_issue(
                result,
                "error",
                "\u4e8b\u4ef6\u7c7b\u578b\u4e3a\u7a7a",
                event_id=ev_id,
            )
        elif ev_type_str not in VALID_EVENT_TYPES:
            _add_issue(
                result,
                "error",
                "\u975e\u6cd5\u4e8b\u4ef6\u7c7b\u578b",
                event_id=ev_id,
                detail=f"type={ev_type_str[:20]}",
            )

    result["type_distribution"] = type_counter
    result["score"] = _calc_score(result)
    return result


# ==================== 4. Agent 身份有效性校验 ====================


def validate_agent_identity(events: list[Any]) -> dict[str, Any]:
    """校验 Agent 身份有效性

    检查 sender 和 recipient 字段：
    - 非空
    - 长度在合理范围内（1-64字符）
    - 无明显的注入特征（<script>, javascript: 等）
    """
    result = _empty_result("Agent \u8eab\u4efd\u6709\u6548\u6027")

    # 已知 Agent 集合（从事件中提取）
    known_agents: dict[str, int] = {}

    # 注入特征模式
    injection_patterns = [
        "<script",
        "javascript:",
        "data:text/html",
        "onerror=",
        "onload=",
        "eval(",
    ]

    def _check_agent_field(value: str, field_name: str, ev_id: str) -> None:
        """检查单个 agent 字段"""
        if not value or not value.strip():
            _add_issue(
                result,
                "error",
                f"{field_name} \u4e3a\u7a7a",
                event_id=ev_id,
            )
            return

        if len(value) > MAX_AGENT_NAME_LEN:
            _add_issue(
                result,
                "warn",
                f"{field_name} \u8d85\u957f",
                event_id=ev_id,
                detail=f"len={len(value)}",
            )

        value_lower = value.lower()
        for pattern in injection_patterns:
            if pattern in value_lower:
                _add_issue(
                    result,
                    "error",
                    f"{field_name} \u542b\u6ce8\u5165\u98ce\u9669",
                    event_id=ev_id,
                    detail=f"pattern={pattern}",
                )
                break

    for ev in events:
        result["total_checked"] += 1
        ev_id = getattr(ev, "event_id", "?")
        sender = getattr(ev, "sender", "") or ""
        recipient = getattr(ev, "recipient", "") or ""

        _check_agent_field(sender, "sender", ev_id)
        _check_agent_field(recipient, "recipient", ev_id)

        # 统计已知 Agent
        if sender:
            known_agents[sender] = known_agents.get(sender, 0) + 1
        if recipient:
            known_agents[recipient] = known_agents.get(recipient, 0) + 1

    result["known_agents"] = known_agents
    result["score"] = _calc_score(result)
    return result


# ==================== 统一审计入口 ====================


def run_full_audit(
    event_store: EventStore | None = None,
    session_id: str = "default",
    max_events: int = MAX_EVENTS_TO_CHECK,
) -> dict[str, Any]:
    """对 EventStore 执行全量质量审计

    依次执行四维校验，汇总为统一的质量报告。

    Args:
        event_store: EventStore 实例
        session_id: 会话ID
        max_events: 最大校验事件数

    Returns:
        包含四维校验结果和总分的质量报告
    """
    timestamp = datetime.now().isoformat()

    if not event_store:
        return {
            "timestamp": timestamp,
            "session_id": session_id,
            "total_events": 0,
            "overall_score": 1.0,
            "overall_status": "pass",
            "checks": {
                "causal_chain": _empty_result("\u56e0\u679c\u94fe"),
                "timestamps": _empty_result("\u65f6\u95f4\u6233"),
                "event_types": _empty_result("\u4e8b\u4ef6\u7c7b\u578b"),
                "agent_identity": _empty_result("Agent\u8eab\u4efd"),
            },
            "message": "EventStore \u672a\u521d\u59cb\u5316\uff0c\u8df3\u8fc7\u5ba1\u8ba1",
        }

    # 获取事件数据
    try:
        events = event_store.query_by_session(session_id, limit=max_events, newest_first=False)
    except Exception as e:
        logger.warning(f"\u6570\u636e\u8d28\u91cf\u5ba1\u8ba1\u67e5\u8be2\u5931\u8d25: {e}")
        return {
            "timestamp": timestamp,
            "session_id": session_id,
            "total_events": 0,
            "overall_score": 0.0,
            "overall_status": "error",
            "error": str(e)[:200],
            "checks": {},
        }

    total_events = len(events)
    if total_events == 0:
        return {
            "timestamp": timestamp,
            "session_id": session_id,
            "total_events": 0,
            "overall_score": 1.0,
            "overall_status": "pass",
            "checks": {
                "causal_chain": _empty_result("\u56e0\u679c\u94fe"),
                "timestamps": _empty_result("\u65f6\u95f4\u6233"),
                "event_types": _empty_result("\u4e8b\u4ef6\u7c7b\u578b"),
                "agent_identity": _empty_result("Agent\u8eab\u4efd"),
            },
            "message": "\u65e0\u4e8b\u4ef6\u6570\u636e\uff0c\u8df3\u8fc7\u5ba1\u8ba1",
        }

    # 执行四维校验
    checks = {
        "causal_chain": validate_causal_chain(events, event_store),
        "timestamps": validate_timestamps(events),
        "event_types": validate_event_types(events),
        "agent_identity": validate_agent_identity(events),
    }

    # 计算总分
    scores = [c["score"] for c in checks.values()]
    overall_score = round(sum(scores) / len(scores), 4) if scores else 1.0

    # 总体状态
    statuses = [c["status"] for c in checks.values()]
    if "error" in statuses:
        overall_status = "error"
    elif "warn" in statuses:
        overall_status = "warn"
    else:
        overall_status = "pass"

    # 统计问题数
    total_issues = sum(len(c["issues"]) for c in checks.values())
    error_count = sum(1 for c in checks.values() for i in c["issues"] if i["severity"] == "error")
    warn_count = sum(1 for c in checks.values() for i in c["issues"] if i["severity"] == "warn")

    return {
        "timestamp": timestamp,
        "session_id": session_id,
        "total_events": total_events,
        "overall_score": overall_score,
        "overall_status": overall_status,
        "total_issues": total_issues,
        "error_count": error_count,
        "warn_count": warn_count,
        "checks": checks,
    }
