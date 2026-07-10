"""
HomeStream 架构可视化引擎 + 数据质量守卫 测试

覆盖两个模块：
  - arch_visualizer.py: SVG 架构图生成
  - data_guardian.py: 事件数据质量校验

测试策略：
  - 使用 FakeEventStore 模拟事件数据
  - 验证 SVG 输出的有效性和安全性
  - 验证四维质量校验的准确性
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from arch_visualizer import (
    _escape_xml,
    _truncate,
    collect_architecture_data,
    generate_agent_topology,
    generate_event_flow,
    generate_router_status,
)
from data_guardian import (
    VALID_EVENT_TYPES,
    run_full_audit,
    validate_agent_identity,
    validate_causal_chain,
    validate_event_types,
    validate_timestamps,
)
from event_stream import Action, EventSource, EventType

# ==================== 测试 fixtures ====================


class FakeEventStore:
    """模拟 EventStore，用于测试"""

    def __init__(self, events=None):
        self._events = events or []
        self._event_map = {e.event_id: e for e in self._events}

    def query_by_session(self, session_id, limit=100, offset=0, newest_first=True):
        events = list(self._events)
        if newest_first:
            events = list(reversed(events))
        return events[offset : offset + limit]

    def query_range(self, session_id, start=None, end=None, limit=100):
        return self._events[:limit]

    def get_event_by_id(self, event_id):
        return self._event_map.get(event_id)

    def stats(self, session_id="default"):
        from collections import Counter

        by_type = Counter(str(e.event_type) for e in self._events)
        top_senders = Counter(e.sender for e in self._events)
        sessions = Counter(
            e.session_id if hasattr(e, "session_id") else "default" for e in self._events
        )
        return {
            "db_path": ":memory:",
            "session_id": session_id,
            "total_events": len(self._events),
            "by_type": dict(by_type),
            "top_senders": dict(top_senders),
            "sessions": dict(sessions),
        }

    def count(self):
        return len(self._events)


def make_event(
    event_id="evt-1",
    event_type=EventType.INFO,
    sender="alice",
    recipient="bob",
    content="hello",
    cause=None,
    timestamp=None,
):
    """创建测试事件"""
    return Action(
        event_id=event_id,
        event_type=event_type,
        sender=sender,
        recipient=recipient,
        content=content,
        cause=cause,
        timestamp=timestamp or datetime.now(),
        source=EventSource.AGENT,
    )


@pytest.fixture
def sample_events():
    """一组正常事件"""
    base = datetime(2026, 7, 8, 12, 0, 0)
    return [
        make_event("evt-1", EventType.TASK, "alice", "bob", "do task", None, base),
        make_event(
            "evt-2", EventType.DONE, "bob", "alice", "done", "evt-1", base + timedelta(seconds=10)
        ),
        make_event(
            "evt-3", EventType.INFO, "alice", "carol", "fyi", "evt-2", base + timedelta(seconds=20)
        ),
        make_event(
            "evt-4",
            EventType.ASK,
            "carol",
            "alice",
            "question?",
            "evt-3",
            base + timedelta(seconds=30),
        ),
        make_event(
            "evt-5",
            EventType.ACK,
            "alice",
            "carol",
            "got it",
            "evt-4",
            base + timedelta(seconds=40),
        ),
    ]


@pytest.fixture
def sample_store(sample_events):
    return FakeEventStore(sample_events)


# ==================== arch_visualizer 测试 ====================


class TestArchVisualizer:
    def test_placeholder_when_no_event_store(self):
        """EventStore 为 None 时返回占位 SVG"""
        svg = generate_agent_topology(None)
        assert "<svg" in svg
        assert "EventStore" in svg or "未初始化" in svg

    def test_placeholder_when_no_events(self):
        """无事件数据时返回占位 SVG"""
        store = FakeEventStore([])
        svg = generate_agent_topology(store)
        assert "<svg" in svg
        assert "暂无" in svg or "无事件" in svg

    def test_topology_generates_valid_svg(self, sample_store):
        """拓扑图生成有效 SVG"""
        svg = generate_agent_topology(sample_store)
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        assert "alice" in svg or "bob" in svg

    def test_topology_has_nodes_and_edges(self, sample_store):
        """拓扑图包含节点和连线"""
        svg = generate_agent_topology(sample_store)
        assert "<circle" in svg  # 节点
        assert "<line" in svg  # 连线

    def test_flow_generates_valid_svg(self, sample_store):
        """事件流向图生成有效 SVG"""
        svg = generate_event_flow(sample_store)
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")

    def test_flow_shows_event_types(self, sample_store):
        """流向图显示事件类型"""
        svg = generate_event_flow(sample_store)
        assert "TASK" in svg or "DONE" in svg or "INFO" in svg

    def test_flow_shows_timestamps(self, sample_store):
        """流向图显示时间戳"""
        svg = generate_event_flow(sample_store)
        assert "12:" in svg  # 12:00:xx

    def test_router_placeholder_when_no_router(self):
        """ModelRouter 为 None 时返回占位 SVG"""
        svg = generate_router_status(None)
        assert "<svg" in svg
        assert "未初始化" in svg or "ModelRouter" in svg

    def test_router_with_mock_router(self):
        """使用 mock ModelRouter 生成路由状态图"""
        mock_router = MagicMock()
        mock_router._initialized = True
        mock_router.get_status.return_value = {
            "strategy": "cascade",
            "providers": [
                {
                    "name": "silicon",
                    "display_name": "Silicon",
                    "tier": "L1",
                    "model": "qwen-7b",
                    "status": "healthy",
                    "enabled": True,
                    "stats": {"requests": 10},
                },
                {
                    "name": "deepseek",
                    "display_name": "DeepSeek",
                    "tier": "L2",
                    "model": "deepseek-chat",
                    "status": "healthy",
                    "enabled": True,
                    "stats": {"requests": 5},
                },
                {
                    "name": "ollama",
                    "display_name": "Ollama",
                    "tier": "L3",
                    "model": "qwen2.5:3b",
                    "status": "healthy",
                    "enabled": True,
                    "stats": {"requests": 20},
                },
            ],
        }
        svg = generate_router_status(mock_router)
        assert "<svg" in svg
        assert "L1" in svg
        assert "L2" in svg
        assert "L3" in svg
        assert "Silicon" in svg or "silicon" in svg.lower()

    def test_collect_architecture_data(self, sample_store):
        """collect_architecture_data 返回完整数据"""
        data = collect_architecture_data(sample_store, None)
        assert "topology_svg" in data
        assert "flow_svg" in data
        assert "router_svg" in data
        assert "meta" in data
        assert data["meta"]["event_count"] == 5

    def test_svg_escapes_special_chars(self):
        """SVG 正确转义特殊字符"""
        result = _escape_xml("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_truncate_long_text(self):
        """_truncate 截断长文本"""
        result = _truncate("a" * 20, max_len=10)
        assert len(result) == 10
        assert result.endswith("\u2026")

    def test_truncate_short_text_unchanged(self):
        """_truncate 不修改短文本"""
        result = _truncate("hello", max_len=10)
        assert result == "hello"


# ==================== data_guardian 测试 ====================


class TestDataGuardian:
    def test_empty_event_store(self):
        """空 EventStore 返回 pass"""
        store = FakeEventStore([])
        report = run_full_audit(store)
        assert report["overall_status"] == "pass"
        assert report["total_events"] == 0

    def test_none_event_store(self):
        """None EventStore 返回 pass"""
        report = run_full_audit(None)
        assert report["overall_status"] == "pass"
        assert report["total_events"] == 0

    def test_valid_events_all_pass(self, sample_store):
        """正常事件全部校验通过"""
        report = run_full_audit(sample_store)
        assert report["overall_status"] == "pass"
        assert report["overall_score"] == 1.0
        assert report["total_events"] == 5

    def test_broken_causal_chain(self):
        """检测因果链断裂"""
        events = [
            make_event("evt-1", EventType.INFO, "a", "b", "ok", None),
            make_event("evt-2", EventType.INFO, "a", "b", "ok", "nonexistent-cause"),
        ]
        result = validate_causal_chain(events, FakeEventStore(events))
        assert result["status"] == "error"
        assert len(result["issues"]) == 1
        assert (
            "断" in result["issues"][0]["message"]
            or "cause" in result["issues"][0]["message"].lower()
        )

    def test_valid_causal_chain(self, sample_events):
        """正常因果链通过"""
        result = validate_causal_chain(sample_events, FakeEventStore(sample_events))
        assert result["status"] == "pass"
        assert len(result["issues"]) == 0

    def test_time_travel_detected(self):
        """检测时间倒流"""
        now = datetime.now()
        events = [
            make_event("evt-1", EventType.INFO, "a", "b", "ok", None, now),
            make_event("evt-2", EventType.INFO, "a", "b", "ok", "evt-1", now - timedelta(hours=2)),
        ]
        result = validate_timestamps(events)
        assert result["status"] == "warn"
        assert any("倒" in i["message"] or "back" in i["message"].lower() for i in result["issues"])

    def test_future_timestamp_detected(self):
        """检测未来时间戳"""
        future = datetime.now() + timedelta(hours=1)
        events = [
            make_event("evt-1", EventType.INFO, "a", "b", "ok", None, future),
        ]
        result = validate_timestamps(events)
        assert result["status"] == "warn"
        assert any(
            "未来" in i["message"] or "future" in i["message"].lower() for i in result["issues"]
        )

    def test_valid_timestamps_pass(self, sample_events):
        """正常时间戳通过"""
        result = validate_timestamps(sample_events)
        assert result["status"] == "pass"

    def test_invalid_event_type(self):
        """检测非法事件类型"""
        events = [
            make_event("evt-1", EventType.INFO, "a", "b", "ok"),
            make_event("evt-2", EventType.INFO, "a", "b", "ok"),
        ]
        # 手动修改第二个事件的 event_type 为非法值
        events[1].event_type = "HACKED"
        result = validate_event_types(events)
        assert result["status"] == "error"
        assert len(result["issues"]) == 1

    def test_valid_event_types_pass(self, sample_events):
        """合法事件类型通过"""
        result = validate_event_types(sample_events)
        assert result["status"] == "pass"
        assert "TASK" in result["type_distribution"]
        assert "DONE" in result["type_distribution"]

    def test_empty_agent_name(self):
        """检测空 Agent 名称"""
        events = [
            make_event("evt-1", EventType.INFO, "", "bob", "ok"),
        ]
        result = validate_agent_identity(events)
        assert result["status"] == "error"
        assert any(
            "空" in i["message"] or "empty" in i["message"].lower() for i in result["issues"]
        )

    def test_injection_in_agent_name(self):
        """检测注入风险"""
        events = [
            make_event("evt-1", EventType.INFO, "<script>alert(1)</script>", "bob", "ok"),
        ]
        result = validate_agent_identity(events)
        assert result["status"] == "error"
        assert any(
            "注入" in i["message"] or "injection" in i["message"].lower() for i in result["issues"]
        )

    def test_valid_agent_identity_pass(self, sample_events):
        """正常 Agent 身份通过"""
        result = validate_agent_identity(sample_events)
        assert result["status"] == "pass"
        assert "alice" in result["known_agents"]

    def test_overall_score_calculation(self):
        """总分计算正确"""
        events = [
            make_event("evt-1", EventType.INFO, "a", "b", "ok", None),
            make_event("evt-2", EventType.INFO, "a", "b", "ok", "nonexistent"),
        ]
        report = run_full_audit(FakeEventStore(events))
        assert 0.0 <= report["overall_score"] <= 1.0
        assert report["overall_score"] < 1.0  # 有问题应该低于1.0

    def test_full_audit_structure(self, sample_store):
        """完整审计返回结构正确"""
        report = run_full_audit(sample_store)
        assert "timestamp" in report
        assert "session_id" in report
        assert "total_events" in report
        assert "overall_score" in report
        assert "overall_status" in report
        assert "checks" in report
        assert "causal_chain" in report["checks"]
        assert "timestamps" in report["checks"]
        assert "event_types" in report["checks"]
        assert "agent_identity" in report["checks"]

    def test_valid_event_types_set(self):
        """VALID_EVENT_TYPES 包含所有9种类型"""
        assert len(VALID_EVENT_TYPES) == 9
        for t in ["INFO", "ASK", "TASK", "UPD", "DONE", "WARN", "ACK", "PING", "LOG"]:
            assert t in VALID_EVENT_TYPES
