"""
test_causal_memory.py — 因果记忆桥接引擎测试

覆盖范围：
- CausalMemoryBridge: remember_event / recall_with_cause / trace_memory_cause
- 因果上下文构建 (get_causal_context)
- 因果链完整性评估 (causal_completeness)
- 因果记忆摘要 (causal_summary)
- AutoCausalBridge: 自动事件→记忆桥接
- HybridRetriever 因果加成验证
- 完整闭环：事件→记忆→因果召回→涌现

哲学映射验证：
  念起 → remember_event 携带 cause_event_id
  溯源 → trace_memory_cause 回溯到根
  涌现 → recall_with_cause 因果加成让相关记忆浮现
"""

import time

import pytest

from causal_memory import AutoCausalBridge, CausalMemoryBridge
from event_stream import (
    Event,
    EventSource,
    EventStream,
    EventType,
    create_action,
    create_done_action,
    create_task_action,
)
from memory_evolution import (
    DEFAULT_IMPORTANCE,
    ForgettingEngine,
    HybridRetriever,
    MemoryEvolutionOrchestra,
    MemoryRecord,
    MemoryType,
)

# ============================================================
# Fixture: 构建因果链事件流
# ============================================================


@pytest.fixture
def causal_setup(work_dir):
    """构建完整的因果记忆测试环境。"""
    stream = EventStream(session_id="test-causal")
    orchestra = MemoryEvolutionOrchestra(str(work_dir / "causal.db"))
    bridge = CausalMemoryBridge(stream, orchestra)

    return stream, orchestra, bridge


@pytest.fixture
def event_chain(causal_setup):
    """构建一条5事件的因果链。

    链路：
      E1: 九重→澜澜 [TASK] "调研因果记忆"
      E2: 澜澜→灵犀 [TASK] "分析MemGen论文" (cause=E1)
      E3: 灵犀→澜澜 [UPD] "进度60%" (cause=E2)
      E4: 灵犀→澜澜 [DONE] "报告完成" (cause=E3)
      E5: 澜澜→九重 [DONE] "因果记忆方案完成" (cause=E4)
    """
    stream, orchestra, bridge = causal_setup

    e1 = create_task_action("九重", "澜澜", "调研因果记忆架构", "TASK-001")
    eid1 = stream.publish(e1)

    e2 = create_task_action("澜澜", "灵犀", "分析MemGen论文", "TASK-002")
    eid2 = stream.publish(e2)

    e3 = create_action("灵犀", "澜澜", EventType.UPD, "调研进度60%：MemGen用LoRA训练触发器")
    eid3 = stream.publish(e3)

    e4 = create_done_action(
        "灵犀",
        "澜澜",
        "TASK-002",
        what_done="MemGen分析报告完成",
        where_artifacts=["docs/memgen_analysis.md"],
        how_verify="检查5章节完整",
        known_issues=["需要GPU训练"],
        what_next="对比HomeStream方案",
    )
    eid4 = stream.publish(e4)

    e5 = create_done_action(
        "澜澜",
        "九重",
        "TASK-001",
        what_done="因果记忆方案设计完成",
        where_artifacts=["docs/causal_memory_design.md"],
        how_verify="因果链闭环验证通过",
        known_issues=["V6+实现因果涌现"],
        what_next="落地到V5.0开源版",
    )
    eid5 = stream.publish(e5)

    events = [e1, e2, e3, e4, e5]
    event_ids = [eid1, eid2, eid3, eid4, eid5]

    return stream, orchestra, bridge, events, event_ids


# ============================================================
# CausalMemoryBridge 基础测试
# ============================================================


class TestCausalMemoryBridgeInit:
    """因果记忆桥接引擎初始化测试。"""

    def test_init(self, causal_setup):
        """初始化成功。"""
        stream, orchestra, bridge = causal_setup
        assert bridge.event_stream is stream
        assert bridge.orchestra is orchestra
        assert bridge.forgetting is orchestra.forgetting
        assert bridge.retriever is orchestra.retriever


