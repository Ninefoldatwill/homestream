"""
桥v7 EventStream - Action类型定义（Day 1 完整实现）

参考：OpenHands arxiv 2407.16741 的13种Action
适配：九重生态协作场景（精简为6种核心Action + 3种辅助Action）

融优主义实践：
- 任务生命周期 → 融合agent-team-orchestration
- Handoff 5要素 → 融合agent-team-orchestration
- WAL写入 → 融合proactive-agent
- .learnings/触发 → 融合self-improving-agent
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from event_stream import (
    Action,
    Event,
    EventSource,
    EventType,
    _gen_event_id,
    create_action,
    create_ask_action,
    create_done_action,
    create_task_action,
    create_warn_action,
    parse_handoff_text,
)

# ==================== 桥v7 Action类型（6种核心 + 3种辅助）====================

# --- 核心Action（覆盖90%协作场景）---


class SendMessageAction(Action):
    """发ICP消息（对应[TASK]/[DONE]/[INFO]/[ACK]/[UPD]/[WARN]/[PING]/[LOG]）

    最通用的Action，所有ICP v1.1消息都通过这个Action发送。
    通过event_type字段区分消息类型。
    """

    pass


class AssignTaskAction(Action):
    """分配任务（Orchestrator → Builder）

    融合agent-team-orchestration的任务分配机制
    自动触发Kanban创建任务
    """

    task_id: str = Field(..., description="任务ID")
    title: str = Field(..., description="任务标题")
    description: str = Field("", description="任务描述")
    deadline: str | None = Field(None, description="截止时间")
    priority: str = Field("medium", description="优先级: low/medium/high/urgent")

    def to_kanban_dict(self) -> dict[str, Any]:
        """转为Kanban创建格式"""
        return {
            "title": self.title,
            "body": self.description,
            "assignee": self.recipient,
            "channel": "#tech" if self.recipient in ["澜舟", "灵犀"] else "#general",
            "priority": self.priority,
            "deadline": self.deadline,
        }


class HandoffTaskAction(Action):
    """交接任务（Builder → Reviewer，含Handoff 5要素）

    融合agent-team-orchestration的Handoff协议：
    - What Done: 完成了什么
    - Where: 产出物在哪
    - How Verify: 如何验证
    - Known Issues: 已知问题
    - What Next: 下一步建议
    """

    task_id: str = Field(..., description="任务ID")
    build_agent: str = Field(..., description="构建Agent名称")
    review_agent: str = Field(..., description="审查Agent名称")

    # Handoff 5要素
    what_done: str = Field(..., description="[What Done] 完成了什么")
    where_artifacts: list[str] = Field(default_factory=list, description="[Where] 产出物路径")
    how_verify: str = Field("", description="[How Verify] 如何验证")
    known_issues: list[str] = Field(default_factory=list, description="[Known Issues] 已知问题")
    what_next: str = Field("", description="[What Next] 下一步建议")

    def get_handoff_dict(self) -> dict[str, Any]:
        """获取Handoff 5要素字典"""
        return {
            "what_done": self.what_done,
            "where_artifacts": self.where_artifacts,
            "how_verify": self.how_verify,
            "known_issues": self.known_issues,
            "what_next": self.what_next,
        }


class ReviewTaskAction(Action):
    """审查任务（Reviewer → Orchestrator）

    融合agent-team-orchestration的Review流程
    """

    task_id: str = Field(..., description="任务ID")
    approved: bool = Field(..., description="是否通过")
    comments: str | None = Field(None, description="审查意见")
    issues_found: list[str] = Field(default_factory=list, description="发现的问题")
    score: int | None = Field(None, ge=0, le=100, description="质量评分")


class QueryKnowledgeAction(Action):
    """查询知识库（书阁/知识图谱/向量搜索）

    融合knowledge-graph + vector-memory + fusion-query
    """

    query: str = Field(..., description="查询内容")
    sources: list[str] = Field(
        default_factory=lambda: ["shuge", "knowledge_graph", "vector"], description="查询源"
    )
    top_k: int = Field(5, ge=1, le=20, description="返回结果数")
    query_type: str = Field("hybrid", description="查询类型: exact/semantic/hybrid")


class UpdateLearningAction(Action):
    """更新.learnings/（触发self-improving-agent）

    融合self-improving-agent的4步闭环：
    1. 触发捕获 → 创建此Action
    2. 暂存沉淀 → 写入文件
    3. 验证迭代 → 3次重现+跨2任务
    4. 晋升固化 → 封装为Skill
    """

    learning_type: str = Field(
        ..., description="类型: error/correction/best_practice/feature_request"
    )
    content: str = Field(..., description="学习内容")
    file_path: str | None = Field(None, description="写入哪个文件")
    occurrence_count: int = Field(1, ge=1, description="出现次数")
    cross_task: bool = Field(False, description="是否跨任务")


# --- 辅助Action（扩展场景）---


class PingAction(Action):
    """心跳PING（九重上线→全员ACK）"""

    online_status: str = Field("online", description="online/offline")
    heartbeat_interval: int = Field(30, description="心跳间隔（分钟）")


class LogAction(Action):
    """系统日志（审计/调试用）"""

    log_level: str = Field("INFO", description="DEBUG/INFO/WARNING/ERROR")
    module: str = Field("", description="模块名")
    data: dict[str, Any] | None = Field(None, description="附加数据")


class BroadcastAction(Action):
    """广播消息（对所有Agent）"""

    channels: list[str] = Field(default_factory=lambda: ["#general"], description="目标频道")
    urgency: str = Field("normal", description="normal/high/critical")


# ==================== Action创建工厂（增强版）====================


def create_assign_task(
    orchestrator: str,
    builder: str,
    task_id: str,
    title: str,
    description: str = "",
    deadline: str | None = None,
    priority: str = "medium",
    confidence: float | None = None,
) -> AssignTaskAction:
    """工厂：创建AssignTaskAction"""
    return AssignTaskAction(
        event_id=_gen_event_id("asn"),
        event_type=EventType.TASK,
        sender=orchestrator,
        recipient=builder,
        content=f"任务分配: {title}",
        source=EventSource.AGENT,
        task_id=task_id,
        title=title,
        description=description,
        deadline=deadline,
        priority=priority,
        confidence=confidence,
    )


def create_handoff(
    task_id: str,
    build_agent: str,
    review_agent: str,
    what_done: str,
    where_artifacts: list[str],
    how_verify: str,
    known_issues: list[str],
    what_next: str,
    confidence: float | None = None,
) -> HandoffTaskAction:
    """工厂：创建HandoffTaskAction（融合agent-team-orchestration）"""
    return HandoffTaskAction(
        event_id=_gen_event_id("hnd"),
        event_type=EventType.DONE,
        sender=build_agent,
        recipient=review_agent,
        content=f"任务{task_id}完成，进入审查",
        source=EventSource.AGENT,
        task_id=task_id,
        build_agent=build_agent,
        review_agent=review_agent,
        what_done=what_done,
        where_artifacts=where_artifacts,
        how_verify=how_verify,
        known_issues=known_issues,
        what_next=what_next,
        confidence=confidence,
        handoff={
            "what_done": what_done,
            "where_artifacts": where_artifacts,
            "how_verify": how_verify,
            "known_issues": known_issues,
            "what_next": what_next,
        },
        trigger_learning=True,
        learning_type="best_practice",
    )


def create_review(
    task_id: str,
    reviewer: str,
    orchestrator: str,
    approved: bool,
    comments: str | None = None,
    issues_found: list[str] | None = None,
    score: int | None = None,
) -> ReviewTaskAction:
    """工厂：创建ReviewTaskAction"""
    content = "审查通过" if approved else "审查未通过"
    if comments:
        content += f": {comments}"

    return ReviewTaskAction(
        event_id=_gen_event_id("rev"),
        event_type=EventType.DONE if approved else EventType.WARN,
        sender=reviewer,
        recipient=orchestrator,
        content=content,
        source=EventSource.AGENT,
        task_id=task_id,
        approved=approved,
        comments=comments,
        issues_found=issues_found or [],
        score=score,
    )


def create_query_knowledge(
    sender: str, query: str, sources: list[str] | None = None, top_k: int = 5
) -> QueryKnowledgeAction:
    """工厂：创建QueryKnowledgeAction"""
    return QueryKnowledgeAction(
        event_id=_gen_event_id("qry"),
        event_type=EventType.ASK,
        sender=sender,
        recipient="书阁",  # 知识库路由
        content=f"查询: {query}",
        source=EventSource.AGENT,
        query=query,
        sources=sources or ["shuge", "knowledge_graph", "vector"],
        top_k=top_k,
    )


def create_update_learning(
    sender: str, learning_type: str, content: str, file_path: str | None = None
) -> UpdateLearningAction:
    """工厂：创建UpdateLearningAction"""
    return UpdateLearningAction(
        event_id=_gen_event_id("lrn"),
        event_type=EventType.LOG,
        sender=sender,
        recipient="System",
        content=f"学习记录: {content[:50]}",
        source=EventSource.AGENT,
        learning_type=learning_type,
        learning_content=content,
        file_path=file_path,
    )


# ==================== 导出列表 ====================

__all__ = [
    # 核心Action
    "SendMessageAction",
    "AssignTaskAction",
    "HandoffTaskAction",
    "ReviewTaskAction",
    "QueryKnowledgeAction",
    "UpdateLearningAction",
    # 辅助Action
    "PingAction",
    "LogAction",
    "BroadcastAction",
    # 工厂函数
    "create_assign_task",
    "create_handoff",
    "create_review",
    "create_query_knowledge",
    "create_update_learning",
]
