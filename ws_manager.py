"""
桥v7 WebSocket 实时推送管理器

融优主义分级: A类 — 直接融
来源: FastAPI 原生 WebSocket + 标准协议
融优率: 90%（标准协议直接用，仅针对九重生态定制集成层）

核心设计：
1. ConnectionManager — 连接/断开/频道订阅/广播/心跳
2. EventStreamWSBridge — EventStream 事件 → WS 广播
3. FastAPI 端点集成（/ws/{agent_name}）
4. 心跳检测（30s ping / 60s 超时断开）
5. 降级兼容（轮询 API 保留）

日期: 2026-06-18
作者: 澜舟
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("ws_manager")


# ==================== 连接管理器 ====================


class ConnectionManager:
    """
    WebSocket 连接管理器

    职责：
    1. 维护 agent_name → WebSocket 映射
    2. 频道订阅（每个连接只接收已订阅频道的事件）
    3. 心跳检测（30s ping / 60s 超时自动断开）
    4. 按频道广播 / 定向单发

    线程安全：所有写操作通过 asyncio 事件循环保证串行。
    """

    HEARTBEAT_INTERVAL: float = 30.0  # 发送 ping 的间隔（秒）
    HEARTBEAT_TIMEOUT: float = 60.0  # 超过此时间无 pong → 断开（秒）
    DEFAULT_CHANNELS: set[str] = frozenset({"#general"})

    def __init__(self) -> None:
        # agent_name → WebSocket 对象
        self._connections: dict[str, Any] = {}
        # agent_name → 已订阅频道集合
        self._subscriptions: dict[str, set[str]] = {}
        # agent_name → 最近一次收到 pong 的时间戳
        self._last_pong: dict[str, float] = {}
        # 心跳后台任务句柄
        self._heartbeat_task: asyncio.Task | None = None

    # ---------- 连接生命周期 ----------

    async def connect(self, agent_name: str, websocket: Any) -> None:
        """接受新 WebSocket 连接"""
        await websocket.accept()
        self._connections[agent_name] = websocket
        self._subscriptions[agent_name] = set(self.DEFAULT_CHANNELS)
        self._last_pong[agent_name] = time.time()
        logger.info(f"[WS] {agent_name} 已连接，当前连接数: {len(self._connections)}")

        # 发送欢迎消息
        await self._send_raw(
            agent_name,
            {
                "type": "connected",
                "agent": agent_name,
                "channels": sorted(self._subscriptions[agent_name]),
            },
        )

    def disconnect(self, agent_name: str) -> None:
        """移除 WebSocket 连接"""
        removed = agent_name in self._connections
        self._connections.pop(agent_name, None)
        self._subscriptions.pop(agent_name, None)
        self._last_pong.pop(agent_name, None)
        if removed:
            logger.info(f"[WS] {agent_name} 已断开，当前连接数: {len(self._connections)}")

    # ---------- 频道订阅 ----------

    def subscribe(self, agent_name: str, channel: str) -> bool:
        """订阅频道，返回是否成功"""
        if agent_name not in self._subscriptions:
            return False
        self._subscriptions[agent_name].add(channel)
        logger.debug(f"[WS] {agent_name} 订阅 {channel}")
        return True

    def unsubscribe(self, agent_name: str, channel: str) -> bool:
        """取消订阅频道，返回是否成功"""
        if agent_name not in self._subscriptions:
            return False
        self._subscriptions[agent_name].discard(channel)
        logger.debug(f"[WS] {agent_name} 取消订阅 {channel}")
        return True

    def get_subscriptions(self, agent_name: str) -> set[str]:
        """获取指定 Agent 的订阅频道集合"""
        return set(self._subscriptions.get(agent_name, set()))

    # ---------- 消息发送 ----------

    async def broadcast(self, channel: str, message: dict) -> int:
        """按频道广播消息，返回成功发送数量"""
        msg_json = json.dumps(message, ensure_ascii=False)
        sent = 0
        disconnected = []

        for agent, ws in list(self._connections.items()):
            if channel not in self._subscriptions.get(agent, set()):
                continue
            try:
                await ws.send_text(msg_json)
                sent += 1
            except Exception as exc:
                logger.warning(f"[WS] broadcast 到 {agent} 失败: {exc}")
                disconnected.append(agent)

        for agent in disconnected:
            self.disconnect(agent)

        return sent

    async def send_to(self, agent_name: str, message: dict) -> bool:
        """向特定 Agent 发送消息，返回是否成功"""
        return await self._send_raw(agent_name, message)

    async def _send_raw(self, agent_name: str, message: dict) -> bool:
        ws = self._connections.get(agent_name)
        if not ws:
            return False
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
            return True
        except Exception as exc:
            logger.warning(f"[WS] send_to {agent_name} 失败: {exc}")
            self.disconnect(agent_name)
            return False

    # ---------- 心跳检测 ----------

    async def start_heartbeat(self) -> None:
        """启动心跳后台任务（在 FastAPI startup 中调用）"""
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("[WS] 心跳任务已启动")

    def stop_heartbeat(self) -> None:
        """停止心跳任务"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            logger.info("[WS] 心跳任务已停止")

    async def _heartbeat_loop(self) -> None:
        """心跳循环：定期发 ping 并清理超时连接"""
        while True:
            try:
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
                now = time.time()

                # 检查超时，清理死连接
                timed_out = [
                    agent
                    for agent, last in self._last_pong.items()
                    if now - last > self.HEARTBEAT_TIMEOUT
                ]
                for agent in timed_out:
                    logger.warning(f"[WS] {agent} 心跳超时，断开连接")
                    self.disconnect(agent)

                # 向剩余连接发 ping
                for agent in list(self._connections.keys()):
                    await self._send_raw(agent, {"type": "ping"})

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[WS] 心跳循环异常: {exc}")

    def handle_pong(self, agent_name: str) -> None:
        """收到 pong 时更新心跳时间戳"""
        if agent_name in self._last_pong:
            self._last_pong[agent_name] = time.time()

    # ---------- 统计信息 ----------

    def get_stats(self) -> dict:
        """返回当前连接统计信息"""
        return {
            "total_connections": len(self._connections),
            "agents": sorted(self._connections.keys()),
            "subscriptions": {k: sorted(v) for k, v in self._subscriptions.items()},
            "uptime_seconds": None,  # 由调用方填写
        }

    def is_connected(self, agent_name: str) -> bool:
        """检查 Agent 是否在线"""
        return agent_name in self._connections


