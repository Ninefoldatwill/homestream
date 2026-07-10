"""
桥v7 Worktree + 审查分离集成模块（Day 2）

三源融优实现：
- ReviewerSubscriber: 制造者/检查者角色映射（C自: Agent Loop审查分离）
- WorktreeSubscriber: Worktree事件联动（A主+B副+C自 集成）
- API端点: /api/v7/worktree/* CRUD + 审查闭环
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from event_stream import (
    Action,
    Event,
    EventSource,
    EventStream,
    EventType,
    Observation,
    create_action,
    create_done_action,
)
from worktree_manager import (
    WorktreeConfig,
    WorktreeManager,
    WorktreeRole,
    WorktreeStatus,
    create_coordinator_worktree,
    create_maker_worktree,
    create_researcher_worktree,
    create_reviewer_worktree,
)

# ==================== ReviewerSubscriber（C自: 审查分离）====================


class ReviewerSubscriber:
    """审查者订阅器 — Agent Loop审查分离的EventStream实现

    核心机制：
    1. 监听DONE事件 → 判断是否需要审查
    2. 自动创建审查Worktree（千寻审查澜舟的产出）
    3. 发布REVIEW事件 → 通知审查者
    4. 审查完成 → 合并回主分支

    九重生态角色映射（C自: 天然适配）：
    - 澜舟(制造者) DONE → 千寻(审查者) REVIEW
    - 灵犀(调研者) INFO → 澜舟(审查者) VERIFY
    - 澜澜(调度者) ACK → 九重(决策者) APPROVE
    """

    # 角色审查映射表
    REVIEW_MAPPING = {
        "澜舟": "千寻",  # 制造者→审查者
        "灵犀": "澜舟",  # 调研者→验证者
        "澜澜": "九重",  # 调度者→决策者
        "千寻": "澜澜",  # 归档者→确认者
    }

    def __init__(self, stream: EventStream, manager: WorktreeManager):
        self.stream = stream
        self.manager = manager

        # 注册事件订阅
        self.stream.subscribe_by_type(EventType.DONE, self._on_done)
        self.stream.subscribe_by_type(EventType.INFO, self._on_info_for_review)
        self.stream.subscribe_by_type(
            EventType.WARN, self._on_info_for_review
        )  # REVIEW:FAIL通过WARN事件传递

    def _on_done(self, event: Event):
        """DONE事件 → 触发审查流程"""
        sender = event.sender

        # 判断是否需要审查
        reviewer = self.REVIEW_MAPPING.get(sender)
        if not reviewer:
            return  # 无需审查的角色（如九重）

        # 查找发送者的活跃Worktree
        sender_worktrees = [
            wt
            for wt in self.manager.list_worktrees()
            if wt.agent == sender and wt.status == WorktreeStatus.ACTIVE
        ]

        if not sender_worktrees:
            return  # 无活跃Worktree

        worktree = sender_worktrees[0]

        # 1. 将Worktree标记为审查中
        self.manager.assign_reviewer(worktree.name, reviewer)

        # 2. 创建审查Worktree
        review_config = create_reviewer_worktree(
            source_name=worktree.name,
            reviewer=reviewer,
            source_branch=worktree.branch,
        )

        try:
            review_path = self.manager.create_worktree(review_config)
        except ValueError:
            # 审查Worktree已存在
            review_path = review_config.worktree_path

        # 3. 发布REVIEW事件
        review_event = create_action(
            sender="System",
            recipient=reviewer,
            event_type=EventType.TASK,
            content=f"[TASK] 审查任务: {sender}的工作已完成，请审查Worktree {worktree.name}。原分支: {worktree.branch}",
            cause=event.event_id,
        )
        self.stream.publish(review_event)

        # 4. 锁定原Worktree（审查期间不可修改）
        self.manager.lock_worktree(worktree.name, f"等待{reviewer}审查")

    def _on_info_for_review(self, event: Event):
        """INFO事件中包含审查结果的特殊处理"""
        content = event.content

        # 检测审查通过标记
        if "[REVIEW:PASS]" in content or "[ACK]" in content:
            self._handle_review_pass(event)
        elif "[REVIEW:FAIL]" in content or "[WARN]" in content:
            self._handle_review_fail(event)

    def _handle_review_pass(self, event: Event):
        """审查通过处理"""
        # 找到被锁定的Worktree并解锁
        for wt in self.manager.list_worktrees():
            if wt.status == WorktreeStatus.LOCKED and wt.reviewer == event.sender:
                self.manager.unlock_worktree(wt.name)

                # 发布审核通过事件（使用Handoff 5要素）
                pass_event = create_done_action(
                    sender=event.sender,
                    recipient=wt.agent,
                    task_id=f"review-{wt.name}",
                    what_done=f"审查通过: Worktree {wt.name} 已解锁",
                    where_artifacts=[wt.name],
                    how_verify="审查者确认通过",
                    known_issues=[],
                    what_next="可以合并回主分支",
                    confidence=None,
                )
                # 保持因果链
                pass_event.cause = event.event_id
                self.stream.publish(pass_event)
                break

    def _handle_review_fail(self, event: Event):
        """审查不通过处理"""
        for wt in self.manager.list_worktrees():
            if wt.status == WorktreeStatus.LOCKED and wt.reviewer == event.sender:
                # 解锁但不标记完成，让制造者继续修改
                self.manager.unlock_worktree(wt.name)

                # 通知制造者
                fail_event = create_action(
                    sender=event.sender,
                    recipient=wt.agent,
                    event_type=EventType.WARN,
                    content=f"[WARN] 审查未通过: Worktree {wt.name}，请修改后重新提交",
                    cause=event.event_id,
                )
                self.stream.publish(fail_event)
                break


# ==================== WorktreeSubscriber（A主+B副+C自集成）====================


class WorktreeSubscriber:
    """Worktree事件订阅器 — 将EventStream与WorktreeManager深度集成

    监听所有事件，维护Worktree与Event的关联关系：
    - TASK事件 → 创建Worktree
    - DONE事件 → 标记Worktree完成
    - WARN事件 → 通知Worktree持有者
    - Handoff → Worktree间交接
    """

    def __init__(self, stream: EventStream, manager: WorktreeManager):
        self.stream = stream
        self.manager = manager

        # Worktree-Event映射
        self._event_worktree_map: dict[str, str] = {}  # event_id → worktree_name

        # 注册订阅
        self.stream.subscribe_by_type(EventType.TASK, self._on_task)
        self.stream.subscribe_by_type(EventType.DONE, self._on_done)
        self.stream.subscribe_by_type(EventType.WARN, self._on_warn)

    def _on_task(self, event: Event):
        """TASK事件 → 可能需要创建Worktree"""
        content = event.content

        # 检测是否包含worktree指令
        if "[WT:CREATE]" in content:
            self._handle_create_command(event)
        elif "[WT:MERGE]" in content:
            self._handle_merge_command(event)

    def _on_done(self, event: Event):
        """DONE事件 → 标记Worktree状态"""
        sender = event.sender

        # 查找发送者的审查中Worktree
        for wt in self.manager.list_worktrees():
            if wt.agent == sender and wt.status == WorktreeStatus.REVIEWING:
                # 审查完成
                wt.status = WorktreeStatus.COMPLETED
                wt.completed_at = datetime.now().isoformat()
                break

    def _on_warn(self, event: Event):
        """WARN事件 → 通知Worktree持有者"""
        # WARN通常需要立即关注，记录到Worktree的DB中
        recipient = event.recipient

        for wt in self.manager.list_worktrees():
            if wt.agent == recipient:
                # 写入Worktree的独立DB
                conn = self.manager.db_manager.get_connection(wt.name)
                if conn:
                    try:
                        conn.execute(
                            "INSERT INTO events (id, type, source, target, data, cause_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                event.event_id,
                                "WARN",
                                event.sender,
                                event.recipient,
                                event.content,
                                event.cause,
                                datetime.now().isoformat(),
                            ),
                        )
                        conn.commit()
                    except Exception:
                        pass

    def _handle_create_command(self, event: Event):
        """处理Worktree创建命令"""
        content = event.content

        # 解析命令格式: [WT:CREATE:name:agent:role]
        # 注意: [^\]:] 匹配任意非']'非':'字符，支持中文Agent名/中文角色名
        import re

        match = re.search(r"\[WT:CREATE:([^\]:]+):([^\]:]+):([^\]:]+)\]", content)
        if not match:
            return

        name, agent, role_str = match.groups()

        role_map = {
            "maker": WorktreeRole.MAKER,
            "reviewer": WorktreeRole.REVIEWER,
            "researcher": WorktreeRole.RESEARCHER,
            "coordinator": WorktreeRole.COORDINATOR,
        }
        role = role_map.get(role_str, WorktreeRole.MAKER)

        # 根据角色创建对应的Worktree
        if role == WorktreeRole.MAKER:
            config = create_maker_worktree(name, agent=agent)
        elif role == WorktreeRole.REVIEWER:
            config = create_reviewer_worktree(name, reviewer=agent)
        elif role == WorktreeRole.RESEARCHER:
            config = create_researcher_worktree(name, agent=agent)
        else:
            config = create_coordinator_worktree(name, agent=agent)

        try:
            path = self.manager.create_worktree(config)
            self._event_worktree_map[event.event_id] = name
        except ValueError:
            pass  # 已存在

    def _handle_merge_command(self, event: Event):
        """处理Worktree合并命令"""
        # 注意: [^\]:] 匹配任意非']'非':'字符，支持中文Worktree名
        import re

        match = re.search(r"\[WT:MERGE:([^\]:]+)\]", event.content)
        if not match:
            return

        name = match.group(1)
        self.manager.verify_and_merge(name)


# ==================== API请求/响应模型 ====================


class WorktreeCreateRequest(BaseModel):
    """创建Worktree请求"""

    name: str = Field(..., min_length=1, max_length=64)
    branch: str = ""
    agent: str = "澜舟"
    role: str = "maker"  # maker/reviewer/researcher/coordinator
    base_branch: str = "main"
    review_required: bool = True
    reviewer: str = ""


class WorktreeActionRequest(BaseModel):
    """Worktree操作请求"""

    name: str
    action: str  # lock/unlock/merge/remove
    force: bool = False
    reviewer: str = ""
    reason: str = ""


class WorktreeResponse(BaseModel):
    """Worktree响应"""

    name: str
    status: str
    agent: str
    role: str
    branch: str
    reviewer: str = ""
    ports: dict[str, int] = {}
    created_at: str = ""


class ReviewSubmitRequest(BaseModel):
    """审查提交请求"""

    worktree_name: str
    reviewer: str
    verdict: str  # pass/fail
    comments: str = ""
    issues: list[str] = []
