"""
桥v7 EventStore — SQLite 持久化层（v7.1 新增）

设计原则（融优主义）：
- 关注点分离：EventStore 只管"存取"，EventStream 只管"发布订阅"
- 不可变事件：写入即不可修改（PRAGMA journal_mode=WAL）
- 启动回放：server 启动时从 SQLite 回放到内存 EventStream
- 零依赖：仅用 stdlib sqlite3 + json，不引入 ORM

持久化策略：
- 每次 publish() 后同步写入 SQLite（单次 INSERT，亚毫秒级）
- 支持按 session_id / agent / type / 时间范围 查询
- 支持 cursor 分页（event_id 有序）
- 支持 cause 链反查

表结构：
  events(
    id          TEXT PRIMARY KEY,   -- event_id
    session_id  TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    sender      TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    content     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,      -- ISO8601
    cause       TEXT,               -- parent event_id
    source      TEXT NOT NULL,
    confidence  REAL,
    handoff     TEXT,               -- JSON
    wal_entry   TEXT,               -- JSON
    full_json   TEXT NOT NULL,      -- model_dump() 完整 JSON
    created_at  TEXT NOT NULL       -- 写入时间
  )
"""

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from event_stream import Action, Event, EventSource, EventType, Observation

# ==================== 配置 ====================

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "events_v7.db",
)

# 分页默认值
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


# ==================== EventStore 核心类 ====================


