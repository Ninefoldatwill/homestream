"""
桥v7 EventStream - Observation类型定义（Day 1 完整实现）

参考：OpenHands 10+种Observation
适配：九重生态（桥v6/书阁/Kanban/Security）

Observation = 系统对Action的响应
- 参考OpenHands的Action/Observation二分法
- 每个Action产生对应的Observation
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

from event_stream import (
    Observation, EventType, EventSource, Event,
    _gen_event_id, create_observation,
)


# ==================== 桥v7 Observation类型（7种核心 + 3种辅助）====================

class MessageReceivedObservation(Observation):
    """收到ICP消息（对应SendMessageAction）
    
    当Agent收到消息时产生此Observation
    """
    original_event_id: str = Field(..., description="原始事件的event_id")
    icp_type: EventType = Field(..., description="ICP消息类型")
    requires_ack: bool = Field(False, description="是否需要ACK回复")
    
    # ASK v1.1扩展
    ask_id: Optional[str] = Field(None, description="ASK消息ID")
    ask_context: Optional[str] = Field(None, description="ASK上下文")
    ask_deadline: Optional[str] = Field(None, description="ASK截止时间")


class TaskAssignedObservation(Observation):
    """任务已分配（对应AssignTaskAction）
    
    当任务被分配给Agent时产生此Observation
    自动触发Kanban创建
    """
    task_id: str = Field(..., description="任务ID")
    title: str = Field(..., description="任务标题")
    assignee: str = Field(..., description="被分配Agent")
    deadline: Optional[str] = Field(None, description="截止时间")
    priority: str = Field("medium", description="优先级")
    
    def to_kanban_payload(self) -> Dict[str, Any]:
        """转为Kanban创建API的请求体"""
        return {
            "title": self.title,
            "body": self.content,
            "assignee": self.assignee,
            "priority": self.priority,
        }


class TaskDoneObservation(Observation):
    """任务完成（含Handoff 5要素，对应HandoffTaskAction）
    
    当Builder完成任务并Handoff给Reviewer时产生
    """
    task_id: str = Field(..., description="任务ID")
    handoff: Dict[str, Any] = Field(..., description="Handoff 5要素")
    what_next: str = Field("", description="下一步建议")
    review_required: bool = Field(True, description="是否需要审查")


class ReviewResultObservation(Observation):
    """审查结果（对应ReviewTaskAction）
    
    当Reviewer完成审查时产生
    """
    task_id: str = Field(..., description="任务ID")
    approved: bool = Field(..., description="是否通过")
    comments: Optional[str] = Field(None, description="审查意见")
    issues_found: List[str] = Field(default_factory=list, description="发现的问题")
    score: Optional[int] = Field(None, ge=0, le=100, description="质量评分")


class KnowledgeResultObservation(Observation):
    """知识库查询结果（对应QueryKnowledgeAction）
    
    融合knowledge-graph + vector-memory + fusion-query
    """
    query: str = Field(..., description="原始查询")
    results: List[Dict[str, Any]] = Field(default_factory=list, description="查询结果")
    sources_queried: List[str] = Field(default_factory=list, description="查询的数据源")
    total_matches: int = Field(0, description="总匹配数")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="结果置信度")


class LearningUpdatedObservation(Observation):
    """ .learnings/ 已更新（对应UpdateLearningAction）
    
    融合self-improving-agent的4步闭环
    """
    learning_type: str = Field(..., description="类型")
    file_path: str = Field(..., description="写入的文件路径")
    entry_summary: str = Field(..., description="条目摘要")
    occurrence_count: int = Field(1, description="出现次数")
    ready_for_promotion: bool = Field(False, description="是否可晋升为Skill")


class ErrorObservation(Observation):
    """错误（触发ERRORS.md记录）
    
    参考OpenHands的ErrorObservation
    融合self-improving-agent的error类型
    """
    error_type: str = Field(..., description="错误类型: integration/tool/logic/network")
    message: str = Field(..., description="错误消息")
    recoverable: bool = Field(True, description="是否可恢复")
    fallback_suggestion: Optional[str] = Field(None, description="回退建议")
    retry_count: int = Field(0, ge=0, description="已重试次数")
    max_retries: int = Field(3, description="最大重试次数")


# --- 辅助Observation ---

class AckObservation(Observation):
    """ACK确认（对应收到TASK/ASK时的回复）"""
    original_event_id: str = Field(..., description="被确认的事件ID")
    ack_type: str = Field("received", description="确认类型: received/processing/done")


class HeartbeatObservation(Observation):
    """心跳响应（对应PingAction）"""
    agent_name: str = Field(..., description="响应Agent名称")
    status: str = Field("online", description="状态: online/busy/offline")
    current_task: Optional[str] = Field(None, description="当前任务")


class SecurityObservation(Observation):
    """安全审查结果（参考OpenHands SecurityAnalyzer）"""
    action_id: str = Field(..., description="被审查的Action ID")
    risk_level: str = Field("safe", description="风险级别: safe/confirmation_required/unsafe")
    reason: Optional[str] = Field(None, description="风险原因")
    blocked: bool = Field(False, description="是否被阻止")


# ==================== Observation创建工厂 ====================

def create_message_received(sender: str, recipient: str, 
                             original_event_id: str,
                             icp_type: EventType,
                             content: str,
                             cause_event_id: Optional[str] = None) -> MessageReceivedObservation:
    """工厂：创建MessageReceivedObservation"""
    requires_ack = icp_type in (EventType.TASK, EventType.ASK, EventType.WARN)
    return MessageReceivedObservation(
        event_id=_gen_event_id("obs"),
        event_type=EventType.ACK,
        sender=sender,
        recipient=recipient,
        content=content,
        source=EventSource.ENVIRONMENT,
        cause=cause_event_id,
        original_event_id=original_event_id,
        icp_type=icp_type,
        requires_ack=requires_ack,
    )


def create_task_assigned(task_id: str, title: str, assignee: str,
                          orchestrator: str = "澜澜",
                          deadline: Optional[str] = None,
                          priority: str = "medium",
                          cause_event_id: Optional[str] = None) -> TaskAssignedObservation:
    """工厂：创建TaskAssignedObservation"""
    return TaskAssignedObservation(
        event_id=_gen_event_id("obs"),
        event_type=EventType.INFO,
        sender=orchestrator,
        recipient=assignee,
        content=f"任务已分配: {title}",
        source=EventSource.ENVIRONMENT,
        cause=cause_event_id,
        task_id=task_id,
        title=title,
        assignee=assignee,
        deadline=deadline,
        priority=priority,
    )


def create_task_done_obs(task_id: str, handoff: Dict[str, Any],
                          builder: str = "unknown",
                          reviewer: str = "Orchestrator",
                          cause_event_id: Optional[str] = None) -> TaskDoneObservation:
    """工厂：创建TaskDoneObservation"""
    return TaskDoneObservation(
        event_id=_gen_event_id("obs"),
        event_type=EventType.DONE,
        sender=builder,
        recipient=reviewer,
        content=f"任务{task_id}完成",
        source=EventSource.ENVIRONMENT,
        cause=cause_event_id,
        task_id=task_id,
        handoff=handoff,
        what_next=handoff.get("what_next", ""),
    )


def create_error_obs(error_type: str, message: str,
                     recoverable: bool = True,
                     fallback: Optional[str] = None,
                     cause_event_id: Optional[str] = None) -> ErrorObservation:
    """工厂：创建ErrorObservation"""
    return ErrorObservation(
        event_id=_gen_event_id("obs"),
        event_type=EventType.WARN,
        sender="System",
        recipient="Orchestrator",
        content=f"错误: {message}",
        source=EventSource.ENVIRONMENT,
        cause=cause_event_id,
        error_type=error_type,
        message=message,
        recoverable=recoverable,
        fallback_suggestion=fallback,
        trigger_learning=True,
        learning_type="error",
    )


def create_knowledge_result(query: str, results: List[Dict[str, Any]],
                            sources: List[str],
                            confidence: float = 0.0,
                            cause_event_id: Optional[str] = None) -> KnowledgeResultObservation:
    """工厂：创建KnowledgeResultObservation"""
    return KnowledgeResultObservation(
        event_id=_gen_event_id("obs"),
        event_type=EventType.INFO,
        sender="书阁",
        recipient="Requester",
        content=f"查询'{query}'返回{len(results)}条结果",
        source=EventSource.ENVIRONMENT,
        cause=cause_event_id,
        query=query,
        results=results,
        sources_queried=sources,
        total_matches=len(results),
        confidence=confidence,
    )


def create_security_obs(action_id: str, risk_level: str,
                         reason: Optional[str] = None,
                         blocked: bool = False) -> SecurityObservation:
    """工厂：创建SecurityObservation"""
    return SecurityObservation(
        event_id=_gen_event_id("obs"),
        event_type=EventType.WARN if blocked else EventType.LOG,
        sender="SecurityAnalyzer",
        recipient="Orchestrator",
        content=f"安全审查: {risk_level}" + (f" - {reason}" if reason else ""),
        source=EventSource.ENVIRONMENT,
        action_id=action_id,
        risk_level=risk_level,
        reason=reason,
        blocked=blocked,
    )


# ==================== 导出列表 ====================

__all__ = [
    # 核心Observation
    "MessageReceivedObservation",
    "TaskAssignedObservation",
    "TaskDoneObservation",
    "ReviewResultObservation",
    "KnowledgeResultObservation",
    "LearningUpdatedObservation",
    "ErrorObservation",
    # 辅助Observation
    "AckObservation",
    "HeartbeatObservation",
    "SecurityObservation",
    # 工厂函数
    "create_message_received",
    "create_task_assigned",
    "create_task_done_obs",
    "create_error_obs",
    "create_knowledge_result",
    "create_security_obs",
]
