"""
因果记忆桥接引擎 — 连接 EventStream 因果链与记忆演化系统。

哲学映射（九重·因果记忆三层次）：
  念起（Trigger）  — 事件发布时，自动沿因果链创建记忆
  溯源（Weaver）   — 检索时，沿因果链追溯关联记忆
  涌现（无为）     — 因果链完整时，相关记忆自然浮现

设计原则：
  - 因果驱动优于检索驱动：记忆沿因果链涌现，而非被显式查找
  - 明文可审计：每条记忆的因果链可见、可溯、可修改
  - 零训练成本：纯因果链分析 + 启发式，不依赖GPU
  - 本地主权：因果链完全存储在本地，不依赖任何API

核心闭环：
  Event发布 → CausalMemoryBridge.remember_event() → 记忆携带cause_event_id入库
  ↓
  新Event发布 → recall_with_cause() → 因果链追溯 → HybridRetriever因果加成
  ↓
  相关记忆沿因果链自然涌现 → 回到Event推理上下文
"""

import time
from typing import Any, Dict, List, Optional, Set

import structlog

from event_stream import Event, EventStream, EventType
from memory_evolution import (
    DEFAULT_IMPORTANCE,
    ForgettingEngine,
    HybridRetriever,
    MemoryEvolutionOrchestra,
    MemoryRecord,
    MemoryType,
)

logger = structlog.get_logger("bridge_v7.causal_memory")


