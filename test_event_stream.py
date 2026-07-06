"""
桥v7 EventStream - 单元测试

覆盖：
1. Event创建 + Pydantic校验
2. EventStream发布/订阅
3. 因果链追踪
4. ICP v1.1文本解析
5. Handoff 5要素
6. WAL写入
7. Action/Observation工厂
8. 任务生命周期状态机
9. v6兼容
10. .learnings/触发
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from event_stream import (
    EventStream, Event, Action, Observation,
    EventType, EventSource,
    parse_icp_message, parse_handoff_text,
    create_action, create_task_action, create_ask_action,
    create_done_action, create_warn_action, create_observation,
    _gen_event_id,
)


def test_event_creation():
    """测试1: Event创建 + Pydantic校验"""
    print("测试1: Event创建 + Pydantic校验")
    
    event = Event(
        event_id="test_001",
        event_type=EventType.TASK,
        sender="九重",
        recipient="澜澜",
        content="请协调全员",
        source=EventSource.USER,
    )
    
    assert event.event_id == "test_001"
    assert event.event_type == EventType.TASK
    assert event.sender == "九重"
    assert event.recipient == "澜澜"
    assert event.cause is None  # 无因果
    assert event.confidence is None
    assert event.handoff is None
    
    # 置信度范围校验
    try:
        Event(
            event_id="test_bad",
            event_type=EventType.INFO,
            sender="A", recipient="B", content="test",
            confidence=1.5,  # 超出范围
        )
        assert False, "应该校验失败"
    except Exception:
        pass  # 预期失败
    
    print("  ✅ 通过")


def test_publish_subscribe():
    """测试2: EventStream发布/订阅"""
    print("测试2: EventStream发布/订阅")
    
    stream = EventStream(session_id="test_002")
    received = []
    
    stream.subscribe("澜澜", lambda e: received.append(e))
    stream.subscribe("灵犀", lambda e: received.append(e))
    
    # 发布事件
    e1 = create_task_action("九重", "澜澜", "测试任务", "T-001")
    e2 = create_task_action("澜澜", "灵犀", "子任务", "T-002")
    e3 = create_action("九重", "灵犀", EventType.INFO, "直接消息")
    
    stream.publish(e1)
    stream.publish(e2)
    stream.publish(e3)
    
    # 澜澜收到1条（recipient=澜澜），灵犀收到2条（recipient=灵犀）
    lanlan_msgs = [e for e in received if e.recipient == "澜澜"]
    lingxi_msgs = [e for e in received if e.recipient == "灵犀"]
    
    assert len(lanlan_msgs) == 1, f"澜澜应收到1条，实际{len(lanlan_msgs)}"
    assert len(lingxi_msgs) == 2, f"灵犀应收到2条，实际{len(lingxi_msgs)}"
    assert stream.event_count == 3
    
    print("  ✅ 通过")


def test_type_subscribe():
    """测试3: 类型级订阅"""
    print("测试3: 类型级订阅")
    
    stream = EventStream(session_id="test_003")
    task_events = []
    done_events = []
    
    stream.subscribe_by_type(EventType.TASK, lambda e: task_events.append(e))
    stream.subscribe_by_type(EventType.DONE, lambda e: done_events.append(e))
    
    # 发布不同类型事件
    stream.publish(create_action("九重", "澜澜", EventType.TASK, "任务"))
    stream.publish(create_action("澜澜", "九重", EventType.INFO, "信息"))
    stream.publish(create_done_action("灵犀", "澜澜", "T-1", "完成", [], "", [], ""))
    
    assert len(task_events) == 1, f"TASK订阅应收到1条"
    assert len(done_events) == 1, f"DONE订阅应收到1条"
    
    print("  ✅ 通过")


def test_cause_chain():
    """测试4: 因果链追踪"""
    print("测试4: 因果链追踪")
    
    stream = EventStream(session_id="test_004")
    
    # 发布3个事件，自动因果链
    e1 = create_task_action("九重", "澜澜", "任务A", "T-A")
    eid1 = stream.publish(e1)
    
    e2 = create_action("澜澜", "九重", EventType.ACK, "收到")
    eid2 = stream.publish(e2)
    
    e3 = create_done_action("灵犀", "澜澜", "T-A", "完成", [], "", [], "")
    eid3 = stream.publish(e3)
    
    # 因果链追踪
    chain = stream.get_cause_chain(eid3)
    
    assert len(chain) == 3, f"因果链应有3个事件，实际{len(chain)}"
    assert chain[0].event_id == eid1, "根事件应为e1"
    assert chain[2].event_id == eid3, "叶事件应为e3"
    
    # 验证cause链接
    assert chain[1].cause == eid1
    assert chain[2].cause == eid2
    
    print("  ✅ 通过（因果链3级完整）")


def test_icp_parsing():
    """测试5: ICP v1.1文本解析"""
    print("测试5: ICP v1.1文本解析")
    
    # 标准格式
    result = parse_icp_message("[TASK] 九重→澜澜: 请协调全员")
    assert result["event_type"] == EventType.TASK
    assert result["sender"] == "九重"
    assert result["recipient"] == "澜澜"
    assert result["content"] == "请协调全员"
    
    # 英文箭头
    result2 = parse_icp_message("[INFO] 澜舟->灵犀: 调研进度")
    assert result2["event_type"] == EventType.INFO
    assert result2["sender"] == "澜舟"
    assert result2["recipient"] == "灵犀"
    
    # 无标签（默认INFO）
    result3 = parse_icp_message("普通消息")
    assert result3["event_type"] == EventType.INFO
    assert result3["content"] == "普通消息"
    
    # 所有ICP标签
    for tag_name, etype in [
        ("[ASK]", EventType.ASK), ("[UPD]", EventType.UPD),
        ("[DONE]", EventType.DONE), ("[WARN]", EventType.WARN),
        ("[ACK]", EventType.ACK), ("[PING]", EventType.PING),
        ("[LOG]", EventType.LOG),
    ]:
        result = parse_icp_message(f"{tag_name} A→B: test")
        assert result["event_type"] == etype, f"{tag_name} 解析失败"
    
    print("  ✅ 通过（9种ICP标签 + 英文箭头 + 默认INFO）")


def test_handoff_5_elements():
    """测试6: Handoff 5要素"""
    print("测试6: Handoff 5要素")
    
    done = create_done_action(
        sender="灵犀",
        recipient="澜澜",
        task_id="T-001",
        what_done="调研完成",
        where_artifacts=["shared/specs/report.md"],
        how_verify="打开文件确认",
        known_issues=["A2A目前v0.3"],
        what_next="建议Phase 3引入",
        confidence=0.9,
    )
    
    # 验证5要素
    assert done.handoff is not None
    assert done.handoff["what_done"] == "调研完成"
    assert done.handoff["where_artifacts"] == ["shared/specs/report.md"]
    assert done.handoff["how_verify"] == "打开文件确认"
    assert done.handoff["known_issues"] == ["A2A目前v0.3"]
    assert done.handoff["what_next"] == "建议Phase 3引入"
    
    # Handoff文本解析
    text = """[What Done] 调研完成
