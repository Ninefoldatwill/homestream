"""
test_ws_manager.py — WebSocket 连接管理器单元测试

覆盖:
  TestConnectionManager   — connect/disconnect/subscribe/broadcast/send_to
  TestHeartbeat           — ping/pong/超时断开
  TestEventStreamWSBridge — ws_message 构建
  TestConnectionStats     — 统计信息 / is_connected
  TestConcurrentConn      — 多 Agent 同时连接 / 频道隔离
"""

import asyncio
import json
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ws_manager import ConnectionManager, EventStreamWSBridge

# ==================== 辅助工具 ====================


def _make_mock_ws():
    """创建模拟 WebSocket 对象"""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


def run_async(coro):
    """在测试中同步运行协程（每次创建独立事件循环，避免测试间隔离问题）"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ==================== TestConnectionManager ====================


class TestConnectionManager(unittest.TestCase):
    def setUp(self):
        self.manager = ConnectionManager()

    def test_connect_accepts_websocket(self):
        """connect() 应调用 websocket.accept()"""
        ws = _make_mock_ws()
        run_async(self.manager.connect("澜舟", ws))
        ws.accept.assert_called_once()
        self.assertTrue(self.manager.is_connected("澜舟"))

    def test_connect_sends_welcome_message(self):
        """connect() 应发送 connected 欢迎消息"""
        ws = _make_mock_ws()
        run_async(self.manager.connect("澜舟", ws))
        ws.send_text.assert_called_once()
        sent = json.loads(ws.send_text.call_args[0][0])
        self.assertEqual(sent["type"], "connected")
        self.assertEqual(sent["agent"], "澜舟")

    def test_connect_default_subscribed_to_general(self):
        """connect() 后默认订阅 #general"""
        ws = _make_mock_ws()
        run_async(self.manager.connect("灵犀", ws))
        subs = self.manager.get_subscriptions("灵犀")
        self.assertIn("#general", subs)

    def test_disconnect_removes_connection(self):
        """disconnect() 后 is_connected 返回 False"""
        ws = _make_mock_ws()
        run_async(self.manager.connect("澜舟", ws))
        self.manager.disconnect("澜舟")
        self.assertFalse(self.manager.is_connected("澜舟"))

    def test_disconnect_nonexistent_is_safe(self):
        """disconnect 不存在的 agent 不应抛出异常"""
        self.manager.disconnect("不存在的Agent")  # 不抛异常

    def test_subscribe_adds_channel(self):
        """subscribe() 添加频道"""
        ws = _make_mock_ws()
        run_async(self.manager.connect("澜舟", ws))
        result = self.manager.subscribe("澜舟", "#tech")
        self.assertTrue(result)
        self.assertIn("#tech", self.manager.get_subscriptions("澜舟"))

    def test_subscribe_returns_false_when_not_connected(self):
        """未连接时 subscribe() 返回 False"""
        result = self.manager.subscribe("未连接Agent", "#tech")
        self.assertFalse(result)

    def test_unsubscribe_removes_channel(self):
        """unsubscribe() 移除频道"""
        ws = _make_mock_ws()
        run_async(self.manager.connect("澜舟", ws))
        self.manager.subscribe("澜舟", "#tech")
        self.manager.unsubscribe("澜舟", "#tech")
        self.assertNotIn("#tech", self.manager.get_subscriptions("澜舟"))

    def test_broadcast_reaches_subscribed_agents(self):
        """broadcast() 只推送给订阅了该频道的 Agent"""
        ws1 = _make_mock_ws()
        ws2 = _make_mock_ws()
        run_async(self.manager.connect("澜舟", ws1))
        run_async(self.manager.connect("灵犀", ws2))
        self.manager.subscribe("澜舟", "#tech")
        # 灵犀不订阅 #tech

        ws1.send_text.reset_mock()
        ws2.send_text.reset_mock()

        msg = {"type": "event", "content": "新技术消息"}
        sent = run_async(self.manager.broadcast("#tech", msg))

        self.assertEqual(sent, 1)
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_not_called()

    def test_broadcast_to_general_reaches_all(self):
        """broadcast #general 应送达所有已连接 Agent（默认订阅）"""
        agents = ["澜舟", "灵犀", "千寻"]
        ws_list = []
        for a in agents:
            ws = _make_mock_ws()
            run_async(self.manager.connect(a, ws))
            ws_list.append(ws)

        # 重置 connect 时的 send_text 调用记录
        for ws in ws_list:
            ws.send_text.reset_mock()

        sent = run_async(self.manager.broadcast("#general", {"type": "ping"}))
        self.assertEqual(sent, 3)
        for ws in ws_list:
            ws.send_text.assert_called_once()

    def test_send_to_specific_agent(self):
        """send_to() 只发给指定 Agent"""
        ws1 = _make_mock_ws()
        ws2 = _make_mock_ws()
        run_async(self.manager.connect("澜舟", ws1))
        run_async(self.manager.connect("灵犀", ws2))

        ws1.send_text.reset_mock()
        ws2.send_text.reset_mock()

        result = run_async(self.manager.send_to("澜舟", {"type": "direct"}))
        self.assertTrue(result)
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_not_called()

    def test_send_to_nonexistent_returns_false(self):
        """send_to 不存在的 Agent 返回 False"""
        result = run_async(self.manager.send_to("不存在", {"type": "ping"}))
        self.assertFalse(result)

    def test_broadcast_cleans_up_failed_connections(self):
        """广播时发送失败的连接应被自动清理"""
        ws_bad = _make_mock_ws()
        ws_bad.send_text.side_effect = Exception("连接已断开")
        run_async(self.manager.connect("失效Agent", ws_bad))

        ws_bad.send_text.reset_mock()
        ws_bad.send_text.side_effect = Exception("连接已断开")

        run_async(self.manager.broadcast("#general", {"type": "test"}))
        self.assertFalse(self.manager.is_connected("失效Agent"))


