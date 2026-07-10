"""
桥v7 统一通讯模块 - 九重工作室全员群聊+会议室合并版

设计理念：
  一套系统，两个入口（/meeting 主入口，/group 自动跳转）
  九重发一条消息 → 全员（澜澜/澜舟/灵犀/千寻）同时收到

核心功能：
  1. 群消息存储（SQLite group_messages 表，含频道字段）
  2. WebSocket 实时广播（按频道推送）
  3. EventStream 事件分发（各Agent收件箱兼容）
  4. 会议通知（特殊卡片格式）
  5. @提及（定向通知特定成员）
  6. 频道系统（#general/#tech/#creative/#admin）
  7. ICP事件标签（INFO/TASK/WARN/DONE/ASK/UPD）
  8. 消息历史查询（分页加载+频道过滤）

日期: 2026-06-23（初版）→ 2026-06-23（合并优化）
作者: 澜舟
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger("group_chat")


# ==================== 群成员定义 ====================

GROUP_MEMBERS: dict[str, dict[str, str]] = {
    "九重": {"role": "总规划", "avatar": "JIU", "color": "#DAA520"},
    "澜澜": {"role": "总调度", "avatar": "LAN", "color": "#4A90D9"},
    "灵犀": {"role": "信息咨询", "avatar": "LIN", "color": "#9B59B6"},
    "澜舟": {"role": "开发工程", "avatar": "ZHOU", "color": "#2ECC71"},
    "千寻": {"role": "书记归档", "avatar": "QIAN", "color": "#E67E22"},
}

ALL_MEMBER_NAMES = list(GROUP_MEMBERS.keys())

# 频道定义（与 bridge_v7_server CHANNELS 保持一致）
CHANNELS: dict[str, dict[str, Any]] = {
    "#general": {"name": "综合大厅", "members": ALL_MEMBER_NAMES, "assignee_default": None},
    "#tech": {"name": "技术研发", "members": ["澜舟", "灵犀"], "assignee_default": "澜舟"},
    "#creative": {"name": "创意工坊", "members": ["千寻", "澜澜"], "assignee_default": "千寻"},
    "#admin": {"name": "行政管理", "members": ["澜澜", "九重"], "assignee_default": "澜澜"},
}


# ==================== 数据模型 ====================


class GroupMessage:
    """群聊消息模型（统一版）"""

    def __init__(
        self,
        msg_id: str,
        sender: str,
        content: str,
        msg_type: str = "text",
        channel: str = "#general",
        mentions: list[str] | None = None,
        meeting_data: dict[str, Any] | None = None,
        event_tag: str = "",
        timestamp: str | None = None,
    ):
        self.msg_id = msg_id
        self.sender = sender
        self.content = content
        self.msg_type = msg_type  # text / meeting / system
        self.channel = channel or "#general"
        self.mentions = mentions or []
        self.meeting_data = meeting_data
        self.event_tag = event_tag  # ICP标签: INFO/TASK/WARN/DONE/ASK/UPD
        self.timestamp = timestamp or datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "sender": self.sender,
            "sender_role": GROUP_MEMBERS.get(self.sender, {}).get("role", ""),
            "sender_avatar": GROUP_MEMBERS.get(self.sender, {}).get("avatar", ""),
            "sender_color": GROUP_MEMBERS.get(self.sender, {}).get("color", "#888"),
            "content": self.content,
            "msg_type": self.msg_type,
            "channel": self.channel,
            "mentions": self.mentions,
            "meeting_data": self.meeting_data,
            "event_tag": self.event_tag,
            "timestamp": self.timestamp,
        }


# ==================== 群聊管理器 ====================


class GroupChatManager:
    """
    统一通讯管理器（合并群聊+会议室）

    职责：
    1. SQLite 持久化（group_messages 表，含频道+ICP标签）
    2. 消息广播（WebSocket + EventStream 双通道）
    3. 会议通知
    4. 历史查询（支持频道过滤）
    5. 频道路由（消息按频道分发）
    """

    def __init__(self, db_path: str, ws_manager: Any = None, event_stream_fn: Any = None):
        self.db_path = db_path
        self.ws_manager = ws_manager
        self.event_stream_fn = event_stream_fn
        self._init_db()

    def _init_db(self):
        """初始化数据库表（含迁移：旧表加 channel/event_tag 列）"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_messages (
                msg_id      TEXT PRIMARY KEY,
                sender      TEXT NOT NULL,
                content     TEXT NOT NULL,
                msg_type    TEXT DEFAULT 'text',
                mentions    TEXT DEFAULT '[]',
                meeting_data TEXT,
                timestamp   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_group_msg_time
            ON group_messages(timestamp DESC)
        """)

        # 迁移：添加 channel 列（如果不存在）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(group_messages)").fetchall()]
        if "channel" not in cols:
            conn.execute("ALTER TABLE group_messages ADD COLUMN channel TEXT DEFAULT '#general'")
            logger.info("[群聊] 数据库迁移: 添加 channel 列")
        if "event_tag" not in cols:
            conn.execute("ALTER TABLE group_messages ADD COLUMN event_tag TEXT DEFAULT ''")
            logger.info("[群聊] 数据库迁移: 添加 event_tag 列")

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_group_msg_channel
            ON group_messages(channel, timestamp DESC)
        """)
        conn.commit()
        conn.close()
        logger.info(f"[群聊] 数据库已初始化: {self.db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------- 消息发送 ----------

    def send_message(
        self,
        sender: str,
        content: str,
        msg_type: str = "text",
        channel: str = "#general",
        mentions: list[str] | None = None,
        meeting_data: dict[str, Any] | None = None,
        event_tag: str = "",
    ) -> GroupMessage:
        """
        发送群聊消息（核心方法）

        流程：
        1. 创建 GroupMessage
        2. 存入 SQLite
        3. WebSocket 广播（按频道推送）
        4. EventStream 分发（各Agent收件箱）
        """
        msg = GroupMessage(
            msg_id=f"gm_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}",
            sender=sender,
            content=content,
            msg_type=msg_type,
            channel=channel,
            mentions=mentions,
            meeting_data=meeting_data,
            event_tag=event_tag,
        )

        # 1. 持久化
        self._persist(msg)

        # 2. WebSocket 广播
        self._broadcast_ws(msg)

        # 3. EventStream 分发
        self._dispatch_to_eventstream(msg)

        logger.info(
            f"[群聊] {sender} -> {channel}: type={msg_type}, "
            f"tag={event_tag}, mentions={mentions}, len={len(content)}"
        )
        return msg

    def _persist(self, msg: GroupMessage):
        """存入 SQLite"""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO group_messages
               (msg_id, sender, content, msg_type, channel, mentions, meeting_data, event_tag, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg.msg_id,
                msg.sender,
                msg.content,
                msg.msg_type,
                msg.channel,
                json.dumps(msg.mentions, ensure_ascii=False),
                json.dumps(msg.meeting_data, ensure_ascii=False) if msg.meeting_data else None,
                msg.event_tag,
                msg.timestamp,
            ),
        )
        conn.commit()
        conn.close()

    def _broadcast_ws(self, msg: GroupMessage):
        """WebSocket 广播给频道内所有连接的客户端"""
        if not self.ws_manager:
            return
        ws_msg = {
            "type": "group_message",
            **msg.to_dict(),
        }
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.ws_manager.broadcast(msg.channel, ws_msg))
            else:
                loop.run_until_complete(self.ws_manager.broadcast(msg.channel, ws_msg))
        except Exception as e:
            logger.warning(f"[群聊] WebSocket广播失败: {e}")

    def _dispatch_to_eventstream(self, msg: GroupMessage):
        """分发到 EventStream（各Agent收件箱兼容）

        根据频道确定接收人：
        - #general → 全员
        - #tech/#creative/#admin → 频道成员
        """
        if not self.event_stream_fn:
            return
        try:
            from event_stream import Action, EventSource, EventType, _gen_event_id

            stream = self.event_stream_fn()

            # 确定接收人列表
            channel_info = CHANNELS.get(msg.channel, {})
            recipients = channel_info.get("members", ALL_MEMBER_NAMES)

            # ICP标签 → EventType 映射
            tag_to_etype = {
                "TASK": EventType.TASK,
                "WARN": EventType.WARN,
                "ASK": EventType.ASK,
                "DONE": EventType.DONE,
                "UPD": EventType.UPD,
            }
            etype = tag_to_etype.get(msg.event_tag, EventType.INFO)

            prefix = "[群聊]" if msg.msg_type == "text" else "[会议通知]"
            if msg.channel != "#general":
                prefix = f"[{msg.channel}]"

            for member in recipients:
                if member == msg.sender:
                    continue

                event = Action(
                    event_id=_gen_event_id("grp"),
                    event_type=etype,
                    sender=msg.sender,
                    recipient=member,
                    content=f"{prefix} {msg.content}",
                    source=EventSource.AGENT,
                )
                stream.publish(event)

        except Exception as e:
            logger.warning(f"[群聊] EventStream分发失败: {e}")

    # ---------- 会议通知 ----------

    def send_meeting_notify(
        self,
        sender: str,
        title: str,
        meeting_time: str,
        agenda: list[str],
        attendees: list[str] | None = None,
        location: str = "线上会议室",
        channel: str = "#general",
    ) -> GroupMessage:
        """发送会议通知（特殊卡片格式）"""
        if attendees is None:
            attendees = ALL_MEMBER_NAMES

        meeting_data = {
            "title": title,
            "time": meeting_time,
            "agenda": agenda,
            "attendees": attendees,
            "location": location,
        }

        content = (
            f"会议通知: {title}\n时间: {meeting_time}\n地点: {location}\n议程: {'; '.join(agenda)}"
        )

        return self.send_message(
            sender=sender,
            content=content,
            msg_type="meeting",
            channel=channel,
            mentions=attendees,
            meeting_data=meeting_data,
            event_tag="TASK",
        )

    # ---------- 历史查询 ----------

    def get_messages(
        self,
        limit: int = 50,
        before: str | None = None,
        channel: str | None = None,
    ) -> list[dict]:
        """查询群聊历史消息（分页，最新在前，支持频道过滤）"""
        conn = self._get_conn()

        if channel and channel != "#all":
            if before:
                rows = conn.execute(
                    """SELECT * FROM group_messages
                       WHERE timestamp < ? AND channel = ?
                       ORDER BY timestamp DESC
                       LIMIT ?""",
                    (before, channel, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM group_messages
                       WHERE channel = ?
                       ORDER BY timestamp DESC
                       LIMIT ?""",
                    (channel, limit),
                ).fetchall()
        else:
            if before:
                rows = conn.execute(
                    """SELECT * FROM group_messages
                       WHERE timestamp < ?
                       ORDER BY timestamp DESC
                       LIMIT ?""",
                    (before, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM group_messages
                       ORDER BY timestamp DESC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()

        conn.close()

        messages = []
        for row in rows:
            msg = GroupMessage(
                msg_id=row["msg_id"],
                sender=row["sender"],
                content=row["content"],
                msg_type=row["msg_type"],
                channel=row["channel"] if "channel" in row.keys() else "#general",
                mentions=json.loads(row["mentions"]) if row["mentions"] else [],
                meeting_data=json.loads(row["meeting_data"]) if row["meeting_data"] else None,
                event_tag=row["event_tag"]
                if "event_tag" in row.keys() and row["event_tag"]
                else "",
                timestamp=row["timestamp"],
            )
            messages.append(msg.to_dict())

        return messages

    def get_stats(self) -> dict:
        """群聊统计"""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM group_messages").fetchone()[0]
        by_type = {}
        for row in conn.execute(
            "SELECT msg_type, COUNT(*) as cnt FROM group_messages GROUP BY msg_type"
        ).fetchall():
            by_type[row["msg_type"]] = row["cnt"]

        by_channel = {}
        try:
            for row in conn.execute(
                "SELECT channel, COUNT(*) as cnt FROM group_messages GROUP BY channel"
            ).fetchall():
                by_channel[row["channel"] or "#general"] = row["cnt"]
        except Exception:
            pass

        last_msg = conn.execute(
            "SELECT sender, timestamp FROM group_messages ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        conn.close()

        return {
            "total_messages": total,
            "by_type": by_type,
            "by_channel": by_channel,
            "last_message": {
                "sender": last_msg["sender"] if last_msg else None,
                "timestamp": last_msg["timestamp"] if last_msg else None,
            }
            if last_msg
            else None,
            "members": ALL_MEMBER_NAMES,
            "channels": list(CHANNELS.keys()),
        }

    def get_online_members(self) -> list[dict]:
        """获取成员在线状态（依赖WebSocket连接管理器）"""
        result = []
        for name, info in GROUP_MEMBERS.items():
            is_online = False
            if self.ws_manager:
                is_online = self.ws_manager.is_connected(name)
            result.append(
                {
                    "name": name,
                    "role": info["role"],
                    "avatar": info["avatar"],
                    "color": info["color"],
                    "online": is_online,
                }
            )
        return result

    def get_channels(self) -> list[dict]:
        """获取频道列表"""
        return [{"id": k, "name": v["name"], "members": v["members"]} for k, v in CHANNELS.items()]