class CausalMemoryBridge:
    """因果记忆桥接引擎。

    连接 EventStream 的因果链（cause字段 + get_cause_chain()）
    与 MemoryEvolutionOrchestra 的记忆演化系统。

    三大能力：
    1. remember_event()  — 念起：将事件转化为因果记忆
    2. recall_with_cause() — 涌现：因果驱动的记忆召回
    3. trace_memory_cause() — 溯源：从记忆回溯到因果根
    """

    # 因果链追溯最大深度（防止无限循环）
    MAX_CAUSE_DEPTH = 20

    # 因果链完整性权重：每深一层，完整性贡献递减
    COMPLETENESS_DECAY = 0.9

    def __init__(self, event_stream: EventStream, orchestra: MemoryEvolutionOrchestra):
        """
        Args:
            event_stream: 事件流引擎（提供因果链）
            orchestra: 记忆演化总指挥（提供记忆存储与检索）
        """
        self.event_stream = event_stream
        self.orchestra = orchestra
        self.forgetting: ForgettingEngine = orchestra.forgetting
        self.retriever: HybridRetriever = orchestra.retriever

    # ===================================================================
    # 念起 — 事件 → 因果记忆
    # ===================================================================

    def remember_event(
        self,
        event: Event,
        memory_type: MemoryType = MemoryType.EPISODIC,
        importance: float = DEFAULT_IMPORTANCE,
        tags: list[str] | None = None,
    ) -> MemoryRecord:
        """将事件转化为记忆，携带因果链。

        这是"念起"——每一次事件发生，都在记忆中留下因果印记。
        记忆的 cause_event_id 指向触发它的事件，形成可追溯的因果链。

        Args:
            event: 要记忆的事件
            memory_type: 认知记忆类型（默认情景记忆）
            importance: 重要性（0-1）
            tags: 自定义标签

        Returns:
            创建的 MemoryRecord（已入库）
        """
        # 构建记忆内容：ICP格式 + 事件元信息
        icp_text = self.event_stream.to_icp_v1_format(event)
        content = f"[{event.event_type.value}] {event.sender}->{event.recipient}: {event.content}"

        # 标签：事件类型 + sender + recipient
        memory_tags = list(tags or [])
        memory_tags.extend(
            [
                event.event_type.value.lower(),
                f"from:{event.sender}",
                f"to:{event.recipient}",
            ]
        )

        rec = MemoryRecord(
            id=f"cmem_{int(time.time() * 1000)}_{abs(hash(content)) % 10000:04d}",
            content=content,
            mtype=memory_type,
            importance=importance,
            source=f"event:{event.event_id}",
            tags=memory_tags,
            cause_event_id=event.event_id,
        )

        self.forgetting.add(rec)
        logger.info(
            "causal_memory.remembered",
            memory_id=rec.id,
            event_id=event.event_id,
            cause=event.cause,
            event_type=event.event_type.value,
        )

        return rec

    # ===================================================================
    # 涌现 — 因果驱动的记忆召回
    # ===================================================================

    def get_causal_context(self, event_id: str, max_depth: int = MAX_CAUSE_DEPTH) -> set[str]:
        """获取事件的因果上下文（因果链上所有事件ID集合）。

        从指定事件出发，沿 cause 链回溯，收集所有祖先事件ID。
        这些ID构成了当前事件的"因果上下文"——
        记忆如果与这些事件关联，将在召回时获得加成。

        Args:
            event_id: 起始事件ID
            max_depth: 最大追溯深度

        Returns:
            因果链上所有事件ID的集合（不含起始事件本身）
        """
        context: set[str] = set()
        chain = self.event_stream.get_cause_chain(event_id)

        # 链中最后一个元素是起始事件本身，排除它
        for event in chain[:-1][:max_depth]:
            context.add(event.event_id)

        return context

    def recall_with_cause(
        self,
        query: str,
        current_event_id: str | None = None,
        top_k: int = 10,
        use_causal_boost: bool = True,
    ) -> list[MemoryRecord]:
        """因果召回：混合检索 + 因果链加成。

        这是"涌现"——当新的"果"在形成时，相关的"因"自然浮现。
        不需要显式查找，因果链连通的记忆会沿着链路涌现。

        Args:
            query: 查询文本
            current_event_id: 当前事件的ID（触发此查询的事件）
            top_k: 返回前K条
            use_causal_boost: 是否启用因果加成

        Returns:
            召回的记忆列表（已按因果加成排序）
        """
        causal_context: set[str] | None = None

        if use_causal_boost and current_event_id:
            causal_context = self.get_causal_context(current_event_id)
            logger.debug(
                "causal_memory.recall",
                current_event=current_event_id,
                causal_chain_length=len(causal_context),
            )

        results = self.retriever.search(
            query=query,
            top_k=top_k,
            causal_context=causal_context,
        )

        return results

    # ===================================================================
    # 溯源 — 从记忆回溯到因果根
    # ===================================================================

    def trace_memory_cause(self, memory_id: str) -> list[Event]:
        """追溯记忆的因果链。

        从一条记忆出发，找到它的 cause_event_id，
        然后沿 EventStream 因果链回溯到根事件。

        这是"追根溯源"——每一条记忆都能回到它的"起心动念"。

        Args:
            memory_id: 记忆ID

        Returns:
            因果链上的事件列表（从根到触发事件）
        """
        rec = self.forgetting.get(memory_id)
        if not rec or not rec.cause_event_id:
            return []

        return self.event_stream.get_cause_chain(rec.cause_event_id)

    def find_causal_memories(self, event_id: str) -> list[MemoryRecord]:
        """查找由指定事件直接触发的所有记忆。

        Args:
            event_id: 事件ID

        Returns:
            cause_event_id == event_id 的所有活跃记忆
        """
        return self.forgetting.get_by_cause(event_id)

    # ===================================================================
    # 因果链完整性评估
    # ===================================================================

    def causal_completeness(self, event_id: str) -> float:
        """评估因果链的完整性。

        从指定事件回溯到根事件，检查链路上是否有断裂。
        完整性 = 实际链长度 / 期望链长度（基于时间跨度估算）。

        完整性越高，说明因果链越连贯，记忆涌现越自然。

        Args:
            event_id: 起始事件ID

        Returns:
            完整性分数 (0.0 - 1.0)
        """
        chain = self.event_stream.get_cause_chain(event_id)
        if not chain:
            return 0.0

        # 链长度贡献：越长越完整（有上限衰减）
        length_score = min(1.0, len(chain) / 10.0)

        # 连续性检查：每个事件的cause是否指向链中的前一个事件
        # chain 是从根到叶排列，chain[i+1].cause 应指向 chain[i].event_id
        continuous_count = 0
        for i, event in enumerate(chain[:-1]):
            next_event = chain[i + 1]
            if next_event.cause == event.event_id:
                continuous_count += 1

        continuity_score = continuous_count / max(len(chain) - 1, 1)

        # 综合完整性
        return round(length_score * 0.4 + continuity_score * 0.6, 4)

    # ===================================================================
    # 因果记忆摘要
    # ===================================================================

    def causal_summary(self, event_id: str) -> dict[str, Any]:
        """生成指定事件的因果记忆摘要。

        Returns:
            {
                "event_id": ...,
                "causal_chain_length": ...,
                "completeness": ...,
                "related_memories": [...],
                "chain_events": [...],
            }
        """
        chain = self.event_stream.get_cause_chain(event_id)
        related = self.find_causal_memories(event_id)
        completeness = self.causal_completeness(event_id)

        # 收集因果链上所有事件的关联记忆
        all_related: list[MemoryRecord] = list(related)
        seen_ids = {r.id for r in all_related}
        for event in chain:
            event_memories = self.find_causal_memories(event.event_id)
            for m in event_memories:
                if m.id not in seen_ids:
                    all_related.append(m)
                    seen_ids.add(m.id)

        return {
            "event_id": event_id,
            "causal_chain_length": len(chain),
            "completeness": completeness,
            "direct_memories": len(related),
            "total_causal_memories": len(all_related),
            "chain_events": [
                {
                    "event_id": e.event_id,
                    "type": e.event_type.value,
                    "sender": e.sender,
                    "recipient": e.recipient,
                    "content_preview": e.content[:80],
                    "has_cause": e.cause is not None,
                }
                for e in chain
            ],
            "memories_preview": [
                {
                    "memory_id": m.id,
                    "content_preview": m.content[:80],
                    "cause_event_id": m.cause_event_id,
                    "importance": round(m.importance, 2),
                }
                for m in all_related[:10]
            ],
        }