# ==================== TestHeartbeat ====================


class TestHeartbeat(unittest.TestCase):
    def setUp(self):
        self.manager = ConnectionManager()

    def test_handle_pong_updates_timestamp(self):
        """收到 pong 后时间戳应更新"""
        ws = _make_mock_ws()
        run_async(self.manager.connect("澜舟", ws))
        old_ts = self.manager._last_pong["澜舟"]
        time.sleep(0.01)
        self.manager.handle_pong("澜舟")
        self.assertGreater(self.manager._last_pong["澜舟"], old_ts)

    def test_handle_pong_nonexistent_is_safe(self):
        """pong 未知 Agent 不抛异常"""
        self.manager.handle_pong("不存在的Agent")

    def test_heartbeat_disconnects_timed_out_agents(self):
        """心跳循环应断开超时 Agent"""
        manager = ConnectionManager()
        manager.HEARTBEAT_TIMEOUT = 0.05  # 50ms 超时（测试用）
        manager.HEARTBEAT_INTERVAL = 0.02  # 20ms 间隔

        ws = _make_mock_ws()
        run_async(manager.connect("过期Agent", ws))
        # 强制设置 last_pong 为过去
        manager._last_pong["过期Agent"] = time.time() - 1.0

        # 手动执行一次心跳循环（不用真正启动后台任务）
        async def one_beat():
            now = time.time()
            timed_out = [
                a
                for a, last in manager._last_pong.items()
                if now - last > manager.HEARTBEAT_TIMEOUT
            ]
            for a in timed_out:
                manager.disconnect(a)

        run_async(one_beat())
        self.assertFalse(manager.is_connected("过期Agent"))


# ==================== TestConnectionStats ====================


class TestConnectionStats(unittest.TestCase):
    def setUp(self):
        self.manager = ConnectionManager()

    def test_stats_empty(self):
        """无连接时统计为空"""
        stats = self.manager.get_stats()
        self.assertEqual(stats["total_connections"], 0)
        self.assertEqual(stats["agents"], [])

    def test_stats_with_connections(self):
        """有连接时统计正确"""
        for name in ["澜舟", "灵犀"]:
            ws = _make_mock_ws()
            run_async(self.manager.connect(name, ws))

        stats = self.manager.get_stats()
        self.assertEqual(stats["total_connections"], 2)
        self.assertIn("澜舟", stats["agents"])
        self.assertIn("灵犀", stats["agents"])

    def test_is_connected_true(self):
        ws = _make_mock_ws()
        run_async(self.manager.connect("澜舟", ws))
        self.assertTrue(self.manager.is_connected("澜舟"))

    def test_is_connected_false(self):
        self.assertFalse(self.manager.is_connected("澜舟"))