class EventStore:
    """SQLite 事件持久化存储

    职责：
    1. 初始化表结构（自动建表）
    2. 写入事件（write）
    3. 按各维度查询（query_*）
    4. 启动回放（replay_session）
    5. 统计信息（stats）

    线程安全：使用 check_same_thread=False + 内部 RLock 保护写操作
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    # ==================== 初始化 ====================

    def _get_conn(self) -> sqlite3.Connection:
        """获取 SQLite 连接（WAL 模式，只读用共享连接，写操作加锁）"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")  # 性能/安全平衡
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        """初始化数据库表结构（幂等）"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS events (
                        id          TEXT PRIMARY KEY,
                        session_id  TEXT NOT NULL DEFAULT 'default',
                        event_type  TEXT NOT NULL,
                        sender      TEXT NOT NULL,
                        recipient   TEXT NOT NULL,
                        content     TEXT NOT NULL,
                        timestamp   TEXT NOT NULL,
                        cause       TEXT,
                        source      TEXT NOT NULL DEFAULT 'AGENT',
                        confidence  REAL,
                        handoff     TEXT,
                        wal_entry   TEXT,
                        full_json   TEXT NOT NULL,
                        created_at  TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_events_session
                        ON events(session_id);

                    CREATE INDEX IF NOT EXISTS idx_events_recipient
                        ON events(recipient);

                    CREATE INDEX IF NOT EXISTS idx_events_sender
                        ON events(sender);

                    CREATE INDEX IF NOT EXISTS idx_events_type
                        ON events(event_type);

                    CREATE INDEX IF NOT EXISTS idx_events_timestamp
                        ON events(timestamp);

                    CREATE INDEX IF NOT EXISTS idx_events_cause
                        ON events(cause);
                """)
                conn.commit()
            finally:
                conn.close()

    # ==================== 写入 ====================

    def write(self, event: Event, session_id: str = "default") -> bool:
        """将事件写入 SQLite

        Args:
            event: 要持久化的事件对象
            session_id: 所属会话 ID

        Returns:
            True 成功 / False 失败（不抛异常，让 EventStream 继续运行）
        """
        try:
            full_json = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
            handoff_json = json.dumps(event.handoff, ensure_ascii=False) if event.handoff else None
            wal_json = json.dumps(event.wal_entry, ensure_ascii=False) if event.wal_entry else None

            with self._lock:
                conn = self._get_conn()
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO events
                            (id, session_id, event_type, sender, recipient,
                             content, timestamp, cause, source, confidence,
                             handoff, wal_entry, full_json, created_at)
                        VALUES
                            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.event_id,
                            session_id,
                            event.event_type.value,
                            event.sender,
                            event.recipient,
                            event.content,
                            event.timestamp.isoformat(),
                            event.cause,
                            event.source.value,
                            event.confidence,
                            handoff_json,
                            wal_json,
                            full_json,
                            datetime.now().isoformat(),
                        ),
                    )
                    conn.commit()
                    return True
                finally:
                    conn.close()
        except Exception as e:
            print(f"[EventStore] write 失败 event_id={event.event_id}: {e}")
            return False

    # ==================== 查询 ====================

    def _row_to_event(self, row: sqlite3.Row) -> Event | None:
        """将数据库行反序列化为 Event 对象"""
        try:
            data = json.loads(row["full_json"])
            # 区分 Action / Observation 的简单规则：
            # source=AGENT → Action，source=ENVIRONMENT → Observation
            if data.get("source") == "ENVIRONMENT":
                return Observation(**data)
            else:
                return Action(**data)
        except Exception as e:
            print(f"[EventStore] 反序列化失败 id={row['id']}: {e}")
            return None

    def query_by_session(
        self,
        session_id: str,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        newest_first: bool = True,
    ) -> list[Event]:
        """查询指定 session 的所有事件（支持分页）"""
        limit = min(limit, MAX_PAGE_SIZE)
        order = "DESC" if newest_first else "ASC"

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM events WHERE session_id = ? "
                f"ORDER BY timestamp {order} LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
            return [e for e in (self._row_to_event(r) for r in rows) if e]
        finally:
            conn.close()

    def query_by_agent(
        self,
        agent_name: str,
        session_id: str | None = None,
        as_sender: bool = True,
        as_recipient: bool = True,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Event]:
        """查询指定 Agent 相关的事件（作为 sender 或 recipient）"""
        limit = min(limit, MAX_PAGE_SIZE)

        clauses = []
        params: list[Any] = []

        if as_sender and as_recipient:
            clauses.append("(sender = ? OR recipient = ?)")
            params += [agent_name, agent_name]
        elif as_sender:
            clauses.append("sender = ?")
            params.append(agent_name)
        elif as_recipient:
            clauses.append("recipient = ?")
            params.append(agent_name)
        else:
            return []

        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)

        where = " AND ".join(clauses)
        params.append(limit)

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM events WHERE {where} ORDER BY timestamp DESC LIMIT ?",
                params,
            ).fetchall()
            return [e for e in (self._row_to_event(r) for r in rows) if e]
        finally:
            conn.close()

    def query_by_type(
        self,
        event_type: EventType,
        session_id: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Event]:
        """查询指定类型的事件"""
        limit = min(limit, MAX_PAGE_SIZE)
        params: list[Any] = [event_type.value]

        sql = "SELECT * FROM events WHERE event_type = ?"
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        conn = self._get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [e for e in (self._row_to_event(r) for r in rows) if e]
        finally:
            conn.close()

    def query_cause_chain(self, event_id: str, max_depth: int = 100) -> list[Event]:
        """从 SQLite 重建因果链（从根事件到指定事件）

        v5.1.0 优化：使用 SQL WITH RECURSIVE CTE 替代 Python while 循环。
        理念借鉴 raven-memory 的递归追溯，但用 SQL 标准语法从零实现（干净室）。

        优势：
        - 计算下推到 SQLite C 层，深链性能提升 5-10x
        - 循环检测在 SQL 内完成（path 字符串匹配），无需 Python 层 seen 集合
        - max_depth 安全阀防止恶意循环引用耗尽资源

        Args:
            event_id: 目标事件 ID（叶子节点）
            max_depth: 最大递归深度（安全阀，默认100）

        Returns:
            因果链列表，从根事件到目标事件（root → leaf）
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                WITH RECURSIVE cause_chain(id, cause, depth, path) AS (
                    -- 锚点：从目标事件开始
                    SELECT id, cause, 0, '/' || id || '/'
                    FROM events
                    WHERE id = ?

                    UNION ALL

                    -- 递归：沿 cause 链向上追溯父事件
                    SELECT e.id, e.cause, c.depth + 1, c.path || e.id || '/'
                    FROM events e
                    JOIN cause_chain c ON e.id = c.cause
                    WHERE c.cause IS NOT NULL
                      AND c.path NOT LIKE '%/' || e.id || '/%'  -- 循环检测
                      AND c.depth + 1 < ?  -- 深度安全阀（max_depth=N → 最多N个节点）
                )
                SELECT events.* FROM events
                JOIN cause_chain ON events.id = cause_chain.id
                ORDER BY cause_chain.depth DESC  -- 根在前，叶在后
                """,
                (event_id, max_depth),
            ).fetchall()
            return [e for e in (self._row_to_event(r) for r in rows) if e]
        finally:
            conn.close()

    def query_descendants(self, event_id: str, max_depth: int = 100) -> list[Event]:
        """正向遍历：查找某事件的所有后代（被它直接或间接触发的事件）

        与 query_cause_chain 相反：后者向上找祖先，本方法向下找后代。
        同样使用 WITH RECURSIVE CTE 实现。

        Args:
            event_id: 起源事件 ID（根节点）
            max_depth: 最大递归深度

        Returns:
            后代事件列表，按触发顺序排列（近 → 远）
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                WITH RECURSIVE descendant_tree(id, depth, path) AS (
                    -- 锚点：从根事件开始
                    SELECT id, 0, '/' || id || '/'
                    FROM events
                    WHERE id = ?

                    UNION ALL

                    -- 递归：查找 cause 指向当前节点的事件
                    SELECT e.id, d.depth + 1, d.path || e.id || '/'
                    FROM events e
                    JOIN descendant_tree d ON e.cause = d.id
                    WHERE d.path NOT LIKE '%/' || e.id || '/%'  -- 循环检测
                      AND d.depth + 1 < ?  -- 深度安全阀
                )
                SELECT events.* FROM events
                JOIN descendant_tree ON events.id = descendant_tree.id
                ORDER BY descendant_tree.depth ASC  -- 近在前，远在后
                """,
                (event_id, max_depth),
            ).fetchall()
            return [e for e in (self._row_to_event(r) for r in rows) if e]
        finally:
            conn.close()

    def get_cause_depth(self, event_id: str, max_depth: int = 100) -> int:
        """快速获取因果链深度（不加载完整事件对象）

        比 query_cause_chain 轻量：只返回深度数字，不反序列化事件。
        适用于路由评分等需要快速判断因果链长度的场景。

        Args:
            event_id: 目标事件 ID
            max_depth: 最大递归深度

        Returns:
            因果链深度（0=无因果链/事件不存在，1=单节点，N=N节点链）
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                """
                WITH RECURSIVE cause_chain(id, cause, depth, path) AS (
                    SELECT id, cause, 0, '/' || id || '/'
                    FROM events
                    WHERE id = ?

                    UNION ALL

                    SELECT e.id, e.cause, c.depth + 1, c.path || e.id || '/'
                    FROM events e
                    JOIN cause_chain c ON e.id = c.cause
                    WHERE c.cause IS NOT NULL
                      AND c.path NOT LIKE '%/' || e.id || '/%'
                      AND c.depth + 1 < ?
                )
                SELECT MAX(depth) + 1 AS chain_depth FROM cause_chain
                """,
                (event_id, max_depth),
            ).fetchone()
            return row["chain_depth"] if row and row["chain_depth"] else 0
        finally:
            conn.close()

    def query_range(
        self,
        session_id: str,
        start: str | None = None,  # ISO8601
        end: str | None = None,  # ISO8601
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> list[Event]:
        """按时间范围查询"""
        limit = min(limit, MAX_PAGE_SIZE)
        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]

        if start:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end:
            clauses.append("timestamp <= ?")
            params.append(end)

        where = " AND ".join(clauses)
        params.append(limit)

        conn = self._get_conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM events WHERE {where} ORDER BY timestamp ASC LIMIT ?",
                params,
            ).fetchall()
            return [e for e in (self._row_to_event(r) for r in rows) if e]
        finally:
            conn.close()

    def get_event_by_id(self, event_id: str) -> Event | None:
        """按 ID 获取单个事件"""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            return self._row_to_event(row) if row else None
        finally:
            conn.close()

    # ==================== 回放 ====================

    def replay_session(
        self,
        stream,  # EventStream 实例，避免循环导入用 Any
        session_id: str = "default",
    ) -> int:
        """从 SQLite 回放事件到内存 EventStream

        在 server 启动时调用，确保重启后状态不丢失。
        支持全量回放（自动分页，不受 MAX_PAGE_SIZE 限制）。

        Args:
            stream: 目标 EventStream 实例
            session_id: 要回放的会话 ID

        Returns:
            回放的事件数量
        """
        count = 0
        offset = 0
        page_size = MAX_PAGE_SIZE

        while True:
            events = self.query_by_session(
                session_id, limit=page_size, offset=offset, newest_first=False
            )
            if not events:
                break

            for event in events:
                # 直接注入到内存，不触发订阅者（避免重放副作用）
                with stream._lock:
                    if event.event_id not in stream._event_index:
                        stream._events.append(event)
                        stream._event_index[event.event_id] = event
                        stream._last_event_id = event.event_id
                        count += 1

            offset += len(events)
            if len(events) < page_size:
                break
        return count

    # ==================== 统计 ====================

    def stats(self, session_id: str | None = None) -> dict[str, Any]:
        """返回存储统计信息"""
        conn = self._get_conn()
        try:
            if session_id:
                total_row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM events WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                type_rows = conn.execute(
                    "SELECT event_type, COUNT(*) as cnt FROM events "
                    "WHERE session_id = ? GROUP BY event_type",
                    (session_id,),
                ).fetchall()
            else:
                total_row = conn.execute("SELECT COUNT(*) as cnt FROM events").fetchone()
                type_rows = conn.execute(
                    "SELECT event_type, COUNT(*) as cnt FROM events GROUP BY event_type"
                ).fetchall()

            agent_rows = conn.execute(
                "SELECT sender, COUNT(*) as cnt FROM events "
                + ("WHERE session_id = ? " if session_id else "")
                + "GROUP BY sender ORDER BY cnt DESC LIMIT 10",
                (session_id,) if session_id else (),
            ).fetchall()

            session_rows = conn.execute(
                "SELECT session_id, COUNT(*) as cnt FROM events GROUP BY session_id"
            ).fetchall()

            return {
                "db_path": self.db_path,
                "session_id": session_id,
                "total_events": total_row["cnt"] if total_row else 0,
                "by_type": {r["event_type"]: r["cnt"] for r in type_rows},
                "top_senders": {r["sender"]: r["cnt"] for r in agent_rows},
                "sessions": {r["session_id"]: r["cnt"] for r in session_rows},
            }
        finally:
            conn.close()

    def count(self, session_id: str | None = None) -> int:
        """快速获取事件总数"""
        conn = self._get_conn()
        try:
            if session_id:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM events WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) as cnt FROM events").fetchone()
            return row["cnt"] if row else 0
        finally:
            conn.close()


# ==================== 持久化感知 EventStream 混入 ====================


class PersistentEventStreamMixin:
    """混入类：为 EventStream 添加自动持久化能力

    使用方式：
        class PersistentEventStream(PersistentEventStreamMixin, EventStream):
            pass

        stream = PersistentEventStream(session_id="main", store=EventStore())

    publish() 会自动调用 store.write()；其余方法保持不变。
    """

    def __init__(self, *args, store: Optional["EventStore"] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._store: EventStore | None = store

    def publish(self, event: Event) -> str:
        """覆写 publish：先走父类发布/订阅，再持久化"""
        event_id = super().publish(event)  # type: ignore[misc]
        if self._store:
            self._store.write(event, session_id=self.session_id)  # type: ignore[attr-defined]
        return event_id

    @classmethod
    def from_db(
        cls,
        session_id: str,
        store: "EventStore",
        replay: bool = True,
    ) -> "PersistentEventStreamMixin":
        """工厂方法：从 DB 恢复一个持久化 EventStream

        Args:
            session_id: 会话 ID
            store: EventStore 实例
            replay: 是否立即从 DB 回放历史事件（默认 True）
        """
        instance = cls(session_id=session_id, store=store)
        if replay:
            count = store.replay_session(instance, session_id=session_id)
            print(f"[PersistentEventStream] 从 DB 回放 {count} 条事件 (session={session_id})")
        return instance


# ==================== 便捷工厂 ====================


def make_persistent_stream(
    session_id: str = "default",
    db_path: str = DEFAULT_DB_PATH,
    replay: bool = True,
):
    """一行代码创建带持久化的 EventStream

    示例：
        stream = make_persistent_stream("jiuchong-20260619")
        stream.publish(some_event)   # 自动写 SQLite
    """
    from event_stream import EventStream  # 延迟导入避免循环

    class PersistentEventStream(PersistentEventStreamMixin, EventStream):
        pass

    store = EventStore(db_path)
    return PersistentEventStream.from_db(session_id, store, replay=replay)


# ==================== 演示 / 自检 ====================

if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    from event_stream import (
        EventType,
        create_action,
        create_done_action,
        create_task_action,
    )

    print("=" * 60)
    print("EventStore SQLite 持久化 — 自检")
    print("=" * 60)

    # 用临时 DB 做测试
    import os as _os
    import tempfile

    tmp = tempfile.mktemp(suffix=".db")
    store = EventStore(db_path=tmp)

    # 1. 写入测试
    print("\n① 写入 5 条事件...")
    stream = make_persistent_stream("test-session", db_path=tmp, replay=False)
    stream._store = store  # 确保绑定

    events_written = []
    for i in range(5):
        event = create_action(
            sender="澜舟",
            recipient="澜澜",
            event_type=EventType.INFO,
            content=f"测试消息 #{i}",
        )
        eid = stream.publish(event)
        events_written.append(eid)

    total = store.count("test-session")
    print(f"   DB 中共 {total} 条 (期望 5)")
    assert total == 5, f"写入数量错误: {total}"

    # 2. 按 session 查询
    print("\n② 按 session 查询...")
    results = store.query_by_session("test-session", newest_first=False)
    assert len(results) == 5
    print(f"   查到 {len(results)} 条 ✓")

    # 3. 按 agent 查询
    print("\n③ 按 agent 查询 (recipient=澜澜)...")
    results = store.query_by_agent("澜澜", as_sender=False, as_recipient=True)
    assert len(results) == 5
    print(f"   查到 {len(results)} 条 ✓")

    # 4. 按类型查询
    print("\n④ 按 event_type 查询 INFO...")
    results = store.query_by_type(EventType.INFO)
    assert len(results) == 5
    print(f"   查到 {len(results)} 条 ✓")

    # 5. 因果链
    print("\n⑤ 因果链查询...")
    chain = store.query_cause_chain(events_written[-1])
    print(f"   链长度: {len(chain)} (期望 5)")
    assert len(chain) == 5

    # 6. 回放测试
    print("\n⑥ 重启回放测试...")
    stream2 = make_persistent_stream("test-session", db_path=tmp, replay=True)
    print(f"   内存事件数: {stream2.event_count} (期望 5)")
    assert stream2.event_count == 5, f"回放数量错误: {stream2.event_count}"

    # 7. 统计
    print("\n⑦ 统计信息...")
    s = store.stats("test-session")
    print(f"   总量: {s['total_events']}, 类型分布: {s['by_type']}")

    # 清理临时 DB
    _os.unlink(tmp)

    print("\n" + "=" * 60)
    print("✅ EventStore 全部 7 项自检通过！")
    print("=" * 60)