# ======================================================================
# 自动桥接：EventStream → CausalMemoryBridge
# ======================================================================


class AutoCausalBridge:
    """自动因果桥接器：订阅EventStream，自动将事件转化为因果记忆。

    使用方式：
        bridge = AutoCausalBridge(event_stream, orchestra)
        bridge.start()  # 开始自动监听

    之后所有发布到EventStream的事件都会自动创建因果记忆。
    可通过 event_filter 过滤需要记忆的事件类型。
    """

    def __init__(
        self,
        event_stream: EventStream,
        orchestra: MemoryEvolutionOrchestra,
        memory_types: dict[EventType, MemoryType] | None = None,
        event_filter: set[EventType] | None = None,
    ):
        """
        Args:
            event_stream: 事件流
            orchestra: 记忆演化系统
            memory_types: 事件类型→认知记忆类型映射（默认全EPISODIC）
            event_filter: 只记忆这些类型的事件（None=全部）
        """
        self.bridge = CausalMemoryBridge(event_stream, orchestra)
        self.event_stream = event_stream
        self.memory_types = memory_types or {}
        self.event_filter = event_filter
        self._active = False

        # 默认映射：不同事件类型→不同认知记忆
        if not memory_types:
            self.memory_types = {
                EventType.INFO: MemoryType.EPISODIC,
                EventType.TASK: MemoryType.PROCEDURAL,
                EventType.DONE: MemoryType.REFLECTIVE,
                EventType.WARN: MemoryType.EMOTIONAL,
                EventType.ASK: MemoryType.EPISODIC,
                EventType.UPD: MemoryType.EPISODIC,
                EventType.ACK: MemoryType.EPISODIC,
                EventType.PING: MemoryType.EPISODIC,
                EventType.LOG: MemoryType.SEMANTIC,
            }

    def start(self):
        """启动自动桥接：订阅所有事件类型。"""
        if self._active:
            return

        for etype in EventType:
            if self.event_filter and etype not in self.event_filter:
                continue
            self.event_stream.subscribe_by_type(etype, self._on_event)

        self._active = True
        logger.info(
            "auto_causal_bridge.started",
            filter=[t.value for t in self.event_filter] if self.event_filter else "all",
        )

    def stop(self):
        """停止自动桥接。"""
        self._active = False
        logger.info("auto_causal_bridge.stopped")

    def _on_event(self, event: Event):
        """事件回调：自动创建因果记忆。"""
        mtype = self.memory_types.get(event.event_type, MemoryType.EPISODIC)

        # 根据事件类型调整重要性
        importance_map = {
            EventType.DONE: 0.9,  # 完成事件最重要
            EventType.WARN: 0.85,  # 警告次之
            EventType.TASK: 0.8,  # 任务分配
            EventType.ASK: 0.75,  # 提问
            EventType.UPD: 0.6,  # 更新
            EventType.INFO: 0.5,  # 信息
            EventType.ACK: 0.4,  # 确认
            EventType.LOG: 0.3,  # 日志
            EventType.PING: 0.2,  # 心跳
        }
        importance = importance_map.get(event.event_type, DEFAULT_IMPORTANCE)

        self.bridge.remember_event(
            event=event,
            memory_type=mtype,
            importance=importance,
        )