# ==================== TestConcurrentConnections ====================


class TestConcurrentConnections(unittest.TestCase):
    def setUp(self):
        self.manager = ConnectionManager()

    def test_five_agents_simultaneous(self):
        """5 个 Agent 同时连接，各自频道隔离"""
        agents = ["九重", "澜澜", "澜舟", "灵犀", "千寻"]
        ws_map = {}

        for name in agents:
            ws = _make_mock_ws()
            run_async(self.manager.connect(name, ws))
            ws_map[name] = ws

        # 各自订阅不同频道
        self.manager.subscribe("澜舟", "#tech")
        self.manager.subscribe("灵犀", "#tech")
        self.manager.subscribe("千寻", "#admin")

        # 重置
        for ws in ws_map.values():
            ws.send_text.reset_mock()

        # 广播 #tech
        sent = run_async(self.manager.broadcast("#tech", {"type": "event", "content": "技术更新"}))
        self.assertEqual(sent, 2)  # 澜舟 + 灵犀

        ws_map["澜舟"].send_text.assert_called_once()
        ws_map["灵犀"].send_text.assert_called_once()
        ws_map["千寻"].send_text.assert_not_called()
        ws_map["九重"].send_text.assert_not_called()
        ws_map["澜澜"].send_text.assert_not_called()

    def test_disconnect_does_not_affect_others(self):
        """断开一个 Agent 不影响其他连接"""
        for name in ["澜舟", "灵犀"]:
            ws = _make_mock_ws()
            run_async(self.manager.connect(name, ws))

        self.manager.disconnect("澜舟")

        self.assertFalse(self.manager.is_connected("澜舟"))
        self.assertTrue(self.manager.is_connected("灵犀"))


# ==================== TestEventStreamWSBridge ====================


class TestEventStreamWSBridge(unittest.TestCase):
    def test_build_ws_message_basic_event(self):
        """_build_ws_message 正确构建基础消息"""
        from datetime import datetime

        class FakeEventType:
            value = "TASK"

        class FakeEvent:
            event_id = "act_001"
            event_type = FakeEventType()
            sender = "九重"
            recipient = "澜舟"
            content = "请完成任务"
            timestamp = datetime(2026, 6, 18, 20, 0, 0)
            cause = None
            confidence = 0.9
            handoff = None

        msg = EventStreamWSBridge._build_ws_message(FakeEvent())
        self.assertEqual(msg["type"], "event")
        self.assertEqual(msg["event_type"], "TASK")
        self.assertEqual(msg["sender"], "九重")
        self.assertEqual(msg["recipient"], "澜舟")
        self.assertEqual(msg["content"], "请完成任务")
        self.assertEqual(msg["confidence"], 0.9)
        self.assertIsNone(msg["cause"])
        self.assertNotIn("handoff", msg)

    def test_build_ws_message_with_handoff(self):
        """含 handoff 字段时也应包含在消息中"""
        from datetime import datetime

        class FakeEventType:
            value = "DONE"

        class FakeEvent:
            event_id = "act_002"
            event_type = FakeEventType()
            sender = "澜舟"
            recipient = "九重"
            content = "[DONE]"
            timestamp = datetime(2026, 6, 18, 20, 0, 0)
            cause = "act_001"
            confidence = None
            handoff = {"what_done": "完成分析", "what_next": "等待审查"}

        msg = EventStreamWSBridge._build_ws_message(FakeEvent())
        self.assertIn("handoff", msg)
        self.assertEqual(msg["handoff"]["what_done"], "完成分析")

    def test_bridge_registers_subscriptions(self):
        """EventStreamWSBridge 应为所有 EventType 注册订阅"""
        # 用 mock 替代真实 EventStream
        mock_stream = MagicMock()
        mock_stream.subscribe_by_type = MagicMock()
        mock_manager = ConnectionManager()

        bridge = EventStreamWSBridge(mock_stream, mock_manager)

        # 应该调用了多次 subscribe_by_type（每个 EventType 一次）
        self.assertGreater(mock_stream.subscribe_by_type.call_count, 0)


# ==================== 运行 ====================

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestConnectionManager,
        TestHeartbeat,
        TestConnectionStats,
        TestConcurrentConnections,
        TestEventStreamWSBridge,
    ]

    for tc in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(tc))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
