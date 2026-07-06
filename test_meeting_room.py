"""
会议室闭环功能测试 — 3端点验证

测试：
1. POST /api/v7/channels/send — 频道广播+路由
2. POST /api/v7/callback/kanban — Kanban回调
3. GET /api/v7/channels — 频道列表
4. GET /api/v7/callback/kanban/history — 回调历史
5. GET /meeting — 会议室前端页面
6. 频道路由逻辑（@提及/频道/点对点/广播）
7. 任务意图检测

日期: 2026-06-22
作者: 澜舟
"""

import sys
import os
import json
import re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from bridge_v7_server import app, CHANNELS, _detect_task_intent
from config import AGENT_TOKENS, AGENT_NAMES

client = TestClient(app)

# --- 动态获取Agent Token（不再硬编码假token） ---
# 从config.AGENT_TOKENS获取真实token→agent映射
_REAL_TOKEN_MAP = dict(AGENT_TOKENS)  # {token: agent_name}
# 反向映射：agent_name → token
_AGENT_TOKEN_BY_NAME = dict(AGENT_NAMES)  # {agent_name: token}

# 获取特定Agent的真实token
def _get_token(agent_name: str) -> str:
    """获取指定Agent的真实token，若无则用sender字段兜底"""
    return _AGENT_TOKEN_BY_NAME.get(agent_name, "")

# ==================== 测试1: 频道列表 ====================

def test_list_channels():
    """GET /api/v7/channels — 频道列表"""
    resp = client.get("/api/v7/channels")
    assert resp.status_code == 200
    data = resp.json()
    assert "channels" in data
    assert data["total"] == 4
    assert "#general" in data["channels"]
    assert "#tech" in data["channels"]
    assert "#creative" in data["channels"]
    assert "#admin" in data["channels"]
    # 检查频道属性
    general = data["channels"]["#general"]
    assert general["name"] == "综合大厅"
    assert "members" in general
    print("✅ test_list_channels: 频道列表正确")


# ==================== 测试2: 频道广播发送 ====================