# ==================== EventStream → WebSocket 桥接器 ====================


class EventStreamWSBridge:
    """
    EventStream → WebSocket 桥接器

    工作流程：
      EventStream.publish(event)
        → _on_event() (同步订阅回调)
        → asyncio.create_task(_broadcast_event())
        → ConnectionManager.broadcast(channel, ws_message)
        → 前端 WebSocket 客户端即时收到

    注意：EventStream 的订阅回调是同步调用，因此需要用
    asyncio.get_event_loop().create_task() 切换到异步上下文。
    """

    def __init__(
        self,
        stream: Any,  # EventStream 实例（避免循环导入用 Any）
        manager: ConnectionManager,
        default_channel: str = "#general",
    ) -> None:
        self.stream = stream
        self.manager = manager
        self.default_channel = default_channel

        # 订阅 EventStream 所有类型事件
        self._register_subscriptions()

    def _register_subscriptions(self) -> None:
        """注册对 EventStream 所有 EventType 的订阅"""
        try:
            from event_stream import EventType

            for etype in EventType:
                self.stream.subscribe_by_type(etype, self._on_event)
        except ImportError:
            # 如果 EventStream 不可用，跳过注册
            logger.warning("[WSBridge] EventStream 导入失败，跳过订阅注册")

    def _on_event(self, event: Any) -> None:
        """EventStream 回调（同步）→ 调度异步广播任务"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._broadcast_event(event))
            else:
                # 测试环境中 loop 未运行，直接调用
                asyncio.run(self._broadcast_event(event))
        except RuntimeError:
            # 无事件循环时忽略（纯测试场景）
            pass

    async def _broadcast_event(self, event: Any) -> None:
        """构建 WS 消息并按频道广播"""
        try:
            channel = getattr(event, "channel", self.default_channel)
            if not channel:
                channel = self.default_channel

            # 构建标准 WS 事件消息
            ws_message = self._build_ws_message(event)
            await self.manager.broadcast(channel, ws_message)

        except Exception as exc:
            logger.error(f"[WSBridge] 广播事件失败: {exc}")

    @staticmethod
    def _build_ws_message(event: Any) -> dict:
        """Event → WebSocket 消息格式"""
        msg = {
            "type": "event",
            "event_id": getattr(event, "event_id", ""),
            "event_type": (
                event.event_type.value
                if hasattr(event.event_type, "value")
                else str(getattr(event, "event_type", ""))
            ),
            "sender": getattr(event, "sender", ""),
            "recipient": getattr(event, "recipient", ""),
            "content": getattr(event, "content", ""),
            "timestamp": (
                event.timestamp.isoformat()
                if hasattr(event, "timestamp") and hasattr(event.timestamp, "isoformat")
                else str(getattr(event, "timestamp", ""))
            ),
            "cause": getattr(event, "cause", None),
        }
        # 可选字段
        if getattr(event, "confidence", None) is not None:
            msg["confidence"] = event.confidence
        if getattr(event, "handoff", None) is not None:
            msg["handoff"] = event.handoff
        return msg


# ==================== FastAPI 路由工厂函数 ====================


def create_ws_router(manager: ConnectionManager, get_agent_token_fn=None):
    """创建 WebSocket FastAPI Router（可挂载到现有 app）

    Args:
        manager: ConnectionManager 实例
        get_agent_token_fn: (agent_name: str) → str，获取 agent token 的回调

    Returns:
        FastAPI APIRouter，包含 /ws/{agent_name} 端点和统计端点

    用法:
        from fastapi import FastAPI
        app = FastAPI()
        ws_router = create_ws_router(manager)
        app.include_router(ws_router)
    """
    try:
        from fastapi import APIRouter, WebSocket, WebSocketDisconnect
        from fastapi.responses import JSONResponse
    except ImportError:
        logger.error("[WS] FastAPI 未安装，无法创建路由")
        return None

    router = APIRouter(tags=["WebSocket"])

    @router.websocket("/ws/{agent_name}")
    async def websocket_endpoint(websocket: WebSocket, agent_name: str):
        """
        WebSocket 连接入口

        双向消息协议:
        - 服务端 → 客户端:
            {"type": "connected", "agent": "...", "channels": [...]}
            {"type": "event", "event_id": "...", "event_type": "...", ...}
            {"type": "ping"}
        - 客户端 → 服务端:
            {"type": "pong"}
            {"type": "subscribe", "channel": "#tech"}
            {"type": "unsubscribe", "channel": "#tech"}
            {"type": "send", "channel": "#general", "content": "..."}
        """
        await manager.connect(agent_name, websocket)
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await manager.send_to(agent_name, {"type": "error", "reason": "invalid JSON"})
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "pong":
                    manager.handle_pong(agent_name)

                elif msg_type == "subscribe":
                    channel = msg.get("channel", "")
                    if channel:
                        manager.subscribe(agent_name, channel)
                        await manager.send_to(
                            agent_name,
                            {
                                "type": "subscribed",
                                "channel": channel,
                            },
                        )

                elif msg_type == "unsubscribe":
                    channel = msg.get("channel", "")
                    if channel:
                        manager.unsubscribe(agent_name, channel)
                        await manager.send_to(
                            agent_name,
                            {
                                "type": "unsubscribed",
                                "channel": channel,
                            },
                        )

                elif msg_type == "send":
                    # 通过桥 v6 REST API 转发（保持兼容）
                    channel = msg.get("channel", "#general")
                    content = msg.get("content", "")
                    if content:
                        await _forward_to_bridge(agent_name, channel, content, get_agent_token_fn)

                else:
                    await manager.send_to(
                        agent_name,
                        {
                            "type": "error",
                            "reason": f"unknown message type: {msg_type}",
                        },
                    )

        except WebSocketDisconnect:
            manager.disconnect(agent_name)

    @router.get("/api/v7/ws/stats")
    async def ws_stats():
        """WebSocket 连接统计"""
        stats = manager.get_stats()
        stats["uptime_seconds"] = None  # 可由调用方补充
        return JSONResponse(stats)

    @router.get("/api/v7/ws/channels")
    async def ws_channels():
        """当前频道订阅情况"""
        return JSONResponse(manager.get_stats()["subscriptions"])

    return router


async def _forward_to_bridge(
    agent_name: str,
    channel: str,
    content: str,
    get_token_fn=None,
) -> None:
    """将前端发送的消息通过桥 v6 REST API 转发"""
    try:
        import aiohttp  # 优先用异步 HTTP

        token = get_token_fn(agent_name) if get_token_fn else ""
        async with aiohttp.ClientSession() as session:
            await session.post(
                "http://localhost:3459/api/v6/send",
                json={
                    "channel": channel,
                    "from": agent_name,
                    "content": content,
                    "token": token,
                },
            )
    except ImportError:
        # aiohttp 不可用时降级到同步 requests（在 executor 中运行）
        import functools

        import requests

        loop = asyncio.get_event_loop()
        token = get_token_fn(agent_name) if get_token_fn else ""
        await loop.run_in_executor(
            None,
            functools.partial(
                requests.post,
                "http://localhost:3459/api/v6/send",
                json={
                    "channel": channel,
                    "from": agent_name,
                    "content": content,
                    "token": token,
                },
                timeout=5,
            ),
        )
    except Exception as exc:
        logger.error(f"[WS] 转发消息到桥失败: {exc}")


# ==================== 集成到现有 bridge_v7_server 的挂载函数 ====================


def mount_websocket_to_app(
    app: Any,
    event_stream: Any = None,
    get_agent_token_fn=None,
) -> ConnectionManager:
    """
    一行代码将 WebSocket 功能挂载到现有 FastAPI app

    用法（在 bridge_v7_server.py 中）：
        from ws_manager import mount_websocket_to_app
        ws_manager = mount_websocket_to_app(app, event_stream=stream)

    Args:
        app:                现有 FastAPI 实例
        event_stream:       EventStream 实例（可选，传入后自动桥接）
        get_agent_token_fn: (agent_name) → token，用于转发消息

    Returns:
        ConnectionManager 实例（可用于在业务逻辑中主动推送）
    """
    manager = ConnectionManager()

    # 挂载路由
    ws_router = create_ws_router(manager, get_agent_token_fn)
    if ws_router:
        app.include_router(ws_router)

    # 绑定 EventStream（可选）
    if event_stream is not None:
        EventStreamWSBridge(event_stream, manager)
        logger.info("[WS] EventStream → WebSocket 桥接已激活")

    # 注册 startup/shutdown 钩子
    @app.on_event("startup")
    async def _ws_startup():
        await manager.start_heartbeat()
        logger.info("[WS] WebSocket 服务已启动")

    @app.on_event("shutdown")
    async def _ws_shutdown():
        manager.stop_heartbeat()
        logger.info("[WS] WebSocket 服务已停止")

    logger.info("[WS] WebSocket 模块已挂载到 app")
    return manager
