#!/usr/bin/env python3
"""HomeStream VoiceBridge 浏览器 Demo 服务器

同时提供:
1. 静态文件服务 (voice/browser_demo/ 下的 index.html / JS / 资源)
2. /dispatch 端点: 用户连接后触发显式 Agent dispatch

LiveKit 1.6.5 自托管环境下, 空 agent_name 的自动 dispatch 在 reconnect / 二次进房
等场景不可靠。因此 Worker 设置 agent_name="homestream-voice", 浏览器通过本端点
显式调用 AgentDispatchService API 完成分发。
"""
import asyncio
import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 允许从项目根 import livekit
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from livekit import api  # noqa: E402

DEMO_DIR = Path(__file__).parent / "browser_demo"
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "devsecret")
AGENT_NAME = os.environ.get("AGENT_NAME", "homestream-voice")
DEFAULT_ROOM = os.environ.get("DEFAULT_ROOM", "voice-test")


class DemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DEMO_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/dispatch":
            self._handle_dispatch(parsed.query)
        else:
            super().do_GET()

    def _handle_dispatch(self, query: str):
        params = parse_qs(query)
        room = params.get("room", [DEFAULT_ROOM])[0]
        try:
            asyncio.run(self._create_dispatch(room))
            self._send_json(200, {"ok": True, "room": room, "agent": AGENT_NAME})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": str(e)})

    async def _create_dispatch(self, room: str):
        lk = api.LiveKitAPI(url=LIVEKIT_URL, api_key=API_KEY, api_secret=API_SECRET)
        try:
            req = api.CreateAgentDispatchRequest()
            req.agent_name = AGENT_NAME
            req.room = room
            req.metadata = json.dumps({"source": "browser_demo"})
            await lk.agent_dispatch.create_dispatch(req)
        finally:
            await lk.aclose()

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args):
        # 简化日志, 避免刷屏
        print(f"[demo-server] {fmt % args}")


def main():
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    server = HTTPServer((host, port), DemoHandler)
    print(f"[demo-server] serving {DEMO_DIR} at http://{host}:{port}")
    print(f"[demo-server] dispatch endpoint: http://{host}:{port}/dispatch?room={DEFAULT_ROOM}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[demo-server] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