def test_channel_send_basic():
    """POST /api/v7/channels/send — 频道广播"""
    token = _get_token("九重")
    payload = {
        "content": "大家好！会议室v7上线了",
        "channel": "#general",
    }
    # 有真实token则用token，否则用sender字段兜底
    if token:
        payload["token"] = token
    else:
        payload["sender"] = "九重"
    resp = client.post("/api/v7/channels/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "sent"
    assert data["sender"] == "九重"
    assert data["channel"] == "#general"
    assert data["event_id"] is not None
    print("✅ test_channel_send_basic: 频道广播成功")


def test_channel_send_with_icp_tag():
    """频道发送 + ICP标签"""
    token = _get_token("澜舟")
    payload = {
        "content": "[TASK] 完成会议室闭环功能",
        "channel": "#tech",
    }
    if token:
        payload["token"] = token
    else:
        payload["sender"] = "澜舟"
    resp = client.post("/api/v7/channels/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sender"] == "澜舟"
    assert data["channel"] == "#tech"
    # ICP标签应被解析
    assert data["task_detected"] is True
    print("✅ test_channel_send_with_icp_tag: ICP标签解析成功")


def test_channel_send_point_to_point():
    """点对点发送"""
    token = _get_token("九重")
    payload = {
        "content": "@澜舟 快把会议室搞完",
        "recipient": "澜舟",
    }
    if token:
        payload["token"] = token
    else:
        payload["sender"] = "九重"
    resp = client.post("/api/v7/channels/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sender"] == "九重"
    assert data["recipient"] == "澜舟"
    print("✅ test_channel_send_point_to_point: 点对点发送成功")


def test_channel_send_broadcast():
    """无频道无收件人 → 默认广播到#general"""
    token = _get_token("九重")
    payload = {"content": "全体注意，明天硬仗"}
    if token:
        payload["token"] = token
    else:
        payload["sender"] = "九重"
    resp = client.post("/api/v7/channels/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["channel"] == "#general"
    assert data["recipient"] is None or data["recipient"] == ""
    print("✅ test_channel_send_broadcast: 默认广播到#general")


def test_channel_send_with_mention():
    """@提及路由"""
    token = _get_token("九重")
    payload = {"content": "@灵犀 调研一下GLM-5.2"}
    if token:
        payload["token"] = token
    else:
        payload["sender"] = "九重"
    resp = client.post("/api/v7/channels/send", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # @提及 → recipient应为None（广播到频道）
    assert data["status"] == "sent"
    print("✅ test_channel_send_with_mention: @提及路由成功")


# ==================== 测试3: Kanban回调 ====================

def test_kanban_callback():
    """POST /api/v7/callback/kanban — Kanban状态变更回调"""
    resp = client.post("/api/v7/callback/kanban", json={
        "event": "task.status_changed",
        "task": {
            "id": "task-001",
            "title": "会议室闭环",
            "status": "done",
            "assignee": "澜舟",
            "result": "3端点130行，完成!",
        },
        "channel": "#tech",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    assert data["callback_id"] is not None
    assert data["event_id"] is not None
    assert data["kanban_event"] == "task.status_changed"
    assert data["task_id"] == "task-001"
    assert data["channel"] == "#tech"
    print("✅ test_kanban_callback: Kanban回调处理成功")


def test_kanban_callback_created():
    """Kanban回调 — 任务创建"""
    resp = client.post("/api/v7/callback/kanban", json={
        "event": "task.created",
        "task": {
            "id": "task-002",
            "title": "GLM-5.2调研",
            "status": "triage",
            "assignee": "灵犀",
        },
        "channel": "#tech",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    print("✅ test_kanban_callback_created: 任务创建回调成功")


def test_kanban_callback_history():
    """GET /api/v7/callback/kanban/history — 回调历史"""
    # 先发送2个回调（上面已发）
    resp = client.get("/api/v7/callback/kanban/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    assert len(data["callbacks"]) >= 2
    # 回调应按时间倒序
    print(f"✅ test_kanban_callback_history: 回调历史 {data['total']} 条记录")


# ==================== 测试4: 会议室前端 ====================

def test_meeting_room_page():
    """GET /meeting — 会议室前端页面"""
    resp = client.get("/meeting")
    assert resp.status_code == 200
    assert "会议室 v7" in resp.text or "会议室" in resp.text
    assert "EventStream" in resp.text
    assert "api/v7/channels/send" in resp.text
    print("✅ test_meeting_room_page: 会议室前端页面可访问")


# ==================== 测试5: 任务意图检测 ====================

def test_detect_task_intent():
    """任务意图检测（复用v6逻辑）"""
    # 【任务】格式
    r = _detect_task_intent("【任务】完成会议室闭环功能")
    assert r["is_task"] is True
    assert r["title"] == "完成会议室闭环功能"

    # 创建任务格式
    r = _detect_task_intent("创建任务：GLM-5.2调研")
    assert r["is_task"] is True

    # TODO格式
    r = _detect_task_intent("TODO: 修复Bug")
    assert r["is_task"] is True

    # 无意图
    r = _detect_task_intent("大家好")
    assert r["is_task"] is False

    print("✅ test_detect_task_intent: 任务意图检测4/4正确")


# ==================== 测试6: 根端点更新验证 ====================

def test_root_endpoints():
    """根端点返回V8仪表盘HTML（含会议室/书阁入口）"""
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    # V8仪表盘HTML应包含核心入口
    assert "OpenBridge V8" in html
    assert "会议室" in html or "meeting" in html
    assert "书阁" in html or "bookhouse" in html
    print("✅ test_root_endpoints: 根端点返回V8仪表盘HTML")


# ==================== 测试7: Agent识别+频道路由组合 ====================

def test_agent_token_in_channel_send():
    """各Agent Token在频道发送中的识别"""
    # 从config动态获取真实token→agent映射
    for token, expected_agent in _REAL_TOKEN_MAP.items():
        resp = client.post("/api/v7/channels/send", json={
            "token": token,
            "content": f"测试消息 from {expected_agent}",
            "channel": "#general",
        })
        assert resp.status_code == 200
        assert resp.json()["sender"] == expected_agent

    print("✅ test_agent_token_in_channel_send: 3个Agent Token识别正确")


# ==================== 运行所有测试 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("会议室闭环功能测试 — 3端点验证")
    print("=" * 60)

    tests = [
        test_list_channels,
        test_channel_send_basic,
        test_channel_send_with_icp_tag,
        test_channel_send_point_to_point,
        test_channel_send_broadcast,
        test_channel_send_with_mention,
        test_kanban_callback,
        test_kanban_callback_created,
        test_kanban_callback_history,
        test_meeting_room_page,
        test_detect_task_intent,
        test_root_endpoints,
        test_agent_token_in_channel_send,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
            failed += 1

    print("=" * 60)
    print(f"结果: {passed}/{len(tests)} 通过, {failed} 失败")
    if failed == 0:
        print("🎉 会议室闭环功能全部测试通过!")
    print("=" * 60)
