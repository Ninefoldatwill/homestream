"""
test_event_store.py — EventStore SQLite 持久化层单元测试

覆盖范围：
- 初始化 & 表结构
- 写入（单条 / 批量 / 幂等）
- 查询（session / agent / type / range / by_id）
- 因果链重建
- 启动回放（replay_session）
- PersistentEventStreamMixin
- 统计信息
"""

import os
import tempfile
import time
from datetime import datetime, timedelta

import pytest

from event_store import (
    DEFAULT_PAGE_SIZE,
    EventStore,
    PersistentEventStreamMixin,
    make_persistent_stream,
)
from event_stream import (
    Action,
    Event,
    EventSource,
    EventStream,
    EventType,
    Observation,
    _gen_event_id,
    create_action,
    create_done_action,
    create_observation,
    create_task_action,
)

# ==================== fixtures ====================


@pytest.fixture
def tmp_db():
    """提供临时 SQLite 文件路径，测试结束后自动清理"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def store(tmp_db):
    return EventStore(db_path=tmp_db)


@pytest.fixture
def stream(tmp_db):
    return make_persistent_stream("test", db_path=tmp_db, replay=False)


def _make_action(sender="澜舟", recipient="澜澜", etype=EventType.INFO, content="测试") -> Action:
    return create_action(sender=sender, recipient=recipient, event_type=etype, content=content)


def _make_obs(sender="System", recipient="澜舟", etype=EventType.ACK, content="ok") -> Observation:
    return create_observation(sender=sender, recipient=recipient, event_type=etype, content=content)


# ==================== 1. 初始化 ====================


class TestInit:
    def test_db_file_created(self, store, tmp_db):
        assert os.path.exists(tmp_db)

    def test_table_exists(self, store, tmp_db):
        import sqlite3

        conn = sqlite3.connect(tmp_db)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {t[0] for t in tables}
        assert "events" in names
        conn.close()

    def test_indexes_created(self, store, tmp_db):
        import sqlite3

        conn = sqlite3.connect(tmp_db)
        idxs = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        idx_names = {i[0] for i in idxs}
        assert "idx_events_session" in idx_names
        assert "idx_events_recipient" in idx_names
        assert "idx_events_sender" in idx_names
        conn.close()

    def test_idempotent_init(self, tmp_db):
        """重复初始化不报错（幂等）"""
        store1 = EventStore(tmp_db)
        store2 = EventStore(tmp_db)  # 第二次初始化
        assert store1.count() == 0
        assert store2.count() == 0


# ==================== 2. 写入 ====================


class TestWrite:
    def test_write_action(self, store):
        event = _make_action()
        result = store.write(event)
        assert result is True
        assert store.count() == 1

    def test_write_observation(self, store):
        obs = _make_obs()
        result = store.write(obs)
        assert result is True
        assert store.count() == 1

    def test_write_with_session(self, store):
        store.write(_make_action(), session_id="sess-A")
        store.write(_make_action(), session_id="sess-B")
        assert store.count("sess-A") == 1
        assert store.count("sess-B") == 1

    def test_write_idempotent(self, store):
        """同一 event_id 写两次，只保留一条"""
        event = _make_action()
        store.write(event)
        store.write(event)  # 重复写入
        assert store.count() == 1

    def test_write_with_handoff(self, store):
        event = create_done_action(
            sender="澜舟",
            recipient="澜澜",
            task_id="T001",
            what_done="完成测试",
            where_artifacts=["test.py"],
            how_verify="pytest",
            known_issues=[],
            what_next="上线",
        )
        result = store.write(event)
        assert result is True
        restored = store.get_event_by_id(event.event_id)
        assert restored is not None
        assert restored.handoff is not None
        assert restored.handoff["what_done"] == "完成测试"

    def test_write_with_confidence(self, store):
        event = _make_action(content="高置信度消息")
        event.confidence = 0.95
        store.write(event)
        restored = store.get_event_by_id(event.event_id)
        assert restored.confidence == pytest.approx(0.95, abs=0.001)

    def test_write_batch(self, store):
        for i in range(20):
            store.write(_make_action(content=f"消息{i}"))
        assert store.count() == 20


# ==================== 3. 查询 ====================


class TestQuery:
    def _fill(self, store, n=5, session="default"):
        ids = []
        for i in range(n):
            e = _make_action(content=f"msg-{i}")
            store.write(e, session_id=session)
            ids.append(e.event_id)
        return ids

    def test_query_by_session_count(self, store):
        self._fill(store, 5, "s1")
        self._fill(store, 3, "s2")
        assert len(store.query_by_session("s1")) == 5
        assert len(store.query_by_session("s2")) == 3

    def test_query_by_session_newest_first(self, store):
        self._fill(store, 5)
        results = store.query_by_session("default", newest_first=True)
        timestamps = [r.timestamp for r in results]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_query_by_session_oldest_first(self, store):
        self._fill(store, 5)
        results = store.query_by_session("default", newest_first=False)
        timestamps = [r.timestamp for r in results]
        assert timestamps == sorted(timestamps)

    def test_query_by_session_pagination(self, store):
        self._fill(store, 10)
        page1 = store.query_by_session("default", limit=4, offset=0)
        page2 = store.query_by_session("default", limit=4, offset=4)
        all_ids = {e.event_id for e in page1 + page2}
        assert len(all_ids) == 8  # 无重叠

    def test_query_by_agent_sender(self, store):
        store.write(create_action("澜舟", "澜澜", EventType.INFO, "a"))
        store.write(create_action("澜澜", "澜舟", EventType.INFO, "b"))
        results = store.query_by_agent("澜舟", as_sender=True, as_recipient=False)
        assert all(r.sender == "澜舟" for r in results)

    def test_query_by_agent_recipient(self, store):
        store.write(create_action("澜舟", "澜澜", EventType.INFO, "a"))
        store.write(create_action("灵犀", "澜澜", EventType.INFO, "b"))
        results = store.query_by_agent("澜澜", as_sender=False, as_recipient=True)
        assert len(results) == 2

    def test_query_by_agent_both(self, store):
        store.write(create_action("澜舟", "澜澜", EventType.INFO, "a"))
        store.write(create_action("澜澜", "澜舟", EventType.INFO, "b"))
        results = store.query_by_agent("澜舟")
        assert len(results) == 2

    def test_query_by_type(self, store):
        store.write(create_action("澜舟", "澜澜", EventType.INFO, "info"))
        store.write(create_action("澜舟", "澜澜", EventType.TASK, "task"))
        store.write(create_action("澜舟", "澜澜", EventType.DONE, "done"))
        assert len(store.query_by_type(EventType.INFO)) == 1
        assert len(store.query_by_type(EventType.TASK)) == 1
        assert len(store.query_by_type(EventType.DONE)) == 1

    def test_query_by_id(self, store):
        event = _make_action(content="唯一消息")
        store.write(event)
        restored = store.get_event_by_id(event.event_id)
        assert restored is not None
        assert restored.event_id == event.event_id
        assert restored.content == "唯一消息"

    def test_query_by_id_not_found(self, store):
        result = store.get_event_by_id("nonexistent-id")
        assert result is None

    def test_query_range(self, store):
        # 写 3 条，用时间范围过滤
        now = datetime.now()
        e1 = _make_action(content="early")
        store.write(e1)
        time.sleep(0.01)
        mid = datetime.now().isoformat()
        time.sleep(0.01)
        e2 = _make_action(content="late")
        store.write(e2)

        results = store.query_range("default", start=mid)
        ids = {r.event_id for r in results}
        # e2 在 mid 之后
        assert e2.event_id in ids


# ==================== 4. 因果链 ====================


class TestCauseChain:
    def test_chain_single(self, store):
        e = _make_action()
        store.write(e)
        chain = store.query_cause_chain(e.event_id)
        assert len(chain) == 1

    def test_chain_linear(self, store):
        """A → B → C 三节点链"""
        a = _make_action(content="A")
        store.write(a)

        b = _make_action(content="B")
        b.cause = a.event_id
        store.write(b)

        c = _make_action(content="C")
        c.cause = b.event_id
        store.write(c)

        chain = store.query_cause_chain(c.event_id)
        assert len(chain) == 3
        assert chain[0].event_id == a.event_id
        assert chain[2].event_id == c.event_id

    def test_chain_cycle_guard(self, store):
        """循环引用不应导致死循环"""
        e = _make_action()
        e.cause = e.event_id  # 自引用
        store.write(e)
        chain = store.query_cause_chain(e.event_id)
        assert len(chain) == 1  # 不崩溃

    def test_chain_broken(self, store):
        """父节点不在 DB 中时，链在此截断"""
        e = _make_action()
        e.cause = "ghost-event-id"  # 不存在的 cause
        store.write(e)
        chain = store.query_cause_chain(e.event_id)
        assert len(chain) == 1


# ==================== 4b. 因果链 CTE 优化（v5.1.0） ====================


class TestCauseChainCTE:
    """v5.1.0: WITH RECURSIVE CTE 因果链优化测试"""

    def _build_chain(self, store, n=10, session="default"):
        """构建 n 节点线性因果链，返回 [event_id_0, ..., event_id_n-1]"""
        ids = []
        prev_id = None
        for i in range(n):
            e = _make_action(content=f"node-{i}")
            if prev_id:
                e.cause = prev_id
            store.write(e, session_id=session)
            ids.append(e.event_id)
            prev_id = e.event_id
        return ids

    def test_cte_chain_single(self, store):
        """单节点因果链"""
        e = _make_action()
        store.write(e)
        chain = store.query_cause_chain(e.event_id)
        assert len(chain) == 1
        assert chain[0].event_id == e.event_id

    def test_cte_chain_linear_3(self, store):
        """A → B → C 三节点链，验证顺序（root → leaf）"""
        a = _make_action(content="A")
        store.write(a)
        b = _make_action(content="B")
        b.cause = a.event_id
        store.write(b)
        c = _make_action(content="C")
        c.cause = b.event_id
        store.write(c)

        chain = store.query_cause_chain(c.event_id)
        assert len(chain) == 3
        assert chain[0].content == "A"  # root first
        assert chain[1].content == "B"
        assert chain[2].content == "C"  # leaf last

    def test_cte_chain_deep_50(self, store):
        """50 节点深链 — CTE 性能验证"""
        ids = self._build_chain(store, n=50)
        chain = store.query_cause_chain(ids[-1])
        assert len(chain) == 50
        assert chain[0].content == "node-0"  # root
        assert chain[49].content == "node-49"  # leaf

    def test_cte_chain_cycle_self_ref(self, store):
        """自引用循环检测"""
        e = _make_action()
        e.cause = e.event_id
        store.write(e)
        chain = store.query_cause_chain(e.event_id)
        assert len(chain) == 1

    def test_cte_chain_cycle_mutual(self, store):
        """互引用循环检测：A → B → A"""
        a = _make_action(content="A")
        store.write(a)
        b = _make_action(content="B")
        b.cause = a.event_id
        store.write(b)
        # 手动更新 A 的 cause 指向 B（制造循环）
        conn = store._get_conn()
        conn.execute("UPDATE events SET cause = ? WHERE id = ?", (b.event_id, a.event_id))
        conn.commit()
        conn.close()

        chain = store.query_cause_chain(a.event_id)
        # 循环检测应截断，返回最多 2 个节点
        assert len(chain) <= 2

    def test_cte_chain_broken_parent(self, store):
        """父节点不存在时截断"""
        e = _make_action()
        e.cause = "nonexistent-parent"
        store.write(e)
        chain = store.query_cause_chain(e.event_id)
        assert len(chain) == 1

    def test_cte_chain_not_found(self, store):
        """查询不存在的事件"""
        chain = store.query_cause_chain("does-not-exist")
        assert len(chain) == 0

    def test_cte_max_depth(self, store):
        """max_depth 安全阀限制递归深度"""
        ids = self._build_chain(store, n=10)
        # max_depth=3 → 只返回最近 3 个节点
        chain = store.query_cause_chain(ids[-1], max_depth=3)
        assert len(chain) == 3

    def test_cte_max_depth_1(self, store):
        """max_depth=1 → 只返回目标事件自身"""
        ids = self._build_chain(store, n=5)
        chain = store.query_cause_chain(ids[-1], max_depth=1)
        assert len(chain) == 1

    def test_cte_chain_content_intact(self, store):
        """CTE 返回的事件内容完整"""
        ids = self._build_chain(store, n=5)
        chain = store.query_cause_chain(ids[-1])
        for i, event in enumerate(chain):
            assert event.content == f"node-{i}"
            assert event.event_id == ids[i]


class TestDescendants:
    """v5.1.0: query_descendants 正向遍历测试"""

    def test_descendants_linear(self, store):
        """A → B → C 线性链，从 A 查后代"""
        a = _make_action(content="A")
        store.write(a)
        b = _make_action(content="B")
        b.cause = a.event_id
        store.write(b)
        c = _make_action(content="C")
        c.cause = b.event_id
        store.write(c)

        desc = store.query_descendants(a.event_id)
        assert len(desc) == 3  # A, B, C
        assert desc[0].content == "A"  # root first
        assert desc[1].content == "B"
        assert desc[2].content == "C"

    def test_descendants_branching(self, store):
        """分支结构：A → B, A → C（同一根的两个子事件）"""
        a = _make_action(content="A")
        store.write(a)
        b = _make_action(content="B")
        b.cause = a.event_id
        store.write(b)
        c = _make_action(content="C")
        c.cause = a.event_id
        store.write(c)

        desc = store.query_descendants(a.event_id)
        assert len(desc) == 3  # A, B, C
        contents = {e.content for e in desc}
        assert contents == {"A", "B", "C"}

    def test_descendants_leaf_only(self, store):
        """叶子节点无后代"""
        a = _make_action(content="A")
        store.write(a)
        desc = store.query_descendants(a.event_id)
        assert len(desc) == 1  # only itself

    def test_descendants_not_found(self, store):
        """根节点不存在"""
        desc = store.query_descendants("nonexistent")
        assert len(desc) == 0

    def test_descendants_deep(self, store):
        """10 节点深链正向遍历"""
        ids = []
        prev_id = None
        for i in range(10):
            e = _make_action(content=f"desc-{i}")
            if prev_id:
                e.cause = prev_id
            store.write(e)
            ids.append(e.event_id)
            prev_id = e.event_id

        desc = store.query_descendants(ids[0])
        assert len(desc) == 10
        assert desc[0].content == "desc-0"
        assert desc[9].content == "desc-9"

    def test_descendants_cycle_guard(self, store):
        """循环引用安全处理"""
        a = _make_action(content="A")
        store.write(a)
        b = _make_action(content="B")
        b.cause = a.event_id
        store.write(b)
        # 制造循环
        conn = store._get_conn()
        conn.execute("UPDATE events SET cause = ? WHERE id = ?", (b.event_id, a.event_id))
        conn.commit()
        conn.close()

        desc = store.query_descendants(a.event_id)
        assert len(desc) <= 2  # 循环截断


class TestCauseDepth:
    """v5.1.0: get_cause_depth 快速深度查询测试"""

    def test_depth_single(self, store):
        """单节点深度=1"""
        e = _make_action()
        store.write(e)
        assert store.get_cause_depth(e.event_id) == 1

    def test_depth_linear_3(self, store):
        """A → B → C 深度=3"""
        a = _make_action(content="A")
        store.write(a)
        b = _make_action(content="B")
        b.cause = a.event_id
        store.write(b)
        c = _make_action(content="C")
        c.cause = b.event_id
        store.write(c)

        assert store.get_cause_depth(c.event_id) == 3
        assert store.get_cause_depth(b.event_id) == 2
        assert store.get_cause_depth(a.event_id) == 1

    def test_depth_not_found(self, store):
        """不存在的事件深度=0"""
        assert store.get_cause_depth("nonexistent") == 0

    def test_depth_broken_chain(self, store):
        """断裂的因果链深度=1"""
        e = _make_action()
        e.cause = "ghost"
        store.write(e)
        assert store.get_cause_depth(e.event_id) == 1

    def test_depth_max_depth_limit(self, store):
        """max_depth 限制返回深度"""
        ids = []
        prev_id = None
        for i in range(10):
            e = _make_action(content=f"d-{i}")
            if prev_id:
                e.cause = prev_id
            store.write(e)
            ids.append(e.event_id)
            prev_id = e.event_id

        # max_depth=5 → 深度被截断为 5
        assert store.get_cause_depth(ids[-1], max_depth=5) == 5

    def test_depth_cycle_guard(self, store):
        """循环引用不崩溃"""
        e = _make_action()
        e.cause = e.event_id
        store.write(e)
        assert store.get_cause_depth(e.event_id) == 1


# ==================== 5. 回放 ====================


class TestReplay:
    def test_replay_empty(self, store, tmp_db):
        """DB 空时回放不报错"""
        stream = EventStream("session-empty")
        count = store.replay_session(stream, "session-empty")
        assert count == 0
        assert stream.event_count == 0

    def test_replay_populates_stream(self, store, tmp_db):
        """写入 5 条后，新 stream 回放得到 5 条"""
        for _ in range(5):
            store.write(_make_action(), session_id="r1")

        stream = EventStream("r1")
        count = store.replay_session(stream, "r1")
        assert count == 5
        assert stream.event_count == 5

    def test_replay_idempotent(self, store, tmp_db):
        """多次回放同一 session，不重复"""
        for _ in range(3):
            store.write(_make_action(), session_id="r2")

        stream = EventStream("r2")
        store.replay_session(stream, "r2")
        store.replay_session(stream, "r2")  # 第二次回放
        assert stream.event_count == 3  # 仍是 3，不重复

    def test_replay_no_side_effects(self, store):
        """回放时不触发订阅者（避免副作用）"""
        received = []
        store.write(create_action("澜舟", "澜澜", EventType.TASK, "task"), "s3")

        stream = EventStream("s3")
        stream.subscribe("澜澜", lambda e: received.append(e))
        store.replay_session(stream, "s3")  # 回放注入，不走订阅

        assert len(received) == 0, "回放不应触发订阅者"


# ==================== 6. PersistentEventStreamMixin ====================


class TestPersistentMixin:
    def test_publish_auto_persists(self, tmp_db):
        stream = make_persistent_stream("pm1", db_path=tmp_db, replay=False)
        store = stream._store

        event = _make_action(content="持久化测试")
        stream.publish(event)

        assert store.count("pm1") == 1
        restored = store.get_event_by_id(event.event_id)
        assert restored is not None
        assert restored.content == "持久化测试"

    def test_publish_10_events(self, tmp_db):
        stream = make_persistent_stream("pm2", db_path=tmp_db, replay=False)
        for i in range(10):
            stream.publish(_make_action(content=f"event-{i}"))
        assert stream._store.count("pm2") == 10

    def test_from_db_replay(self, tmp_db):
        """先写入，再 from_db 恢复，内存事件数正确"""
        store = EventStore(tmp_db)
        for _ in range(7):
            store.write(_make_action(), session_id="pm3")

        stream = make_persistent_stream("pm3", db_path=tmp_db, replay=True)
        assert stream.event_count == 7

    def test_no_store_no_crash(self, tmp_db):
        """未绑定 store 时 publish 不崩溃"""
        stream = make_persistent_stream("pm4", db_path=tmp_db, replay=False)
        stream._store = None  # 手动摘除
        event = _make_action()
        eid = stream.publish(event)  # 不崩溃
        assert eid == event.event_id

    def test_subscribers_still_work(self, tmp_db):
        """持久化不影响订阅者触发"""
        stream = make_persistent_stream("pm5", db_path=tmp_db, replay=False)
        received = []
        stream.subscribe("澜澜", lambda e: received.append(e))

        event = _make_action(recipient="澜澜", content="订阅测试")
        stream.publish(event)

        assert len(received) == 1
        assert received[0].content == "订阅测试"


# ==================== 7. 统计 ====================


class TestStats:
    def test_stats_empty(self, store):
        s = store.stats()
        assert s["total_events"] == 0

    def test_stats_total(self, store):
        for i in range(5):
            store.write(_make_action(), session_id="stat1")
        s = store.stats("stat1")
        assert s["total_events"] == 5

    def test_stats_by_type(self, store):
        store.write(create_action("A", "B", EventType.INFO, "i"), "s")
        store.write(create_action("A", "B", EventType.TASK, "t"), "s")
        store.write(create_action("A", "B", EventType.TASK, "t2"), "s")
        s = store.stats("s")
        assert s["by_type"].get("INFO") == 1
        assert s["by_type"].get("TASK") == 2

    def test_stats_sessions(self, store):
        store.write(_make_action(), session_id="sa")
        store.write(_make_action(), session_id="sb")
        s = store.stats()
        assert "sa" in s["sessions"]
        assert "sb" in s["sessions"]

    def test_count_global(self, store):
        store.write(_make_action(), session_id="c1")
        store.write(_make_action(), session_id="c2")
        assert store.count() == 2

    def test_count_per_session(self, store):
        store.write(_make_action(), session_id="x")
        store.write(_make_action(), session_id="x")
        store.write(_make_action(), session_id="y")
        assert store.count("x") == 2
        assert store.count("y") == 1


# ==================== 入口 ====================

if __name__ == "__main__":
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.dirname(__file__) or ".",
    )
    sys.exit(result.returncode)
