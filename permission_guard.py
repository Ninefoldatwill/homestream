"""
桥v7 权限守卫 — 权限边界声明 + 审计因果链（v7.3+P1安全）

三层分治权限模型：
- L1 基础设施（开源）：只读数据 + 公开API
- L2 协作层（插件）：读写数据 + 受控工具调用
- L3 核心壁垒（自用）：全权限 + 内部API

审计三要素：谁(agent_id) + 何时(timestamp) + 做了什么(action)
所有操作可溯源，利用已有 request_id + structlog 全链路追踪。

来源：6/29六维生态健康冲浪融优 — Erlang/OTP监管者模式 + Bulkhead隔离
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from enum import Enum
from typing import List, Optional, Set

import structlog

logger = structlog.get_logger("bridge_v7.permission_guard")


# ============================================================
# 权限层级定义
# ============================================================


class PermissionLevel(str, Enum):
    """三层分治权限等级"""

    L1_PUBLIC = "L1_PUBLIC"  # 基础设施·公开资源（只读数据+公开API）
    L2_PLUGIN = "L2_PLUGIN"  # 协作层·插件权限（读写数据+受控工具）
    L3_CORE = "L3_CORE"  # 核心壁垒·内部全权限（全API+内部工具）
    ADMIN = "ADMIN"  # 管理员·最高权限（配置变更+安全操作）


class ActionScope(str, Enum):
    """操作范围分类"""

    READ = "READ"  # 读操作
    WRITE = "WRITE"  # 写操作
    EXECUTE = "EXECUTE"  # 工具执行（Skill/MCP调用）
    ADMIN = "ADMIN"  # 管理操作（配置/安全变更）
    SYSTEM = "SYSTEM"  # 系统操作（启动/关闭/健康检查）


# 每个权限等级允许的操作范围
PERMISSION_MATRIX: dict[PermissionLevel, set[ActionScope]] = {
    PermissionLevel.L1_PUBLIC: {ActionScope.READ},
    PermissionLevel.L2_PLUGIN: {ActionScope.READ, ActionScope.WRITE, ActionScope.EXECUTE},
    PermissionLevel.L3_CORE: {
        ActionScope.READ,
        ActionScope.WRITE,
        ActionScope.EXECUTE,
        ActionScope.SYSTEM,
    },
    PermissionLevel.ADMIN: {
        ActionScope.READ,
        ActionScope.WRITE,
        ActionScope.EXECUTE,
        ActionScope.ADMIN,
        ActionScope.SYSTEM,
    },
}


# ============================================================
# Agent/Tool/Skill 权限声明
# ============================================================


@dataclass
class AgentPermission:
    """Agent权限声明卡片"""

    agent_id: str  # Agent唯一标识
    agent_name: str  # Agent名称
    level: PermissionLevel  # 权限等级
    allowed_skills: list[str] = field(default_factory=list)  # 允许使用的Skill列表
    description: str = ""  # 权限说明
    risk_score: float = 0.0  # 风险评分（0-10）


# 预注册的Agent权限配置
REGISTERED_AGENTS: dict[str, AgentPermission] = {
    "bridge_v7": AgentPermission(
        agent_id="bridge_v7",
        agent_name="桥v7服务器",
        level=PermissionLevel.L3_CORE,
        allowed_skills=["*"],  # 通配：所有Skill
        description="核心通讯枢纽·三层模型路由+EventStream引擎",
        risk_score=0.0,
    ),
    "lingxi": AgentPermission(
        agent_id="lingxi",
        agent_name="灵犀·信息咨询",
        level=PermissionLevel.L2_PLUGIN,
        allowed_skills=["web_search", "web_fetch", "data_analysis"],
        description="信息调研·战略分析·公开数据访问",
        risk_score=2.0,
    ),
    "qianxun": AgentPermission(
        agent_id="qianxun",
        agent_name="千寻·书记归档",
        level=PermissionLevel.L2_PLUGIN,
        allowed_skills=["file_write", "archive", "knowledge_query"],
        description="知识管理·文档归档·书阁数据读写",
        risk_score=1.0,
    ),
    "lanlan": AgentPermission(
        agent_id="lanlan",
        agent_name="澜澜·总调度",
        level=PermissionLevel.L2_PLUGIN,
        allowed_skills=["task_assign", "channel_manage", "schedule"],
        description="行政统筹·任务协调·频道管理",
        risk_score=1.5,
    ),
    "external_agent": AgentPermission(
        agent_id="external_agent",
        agent_name="外部Agent（零信任）",
        level=PermissionLevel.L1_PUBLIC,
        allowed_skills=["web_search"],  # 最小权限
        description="外部接入Agent·零信任原则·仅公开只读",
        risk_score=8.0,
    ),
}


# ============================================================
# 权限检查引擎
# ============================================================


@dataclass
class AuditEntry:
    """审计记录"""

    audit_id: str
    agent_id: str
    action: ActionScope
    resource: str
    timestamp: datetime
    request_id: str
    result: str = "ALLOWED"  # ALLOWED / DENIED
    detail: str = ""


_audit_log: list[AuditEntry] = []  # 内存审计日志（生产应持久化到DB）
_audit_max_size = 1000  # 内存审计日志上限


def check_permission(
    agent_id: str,
    action: ActionScope,
    resource: str = "",
    request_id: str = "",
) -> tuple[bool, str]:
    """检查Agent的权限。

    返回: (是否允许, 原因)
    """
    agent = REGISTERED_AGENTS.get(agent_id)
    if agent is None:
        # 未知Agent → 零信任·最小权限（L1只读）
        agent = AgentPermission(
            agent_id=agent_id,
            agent_name="未知Agent",
            level=PermissionLevel.L1_PUBLIC,
            allowed_skills=[],
            description="自动降级至零信任·最小权限",
            risk_score=10.0,
        )

    allowed_scopes = PERMISSION_MATRIX.get(agent.level, set())
    allowed = action in allowed_scopes

    reason = (
        f"ALLOWED: {agent_id}({agent.level.value}) -> {action.value} on {resource}"
        if allowed
        else f"DENIED: {agent_id}({agent.level.value}) 无 {action.value} 权限(需>=L2)"
    )

    # 高风险Agent即使有权也降低信任
    if agent.risk_score >= 8.0 and action in (ActionScope.WRITE, ActionScope.EXECUTE):
        allowed = False
        reason = f"DENIED: {agent_id} 风险评分{agent.risk_score}过高·拒绝{action.value}操作"

    # 记录审计
    _record_audit(
        agent_id, action, resource, request_id, "ALLOWED" if allowed else "DENIED", reason
    )

    return allowed, reason


def check_skill_permission(
    agent_id: str,
    skill_name: str,
    request_id: str = "",
) -> tuple[bool, str]:
    """检查Agent是否允许使用指定Skill。

    返回: (是否允许, 原因)
    """
    agent = REGISTERED_AGENTS.get(agent_id)
    if agent is None:
        return False, f"DENIED: 未知Agent {agent_id} 无Skill权限"

    if "*" in agent.allowed_skills:
        return True, f"ALLOWED: {agent_id} 有通配Skill权限"

    if skill_name in agent.allowed_skills:
        return True, f"ALLOWED: {agent_id} -> Skill {skill_name}"

    return False, f"DENIED: {agent_id} 无Skill {skill_name} 权限"


def _record_audit(
    agent_id: str,
    action: ActionScope,
    resource: str,
    request_id: str,
    result: str,
    detail: str,
):
    """记录审计日志"""
    entry = AuditEntry(
        audit_id=str(uuid.uuid4())[:8],
        agent_id=agent_id,
        action=action,
        resource=resource,
        timestamp=datetime.now(UTC),
        request_id=request_id,
        result=result,
        detail=detail,
    )
    _audit_log.append(entry)

    # 内存审计日志上限保护
    while len(_audit_log) > _audit_max_size:
        _audit_log.pop(0)

    # structlog记录
    log_level = "warning" if result == "DENIED" else "info"
    getattr(logger, log_level)(
        "permission_check",
        audit_id=entry.audit_id,
        agent_id=agent_id,
        action=action.value,
        resource=resource,
        result=result,
        request_id=request_id,
    )


def get_audit_trail(
    agent_id: str = "",
    action: ActionScope | None = None,
    limit: int = 50,
) -> list[AuditEntry]:
    """查询审计追踪链（因果链）。"""
    filtered = _audit_log
    if agent_id:
        filtered = [e for e in filtered if e.agent_id == agent_id]
    if action:
        filtered = [e for e in filtered if e.action == action]
    return filtered[-limit:]


def get_permission_boundary_report() -> dict:
    """生成权限边界状态报告"""
    return {
        "model": "三层分治(L1公开/L2插件/L3核心/ADMIN管理)",
        "registered_agents": len(REGISTERED_AGENTS),
        "agents": {
            aid: {
                "name": ap.agent_name,
                "level": ap.level.value,
                "risk_score": ap.risk_score,
                "allowed_skills_count": len(ap.allowed_skills),
            }
            for aid, ap in REGISTERED_AGENTS.items()
        },
        "recent_audits": len(_audit_log),
        "recent_denials": sum(1 for e in _audit_log[-20:] if e.result == "DENIED"),
    }
