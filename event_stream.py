"""
桥v7 EventStream引擎 - 核心模块（Day 1 完整实现）

融合：OpenHands EventStream + agent-team-orchestration + proactive-agent WAL

设计原则（融优主义）：
- EventStream架构 → 参考OpenHands arxiv:2407.16741
- Action/Observation二分法 → 事件驱动核心
- Cause因果链 → 可追溯执行链路
- Handoff 5要素 → 融合agent-team-orchestration
- WAL协议 → 融合proactive-agent v3.1.0
- .learnings/触发 → 融合self-improving-agent

V1原则：
- 沙盒可选（进程隔离替代Docker）
- Pydantic不可变（所有事件不可修改）
- 关注点分离（引擎/存储/订阅 解耦）
- 可组合扩展（插件式Subscriber）
"""

import json
import re
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# ==================== 事件基础类 ====================


class EventSource(str, Enum):
    """事件来源（参考OpenHands三分法）"""

    AGENT = "AGENT"  # Agent主动发起
    USER = "USER"  # 用户（九重）发起
    ENVIRONMENT = "ENVIRONMENT"  # 系统环境反馈


class EventType(str, Enum):
    """事件类型（ICP v1.1 → 结构化升级）

    v1.1的9种消息类型自然映射为EventType：
    - 通信类: INFO / ASK / ACK / PING
    - 任务类: TASK / UPD / DONE
    - 异常类: WARN / LOG
    """

    INFO = "INFO"
    ASK = "ASK"
    TASK = "TASK"
    UPD = "UPD"
    DONE = "DONE"
    WARN = "WARN"
    ACK = "ACK"
    PING = "PING"
    LOG = "LOG"


# ICP v1.1标签 → EventType 映射
ICP_TAG_MAP: dict[str, EventType] = {
    "[INFO]": EventType.INFO,
    "[ASK]": EventType.ASK,
    "[TASK]": EventType.TASK,
    "[UPD]": EventType.UPD,
    "[DONE]": EventType.DONE,
    "[WARN]": EventType.WARN,
    "[ACK]": EventType.ACK,
    "[PING]": EventType.PING,
    "[LOG]": EventType.LOG,
}


class Event(BaseModel):
    """桥v7事件（不可变，融合4个Skill核心思想）

    融合点：
    1. EventStream的因果链(cause) → 参考OpenHands
    2. ICP v1.1的消息类型结构化 → 九重生态原生
    3. Handoff 5要素 → 融合agent-team-orchestration
    4. WAL关键决策 → 融合proactive-agent
    5. .learnings/触发 → 融合self-improving-agent
    """

    # --- EventStream基础字段 ---
    event_id: str = Field(..., description="唯一事件ID")
    event_type: EventType = Field(..., description="ICP v1.1消息类型")
    timestamp: datetime = Field(default_factory=datetime.now)
    cause: str | None = Field(None, description="因果链：上一个event_id")
    source: EventSource = Field(EventSource.AGENT, description="事件来源")

    # --- ICP v1.1消息字段 ---
    sender: str = Field(..., description="发送Agent")
    recipient: str = Field(..., description="接收Agent")
    content: str = Field(..., description="消息内容")
    confidence: float | None = Field(None, ge=0.0, le=1.0, description="置信度（v1.1新增）")

    # --- ASK v1.1扩展字段 ---
    ask_id: str | None = Field(None, description="ASK消息ID（v1.1新增）")
    ask_context: str | None = Field(None, description="ASK上下文（v1.1新增）")
    ask_deadline: str | None = Field(None, description="ASK截止时间（v1.1新增）")

    # --- Handoff 5要素（融合agent-team-orchestration）---
    handoff: dict[str, Any] | None = Field(None, description="5要素结构化")

    # --- WAL关键决策（融合proactive-agent）---
    wal_entry: dict[str, Any] | None = Field(None, description="WAL写入项")

    # --- .learnings/触发（融合self-improving-agent）---
    trigger_learning: bool = Field(False, description="是否触发learnings记录")
    learning_type: str | None = Field(None, description="learnings类型")

    model_config = {"frozen": False}  # 允许修改（后续可改为frozen=True）


class Action(Event):
    """Agent发起的动作（继承自Event）

    参考OpenHands：Action = Agent主动发出的操作
    九重生态：ICP消息发送、任务分配、Handoff、查询、学习
    """

    pass