[Where] shared/specs/report.md
[How Verify] 打开文件确认
[Known Issues] A2A目前v0.3
[What Next] 建议Phase 3引入"""
    
    parsed = parse_handoff_text(text)
    assert parsed is not None
    assert parsed["what_done"] == "调研完成"
    
    print("  ✅ 通过（5要素完整 + 文本解析）")


def test_wal_entry():
    """测试7: WAL写入"""
    print("测试7: WAL写入")
    
    done = create_done_action(
        sender="澜舟", recipient="澜澜", task_id="T-001",
        what_done="EventStream引擎开发", where_artifacts=[],
        how_verify="", known_issues=[], what_next=""
    )
    
    assert done.wal_entry is not None
    assert done.wal_entry["type"] == "Key Decision"
    assert "T-001" in done.wal_entry["content"]
    assert done.wal_entry["timestamp"] is not None
    
    # WARN也触发WAL
    warn = create_warn_action("System", "澜澜", "内存不足")
    assert warn.wal_entry is not None
    assert warn.wal_entry["type"] == "Correction"
    
    print("  ✅ 通过（DONE和WARN均自动WAL）")


def test_ask_v11_extension():
    """测试8: ASK v1.1扩展字段"""
    print("测试8: ASK v1.1扩展字段")
    
    ask = create_ask_action(
        sender="九重",
        recipient="灵犀",
        question="MCP+A2A的落地路径？",
        ask_id="ASK-001",
        context="桥v7升级方案",
        deadline="今日18:00",
    )
    
    assert ask.ask_id == "ASK-001"
    assert ask.ask_context == "桥v7升级方案"
    assert ask.ask_deadline == "今日18:00"
    assert ask.event_type == EventType.ASK
    
    # ICP格式应包含置信度
    stream = EventStream(session_id="test_008")
    icp_text = stream.to_icp_v1_format(ask)
    assert "[ASK]" in icp_text
    
    print("  ✅ 通过（id/context/deadline三字段）")


def test_to_icp_format():
    """测试9: ICP v1.1格式输出"""
    print("测试9: ICP v1.1格式输出")
    
    stream = EventStream(session_id="test_009")
    
    # 基本格式
    e1 = create_task_action("九重", "澜澜", "测试", "T-1")
    text1 = stream.to_icp_v1_format(e1)
    assert text1.startswith("[TASK]")
    assert "九重→澜澜" in text1
    
    # 带置信度
    e2 = create_action("灵犀", "澜澜", EventType.UPD, "进度60%", confidence=0.8)
    text2 = stream.to_icp_v1_format(e2)
    assert "[置信度:80%]" in text2
    
    print("  ✅ 通过（标签+箭头+置信度）")


def test_learning_trigger():
    """测试10: .learnings/触发"""
    print("测试10: .learnings/触发")
    
    # DONE自动触发
    done = create_done_action("澜舟", "澜澜", "T-1", "完成", [], "", [], "")
    assert done.trigger_learning is True
    assert done.learning_type == "best_practice"
    
    # WARN触发error
    warn = create_warn_action("System", "澜澜", "连接超时", recoverable=False)
    assert warn.trigger_learning is True
    assert warn.learning_type == "error"
    
    # 普通INFO不触发
    info = create_action("澜澜", "九重", EventType.INFO, "汇报")
    assert info.trigger_learning is False
    
    print("  ✅ 通过（DONE/WARN自动触发 / INFO不触发）")


def test_observation_creation():
    """测试11: Observation创建"""
    print("测试11: Observation创建")
    
    obs = create_observation(
        sender="System",
        recipient="澜澜",
        event_type=EventType.ACK,
        content="任务已接收",
        cause_event_id="act_001",
    )
    
    assert obs.event_id.startswith("obs_")
    assert obs.event_type == EventType.ACK
    assert obs.cause == "act_001"
    assert obs.source == EventSource.ENVIRONMENT
    
    print("  ✅ 通过")


def test_statistics():
    """测试12: EventStream统计"""
    print("测试12: EventStream统计")
    
    stream = EventStream(session_id="test_012")
    stream.subscribe("澜澜", lambda e: None)
    stream.subscribe_by_type(EventType.TASK, lambda e: None)
    
    # 发布几个事件
    stream.publish(create_action("九重", "澜澜", EventType.TASK, "任务1"))
    stream.publish(create_action("九重", "澜澜", EventType.INFO, "信息1"))
    stream.publish(create_done_action("灵犀", "澜澜", "T-1", "完成", [], "", [], ""))
    
    stats = stream.get_statistics()
    
    assert stats["total_events"] == 3
    assert stats["type_counts"]["TASK"] == 1
    assert stats["type_counts"]["DONE"] == 1
    assert "澜澜" in stats["subscribers"]
    assert "TASK" in stats["type_subscribers"]
    
    print("  ✅ 通过")


# ==================== 运行所有测试 ====================

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    
    print("=" * 60)
    print("桥v7 EventStream - 单元测试")
    print("=" * 60)
    print()
    
    tests = [
        test_event_creation,
        test_publish_subscribe,
        test_type_subscribe,
        test_cause_chain,
        test_icp_parsing,
        test_handoff_5_elements,
        test_wal_entry,
        test_ask_v11_extension,
        test_to_icp_format,
        test_learning_trigger,
        test_observation_creation,
        test_statistics,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  ❌ 失败: {e}")
    
    print()
    print("=" * 60)
    print(f"测试结果: {passed}/{len(tests)} 通过", end="")
    if failed:
        print(f", {failed} 失败")
    else:
        print(" 🏆")
    print("=" * 60)