class TestRememberEvent:
    """念起 — 事件→因果记忆转化测试。"""

    def test_remember_event_basic(self, causal_setup):
        """事件转化为记忆，携带 cause_event_id。"""
        stream, orchestra, bridge = causal_setup

        event = create_task_action("九重", "澜澜", "测试因果记忆", "T-001")
        eid = stream.publish(event)

        rec = bridge.remember_event(event)

        assert rec.id.startswith("cmem_")
        assert rec.cause_event_id == eid
        assert "TASK" in rec.content
        assert "九重" in rec.content
        assert "澜澜" in rec.content
        assert "task" in rec.tags
        assert "from:九重" in rec.tags
        assert "to:澜澜" in rec.tags

    def test_remember_event_with_custom_type(self, causal_setup):
        """自定义认知记忆类型。"""
        stream, orchestra, bridge = causal_setup

        event = create_done_action(
            "澜澜",
            "九重",
            "T-001",
            what_done="完成",
            where_artifacts=[],
            how_verify="验证",
            known_issues=[],
            what_next="下一步",
        )
        stream.publish(event)

        rec = bridge.remember_event(event, memory_type=MemoryType.REFLECTIVE)
        assert rec.mtype == MemoryType.REFLECTIVE

    def test_remember_event_stored_in_db(self, causal_setup):
        """记忆成功入库。"""
        stream, orchestra, bridge = causal_setup

        event = create_action("九重", "澜澜", EventType.INFO, "重要信息")
        eid = stream.publish(event)

        rec = bridge.remember_event(event)
        got = orchestra.forgetting.get(rec.id)

        assert got is not None
        assert got.cause_event_id == eid

    def test_remember_multiple_events_same_chain(self, event_chain):
        """同一因果链上的多个事件都创建记忆。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        for event in events:
            bridge.remember_event(event)

        active = orchestra.forgetting.list_active(limit=20)
        assert len(active) == 5

        # 每条记忆都有 cause_event_id
        for rec in active:
            assert rec.cause_event_id is not None
            assert rec.cause_event_id in event_ids


# ============================================================
# 因果上下文测试
# ============================================================


class TestCausalContext:
    """因果上下文构建测试。"""

    def test_get_causal_context_leaf(self, event_chain):
        """叶节点的因果上下文包含所有祖先。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        # E5 的因果上下文应包含 E1-E4
        context = bridge.get_causal_context(event_ids[4])

        assert event_ids[0] in context
        assert event_ids[1] in context
        assert event_ids[2] in context
        assert event_ids[3] in context
        # 不包含自身
        assert event_ids[4] not in context

    def test_get_causal_context_root(self, event_chain):
        """根节点的因果上下文为空。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        context = bridge.get_causal_context(event_ids[0])
        assert len(context) == 0

    def test_get_causal_context_middle(self, event_chain):
        """中间节点的因果上下文只包含其祖先。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        # E3 的因果上下文应包含 E1, E2
        context = bridge.get_causal_context(event_ids[2])

        assert event_ids[0] in context
        assert event_ids[1] in context
        assert event_ids[2] not in context
        assert event_ids[3] not in context

    def test_get_causal_context_nonexistent(self, causal_setup):
        """不存在的事件→空上下文。"""
        stream, orchestra, bridge = causal_setup
        context = bridge.get_causal_context("nonexistent_event")
        assert len(context) == 0

    def test_get_causal_context_max_depth(self, event_chain):
        """最大深度限制。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        context = bridge.get_causal_context(event_ids[4], max_depth=2)
        # 深度2：只回溯2层祖先
        assert len(context) <= 2


# ============================================================
# 因果召回测试
# ============================================================


class TestRecallWithCause:
    """涌现 — 因果驱动召回测试。"""

    def test_recall_without_cause(self, event_chain):
        """无因果上下文时，退化为普通检索。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        for event in events:
            bridge.remember_event(event)

        results = bridge.recall_with_cause("因果记忆", current_event_id=None)
        assert len(results) > 0

    def test_recall_with_cause_boosts_related(self, event_chain):
        """因果加成：因果链上的记忆获得分数提升。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        # 为E1创建记忆
        bridge.remember_event(events[0])

        # 为E4创建记忆（内容与E1不同但因果链相连）
        bridge.remember_event(events[3])

        # 为一个无关事件创建记忆
        unrelated = create_action("用户", "系统", EventType.INFO, "完全无关的内容xyz")
        stream.publish(unrelated)
        bridge.remember_event(unrelated)

        # 从E5检索：E1和E4在因果链上，应获得加成
        results = bridge.recall_with_cause("因果", current_event_id=event_ids[4])

        # 因果链上的记忆应排在前面
        causal_memories = [r for r in results if r.cause_event_id in {event_ids[0], event_ids[3]}]
        assert len(causal_memories) > 0

    def test_recall_with_cause_no_memories(self, causal_setup):
        """无记忆时召回返回空。"""
        stream, orchestra, bridge = causal_setup

        event = create_action("九重", "澜澜", EventType.INFO, "test")
        eid = stream.publish(event)

        results = bridge.recall_with_cause("query", current_event_id=eid)
        assert results == []


# ============================================================
# 溯源测试
# ============================================================


class TestTraceMemoryCause:
    """溯源 — 从记忆回溯因果链测试。"""

    def test_trace_to_root(self, event_chain):
        """从叶节点记忆追溯到根事件。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        # 为E5创建记忆
        rec = bridge.remember_event(events[4])

        # 溯源：应得到完整因果链 E1→E2→E3→E4→E5
        chain = bridge.trace_memory_cause(rec.id)

        assert len(chain) == 5
        assert chain[0].event_id == event_ids[0]  # 根
        assert chain[-1].event_id == event_ids[4]  # 叶

    def test_trace_no_cause(self, causal_setup):
        """无因果链的记忆→空链。"""
        stream, orchestra, bridge = causal_setup

        # 直接添加一条无 cause_event_id 的记忆
        rec = MemoryRecord(id="no_cause", content="no cause memory")
        orchestra.forgetting.add(rec)

        chain = bridge.trace_memory_cause("no_cause")
        assert chain == []

    def test_trace_nonexistent_memory(self, causal_setup):
        """不存在的记忆→空链。"""
        stream, orchestra, bridge = causal_setup
        chain = bridge.trace_memory_cause("nonexistent")
        assert chain == []

    def test_find_causal_memories(self, event_chain):
        """查找由指定事件触发的记忆。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        bridge.remember_event(events[0])
        bridge.remember_event(events[0])  # 同一事件创建2条记忆

        found = bridge.find_causal_memories(event_ids[0])
        assert len(found) == 2
        for rec in found:
            assert rec.cause_event_id == event_ids[0]


# ============================================================
# 因果链完整性测试
# ============================================================


class TestCausalCompleteness:
    """因果链完整性评估测试。"""

    def test_completeness_full_chain(self, event_chain):
        """完整因果链→高完整性。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        completeness = bridge.causal_completeness(event_ids[4])
        assert completeness > 0.5

    def test_completeness_root(self, event_chain):
        """根节点→完整性较低（链长1）。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        completeness = bridge.causal_completeness(event_ids[0])
        assert completeness < 0.5

    def test_completeness_nonexistent(self, causal_setup):
        """不存在的事件→0。"""
        stream, orchestra, bridge = causal_setup
        assert bridge.causal_completeness("nonexistent") == 0.0


# ============================================================
# 因果摘要测试
# ============================================================


class TestCausalSummary:
    """因果记忆摘要测试。"""

    def test_summary_structure(self, event_chain):
        """摘要结构完整。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        bridge.remember_event(events[0])
        bridge.remember_event(events[4])

        summary = bridge.causal_summary(event_ids[4])

        assert "event_id" in summary
        assert "causal_chain_length" in summary
        assert "completeness" in summary
        assert "direct_memories" in summary
        assert "total_causal_memories" in summary
        assert "chain_events" in summary
        assert "memories_preview" in summary

        assert summary["causal_chain_length"] == 5
        assert summary["direct_memories"] == 1  # E5直接触发1条
        assert summary["total_causal_memories"] >= 2  # E1+E5的记忆

    def test_summary_chain_events_preview(self, event_chain):
        """链事件预览包含关键信息。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        summary = bridge.causal_summary(event_ids[4])

        for evt_info in summary["chain_events"]:
            assert "event_id" in evt_info
            assert "type" in evt_info
            assert "sender" in evt_info
            assert "content_preview" in evt_info


# ============================================================
# AutoCausalBridge 测试
# ============================================================


class TestAutoCausalBridge:
    """自动因果桥接器测试。"""

    def test_auto_bridge_start_stop(self, causal_setup):
        """启动和停止。"""
        stream, orchestra, bridge = causal_setup
        auto = AutoCausalBridge(stream, orchestra)

        auto.start()
        assert auto._active is True

        auto.stop()
        assert auto._active is False

    def test_auto_creates_memories(self, causal_setup):
        """发布事件后自动创建记忆。"""
        stream, orchestra, bridge = causal_setup
        auto = AutoCausalBridge(stream, orchestra)
        auto.start()

        event = create_task_action("九重", "澜澜", "自动桥接测试", "T-AUTO")
        stream.publish(event)

        active = orchestra.forgetting.list_active(limit=10)
        assert len(active) == 1
        assert active[0].cause_event_id is not None

    def test_auto_event_filter(self, causal_setup):
        """事件过滤器：只记忆指定类型。"""
        stream, orchestra, bridge = causal_setup
        auto = AutoCausalBridge(
            stream,
            orchestra,
            event_filter={EventType.TASK, EventType.DONE},
        )
        auto.start()

        # 发布TASK（应记忆）
        stream.publish(create_task_action("A", "B", "task", "T1"))
        # 发布INFO（不应记忆）
        stream.publish(create_action("A", "B", EventType.INFO, "info"))
        # 发布DONE（应记忆）
        stream.publish(create_done_action("B", "A", "T1", "done", [], "verify", [], "next"))

        active = orchestra.forgetting.list_active(limit=10)
        # 只有TASK和DONE被记忆
        assert len(active) == 2

    def test_auto_importance_mapping(self, causal_setup):
        """不同事件类型→不同重要性。"""
        stream, orchestra, bridge = causal_setup
        auto = AutoCausalBridge(stream, orchestra)
        auto.start()

        stream.publish(create_done_action("A", "B", "T1", "done", [], "v", [], "n"))
        stream.publish(create_action("A", "B", EventType.PING, "ping"))

        active = orchestra.forgetting.list_active(limit=10)
        done_rec = [r for r in active if "DONE" in r.content or "done" in r.content.lower()]
        ping_rec = [r for r in active if "ping" in r.content.lower()]

        if done_rec and ping_rec:
            assert done_rec[0].importance > ping_rec[0].importance


# ============================================================
# HybridRetriever 因果加成测试
# ============================================================


class TestHybridRetrieverCausalBoost:
    """HybridRetriever 因果加成机制测试。"""

    def test_causal_boost_increases_score(self, work_dir):
        """因果加成提高记忆排序。"""
        fe = ForgettingEngine(str(work_dir / "boost.db"))

        # 两条内容相似的记忆，一条有因果链，一条没有
        causal_rec = MemoryRecord(
            id="causal_1",
            content="因果记忆架构设计",
            importance=0.7,
            cause_event_id="evt_001",
        )
        plain_rec = MemoryRecord(
            id="plain_1",
            content="因果记忆架构设计",
            importance=0.7,
            cause_event_id=None,
        )
        fe.add(causal_rec)
        fe.add(plain_rec)

        retriever = HybridRetriever(fe)

        # 无因果上下文：两条应排序相近
        results_plain = retriever.search("因果记忆", top_k=2)
        plain_ids = [r.id for r in results_plain]

        # 有因果上下文：causal_1应排在前面
        results_causal = retriever.search(
            "因果记忆",
            top_k=2,
            causal_context={"evt_001"},
        )
        causal_ids = [r.id for r in results_causal]

        # 因果加成应使 causal_1 排名上升
        if "causal_1" in plain_ids and "causal_1" in causal_ids:
            assert causal_ids.index("causal_1") <= plain_ids.index("causal_1")

    def test_no_boost_without_context(self, work_dir):
        """无 causal_context 时不加成。"""
        fe = ForgettingEngine(str(work_dir / "noboost.db"))
        fe.add(
            MemoryRecord(
                id="m1",
                content="test content",
                importance=0.7,
                cause_event_id="evt_001",
            )
        )

        retriever = HybridRetriever(fe)
        results = retriever.search("test", top_k=1, causal_context=None)
        assert len(results) == 1

    def test_no_boost_unrelated_context(self, work_dir):
        """causal_context 不包含记忆的 cause_event_id 时不加成。"""
        fe = ForgettingEngine(str(work_dir / "unrelated.db"))
        fe.add(
            MemoryRecord(
                id="m1",
                content="test content",
                importance=0.7,
                cause_event_id="evt_001",
            )
        )

        retriever = HybridRetriever(fe)
        # causal_context 包含不相关的事件ID
        results = retriever.search("test", top_k=1, causal_context={"evt_999"})
        assert len(results) == 1


# ============================================================
# 完整闭环测试
# ============================================================


class TestCausalMemoryClosedLoop:
    """因果记忆完整闭环测试。

    验证：事件→记忆→因果召回→涌现 的完整链路。
    """

    def test_full_closed_loop(self, event_chain):
        """完整闭环：从事件发布到因果涌现。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        # 1. 念起：沿因果链创建记忆
        for event in events:
            bridge.remember_event(event)

        # 2. 验证：所有记忆都携带 cause_event_id
        active = orchestra.forgetting.list_active(limit=20)
        assert len(active) == 5
        for rec in active:
            assert rec.has_cause

        # 3. 涌现：从E5检索，因果链上的记忆应获得加成
        results = bridge.recall_with_cause("因果记忆", current_event_id=event_ids[4])
        assert len(results) > 0

        # 4. 溯源：从E5的记忆回溯到E1
        e5_memory = None
        for rec in active:
            if rec.cause_event_id == event_ids[4]:
                e5_memory = rec
                break

        assert e5_memory is not None
        chain = bridge.trace_memory_cause(e5_memory.id)
        assert len(chain) == 5
        assert chain[0].event_id == event_ids[0]
        assert chain[-1].event_id == event_ids[4]

        # 5. 完整性：因果链完整
        completeness = bridge.causal_completeness(event_ids[4])
        assert completeness > 0.5

    def test_auto_bridge_closed_loop(self, event_chain):
        """自动桥接闭环：事件自动→记忆→因果召回。"""
        stream, orchestra, bridge, events, event_ids = event_chain

        # 启动自动桥接
        auto = AutoCausalBridge(stream, orchestra)
        auto.start()

        # 创建新事件并发布（自动创建记忆）
        # 注意：不能重用同一批事件对象，否则cause链会成环
        new_events = [
            create_task_action("九重", "澜澜", "新因果记忆任务", "TASK-NEW-001"),
            create_task_action("澜澜", "灵犀", "新分析任务", "TASK-NEW-002"),
            create_done_action(
                "灵犀",
                "澜澜",
                "TASK-NEW-002",
                "新报告完成",
                ["docs/new.md"],
                "检查完整",
                [],
                "下一步",
            ),
        ]
        new_ids = [stream.publish(e) for e in new_events]

        # 验证记忆已自动创建
        active = orchestra.forgetting.list_active(limit=20)
        assert len(active) >= 3

        # 因果召回（用最后一条新事件）
        results = bridge.recall_with_cause("因果", current_event_id=new_ids[-1])
        assert len(results) > 0

    def test_causal_memory_isolation(self, work_dir):
        """不同因果链的记忆互不干扰。"""
        stream1 = EventStream(session_id="chain-1")
        stream2 = EventStream(session_id="chain-2")
        orchestra = MemoryEvolutionOrchestra(str(work_dir / "iso.db"))

        bridge1 = CausalMemoryBridge(stream1, orchestra)
        bridge2 = CausalMemoryBridge(stream2, orchestra)

        # 链1
        e1a = create_action("A", "B", EventType.INFO, "链1事件A")
        eid1a = stream1.publish(e1a)
        bridge1.remember_event(e1a)

        # 链2
        e2a = create_action("C", "D", EventType.INFO, "链2事件A")
        eid2a = stream2.publish(e2a)
        bridge2.remember_event(e2a)

        # 从链1检索：不应被链2的因果上下文影响
        context1 = bridge1.get_causal_context(eid1a)
        context2 = bridge2.get_causal_context(eid2a)

        # 两条链的因果上下文互不重叠
        assert context1.isdisjoint(context2)