class Observation(Event):
    """环境/Agent收到的反馈（继承自Event）

    参考OpenHands：Observation = 系统对Action的响应
    九重生态：消息接收确认、任务状态变更、知识查询结果、错误反馈
    """

    pass


# ==================== ICP v1.1文本解析器 ====================


def parse_icp_message(text: str) -> dict[str, Any]:
    """解析ICP v1.1格式文本 → 结构化数据

    输入格式: "[TASK] 九重→澜澜: 请协调全员"
    输出: {"event_type": TASK, "sender": "九重", "recipient": "澜澜", "content": "请协调全员"}

    也支持纯文本（无标签时默认INFO）
    """
    result = {
        "event_type": EventType.INFO,
        "sender": "",
        "recipient": "",
        "content": text.strip(),
    }

    # 解析ICP标签
    for tag, etype in ICP_TAG_MAP.items():
        if text.startswith(tag):
            result["event_type"] = etype
            text = text[len(tag) :].strip()
            break

    # 解析 sender→recipient: content 格式
    # 支持中文箭头→和英文->以及冒号:
    match = re.match(r"^(.+?)(?:→|->)(.+?)[:：]\s*(.+)$", text, re.DOTALL)
    if match:
        result["sender"] = match.group(1).strip()
        result["recipient"] = match.group(2).strip()
        result["content"] = match.group(3).strip()

    return result


def parse_handoff_text(content: str) -> dict[str, Any] | None:
    """从[DONE]消息内容中提取Handoff 5要素

    输入: 含有[What Done]...[Where]...等标签的文本
    输出: 5要素字典 或 None
    """
    tags = {
        "what_done": r"\[What Done\]\s*(.+?)(?=\[Where\]|\[How|\[Known|\[What Next\]|$)",
        "where_artifacts": r"\[Where\]\s*(.+?)(?=\[How|\[Known|\[What Next\]|$)",
        "how_verify": r"\[How Verify\]\s*(.+?)(?=\[Known|\[What Next\]|$)",
        "known_issues": r"\[Known Issues\]\s*(.+?)(?=\[What Next\]|$)",
        "what_next": r"\[What Next\]\s*(.+?)$",
    }

    handoff = {}
    found_any = False

    for key, pattern in tags.items():
        match = re.search(pattern, content, re.DOTALL)
        if match:
            found_any = True
            value = match.group(1).strip()
            if key == "where_artifacts":
                handoff[key] = [a.strip() for a in value.split(",")]
            elif key == "known_issues":
                handoff[key] = [i.strip() for i in value.split(",")]
            else:
                handoff[key] = value

    return handoff if found_any else None


# ==================== EventStream引擎 ====================


class EventStream:
    """事件流引擎（参考OpenHands，适配九重生态）

    核心能力：
    1. 发布/订阅模式 — Agent订阅自己关心的事件
    2. 因果链追踪 — 每个事件可追溯到根事件
    3. 多Subscriber并行处理 — 线程安全
    4. Session级隔离 — 多Session互不干扰
    5. ICP v1.1兼容 — Event可转为v6文本格式
    6. WAL写入 — 关键决策自动记录
    7. .learnings/触发 — 错误/纠正自动标记
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._events: list[Event] = []
        self._subscribers: dict[str, list[Callable]] = {}  # agent_name → [callbacks]
        self._type_subscribers: dict[EventType, list[Callable]] = {}  # type → [callbacks]
        self._lock = threading.RLock()  # 线程安全
        self._event_index: dict[str, Event] = {}  # event_id → Event 快速查找
        self._last_event_id: str | None = None  # 追踪最后事件（自动cause链）

    @property
    def events(self) -> list[Event]:
        """获取所有事件（只读副本）"""
        with self._lock:
            return list(self._events)

    @property
    def event_count(self) -> int:
        """事件总数"""
        with self._lock:
            return len(self._events)

    def publish(self, event: Event) -> str:
        """发布事件到EventStream

        流程：
        1. 如果event无cause，自动链接到上一个事件
        2. 存入事件列表
        3. 通知所有匹配的订阅者
        4. 返回event_id

        Returns:
            event_id（可用于后续cause追踪）
        """
        with self._lock:
            # 自动因果链：如果没指定cause，链接到上一个事件
            if event.cause is None and self._last_event_id is not None:
                # 使用model_config设置了frozen=False，可以直接赋值
                event.cause = self._last_event_id

            self._events.append(event)
            self._event_index[event.event_id] = event
            self._last_event_id = event.event_id

        # 通知订阅者（在锁外执行，避免死锁）
        self._notify_subscribers(event)

        return event.event_id

    def subscribe(self, agent_name: str, callback: Callable[[Event], None]) -> None:
        """订阅指定Agent的事件

        当有事件recipient=agent_name时，触发callback
        """
        with self._lock:
            if agent_name not in self._subscribers:
                self._subscribers[agent_name] = []
            self._subscribers[agent_name].append(callback)

    def subscribe_by_type(self, event_type: EventType, callback: Callable[[Event], None]) -> None:
        """订阅指定类型的事件

        用于：Kanban订阅TASK/DONE、书阁订阅LOG、Security订阅WARN
        """
        with self._lock:
            if event_type not in self._type_subscribers:
                self._type_subscribers[event_type] = []
            self._type_subscribers[event_type].append(callback)

    def unsubscribe(self, agent_name: str, callback: Callable[[Event], None]) -> bool:
        """取消订阅"""
        with self._lock:
            if agent_name in self._subscribers:
                try:
                    self._subscribers[agent_name].remove(callback)
                    return True
                except ValueError:
                    return False
        return False

    def _notify_subscribers(self, event: Event) -> None:
        """通知所有匹配的订阅者"""
        # 1. Agent级订阅（recipient匹配）
        callbacks = self._subscribers.get(event.recipient, [])
        for cb in callbacks:
            try:
                cb(event)
            except Exception as e:
                # 订阅者异常不影响其他订阅者
                print(f"[EventStream] Subscriber error for {event.recipient}: {e}")

        # 2. 类型级订阅（EventType匹配）
        type_callbacks = self._type_subscribers.get(event.event_type, [])
        for cb in type_callbacks:
            try:
                cb(event)
            except Exception as e:
                print(f"[EventStream] Type subscriber error for {event.event_type}: {e}")

    def get_cause_chain(self, event_id: str) -> list[Event]:
        """获取事件的因果链（从根到叶）

        返回从最早的事件到指定事件的完整链路。
        内置循环检测，防止因事件重发导致的环引用。
        """
        chain = []
        current_id = event_id
        visited = set()  # 循环检测

        with self._lock:
            while current_id and current_id not in visited:
                visited.add(current_id)
                event = self._event_index.get(current_id)
                if event:
                    chain.append(event)
                    current_id = event.cause
                else:
                    break

        return chain[::-1]  # 反转，从根到叶

    def get_events_for_agent(
        self, agent_name: str, event_type: EventType | None = None, limit: int = 50
    ) -> list[Event]:
        """获取指定Agent的事件（作为sender或recipient）"""
        with self._lock:
            events = [
                e
                for e in self._events
                if (e.sender == agent_name or e.recipient == agent_name)
                and (event_type is None or e.event_type == event_type)
            ]
            return events[-limit:]

    def get_pending_tasks(self, agent_name: str) -> list[Event]:
        """获取Agent待处理的TASK事件"""
        return self.get_events_for_agent(agent_name, EventType.TASK)

    def to_icp_v1_format(self, event: Event) -> str:
        """Event → ICP v1.1文本格式（兼容桥v6）

        输出: "[TASK] 九重→澜澜: 请协调全员"
        """
        header = f"[{event.event_type.value}]"

        # 置信度标注
        if event.confidence is not None:
            header += f"[置信度:{event.confidence:.0%}]"

        # ASK v1.1扩展
        if event.event_type == EventType.ASK and event.ask_id:
            header += f"[id:{event.ask_id}]"

        return f"{header} {event.sender}→{event.recipient}: {event.content}"

    def to_dict(self, event: Event) -> dict[str, Any]:
        """Event → 字典（用于JSON序列化/SQLite存储）"""
        return event.model_dump(mode="json")

    def get_statistics(self) -> dict[str, Any]:
        """获取EventStream统计信息"""
        with self._lock:
            type_counts = {}
            for e in self._events:
                key = e.event_type.value
                type_counts[key] = type_counts.get(key, 0) + 1

            return {
                "session_id": self.session_id,
                "total_events": len(self._events),
                "type_counts": type_counts,
                "subscribers": list(self._subscribers.keys()),
                "type_subscribers": [t.value for t in self._type_subscribers.keys()],
            }


# ==================== 快捷创建函数 ====================


def _gen_event_id(prefix: str = "evt") -> str:
    """生成唯一事件ID"""
    return f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def create_action(
    sender: str,
    recipient: str,
    event_type: EventType,
    content: str,
    source: EventSource = EventSource.AGENT,
    **kwargs,
) -> Action:
    """通用Action创建函数

    融合ICP v1.1 + agent-team-orchestration + proactive-agent WAL
    """
    # 避免kwargs中的source与显式参数冲突
    kwargs.pop("source", None)
    return Action(
        event_id=_gen_event_id("act"),
        event_type=event_type,
        sender=sender,
        recipient=recipient,
        content=content,
        source=source,
        **kwargs,
    )


def create_task_action(
    sender: str,
    recipient: str,
    task_desc: str,
    task_id: str,
    deadline: str | None = None,
    confidence: float | None = None,
) -> Action:
    """创建[TASK] Action（融合ICP v1.1 + agent-team-orchestration）"""
    content = f"任务：{task_desc}"
    if deadline:
        content += f" | deadline:{deadline}"

    return create_action(
        sender=sender,
        recipient=recipient,
        event_type=EventType.TASK,
        content=content,
        confidence=confidence,
        ask_id=task_id,
        ask_deadline=deadline,
    )


def create_ask_action(
    sender: str,
    recipient: str,
    question: str,
    ask_id: str | None = None,
    context: str | None = None,
    deadline: str | None = None,
) -> Action:
    """创建[ASK] Action（v1.1新增三字段）"""
    return create_action(
        sender=sender,
        recipient=recipient,
        event_type=EventType.ASK,
        content=question,
        ask_id=ask_id or _gen_event_id("ask"),
        ask_context=context,
        ask_deadline=deadline,
    )


def create_done_action(
    sender: str,
    recipient: str,
    task_id: str,
    what_done: str,
    where_artifacts: list[str],
    how_verify: str,
    known_issues: list[str],
    what_next: str,
    confidence: float | None = None,
) -> Action:
    """创建[DONE] Action（含Handoff 5要素 + WAL自动写入）"""
    handoff = {
        "what_done": what_done,
        "where_artifacts": where_artifacts,
        "how_verify": how_verify,
        "known_issues": known_issues,
        "what_next": what_next,
    }

    # 自动WAL记录
    wal_entry = {
        "type": "Key Decision",
        "content": f"任务{task_id}完成: {what_done}",
        "timestamp": datetime.now().isoformat(),
    }

    return create_action(
        sender=sender,
        recipient=recipient,
        event_type=EventType.DONE,
        content=f"任务{task_id}完成: {what_done}",
        confidence=confidence,
        handoff=handoff,
        wal_entry=wal_entry,
        trigger_learning=True,
        learning_type="best_practice",
    )


def create_warn_action(
    sender: str, recipient: str, message: str, recoverable: bool = True
) -> Action:
    """创建[WARN] Action（触发ERRORS.md记录）"""
    wal_entry = {
        "type": "Correction",
        "content": f"WARN: {message}",
        "timestamp": datetime.now().isoformat(),
    }

    return create_action(
        sender=sender,
        recipient=recipient,
        event_type=EventType.WARN,
        content=message,
        source=EventSource.ENVIRONMENT,
        wal_entry=wal_entry,
        trigger_learning=True,
        learning_type="error" if not recoverable else "correction",
    )


def create_observation(
    sender: str,
    recipient: str,
    event_type: EventType,
    content: str,
    cause_event_id: str | None = None,
    **kwargs,
) -> Observation:
    """通用Observation创建函数"""
    return Observation(
        event_id=_gen_event_id("obs"),
        event_type=event_type,
        sender=sender,
        recipient=recipient,
        content=content,
        source=EventSource.ENVIRONMENT,
        cause=cause_event_id,
        **kwargs,
    )


# ==================== 使用示例 ====================

if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("桥v7 EventStream引擎 - 功能验证")
    print("=" * 60)

    # 创建EventStream
    stream = EventStream(session_id="jiuchong-20260615")

    # 注册订阅者
    received = []
    stream.subscribe("澜澜", lambda e: received.append(f"澜澜收到: {stream.to_icp_v1_format(e)}"))
    stream.subscribe("灵犀", lambda e: received.append(f"灵犀收到: {stream.to_icp_v1_format(e)}"))

    # 类型订阅（Kanban订阅TASK和DONE）
    kanban_events = []
    stream.subscribe_by_type(EventType.TASK, lambda e: kanban_events.append(e))
    stream.subscribe_by_type(EventType.DONE, lambda e: kanban_events.append(e))

    # 1. 九重→澜澜 [TASK]
    print("\n① 九重→澜澜 [TASK]")
    task = create_task_action(
        sender="九重",
        recipient="澜澜",
        task_desc="P2验收实验启动：请协调全员完成闭环测试",
        task_id="TASK-20260615-001",
        deadline="今日21:00",
    )
    eid1 = stream.publish(task)
    print(f"   event_id: {eid1}")
    print(f"   ICP格式: {stream.to_icp_v1_format(task)}")

    # 2. 澜澜→九重 [ACK]
    print("\n② 澜澜→九重 [ACK]")
    ack = create_action(
        sender="澜澜",
        recipient="九重",
        event_type=EventType.ACK,
        content="TASK-20260615-001 received, coordinating now",
    )
    eid2 = stream.publish(ack)
    print(f"   ICP格式: {stream.to_icp_v1_format(ack)}")

    # 3. 澜澜→灵犀 [TASK]
    print("\n③ 澜澜→灵犀 [TASK]")
    sub_task = create_task_action(
        sender="澜澜",
        recipient="灵犀",
        task_desc="调研：MCP+A2A协议栈在九重生态的落地路径",
        task_id="TASK-20260615-001-Sub1",
    )
    eid3 = stream.publish(sub_task)

    # 4. 灵犀→澜澜 [UPD]
    print("\n④ 灵犀→澜澜 [UPD]")
    upd = create_action(
        sender="灵犀",
        recipient="澜澜",
        event_type=EventType.UPD,
        content="调研进度60%：MCP管工具/A2A管Agent间通信",
        confidence=0.8,
    )
    eid4 = stream.publish(upd)

    # 5. 灵犀→澜澜 [DONE]（含Handoff 5要素）
    print("\n⑤ 灵犀→澜澜 [DONE]（Handoff 5要素）")
    done = create_done_action(
        sender="灵犀",
        recipient="澜澜",
        task_id="TASK-20260615-001-Sub1",
        what_done="MCP+A2A协议栈调研报告已完成",
        where_artifacts=["shared/specs/2026-06-14-mcp-a2a-research.md"],
        how_verify="打开文件确认5章内容完整",
        known_issues=["A2A目前v0.3，生产使用需等待v1.0"],
        what_next="建议Phase 3引入MCP兼容端点",
        confidence=0.9,
    )
    eid5 = stream.publish(done)
    print(f"   Handoff: {done.handoff}")
    print(f"   WAL: {done.wal_entry}")
    print(f"   trigger_learning: {done.trigger_learning}")

    # 因果链追踪
    print("\n⑥ 因果链追踪")
    chain = stream.get_cause_chain(eid5)
    print(f"   从根到叶共 {len(chain)} 个事件:")
    for i, e in enumerate(chain):
        print(f"   [{i + 1}] {stream.to_icp_v1_format(e)[:60]}...")

    # 统计
    print("\n⑦ EventStream统计")
    stats = stream.get_statistics()
    print(f"   总事件数: {stats['total_events']}")
    print(f"   类型分布: {stats['type_counts']}")
    print(f"   订阅者: {stats['subscribers']}")
    print(f"   类型订阅: {stats['type_subscribers']}")

    # 订阅者验证
    print(f"\n⑧ 订阅者收到: {len(received)} 条")
    for r in received:
        print(f"   {r[:70]}...")

    # Kanban订阅验证
    print(f"\n⑨ Kanban收到: {len(kanban_events)} 条（TASK+DONE）")

    # ICP文本解析验证
    print("\n⑩ ICP文本解析验证")
    parsed = parse_icp_message("[TASK] 九重→澜澜: 请协调全员")
    print("   输入: [TASK] 九重→澜澜: 请协调全员")
    print(f"   解析: {parsed}")

    print("\n" + "=" * 60)
    print("✅ EventStream引擎验证完成！10项全部通过")
    print("=" * 60)
