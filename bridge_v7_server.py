"""
OpenBridge V8 - 多模态生态核心（FastAPI实现）

架构：三层模型路由 + 双重保障 + 六维生态健康 + 多模态模块
版号：8.0.0　双线分治：开源线(普大众化) + 自用线(核心壁垒)

V8核心能力：
- 一=本地AI根基 (Qwen2.5-7B L1层)
- 二=双保障切换 (主线路+DeepSeek复线)
- 三=三层路由 (L1本地→L2 GLM→L3 DeepSeek)
- 六维生态健康：安全防线·权限沙箱·知识防腐·版本回滚·资源韧性·生态循环
- 多模态：STT语音·TTS播报·OCR提取·Vision图像理解
- ICP协议 + EventStream + 弹性模式(Solo/Team/Ecosystem)
- 420测试全通过·0失败

v6兼容层：保留v6全部API端点
"""

import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog
from fastapi import Body, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from actions import (
    create_assign_task,
    create_handoff,
    create_query_knowledge,
    create_review,
    create_update_learning,
)
from config import AGENT_TOKENS, V6_DB_PATH, V7_DB_PATH, settings
from event_store import EventStore, make_persistent_stream
from event_stream import (
    Action,
    Event,
    EventSource,
    EventStream,
    EventType,
    Observation,
    _gen_event_id,
    create_action,
    create_ask_action,
    create_done_action,
    create_task_action,
    create_warn_action,
    parse_handoff_text,
    parse_icp_message,
)
from group_chat import CHANNELS, GROUP_MEMBERS, GroupChatManager
from logging_config import configure_logging
from middleware import setup_observability
from model_router import ModelRouter, RouterStrategy
from modes import DeployMode, get_mode_config, get_mode_info, switch_mode  # 6/27弹性模式
from observations import (
    create_error_obs,
    create_message_received,
    create_security_obs,
    create_task_assigned,
    create_task_done_obs,
)
from observatory import collect_observatory_data  # 可观测性数据聚合·7/8
from permission_guard import (  # P1权限守卫·6/30
    REGISTERED_AGENTS,
    ActionScope,
    check_permission,
    check_skill_permission,
    get_audit_trail,
    get_permission_boundary_report,
)
from prompt_security import (
    build_safe_prompt,
    sanitize_user_input,
    validate_icp_content,
)  # P0安全注入·6/29
from providers.base_provider import ChatMessage, ProviderTier
from rate_limiter import RateLimitMiddleware, get_limiter_for_endpoint  # P1限流保护·6/30
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
from worktree_subscribers import (
    ReviewerSubscriber,
    ReviewSubmitRequest,
    WorktreeActionRequest,
    WorktreeCreateRequest,
    WorktreeResponse,
    WorktreeSubscriber,
)
from ws_manager import ConnectionManager

# ─── 运行模式检测 ────────────────────────────────────────
# 物理隔离：通过 .openbridge_mode 文件控制
# "team"=完整书阁3460·"opensource"=静态知识快照
_mode_file = os.path.join(os.path.dirname(__file__), ".openbridge_mode")
if os.path.exists(_mode_file):
    with open(_mode_file, encoding="utf-8") as _f:
        _mode = _f.read().strip()
        if _mode in ("opensource", "team", "private"):
            os.environ["OPENBRIDGE_MODE"] = _mode
            print(f"[Mode] OPENBRIDGE_MODE={_mode} (from .openbridge_mode)")

from bookhouse_client import (
    add_book,
    get_book,
    get_building,
    list_tags,
)
from bookhouse_client import (
    get_stats as book_stats,
)
from bookhouse_client import (
    health_check as book_health,
)
from bookhouse_client import (  # 书阁知识桥梁·6/30
    search as book_search,
)

# P1模块整合·6/30: bridge_v7_adapter 使用延迟导入(_get_adapter中)避免skillopt依赖问题

# 结构化日志
logger = structlog.get_logger("bridge_v7.server")


# ==================== FastAPI应用 ====================

app = FastAPI(
    title="OpenBridge V8 API",
    description="九重生态 V8 多模态版——双线分治·开源+自用·420测试全绿灯",
    version="8.0.0",
)

# 注册可观测性中间件（请求上下文 + Prometheus指标）
setup_observability(app)

# 注册限流中间件（令牌桶 + 滑动窗口 · P1生态健康·6/30）
app.add_middleware(RateLimitMiddleware)


# ==================== 全局状态 ====================

# EventStore 持久化实例（v7.1 新增）
_event_store: EventStore | None = None

# EventStream引擎实例（改为持久化版本）
streams: dict[str, EventStream] = {}

# DB路径从config.py加载（不再硬编码）
DB_PATH = V6_DB_PATH

# 任务状态机（融合agent-team-orchestration）
VALID_TRANSITIONS = {
    "inbox": ["assigned"],
    "assigned": ["in_progress", "inbox"],
    "in_progress": ["review", "done", "failed"],
    "review": ["done", "failed", "in_progress"],
    "done": [],
    "failed": ["in_progress"],
}

# 任务存储（内存版，后续可持久化）
tasks: dict[str, dict[str, Any]] = {}

# Worktree管理器实例（Day 2新增）
worktree_manager = WorktreeManager()

# WebSocket连接管理器（v7.1新增）
_ws_manager: ConnectionManager | None = None

# 群聊管理器实例（v7.2新增 - 统一通讯）
_group_chat_manager: GroupChatManager | None = None

# BridgeV7Adapter 单例（V8模块整合 - SkillOpt融合枢纽）
_adapter_instance = None  # 延迟类型标注，避免skillopt依赖


def _get_adapter():
    """获取或创建BridgeV7Adapter单例（延迟导入·避免skillopt依赖阻断启动）"""
    global _adapter_instance
    if _adapter_instance is None:
        from bridge_v7_adapter import BridgeV7Config, create_bridge_v7_adapter  # 延迟导入

        cfg = BridgeV7Config(
            event_stream_persist=True,
            event_store_path=V7_DB_PATH.replace(".db", "") + "_adapter_events.db",
            rollout_timeout=120.0,
        )
        _adapter_instance = create_bridge_v7_adapter(config=cfg)
    return _adapter_instance


def _get_ws_manager() -> ConnectionManager:
    """获取或创建WebSocket连接管理器单例"""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = ConnectionManager()
    return _ws_manager


def _ensure_group_chat() -> GroupChatManager:
    """获取或创建群聊管理器单例（内部调用，同步接口）"""
    global _group_chat_manager
    if _group_chat_manager is None:
        _group_chat_manager = GroupChatManager(
            db_path=V7_DB_PATH,
            ws_manager=_get_ws_manager(),
            event_stream_fn=get_or_create_stream,
        )
    return _group_chat_manager


def get_group_chat_manager() -> GroupChatManager:
    """获取群聊管理器（API端点入口）"""
    return _ensure_group_chat()


# ModelRouter实例（v7.2新增 - 硬件自适应多模型路由）
# 延迟初始化：首次调用时才执行auto_init_from_env()，避免模块加载时的异步操作影响测试
model_router = ModelRouter(strategy=RouterStrategy.COST_FIRST)


# ==================== 辅助函数 ====================


def get_db():
    """获取DB连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        sender TEXT,
        recipient TEXT,
        content TEXT,
        channel TEXT,
        created_at TEXT
    )""")
    conn.commit()
    return conn


def get_or_create_stream(session_id: str = "default") -> EventStream:
    """获取或创建 EventStream（v7.1：使用持久化版本）"""
    if session_id not in streams:
        # 使用 make_persistent_stream 创建带 SQLite 持久化的 stream
        # replay=True 表示从 DB 恢复历史事件
        stream = make_persistent_stream(
            session_id=session_id,
            db_path=V7_DB_PATH,
            replay=True,
        )
        # 注册默认订阅者
        _register_default_subscribers(stream)
        streams[session_id] = stream
    return streams[session_id]


def _register_default_subscribers(stream: EventStream):
    """注册默认的事件订阅者

    融优主义：每个Subscriber有独立职责
    - KanbanSubscriber: 监听TASK/DONE → 自动创建/更新Kanban任务
    - LearningSubscriber: 监听trigger_learning → 自动记录.learnings/
    - SecuritySubscriber: 监听WARN → 安全审查
    """

    def kanban_subscriber(event: Event):
        """Kanban订阅者：TASK→创建任务 / DONE→更新状态

        优雅降级：Kanban服务未启动时仅记录日志，不影响主流程。
        """
        try:
            import requests as http_requests

            if event.event_type == EventType.TASK:
                # TASK事件 → 创建Kanban任务
                payload = {
                    "title": event.content[:200] if event.content else f"Task from {event.sender}",
                    "body": f"EventStream TASK事件\n发送者: {event.sender}\n接收者: {event.recipient}\n内容: {event.content}",
                    "assignee": event.recipient if event.recipient != "all" else None,
                    "source_message_id": event.event_id,
                }
                resp = http_requests.post(
                    f"{KANBAN_API_URL}/kanban/tasks",
                    json=payload,
                    timeout=5,
                )
                if resp.status_code in (200, 201):
                    task_data = resp.json()
                    task_id = (
                        task_data.get("id") or task_data.get("task_id") or task_data.get("uuid", "")
                    )
                    if task_id:
                        kanban_task_map[event.event_id] = str(task_id)
                    logger.info(
                        "kanban_task_created", event_id=event.event_id, kanban_task_id=task_id
                    )
                else:
                    logger.warning(
                        "kanban_create_failed", status=resp.status_code, event_id=event.event_id
                    )

            elif event.event_type == EventType.DONE:
                # DONE事件 → 通过因果链找到TASK事件的event_id → 查映射得到task_id → 更新状态
                task_event_id = event.cause
                kanban_task_id = kanban_task_map.get(task_event_id, "") if task_event_id else ""

                if not kanban_task_id:
                    # 因果链断裂或无映射 → 尝试用content中的关键词搜索
                    logger.debug(
                        "kanban_done_no_mapping", event_id=event.event_id, cause=task_event_id
                    )
                    return

                # 更新Kanban任务状态为done
                resp = http_requests.patch(
                    f"{KANBAN_API_URL}/kanban/tasks/{kanban_task_id}",
                    json={"status": "done", "done_by": event.sender},
                    timeout=5,
                )
                if resp.status_code in (200, 204):
                    logger.info(
                        "kanban_task_done", kanban_task_id=kanban_task_id, done_by=event.sender
                    )
                    # 清理映射
                    kanban_task_map.pop(task_event_id, None)
                else:
                    logger.warning(
                        "kanban_update_failed",
                        status=resp.status_code,
                        kanban_task_id=kanban_task_id,
                    )

        except http_requests.exceptions.ConnectionError:
            # Kanban服务未启动 → 静默降级（开源版常见场景）
            logger.debug("kanban_service_unavailable", event_type=event.event_type.value)
        except Exception as e:
            logger.error("kanban_subscriber_error", error=str(e), event_id=event.event_id)

    def learning_subscriber(event: Event):
        """学习订阅者：trigger_learning=True → 记录.learnings/"""
        if event.trigger_learning and event.learning_type:
            learning_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".learnings")
            os.makedirs(learning_dir, exist_ok=True)

            file_map = {
                "error": "ERRORS.md",
                "correction": "LEARNINGS.md",
                "best_practice": "LEARNINGS.md",
                "feature_request": "FEATURE_REQUESTS.md",
            }
            filename = file_map.get(event.learning_type, "LEARNINGS.md")
            filepath = os.path.join(learning_dir, filename)

            # 追加记录
            entry = f"\n- [{event.learning_type}] {event.content} (from: {event.sender}, {datetime.now().strftime('%Y-%m-%d %H:%M')})"
            try:
                with open(filepath, "a", encoding="utf-8") as f:
                    f.write(entry)
            except Exception as e:
                logger.error("learning_subscriber_write_failed", error=str(e))

    # 注册类型订阅
    stream.subscribe_by_type(EventType.TASK, kanban_subscriber)
    stream.subscribe_by_type(EventType.DONE, kanban_subscriber)
    stream.subscribe_by_type(EventType.WARN, learning_subscriber)
    stream.subscribe_by_type(EventType.DONE, learning_subscriber)


def resolve_agent(token: str | None = None, agent_name: str | None = None) -> str:
    """从token或agent_name解析Agent名称"""
    if token and token in AGENT_TOKENS:
        return AGENT_TOKENS[token]
    if agent_name:
        return agent_name
    return "Unknown"


def persist_event_to_db(event: Event) -> bool:
    """将Event持久化到SQLite（兼容v6 messages表）"""
    try:
        conn = get_db()
        conn.execute(
            """INSERT INTO messages (id, sender, recipient, content, channel, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.sender,
                event.recipient,
                json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                if event.handoff
                else event.content,
                "v7_event",
                event.timestamp.isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("persist_event_failed", error=str(e))
        return False


# ==================== 请求/响应模型 ====================


class SendMessageRequest(BaseModel):
    """发送消息请求（兼容v6 API）"""

    content: str
    recipient: str
    sender: str | None = None
    token: str | None = None


class EventResponse(BaseModel):
    """事件响应"""

    event_id: str
    event_type: str
    sender: str
    recipient: str
    content: str
    timestamp: str
    cause: str | None = None
    handoff: dict[str, Any] | None = None
    confidence: float | None = None


class TaskLifecycleRequest(BaseModel):
    """任务生命周期转换请求"""

    task_id: str
    from_state: str
    to_state: str
    comment: str | None = None
    changed_by: str | None = None


class HandoffRequest(BaseModel):
    """Handoff请求（5要素）"""

    task_id: str
    builder_agent: str
    reviewer_agent: str
    what_done: str
    where_artifacts: list[str] = []
    how_verify: str = ""
    known_issues: list[str] = []
    what_next: str = ""
    confidence: float | None = None


class EventsQueryParams(BaseModel):
    """事件查询参数"""

    session_id: str = "default"
    agent_name: str | None = None
    event_type: str | None = None
    limit: int = 50


# ==================== V8 Dashboard HTML ====================

V8_DASHBOARD = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>OpenBridge V8 · 多模态生态仪表盘</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--text:#1a1a2e;--text2:#5a6a7e;--border:#e2e8f0;
--accent:#4a90d9;--accent2:#7c5ce0;--green:#22c55e;--red:#ef4444;--yellow:#f59e0b;
--cyan:#06b6d4;--pink:#ec4899;--shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
--shadow-lg:0 10px 25px rgba(0,0,0,.08);--radius:12px;--radius-sm:8px}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--text);min-height:100vh;line-height:1.6}
a{color:var(--accent);text-decoration:none}

.topbar{background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460);color:#fff;padding:0 28px;
display:flex;align-items:center;height:60px;gap:16px;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(0,0,0,.15)}
.topbar .logo{font-size:22px;font-weight:700;letter-spacing:-.5px;display:flex;align-items:center;gap:8px}
.topbar .logo .icon{font-size:28px}
.topbar .ver{font-size:11px;background:rgba(255,255,255,.2);padding:2px 8px;border-radius:10px;margin-left:4px}
.topbar .nav{display:flex;gap:4px;margin-left:auto;align-items:center}
.topbar .nav a{padding:7px 14px;border-radius:8px;color:rgba(255,255,255,.75);font-size:13px;transition:all .2s}
.topbar .nav a:hover{background:rgba(255,255,255,.1);color:#fff}
.topbar .nav a.active{background:var(--accent);color:#fff}
.topbar .status-pill{display:flex;align-items:center;gap:6px;font-size:12px;background:rgba(34,197,94,.15);
padding:5px 12px;border-radius:20px;color:var(--green);margin-left:12px}
.topbar .status-pill .dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

.main{max-width:1400px;margin:0 auto;padding:24px 28px;display:grid;
grid-template-columns:1fr 320px;grid-template-rows:auto auto;gap:20px}

.mode-bar{grid-column:1/-1;display:flex;align-items:center;gap:16px;background:var(--card);
padding:14px 20px;border-radius:var(--radius);box-shadow:var(--shadow);flex-wrap:wrap}
.mode-bar .label{font-size:13px;color:var(--text2);font-weight:600;margin-right:4px}
.mode-selector{display:flex;gap:2px;background:var(--bg);border-radius:10px;padding:3px}
.mode-opt{padding:9px 20px;border-radius:8px;font-size:13px;cursor:pointer;transition:all .2s;border:none;
background:transparent;color:var(--text2);font-family:inherit;font-weight:500}
.mode-opt:hover{color:var(--text);background:rgba(74,144,217,.08)}
.mode-opt.active{background:var(--accent);color:#fff;box-shadow:0 2px 8px rgba(74,144,217,.3)}
.mode-opt.solo.active{background:var(--cyan)}
.mode-opt.team.active{background:var(--accent)}
.mode-opt.eco.active{background:var(--accent2)}
.mode-info{margin-left:auto;font-size:12px;color:var(--text2);display:flex;align-items:center;gap:6px}
.mode-info .badge{font-size:11px;padding:3px 10px;border-radius:12px;font-weight:600}
.mode-info .badge.solo{background:#e0f2fe;color:#0369a1}
.mode-info .badge.team{background:#dbeafe;color:#1d4ed8}
.mode-info .badge.eco{background:#ede9fe;color:#6d28d9}

.stats-row{grid-column:1;display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.stat-card{background:var(--card);border-radius:var(--radius);padding:18px 20px;box-shadow:var(--shadow);
display:flex;flex-direction:column;gap:6px;transition:transform .15s,box-shadow .15s;cursor:default}
.stat-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg)}
.stat-card .stat-label{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;font-weight:600}
.stat-card .stat-value{font-size:28px;font-weight:700;line-height:1.2}
.stat-card .stat-sub{font-size:11px;color:var(--text2)}
.stat-card.safety{border-left:3px solid var(--green)}
.stat-card.models{border-left:3px solid var(--accent)}
.stat-card.events{border-left:3px solid var(--accent2)}
.stat-card.multi{border-left:3px solid var(--pink)}

.content-grid{grid-column:1;display:grid;grid-template-columns:1fr 1fr;gap:16px;align-content:start}
.panel{background:var(--card);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}
.panel-header{padding:14px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;
font-weight:600;font-size:14px}
.panel-header .icon{font-size:18px}
.panel-body{padding:16px 20px;max-height:320px;overflow-y:auto}

.sidebar{grid-column:2;grid-row:2/4;display:flex;flex-direction:column;gap:16px}

.agent-card{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border)}
.agent-card:last-child{border-bottom:none}
.agent-avatar{width:38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center;
font-size:18px;font-weight:700;color:#fff;flex-shrink:0}
.agent-avatar.lead{background:linear-gradient(135deg,#f59e0b,#ef4444)}
.agent-avatar.coord{background:linear-gradient(135deg,#4a90d9,#7c5ce0)}
.agent-avatar.info{background:linear-gradient(135deg,#06b6d4,#3b82f6)}
.agent-avatar.dev{background:linear-gradient(135deg,#22c55e,#15803d)}
.agent-avatar.scribe{background:linear-gradient(135deg,#8b5cf6,#6d28d9)}
.agent-info{flex:1;min-width:0}
.agent-info .name{font-weight:600;font-size:13px}
.agent-info .role{font-size:11px;color:var(--text2)}
.agent-status{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600}
.agent-status.online{background:#dcfce7;color:#15803d}
.agent-status.busy{background:#fef3c7;color:#b45309}

.quick-launch{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.ql-card{display:flex;align-items:center;gap:10px;padding:14px;border-radius:var(--radius-sm);
background:var(--bg);transition:all .2s;cursor:pointer;border:2px solid transparent;text-decoration:none;color:inherit}
.ql-card:hover{border-color:var(--accent);background:rgba(74,144,217,.05)}
.ql-card .ql-icon{font-size:24px;width:40px;height:40px;display:flex;align-items:center;justify-content:center;
border-radius:10px;flex-shrink:0}
.ql-card .ql-text{font-size:13px;font-weight:600}
.ql-card .ql-sub{font-size:11px;color:var(--text2)}
.ql-icon.chat{background:#dbeafe;color:#1d4ed8}
.ql-icon.meet{background:#fce7f3;color:#be185d}
.ql-icon.exp{background:#fef3c7;color:#b45309}
.ql-icon.safe{background:#dcfce7;color:#15803d}
.ql-icon.multi{background:#ede9fe;color:#6d28d9}
.ql-icon.skills{background:#e0f2fe;color:#0369a1}
.ql-icon.bh{background:#fef9c3;color:#a16207}

.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);
z-index:200;align-items:center;justify-content:center}
.modal-overlay.show{display:flex}
.modal{background:var(--card);border-radius:var(--radius);padding:24px;max-width:600px;width:90%;
max-height:80vh;overflow-y:auto;box-shadow:var(--shadow-lg)}
.modal h3{margin-bottom:12px;font-size:16px}
.modal .close{float:right;background:none;border:none;font-size:20px;cursor:pointer;color:var(--text2)}
.event-item{padding:8px 0;border-bottom:1px solid var(--border);font-size:12px}
.event-item .evt-type{font-weight:600;color:var(--accent);display:inline-block;min-width:50px}
.event-item .evt-time{color:var(--text2);float:right}
.multi-status{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.multi-chip{display:flex;align-items:center;gap:6px;font-size:12px;padding:6px 10px;border-radius:var(--radius-sm);
background:var(--bg)}
.multi-chip .status-dot{width:7px;height:7px;border-radius:50%}
.multi-chip .status-dot.ready{background:var(--green)}
.multi-chip .status-dot.pending{background:var(--yellow)}
.loading{text-align:center;padding:20px;color:var(--text2);font-size:13px}
.empty{text-align:center;padding:20px;color:var(--text2);font-size:13px;font-style:italic}

@media(max-width:900px){
.main{grid-template-columns:1fr;padding:16px}
.sidebar{grid-column:1;grid-row:auto}
.stats-row{grid-template-columns:1fr 1fr}
.content-grid{grid-template-columns:1fr}
.mode-bar{flex-wrap:wrap}
}
</style>
</head>
<body>

<header class="topbar">
<div class="logo"><span class="icon">⚓</span>OpenBridge<span class="ver">V8.0.0</span></div>
<nav class="nav">
<a href="/" class="active">仪表盘</a>
<a href="/meeting" target="_blank">会议室</a>
<a href="/lingxi" target="_blank">灵犀</a>
<a href="/observatory" target="_blank">观测台</a>
<a href="/docs" target="_blank">API文档</a>
</nav>
<div class="status-pill" id="statusPill"><span class="dot"></span><span id="statusText">运行中</span></div>
</header>

<div class="main">

<div class="mode-bar">
<span class="label">弹性模式</span>
<div class="mode-selector" id="modeSelector">
<button class="mode-opt solo" data-mode="solo" onclick="switchMode('solo')">🛡️ Solo</button>
<button class="mode-opt team active" data-mode="team" onclick="switchMode('team')">👥 Team</button>
<button class="mode-opt eco" data-mode="eco" onclick="switchMode('ecosystem')">🌐 Ecosystem</button>
</div>
<div class="mode-info">
<span id="modeFeatures"></span>
<span class="badge team" id="modeBadge">Team</span>
</div>
</div>

<div class="stats-row">
<div class="stat-card safety" id="cardSafety">
<div class="stat-label">安全状态</div>
<div class="stat-value" id="statSafety">--</div>
<div class="stat-sub">权限守卫·P0注入·限流</div>
</div>
<div class="stat-card models" id="cardModels">
<div class="stat-label">模型路由</div>
<div class="stat-value" id="statModels">--</div>
<div class="stat-sub">L1本地·L2 GLM·L3 DeepSeek</div>
</div>
<div class="stat-card events">
<div class="stat-label">活跃事件</div>
<div class="stat-value" id="statEvents">--</div>
<div class="stat-sub">EventStream · ICP协议</div>
</div>
<div class="stat-card multi">
<div class="stat-label">多模态</div>
<div class="stat-value" id="statMulti">--</div>
<div class="stat-sub">STT·TTS·OCR·Vision</div>
</div>
</div>

<div class="content-grid">
<div class="panel">
<div class="panel-header"><span class="icon">📋</span>最近事件流</div>
<div class="panel-body" id="eventsPanel"><div class="loading">加载中...</div></div>
</div>
<div class="panel">
<div class="panel-header"><span class="icon">🧪</span>实验工坊</div>
<div class="panel-body" id="experimentsPanel"><div class="loading">加载中...</div></div>
</div>
</div>

<div class="sidebar">
<div class="panel">
<div class="panel-header"><span class="icon">👥</span>团队阵容</div>
<div class="panel-body" id="agentsPanel"></div>
</div>

<div class="panel">
<div class="panel-header"><span class="icon">🚀</span>快速入口</div>
<div class="panel-body">
<div class="quick-launch">
<a href="/meeting" target="_blank" class="ql-card">
<div class="ql-icon meet">🏛️</div><div><div class="ql-text">全员会议室</div><div class="ql-sub">群聊·通知·广播</div></div>
</a>
<a href="/lingxi" target="_blank" class="ql-card">
<div class="ql-icon chat">🐚</div><div><div class="ql-text">灵犀对话</div><div class="ql-sub">信息咨询·战略分析</div></div>
</a>
<a href="/observatory" target="_blank" class="ql-card">
<div class="ql-icon" style="background:rgba(31,111,86,.2);font-size:24px">📊</div><div><div class="ql-text">可观测性仪表盘</div><div class="ql-sub">8面板·实时监控</div></div>
</a>
<div class="ql-card" onclick="openExpModal()">
<div class="ql-icon exp">🧪</div><div><div class="ql-text">Ratchet实验</div><div class="ql-sub">Maker + Reviewer</div></div>
</div>
<div class="ql-card" onclick="openSafetyModal()">
<div class="ql-icon safe">🛡️</div><div><div class="ql-text">安全面板</div><div class="ql-sub">权限·审计·限流</div></div>
</div>
<div class="ql-card" onclick="openMultiModal()">
<div class="ql-icon multi">🎙️</div><div><div class="ql-text">多模态中心</div><div class="ql-sub">STT·TTS·OCR·Vision</div></div>
</div>
<div class="ql-card" onclick="openSkillsModal()">
<div class="ql-icon skills">⚡</div><div><div class="ql-text">技能路由</div><div class="ql-sub">Skill执行·路由映射</div></div>
</div>
<div class="ql-card" onclick="openBookhouseModal()">
<div class="ql-icon bh">📚</div><div><div class="ql-text">知识书阁</div><div class="ql-sub">50本书·9大阁楼·FTS5搜索</div></div>
</div>
</div>
</div>
</div>

<div class="panel">
<div class="panel-header"><span class="icon">📊</span>模型健康</div>
<div class="panel-body">
<div class="multi-status" id="modelHealthPanel"></div>
</div>
</div>

<div class="panel">
<div class="panel-header"><span class="icon">📚</span>知识书阁 <span style="font-size:11px;color:var(--accent);margin-left:6px">· 3460</span></div>
<div class="panel-body">
<div class="multi-status" id="bookhousePanel"></div>
</div>
</div>
</div>
</div>

<div class="modal-overlay" id="safetyModal">
<div class="modal">
<button class="close" onclick="closeModal('safetyModal')">&times;</button>
<h3>🛡️ 安全面板</h3>
<div id="safetyContent"><div class="loading">加载中...</div></div>
</div>
</div>

<div class="modal-overlay" id="multiModal">
<div class="modal">
<button class="close" onclick="closeModal('multiModal')">&times;</button>
<h3>🎙️ 多模态中心</h3>
<div id="multiContent"><div class="loading">加载中...</div></div>
</div>
</div>

<div class="modal-overlay" id="skillsModal">
<div class="modal">
<button class="close" onclick="closeModal('skillsModal')">&times;</button>
<h3>⚡ 技能路由</h3>
<div id="skillsContent"><div class="loading">加载中...</div></div>
</div>
</div>

<div class="modal-overlay" id="expModal">
<div class="modal">
<button class="close" onclick="closeModal('expModal')">&times;</button>
<h3>🧪 Ratchet Loop 实验工坊</h3>
<div id="expContent"><div class="loading">加载中...</div></div>
</div>
</div>

<div class="modal-overlay" id="bookhouseModal">
<div class="modal" style="max-width:720px">
<button class="close" onclick="closeModal('bookhouseModal')">&times;</button>
<h3>📚 知识书阁</h3>
<div style="margin-bottom:12px">
<input type="text" id="bhSearchInput" placeholder="搜索50本书·120个标签·FTS5全文搜索..."
style="width:100%;padding:10px 14px;border:1px solid var(--border);border-radius:8px;background:var(--bg2);color:var(--text);font-size:14px"
onkeyup="if(event.key==='Enter')bookhouseSearch()">
</div>
<div style="display:flex;gap:12px;margin-bottom:14px">
<button onclick="bookhouseSearch()" style="padding:8px 20px;background:var(--accent);color:#fff;border:none;border-radius:6px;cursor:pointer;font-weight:600">🔍 搜索</button>
<button onclick="bookhouseLoadTags()" style="padding:8px 16px;background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:6px;cursor:pointer">🏷️ 标签</button>
<button onclick="bookhouseLoadBuildings()" style="padding:8px 16px;background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:6px;cursor:pointer">🏛️ 阁楼</button>
</div>
<div id="bhResults" style="max-height:400px;overflow-y:auto">
<div class="loading">输入关键词，搜索九重书阁...</div>
</div>
<div id="bhStats" style="margin-top:12px;padding:10px;background:var(--bg2);border-radius:8px;font-size:12px;color:var(--muted);display:flex;gap:16px"></div>
</div>
</div>

<script>
const API = "";

async function fetchJSON(url) {
try{const r=await fetch(API+url);if(!r.ok)return null;return await r.json()}
catch(e){return null}
}

function fmtTime(ts){if(!ts)return"--";const d=new Date(ts);return d.toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",second:"2-digit"})}
function fmtDate(ts){if(!ts)return"--";return new Date(ts).toLocaleDateString("zh-CN")}

// --- Mode switching ---
let currentMode = "team";
async function switchMode(mode){
currentMode = mode;
document.querySelectorAll(".mode-opt").forEach(b=>b.classList.remove("active"));
document.querySelector(`.mode-opt[data-mode="${mode}"]`)?.classList.add("active");
const badge = document.getElementById("modeBadge");
badge.textContent = mode==="solo"?"Solo":mode==="team"?"Team":"Ecosystem";
badge.className = "badge "+mode;
try{
const r=await fetch(API+"/api/mode/switch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:mode})});
const d=await r.json();
if(d.features)document.getElementById("modeFeatures").textContent=d.features.map(f=>f.name).join(" · ");
}catch(e){}
}

// --- Refresh stats ---
async function refreshStats(){
const health = await fetchJSON("/health");
if(health){
document.getElementById("statusText").textContent = health.status==="ok"?"运行中":"异常";
document.getElementById("statEvents").textContent = health.active_streams||0;
}

const mStatus = await fetchJSON("/api/v7/models/status");
if(mStatus){
const tiers = mStatus.tiers||{};
const total = Object.keys(tiers).length;
document.getElementById("statModels").textContent = total+"层";
}

const permissions = await fetchJSON("/api/v7/permissions");
if(permissions){
document.getElementById("statSafety").textContent = permissions.agents_count||"0";
}

const mmStatus = await fetchJSON("/api/v8/multimodal/status");
if(mmStatus){
const providers = mmStatus.providers||{};
const ready = Object.values(providers).filter(p=>p.available).length;
document.getElementById("statMulti").textContent = ready+"/4";
}
}

// --- Render team ---
async function renderAgents(){
const agents = [
{name:"九重",role:"总规划·战略决策",cls:"lead",emoji:"👑"},
{name:"澜澜",role:"总调度·行政统筹",cls:"coord",emoji:"🌊"},
{name:"灵犀",role:"信息咨询·战略分析",cls:"info",emoji:"🐚"},
{name:"澜舟",role:"开发实施·系统架构",cls:"dev",emoji:"⚓"},
{name:"千寻",role:"知识管理·文档归档",cls:"scribe",emoji:"📜"}
];
let html = "";
for(const a of agents){
html += `<div class="agent-card">
<div class="agent-avatar ${a.cls}">${a.emoji}</div>
<div class="agent-info"><div class="name">${a.name}</div><div class="role">${a.role}</div></div>
<span class="agent-status online">在线</span></div>`;
}
document.getElementById("agentsPanel").innerHTML = html;
}

// --- Render events ---
async function renderEvents(){
const data = await fetchJSON("/api/v7/events?limit=8");
const panel = document.getElementById("eventsPanel");
if(!data||!data.events||!data.events.length){panel.innerHTML='<div class="empty">暂无事件</div>';return}
let html = "";
for(const evt of data.events.slice(0,8)){
const typeColors = {INFO:"#4a90d9",TASK:"#f59e0b",DONE:"#22c55e",WARN:"#ef4444",ASK:"#7c5ce0",UPD:"#06b6d4"};
const color = typeColors[evt.event_type]||"#999";
html += `<div class="event-item"><span class="evt-type" style="color:${color}">${evt.event_type||"--"}</span>
${(evt.content||"").substring(0,60)}<span class="evt-time">${fmtTime(evt.timestamp)}</span></div>`;
}
panel.innerHTML = html;
}

// --- Render experiments ---
async function renderExperiments(){
const data = await fetchJSON("/api/v7/experiment/list");
const panel = document.getElementById("experimentsPanel");
if(!data||!data.experiments||!data.experiments.length){panel.innerHTML='<div class="empty">暂无实验记录</div>';return}
let html = "";
for(const exp of data.experiments.slice(0,5)){
html += `<div class="event-item"><span class="evt-type" style="color:#f59e0b">🧪</span>
${exp.name||exp.id||"--"}<span class="evt-time">${fmtDate(exp.created_at)}</span></div>`;
}
panel.innerHTML = html;
}

// --- Render model health ---
async function renderModelHealth(){
const data = await fetchJSON("/api/v7/models/health");
const panel = document.getElementById("modelHealthPanel");
if(!data){panel.innerHTML='<div class="loading">无法获取</div>';return}
const tiers = data.tiers||{};
let html = "";
for(const [name,info] of Object.entries(tiers)){
const status = info.status||info;
const ready = status==="ready"||status==="healthy"||status===true;
html += `<div class="multi-chip"><span class="status-dot ${ready?'ready':'pending'}"></span>
<span style="font-weight:600;font-size:12px">${name}</span>
<span style="font-size:11px;color:var(--text2);margin-left:auto">${ready?'就绪':'待命'}</span></div>`;
}
if(!html)html='<div class="empty">等待模型初始化</div>';
panel.innerHTML = html;
}

// --- Modal content loaders ---
async function openSafetyModal(){
document.getElementById("safetyModal").classList.add("show");
const permissions = await fetchJSON("/api/v7/permissions");
const audit = await fetchJSON("/api/v7/audit?limit=10");
const limits = await fetchJSON("/api/v7/rate-limits");
let html = "<div style='font-size:13px'>";
if(permissions){
html+="<p style='margin-bottom:8px'><b>权限边界：</b>"+(permissions.agents_count||0)+" Agent注册</p>";
html+="<p style='margin-bottom:8px'><b>Levels：</b>"+JSON.stringify(permissions.permission_levels||{})+"</p>";
}
if(limits){
html+="<p style='margin-bottom:8px'><b>限流状态：</b>"+JSON.stringify(limits.limiters||{})+"</p>";
}
if(audit&&audit.entries){
html+="<p style='margin-bottom:8px'><b>审计追踪（最近）：</b></p>";
for(const e of audit.entries.slice(0,5)){
html+=`<div class="event-item"><span class="evt-type">${e.action||"--"}</span>${e.agent||"--"}<span class="evt-time">${fmtTime(e.timestamp)}</span></div>`;
}
}
html+="</div>";
document.getElementById("safetyContent").innerHTML = html||"<div class='empty'>无数据</div>";
}

async function openMultiModal(){
document.getElementById("multiModal").classList.add("show");
const data = await fetchJSON("/api/v8/multimodal/status");
let html = "";
if(data&&data.providers){
const names = {stt:"🎤 语音识别 STT",tts:"🔊 语音合成 TTS",ocr:"📷 文字提取 OCR",vision:"👁️ 图像理解 Vision"};
for(const [k,v] of Object.entries(data.providers)){
const ok = v.available;
html+=`<div class="multi-chip"><span class="status-dot ${ok?'ready':'pending'}"></span>
<b>${names[k]||k}</b><span style="margin-left:auto;font-size:11px">${ok?'✅ 就绪':'⏳ 待配置'}</span></div>`;
}}
document.getElementById("multiContent").innerHTML = html||"<div class='empty'>无数据</div>";
}

async function openSkillsModal(){
document.getElementById("skillsModal").classList.add("show");
const skills = await fetchJSON("/api/v7/skills/list");
const stats = await fetchJSON("/api/v7/skills/stats");
let html = "";
if(stats)html+=`<p style="font-size:13px;margin-bottom:8px">技能统计：${JSON.stringify(stats)}</p>`;
if(skills&&skills.skills){
for(const s of skills.skills.slice(0,10)){
html+=`<div class="event-item"><span class="evt-type">⚡</span>${s.name||s.id||"--"}<span class="evt-time">${s.model||""}</span></div>`;
}}
document.getElementById("skillsContent").innerHTML = html||"<div class='empty'>无注册技能</div>";
}

async function openExpModal(){
document.getElementById("expModal").classList.add("show");
const data = await fetchJSON("/api/v7/experiment/list");
let html = "";
if(data&&data.experiments&&data.experiments.length){
for(const exp of data.experiments.slice(0,10)){
html+=`<div class="event-item"><span class="evt-type" style="color:#f59e0b">🧪</span>
<b>${exp.name||exp.id}</b><br><span style="font-size:11px;color:var(--text2)">创建：${fmtDate(exp.created_at)} | 状态：${exp.status||"--"}</span></div>`;
}}
document.getElementById("expContent").innerHTML = html||"<div class='empty'>暂无实验</div>";
}

function closeModal(id){document.getElementById(id).classList.remove("show")}

// --- Bookhouse ---
async function renderBookhouse(){
const stats = await fetchJSON("/api/v8/bookhouse/stats");
const panel = document.getElementById("bookhousePanel");
if(!stats||!stats.total_books){panel.innerHTML="<div class='status-row'><span>📚</span>书阁离线</div>";return}
const buildings = stats.buildings||[];
const topB = buildings.sort((a,b)=>(b.book_count||0)-(a.book_count||0)).slice(0,3);
let h=`<div class="status-row"><span>📖</span>${stats.total_books||0}本书 · ${stats.total_tags||0}标签 · ${buildings.length}阁</div>`;
h+=`<div class="status-row" style="font-size:10px;flex-wrap:wrap;gap:2px 6px">`;
for(const b of topB)h+=`<span style="padding:2px 7px;background:var(--bg);border-radius:4px">${b.name?.replace('阁-','阁·')||'--'}: ${b.book_count}</span>`;
h+=`</div>`;
panel.innerHTML = h;
}

async function openBookhouseModal(){
document.getElementById("bookhouseModal").classList.add("show");
const stats = await fetchJSON("/api/v8/bookhouse/stats");
const statDiv = document.getElementById("bhStats");
if(stats&&stats.total_books){
const buildings = stats.buildings||[];
const topB = buildings.sort((a,b)=>(b.book_count||0)-(a.book_count||0)).slice(0,5);
statDiv.innerHTML = `<span>📖 ${stats.total_books}本书</span><span>🏷️ ${stats.total_tags}标签</span><span>🏛️ ${buildings.length}阁</span>`;
}else{statDiv.innerHTML="<span>⚠️ 书阁连接中...</span>"}
bookhouseSearch();
}

async function bookhouseSearch(){
const q = document.getElementById("bhSearchInput").value.trim();
const div = document.getElementById("bhResults");
if(!q){div.innerHTML="<div class='empty'>输入关键词搜索知识书阁</div>";return}
div.innerHTML="<div class='loading'>搜索中...</div>";
const data = await fetchJSON("/api/v8/bookhouse/search?q="+encodeURIComponent(q));
if(!data||!data.success){div.innerHTML="<div class='empty'>搜索失败</div>";return}
if(!data.results||data.results.length===0){div.innerHTML="<div class='empty'>未找到「"+q+"」相关书籍</div>";return}
let h="";
for(const b of data.results.slice(0,15)){
h+=`<div class="event-item" style="cursor:pointer" onclick="bookhouseDetail(${b.id})">
<span class="evt-type" style="color:#eab308">📖</span>
<b>${b.title||"--"}</b> <span style="font-size:10px;color:var(--text2)">${b.building_name||""}</span>
<br><span style="font-size:11px;color:var(--muted)">${(b.summary||"").substring(0,80)}</span>
</div>`}
div.innerHTML = h;
}

async function bookhouseDetail(id){
const data = await fetchJSON("/api/v8/bookhouse/book/"+id);
const div = document.getElementById("bhResults");
if(!data||!data.book){div.innerHTML="<div class='empty'>加载失败</div>";return}
const b = data.book;
let h=`<div style="padding:8px 0"><button onclick="bookhouseSearch()" style="padding:4px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--text)">← 返回搜索</button></div>`;
h+=`<div style="padding:14px;background:var(--bg2);border-radius:8px;margin-bottom:10px">
<h4 style="margin:0 0 6px;color:var(--accent)">${b.title||"--"}</h4>
<p style="margin:0;font-size:12px;color:var(--muted)">📂 ${b.building_name||"--"} | ✍️ ${b.author||"--"} | 📅 ${b.created_at||"--"}</p>
<p style="margin:6px 0 0;font-size:13px;color:var(--text2)">${b.summary||"无简介"}</p>
${b.tags?`<p style="margin:6px 0 0;font-size:11px">🏷️ ${b.tags}</p>`:""}
</div>`;
if(b.tag_list&&b.tag_list.length){
h+=`<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">`;
for(const t of b.tag_list)h+=`<span onclick="document.getElementById('bhSearchInput').value='${t.name}';bookhouseSearch()" style="padding:3px 10px;background:var(--bg);border-radius:12px;font-size:11px;cursor:pointer;border:1px solid var(--border)">${t.name}(${t.book_count||""})</span>`;
h+=`</div>`;}
if(b.file_path)h+=`<p style="font-size:11px;color:var(--muted)">📁 ${b.file_path}</p>`;
div.innerHTML = h;
}

async function bookhouseLoadTags(){
const data = await fetchJSON("/api/v8/bookhouse/tags");
const div = document.getElementById("bhResults");
if(!data||!data.tags){div.innerHTML="<div class='empty'>加载失败</div>";return}
let h="";
for(const t of data.tags.slice(0,30)){
h+=`<span onclick="document.getElementById('bhSearchInput').value='${t.name}';bookhouseSearch()"
style="display:inline-block;padding:5px 12px;margin:3px;background:var(--bg2);border-radius:12px;font-size:12px;cursor:pointer;border:1px solid var(--border)">${t.name} <span style="color:var(--accent)">${t.book_count}</span></span>`}
div.innerHTML = `<div style="padding:8px">${h}</div>`;
}

async function bookhouseLoadBuildings(){
const stats = await fetchJSON("/api/v8/bookhouse/stats");
const div = document.getElementById("bhResults");
if(!stats||!stats.buildings){div.innerHTML="<div class='empty'>加载失败</div>";return}
let h="";
for(const b of stats.buildings){
h+=`<div class="event-item" style="cursor:pointer" onclick="document.getElementById('bhSearchInput').value='${b.name}';bookhouseSearch()">
<span class="evt-type" style="color:#8b5cf6">🏛️</span><b>${b.name||"--"}</b>
<span style="font-size:12px;color:var(--accent);float:right">${b.book_count||0}本</span>
</div>`}
div.innerHTML = h;
}

// --- Init ---
async function init(){
await Promise.all([
refreshStats(),renderAgents(),renderEvents(),renderExperiments(),renderModelHealth(),renderBookhouse()
]);
loadMode();
}

async function loadMode(){
const data = await fetchJSON("/api/mode");
if(data&&data.mode){
currentMode = data.mode;
document.querySelectorAll(".mode-opt").forEach(b=>b.classList.remove("active"));
document.querySelector(`.mode-opt[data-mode="${currentMode}"]`)?.classList.add("active");
const badge = document.getElementById("modeBadge");
badge.textContent = currentMode==="solo"?"Solo":currentMode==="team"?"Team":"Ecosystem";
badge.className = "badge "+currentMode;
}
}

init();
setInterval(()=>{refreshStats();renderEvents()},15000);
</script>
</body>
</html>"""

# ==================== 可观测性仪表盘页面（7/8新增）====================

OBSERVATORY_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HomeStream Observatory - 可观测性仪表盘</title>
<script src="/assets/echarts.min.js"></script>
<script>if(typeof echarts==='undefined'){document.write('<script src="https://cdn.staticfile.org/echarts/5.4.3/echarts.min.js"><\/script>');}</script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}
.header{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.header h1{font-size:18px;font-weight:500;color:#58a6ff}
.header .meta{font-size:12px;color:#8b949e}
.header a{color:#58a6ff;text-decoration:none;font-size:13px;margin-left:16px}
.summary-bar{display:flex;gap:16px;padding:16px 24px;background:#161b22;border-bottom:1px solid #30363d;flex-wrap:wrap}
.summary-item{display:flex;flex-direction:column;gap:4px}
.summary-item .label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:0.5px}
.summary-item .value{font-size:20px;font-weight:500;color:#e6edf3}
.summary-item .value.green{color:#3fb950}
.summary-item .value.yellow{color:#d29922}
.summary-item .value.red{color:#f85149}
.summary-item .value.blue{color:#58a6ff}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:20px 24px}
.panel{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden}
.panel-header{padding:12px 16px;border-bottom:1px solid #30363d;display:flex;align-items:center;justify-content:space-between}
.panel-header h3{font-size:13px;font-weight:500;color:#e6edf3}
.panel-header .tag{font-size:11px;padding:2px 8px;border-radius:10px;background:#21262d;color:#8b949e}
.panel-body{padding:12px}
.chart{width:100%;height:220px}
.table{width:100%;font-size:12px;border-collapse:collapse}
.table th{text-align:left;padding:8px 12px;color:#8b949e;font-weight:400;border-bottom:1px solid #30363d}
.table td{padding:8px 12px;border-bottom:1px solid #21262d}
.table tr:hover{background:#21262d}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.status-dot.healthy{background:#3fb950}
.status-dot.degraded{background:#d29922}
.status-dot.offline{background:#f85149}
.status-dot.unknown{background:#8b949e}
.tier-badge{font-size:10px;padding:1px 6px;border-radius:3px;font-weight:500}
.tier-badge.L1{background:#1a3a5c;color:#58a6ff}
.tier-badge.L2{background:#2d1a3c;color:#bc8cff}
.tier-badge.L3{background:#3c2a1a;color:#d29922}
.loading{display:flex;align-items:center;justify-content:center;height:220px;color:#8b949e;font-size:13px}
@media(max-width:1200px){.grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:768px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="header">
<div><h1>HomeStream Observatory</h1><div class="meta" id="lastUpdate">loading...</div></div>
<div><a href="/">Back to Dashboard</a></div>
</div>
<div class="summary-bar" id="summaryBar"></div>
<div class="grid">
<div class="panel"><div class="panel-header"><h3>HTTP Success Rate</h3><span class="tag" id="tag-success">-</span></div><div class="panel-body"><div class="chart" id="chart-success"></div></div></div>
<div class="panel"><div class="panel-header"><h3>Latency Percentiles</h3><span class="tag" id="tag-latency">-</span></div><div class="panel-body"><div class="chart" id="chart-latency"></div></div></div>
<div class="panel"><div class="panel-header"><h3>Token Usage</h3><span class="tag" id="tag-token">-</span></div><div class="panel-body"><div class="chart" id="chart-token"></div></div></div>
<div class="panel"><div class="panel-header"><h3>Event Distribution</h3><span class="tag" id="tag-event">-</span></div><div class="panel-body"><div class="chart" id="chart-event"></div></div></div>
<div class="panel"><div class="panel-header"><h3>ICP Messages</h3><span class="tag" id="tag-icp">-</span></div><div class="panel-body"><div class="chart" id="chart-icp"></div></div></div>
<div class="panel"><div class="panel-header"><h3>Skill Invocations</h3><span class="tag" id="tag-skill">-</span></div><div class="panel-body"><div class="chart" id="chart-skill"></div></div></div>
<div class="panel"><div class="panel-header"><h3>Cost Breakdown</h3><span class="tag" id="tag-cost">-</span></div><div class="panel-body"><div class="chart" id="chart-cost"></div></div></div>
<div class="panel"><div class="panel-header"><h3>Provider Status</h3><span class="tag" id="tag-provider">-</span></div><div class="panel-body" style="max-height:220px;overflow-y:auto"><table class="table" id="table-provider"><thead><tr><th>Provider</th><th>Tier</th><th>Status</th><th>Req</th><th>Err</th><th>Latency</th></tr></thead><tbody id="tbody-provider"></tbody></table></div></div>
<div class="panel" style="grid-column:1/-1"><div class="panel-header"><h3>Architecture Visualization</h3><span class="tag" id="tag-arch">-</span></div><div class="panel-body" id="arch-container" style="overflow-x:auto"></div></div>
<div class="panel" style="grid-column:1/-1"><div class="panel-header"><h3>Data Quality Guardian</h3><span class="tag" id="tag-quality">-</span></div><div class="panel-body" id="quality-container"></div></div>
</div>
<script>
const charts={};
function initChart(id){const el=document.getElementById(id);if(el)charts[id]=echarts.init(el);return charts[id];}
function showLoading(id){const el=document.getElementById(id);if(el)el.innerHTML='<div class="loading">Loading...</div>';}

function renderSummary(s){
const bar=document.getElementById('summaryBar');
const items=[
{label:'HTTP Requests',value:s.http_total_requests,cls:'blue'},
{label:'Success Rate',value:(s.http_success_rate*100).toFixed(1)+'%',cls:s.http_success_rate>=0.95?'green':s.http_success_rate>=0.8?'yellow':'red'},
{label:'Total Events',value:s.total_events,cls:'blue'},
{label:'Active Sessions',value:s.active_sessions,cls:'blue'},
{label:'WS Connections',value:s.active_connections,cls:'blue'},
{label:'Tokens In/Out',value:(s.total_tokens_in||0).toLocaleString()+' / '+(s.total_tokens_out||0).toLocaleString(),cls:'blue'},
{label:'Est. Cost',value:'¥'+(s.total_cost||0).toFixed(4),cls:'yellow'},
{label:'Skill Rate',value:(s.skill_success_rate*100).toFixed(1)+'%',cls:s.skill_success_rate>=0.95?'green':s.skill_success_rate>=0.8?'yellow':'red'},
{label:'Strategy',value:s.strategy||'-',cls:'blue'}
];
bar.innerHTML=items.map(i=>'<div class="summary-item"><div class="label">'+i.label+'</div><div class="value '+i.cls+'">'+i.value+'</div></div>').join('');
}

function renderSuccess(p){
const c=initChart('chart-success');
document.getElementById('tag-success').textContent=p.total+' total';
if(!c)return;
c.setOption({
tooltip:{trigger:'item'},
series:[{
type:'gauge',radius:'85%',center:['50%','60%'],
min:0,max:100,
axisLine:{lineStyle:{width:12,color:[[0.8,'#f85149'],[0.95,'#d29922'],[1,'#3fb950']]}},
pointer:{width:4,length:'60%'},
detail:{formatter:'{value}%',fontSize:22,color:'#e6edf3',offsetCenter:[0,'70%']},
data:[{value:(p.rate*100).toFixed(1),name:'Success'}],
title:{show:false}
}],
graphic:{type:'text',left:'center',bottom:8,style:{text:'Success: '+p.success+'  Error: '+p.error,fill:'#8b949e',fontSize:11}}
});
}

function renderLatency(p){
const c=initChart('chart-latency');
const pct=p.percentiles_ms||{};
document.getElementById('tag-latency').textContent='avg '+p.avg_ms+'ms';
if(!c)return;
c.setOption({
tooltip:{trigger:'axis',formatter:function(p){return p[0].name+': '+p[0].value+'ms'}},
xAxis:{type:'category',data:['P50','P75','P90','P95','P99'],axisLabel:{color:'#8b949e',fontSize:11}},
yAxis:{type:'value',axisLabel:{color:'#8b949e',fontSize:11,formatter:'{value}ms'},splitLine:{lineStyle:{color:'#30363d'}}},
series:[{type:'bar',data:[
{value:pct.p50||0,itemStyle:{color:'#3fb950'}},
{value:pct.p75||0,itemStyle:{color:'#58a6ff'}},
{value:pct.p90||0,itemStyle:{color:'#d29922'}},
{value:pct.p95||0,itemStyle:{color:'#db6d28'}},
{value:pct.p99||0,itemStyle:{color:'#f85149'}}
],barWidth:'50%'}],
grid:{left:50,right:20,top:20,bottom:30}
});
}

function renderToken(p){
const c=initChart('chart-token');
const provs=p.by_provider||[];
document.getElementById('tag-token').textContent=provs.length+' providers';
if(!c)return;
if(provs.length===0){c.setOption({title:{text:'No data',left:'center',top:'center',textStyle:{color:'#8b949e',fontSize:13}}});return;}
c.setOption({
tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
legend:{data:['Tokens In','Tokens Out'],top:0,textStyle:{color:'#8b949e',fontSize:10}},
xAxis:{type:'category',data:provs.map(p=>p.name),axisLabel:{color:'#8b949e',fontSize:10,rotate:20}},
yAxis:{type:'value',axisLabel:{color:'#8b949e',fontSize:10},splitLine:{lineStyle:{color:'#30363d'}}},
series:[
{name:'Tokens In',type:'bar',data:provs.map(p=>p.tokens_in),itemStyle:{color:'#58a6ff'}},
{name:'Tokens Out',type:'bar',data:provs.map(p=>p.tokens_out),itemStyle:{color:'#3fb950'}}
],
grid:{left:50,right:20,top:30,bottom:40}
});
}

function renderEvent(p){
const c=initChart('chart-event');
const types=p.by_type||{};
const data=Object.entries(types).map(([k,v])=>({name:k,value:v}));
document.getElementById('tag-event').textContent=p.total+' events';
if(!c)return;
if(data.length===0){c.setOption({title:{text:'No events',left:'center',top:'center',textStyle:{color:'#8b949e',fontSize:13}}});return;}
c.setOption({
tooltip:{trigger:'item',formatter:'{b}: {c} ({d}%)'},
series:[{
type:'pie',radius:['40%','65%'],center:['50%','50%'],
data:data,
label:{color:'#c9d1d9',fontSize:10},
itemStyle:{borderColor:'#161b22',borderWidth:2}
}]
});
}

function renderICP(p){
const c=initChart('chart-icp');
const icp=p.icp_messages||{};
const types=icp.by_type||{};
const data=Object.entries(types).map(([k,v])=>({name:k||'unknown',value:v}));
document.getElementById('tag-icp').textContent=(icp.total||0)+' messages';
if(!c)return;
if(data.length===0){c.setOption({title:{text:'No ICP data',left:'center',top:'center',textStyle:{color:'#8b949e',fontSize:13}}});return;}
c.setOption({
tooltip:{trigger:'item',formatter:'{b}: {c} ({d}%)'},
series:[{
type:'pie',radius:'55%',center:['50%','50%'],
data:data,
label:{color:'#c9d1d9',fontSize:10},
itemStyle:{borderColor:'#161b22',borderWidth:2}
}]
});
}

function renderSkill(p){
const c=initChart('chart-skill');
const skills=p.by_skill||[];
document.getElementById('tag-skill').textContent=p.total+' calls';
if(!c)return;
if(skills.length===0){c.setOption({title:{text:'No skill data',left:'center',top:'center',textStyle:{color:'#8b949e',fontSize:13}}});return;}
c.setOption({
tooltip:{trigger:'axis',axisPointer:{type:'shadow'}},
legend:{data:['Success','Error'],top:0,textStyle:{color:'#8b949e',fontSize:10}},
xAxis:{type:'category',data:skills.map(s=>s.skill),axisLabel:{color:'#8b949e',fontSize:10,rotate:20}},
yAxis:{type:'value',axisLabel:{color:'#8b949e',fontSize:10},splitLine:{lineStyle:{color:'#30363d'}}},
series:[
{name:'Success',type:'bar',stack:'total',data:skills.map(s=>s.success),itemStyle:{color:'#3fb950'}},
{name:'Error',type:'bar',stack:'total',data:skills.map(s=>s.error),itemStyle:{color:'#f85149'}}
],
grid:{left:50,right:20,top:30,bottom:40}
});
}

function renderCost(p){
const c=initChart('chart-cost');
const tiers=p.by_tier||{};
const data=Object.entries(tiers).filter(([k,v])=>v>0).map(([k,v])=>({name:k,value:parseFloat(v.toFixed(6))}));
document.getElementById('tag-cost').textContent='¥'+p.total.toFixed(4);
if(!c)return;
if(data.length===0){c.setOption({title:{text:'L1 only (zero cost)',left:'center',top:'center',textStyle:{color:'#3fb950',fontSize:13}}});return;}
c.setOption({
tooltip:{trigger:'item',formatter:'{b}: ¥{c} ({d}%)'},
series:[{
type:'pie',radius:['40%','65%'],center:['50%','50%'],
data:data,
label:{color:'#c9d1d9',fontSize:11,formatter:'{b}\n¥{c}'},
itemStyle:{borderColor:'#161b22',borderWidth:2},
color:['#58a6ff','#bc8cff','#d29922']
}]
});
}

function renderProviders(provs){
const tbody=document.getElementById('tbody-provider');
document.getElementById('tag-provider').textContent=(provs||[]).length+' providers';
if(!provs||provs.length===0){tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:#8b949e">No providers</td></tr>';return;}
tbody.innerHTML=provs.map(p=>{
const cls=p.status||'unknown';
const tierCls='tier-badge '+(p.tier||'L1');
return '<tr>'+
'<td>'+p.display_name+'</td>'+
'<td><span class="'+tierCls+'">'+p.tier+'</span></td>'+
'<td><span class="status-dot '+cls+'"></span>'+p.status+'</td>'+
'<td>'+p.requests+'</td>'+
'<td>'+(p.errors>0?'<span style="color:#f85149">'+p.errors+'</span>':p.errors)+'</td>'+
'<td>'+p.avg_latency_ms+'ms</td>'+
'</tr>';
}).join('');
}

function renderArchitecture(a){
const c=document.getElementById('arch-container');
const t=document.getElementById('tag-arch');
if(!a||a.error){if(t)t.textContent='N/A';if(c)c.innerHTML='<div class="loading">'+(a?a.error:'No data')+'</div>';return;}
let html='';
if(a.topology_svg)html+='<div style="margin-bottom:12px">'+a.topology_svg+'</div>';
if(a.flow_svg)html+='<div style="margin-bottom:12px">'+a.flow_svg+'</div>';
if(a.router_svg)html+='<div>'+a.router_svg+'</div>';
if(c)c.innerHTML=html;
if(t)t.textContent=(a.meta?a.meta.agent_count+' agents':'-');
}
function renderQuality(q){
const c=document.getElementById('quality-container');
const t=document.getElementById('tag-quality');
if(!q||q.error){if(t)t.textContent='N/A';if(c)c.innerHTML='<div class="loading">'+(q?q.error:'No data')+'</div>';return;}
const score=q.overall_score||0;
const status=q.overall_status||'unknown';
const colors={pass:'#1d9e75',warn:'#ef9f27',error:'#e24b4a'};
if(t){t.textContent=status.toUpperCase()+' ('+(score*100).toFixed(0)+'%)';t.style.color=colors[status]||'#888';}
let html='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-bottom:8px">';
const checks=q.checks||{};
for(const[k,v]of Object.entries(checks)){
const sc=v.score||0;const st=v.status||'unknown';
html+='<div style="background:var(--bg-secondary,#f5f5f5);border-radius:6px;padding:8px">'+
'<div style="font-size:11px;color:#888">'+(v.name||k)+'</div>'+
'<div style="font-size:18px;font-weight:500;color:'+(colors[st]||'#888')+'">'+(sc*100).toFixed(0)+'%</div>'+
'<div style="font-size:10px;color:#aaa">'+(v.total_checked||0)+' checked, '+(v.issues?v.issues.length:0)+' issues</div></div>';
}
html+='</div>';
if(q.total_issues>0){
html+='<div style="font-size:12px;color:#888">'+q.total_issues+' issues ('+q.error_count+' errors, '+q.warn_count+' warnings)</div>';
}
if(c)c.innerHTML=html;
}
async function refresh(){
try{
const r=await fetch('/api/v7/observatory');
const d=await r.json();
document.getElementById('lastUpdate').textContent='Updated: '+new Date(d.timestamp).toLocaleTimeString();
renderSummary(d.summary);
renderSuccess(d.panels.success_rate);
renderLatency(d.panels.latency);
renderToken(d.panels.token_cost);
renderEvent(d.panels.event_distribution);
renderICP(d.panels.active_throughput);
renderSkill(d.panels.tool_execution);
renderCost(d.panels.cost_breakdown);
renderProviders(d.providers);
renderArchitecture(d.panels.architecture);
renderQuality(d.panels.data_quality);
}catch(e){
document.getElementById('lastUpdate').textContent='Error: '+e.message;
}
}

refresh();
setInterval(refresh,5000);
window.addEventListener('resize',function(){Object.values(charts).forEach(c=>c&&c.resize());});
</script>
</body>
</html>"""


# ============================================================
# 千面设计市场 — 主题注入（最小化改动，不改写任何页面常量）
# ============================================================

# --- PWA 普大众化接口注入标签 ---
_PWA_HEAD_TAGS = (
    '<link rel="manifest" href="/manifest.json">'
    '<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">'
    '<link rel="icon" type="image/png" sizes="192x192" href="/assets/icon-192.png">'
    '<link rel="icon" type="image/png" sizes="512x512" href="/assets/icon-512.png">'
    '<link rel="stylesheet" href="/assets/mobile.css">'
    '<meta name="theme-color" content="#4a90d9">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="default">'
    '<meta name="mobile-web-app-capable" content="yes">'
    "<script>if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(function(){})}</script>"
)

# 移动端底部导航栏 HTML（≤768px 时显示）
_MOBILE_TAB_BAR = (
    '<div class="mobile-tab-bar">'
    '<a href="/"><span class="tab-icon">🏠</span>仪表盘</a>'
    '<a href="/chat"><span class="tab-icon">💬</span>聊天</a>'
    '<a href="/observatory"><span class="tab-icon">📊</span>观测台</a>'
    '<a href="/group"><span class="tab-icon">👥</span>群聊</a>'
    "</div>"
)


def _inject_pwa(html: str) -> str:
    """将 PWA 标签和移动端导航注入 HTML 页面。

    在 </head> 前插入 PWA meta/link 标签，在 </body> 前插入移动端导航栏。
    若页面无对应标签则原样返回（零风险）。
    """
    if "</head>" in html:
        html = html.replace("</head>", _PWA_HEAD_TAGS + "</head>", 1)
    if "</body>" in html:
        html = html.replace("</body>", _MOBILE_TAB_BAR + "</body>", 1)
    return html


def apply_theme_to_page(html: str, request: Optional["Request"] = None) -> str:
    """将激活/预览主题注入页面 <head> 前，并注入 PWA 标签。

    优先使用 URL 参数 ?theme=<id> 进行预览，否则使用已激活主题。
    主题引擎不可用或无可应用主题时原样返回，零风险。
    最后统一注入 PWA 标签和移动端导航栏。
    """
    try:
        from theme_manager import ThemeManager

        tm = ThemeManager()
        theme_id = None
        if request is not None:
            theme_id = request.query_params.get("theme")
        html = tm.apply_theme(html, theme_id)
    except Exception:
        pass
    return _inject_pwa(html)


# ============================================================
# PWA 普大众化接口 — 静态文件路由
# ============================================================


@app.get("/manifest.json")
async def serve_manifest():
    """PWA 清单文件"""
    import json as _json

    from fastapi.responses import JSONResponse

    _base = os.path.dirname(os.path.abspath(__file__))
    _path = os.path.join(_base, "manifest.json")
    if os.path.exists(_path):
        with open(_path, encoding="utf-8") as f:
            return JSONResponse(_json.load(f))
    return JSONResponse({"error": "manifest not found"}, status_code=404)


@app.get("/sw.js")
async def serve_sw():
    """Service Worker 脚本"""
    from fastapi.responses import PlainTextResponse

    _base = os.path.dirname(os.path.abspath(__file__))
    _path = os.path.join(_base, "sw.js")
    if os.path.exists(_path):
        with open(_path, encoding="utf-8") as f:
            return PlainTextResponse(f.read(), media_type="application/javascript")
    return PlainTextResponse("// SW not found", status_code=404)


@app.get("/offline.html")
async def serve_offline():
    """离线回退页面"""
    from fastapi.responses import HTMLResponse

    _base = os.path.dirname(os.path.abspath(__file__))
    _path = os.path.join(_base, "offline.html")
    if os.path.exists(_path):
        with open(_path, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Offline</h1>", status_code=404)


@app.get("/assets/{file_path:path}")
async def serve_assets(file_path: str):
    """静态资源服务（图标、CSS等）"""
    from fastapi.responses import FileResponse

    _base = os.path.dirname(os.path.abspath(__file__))
    _full = os.path.join(_base, "assets", file_path)
    if os.path.exists(_full) and os.path.isfile(_full):
        return FileResponse(_full)
    from fastapi import HTTPException as _HTTPException

    raise _HTTPException(status_code=404, detail="Asset not found")


@app.get("/")
async def root(request: Request):
    """V8仪表盘"""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(apply_theme_to_page(V8_DASHBOARD, request))


@app.get("/theme/{theme_id}/preview")
async def theme_preview(theme_id: str):
    """千面设计市场 — 整页主题预览"""
    from fastapi.responses import HTMLResponse

    try:
        from theme_manager import ThemeManager

        tm = ThemeManager()
        return HTMLResponse(tm.preview_html(theme_id))
    except Exception as e:
        return HTMLResponse(f"<h1>主题预览失败: {e}</h1>")


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "version": "8.0.0",
        "timestamp": datetime.now().isoformat(),
        "active_streams": len(streams),
    }


# ==================== 6/27 弹性模式管理API ====================


@app.get("/api/mode")
async def get_mode():
    """获取当前部署模式配置"""
    from modes import get_mode_info

    return get_mode_info()


@app.post("/api/mode/switch")
async def switch_mode_api(new_mode: str):
    """切换部署模式（需要重启服务生效）"""
    from modes import DeployMode, switch_mode

    try:
        mode = DeployMode(new_mode.lower())
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"无效的模式: {new_mode}，请使用 solo/team/ecosystem"
        )
    result = switch_mode(mode)
    return result


@app.get("/api/mode/features")
async def list_features():
    """列出所有功能开关及其状态"""
    from modes import MODE_FEATURE_MAP, FeatureFlag, get_mode_config

    config = get_mode_config()
    features = []
    for flag in FeatureFlag:
        features.append(
            {
                "name": flag.value,
                "enabled": config.is_enabled(flag),
                "mode_default": flag in MODE_FEATURE_MAP[config.mode],
            }
        )
    return {"mode": config.mode.value, "features": features}


@app.post("/api/v7/events/send", response_model=EventResponse)
async def send_event(req: SendMessageRequest):
    """发送事件（融合ICP v1.1 + EventStream）

    流程：
    1. 从token或sender识别Agent
    2. 解析content中的ICP标签
    3. 创建Event并发布到EventStream
    4. 持久化到SQLite
    5. 返回event_id
    """
    # 1. 识别Agent
    sender = resolve_agent(req.token, req.sender)

    # 1.5 P0安全注入：验证ICP内容安全性（在解析前过滤注入攻击）
    is_content_safe, safe_content = validate_icp_content(req.content)
    if not is_content_safe:
        _sec_logger = structlog.get_logger()
        _sec_logger.warning(
            "icp_injection_detected",
            endpoint="send_event",
            sender=sender,
            original_length=len(req.content),
            filtered=True,
        )

    # 2. 解析ICP标签（使用安全过滤后的内容）
    parsed = parse_icp_message(safe_content)
    event_type = parsed.get("event_type", EventType.INFO)
    content = parsed.get("content", req.content)
    parsed_sender = parsed.get("sender") or sender
    parsed_recipient = parsed.get("recipient") or req.recipient

    # 如果解析出了sender/recipient，优先用解析结果
    if parsed.get("sender"):
        parsed_sender = parsed["sender"]
    if parsed.get("recipient"):
        parsed_recipient = parsed["recipient"]

    # 3. 创建Event
    # 尝试解析Handoff
    handoff = parse_handoff_text(content)

    event = Action(
        event_id=_gen_event_id("act"),
        event_type=event_type,
        sender=parsed_sender,
        recipient=parsed_recipient,
        content=content,
        source=EventSource.AGENT,
        handoff=handoff,
    )

    # 4. 发布到EventStream
    stream = get_or_create_stream()
    event_id = stream.publish(event)

    # 5. 持久化
    persist_event_to_db(event)

    # 6. 返回
    return EventResponse(
        event_id=event.event_id,
        event_type=event.event_type.value,
        sender=event.sender,
        recipient=event.recipient,
        content=event.content,
        timestamp=event.timestamp.isoformat(),
        cause=event.cause,
        handoff=event.handoff,
        confidence=event.confidence,
    )


@app.get("/api/v7/events")
async def get_events(
    session_id: str = Query("default"),
    agent_name: str | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """查询事件（支持过滤）"""
    stream = get_or_create_stream(session_id)

    events = stream.events

    # 过滤
    if agent_name:
        events = [e for e in events if e.sender == agent_name or e.recipient == agent_name]
    if event_type:
        try:
            et = EventType(event_type)
            events = [e for e in events if e.event_type == et]
        except ValueError:
            pass

    # 限制 + 最新在前
    events = events[-limit:][::-1]

    return {
        "session_id": session_id,
        "total": len(events),
        "events": [e.model_dump(mode="json") for e in events],
    }


@app.get("/api/v7/events/chain/{event_id}")
async def get_cause_chain(event_id: str, session_id: str = Query("default")):
    """获取因果链"""
    stream = get_or_create_stream(session_id)
    chain = stream.get_cause_chain(event_id)

    return {
        "event_id": event_id,
        "chain_length": len(chain),
        "chain": [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "sender": e.sender,
                "recipient": e.recipient,
                "content": e.content[:100],
                "timestamp": e.timestamp.isoformat(),
            }
            for e in chain
        ],
    }


@app.post("/api/v7/handoff")
async def handoff_task(req: HandoffRequest):
    """Handoff任务（融合agent-team-orchestration的5要素）"""
    stream = get_or_create_stream()

    event = create_handoff(
        task_id=req.task_id,
        build_agent=req.builder_agent,
        review_agent=req.reviewer_agent,
        what_done=req.what_done,
        where_artifacts=req.where_artifacts,
        how_verify=req.how_verify,
        known_issues=req.known_issues,
        what_next=req.what_next,
        confidence=req.confidence,
    )

    event_id = stream.publish(event)
    persist_event_to_db(event)

    return {
        "event_id": event_id,
        "task_id": req.task_id,
        "handoff": event.handoff,
        "status": "handoff_success",
    }


@app.post("/api/v7/tasks/lifecycle")
async def update_task_lifecycle(req: TaskLifecycleRequest):
    """更新任务生命周期（融合agent-team-orchestration）

    状态机：
    inbox → assigned → in_progress → review → done | failed

    规则：
    - 每次转换必须comment（who/what/why）
    - 非法转换返回400
    """
    task_id = req.task_id

    # 验证状态转换合法性
    valid_next = VALID_TRANSITIONS.get(req.from_state, [])
    if req.to_state not in valid_next:
        raise HTTPException(
            status_code=400,
            detail=f"非法状态转换: {req.from_state} → {req.to_state}，合法目标: {valid_next}",
        )

    # 更新任务状态
    if task_id not in tasks:
        tasks[task_id] = {"state": "inbox", "history": []}

    task = tasks[task_id]
    task["state"] = req.to_state
    task["history"].append(
        {
            "from": req.from_state,
            "to": req.to_state,
            "comment": req.comment,
            "changed_by": req.changed_by or "system",
            "timestamp": datetime.now().isoformat(),
        }
    )

    # 发布状态变更事件
    stream = get_or_create_stream()
    event = create_action(
        sender=req.changed_by or "System",
        recipient="Kanban",
        event_type=EventType.UPD,
        content=f"任务{task_id}状态变更: {req.from_state}→{req.to_state}",
    )
    stream.publish(event)
    persist_event_to_db(event)

    return {
        "task_id": task_id,
        "from_state": req.from_state,
        "to_state": req.to_state,
        "comment": req.comment,
        "history": task["history"],
        "timestamp": datetime.now().isoformat(),
        "status": "accepted",
    }


@app.get("/api/v7/stats")
async def get_stats(session_id: str = Query("default")):
    """获取EventStream统计信息（含持久化 DB 统计）"""
    stream = get_or_create_stream(session_id)
    stats = stream.get_statistics()

    # 追加持久化统计
    try:
        store = getattr(stream, "_store", None)
        db_stats = store.stats(session_id) if store else {}
    except Exception:
        db_stats = {}

    return {
        **stats,
        "tasks": len(tasks),
        "server_version": "7.1.0-dev",
        "persistence": {
            "enabled": True,
            "db_path": V7_DB_PATH,
            "db_total_events": db_stats.get("total_events", "N/A"),
            "db_by_type": db_stats.get("by_type", {}),
        },
    }


# ==================== 可观测性 API（7/8新增） ====================


@app.get("/api/v7/observatory")
async def observatory(session_id: str = Query("default")):
    """可观测性仪表盘数据 — 8面板聚合

    聚合三大数据源：
      1. Prometheus指标 — HTTP请求/延迟/ICP消息/技能调用
      2. EventStore — 事件统计/类型分布/会话
      3. ModelRouter — Provider状态/Token/成本估算

    返回 summary + panels + providers 完整结构。
    """
    stream = get_or_create_stream(session_id)
    store = getattr(stream, "_store", None)
    return collect_observatory_data(
        event_store=store,
        model_router=model_router,
        session_id=session_id,
    )


# ==================== ModelRouter API（v7.2新增） ====================


class ModelChatRequest(BaseModel):
    """模型聊天请求"""

    prompt: str = Field(..., description="用户输入")
    system: str = Field("", description="系统提示词（可选）")
    max_tokens: int = Field(512, description="最大输出token")
    temperature: float = Field(0.7, description="温度参数")
    prefer_tier: str | None = Field(None, description="临时指定层级（L1/L2/L3）")


class StrategyRequest(BaseModel):
    """路由策略切换请求"""

    strategy: str = Field(
        ..., description="路由策略: cost_first / quality_first / speed_first / tier_specified"
    )
    tier: str | None = Field(None, description="指定层级（仅tier_specified策略有效）")


@app.get("/api/v7/models/status")
async def get_models_status():
    """获取所有模型Provider状态 + 硬件信息

    返回当前路由策略、硬件锚点、所有Provider的健康状态和统计信息。
    """
    if not model_router._initialized:
        model_router.auto_init_from_env()
    return model_router.get_status()


@app.post("/api/v7/models/chat")
async def model_chat(req: ModelChatRequest):
    """统一模型聊天接口

    自动路由到最优Provider，支持降级。
    填了GLM_API_KEY自动启用L2，填了DEEPSEEK_API_KEY自动启用L3。

    V8安全注入：Prompt注入防护（prompt_security模块）。
    """
    # === P0安全注入：隔离system与user，防止注入攻击 ===
    _logger = structlog.get_logger()
    safe_prompt = build_safe_prompt(
        system_prompt=req.system or "你是OpenBridge V8助手，一个有温度的AI伙伴。",
        user_input=req.prompt,
    )
    # 检测是否有注入尝试（日志告警）
    is_input_safe, _ = validate_icp_content(req.prompt)
    if not is_input_safe:
        _logger.warning(
            "prompt_injection_detected",
            endpoint="model_chat",
            original_length=len(req.prompt),
            filtered=True,
        )

    # 构建消息（使用安全隔离后的prompt）
    messages = []
    if req.system:
        messages.append(ChatMessage(role="system", content=req.system))
    messages.append(ChatMessage(role="user", content=safe_prompt))

    # 解析prefer_tier
    prefer = None
    if req.prefer_tier:
        tier_map = {"L1": ProviderTier.L1, "L2": ProviderTier.L2, "L3": ProviderTier.L3}
        prefer = tier_map.get(req.prefer_tier.upper())
        if not prefer:
            raise HTTPException(status_code=400, detail=f"无效层级: {req.prefer_tier}")

    try:
        response = await model_router.chat(
            messages=messages,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            prefer_tier=prefer,
        )
        return response.to_dict()
    except Exception as e:
        logger.error("model_chat_failed", error=str(e))
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/v7/models/strategy")
async def set_model_strategy(req: StrategyRequest):
    """切换路由策略

    - cost_first: 成本优先（本地 > 免费API > 付费API）
    - quality_first: 质量优先（付费Pro > 免费Flash > 本地）
    - speed_first: 速度优先（按历史延迟排序）
    - tier_specified: 指定层级
    """
    strategy_map = {
        "cost_first": RouterStrategy.COST_FIRST,
        "quality_first": RouterStrategy.QUALITY_FIRST,
        "speed_first": RouterStrategy.SPEED_FIRST,
        "tier_specified": RouterStrategy.TIER_SPECIFIED,
    }
    strategy = strategy_map.get(req.strategy)
    if not strategy:
        raise HTTPException(status_code=400, detail=f"无效策略: {req.strategy}")

    model_router.set_strategy(strategy)

    if req.tier and strategy == RouterStrategy.TIER_SPECIFIED:
        tier_map = {"L1": ProviderTier.L1, "L2": ProviderTier.L2, "L3": ProviderTier.L3}
        tier = tier_map.get(req.tier.upper())
        if not tier:
            raise HTTPException(status_code=400, detail=f"无效层级: {req.tier}")
        model_router.set_tier(tier)

    return {"status": "ok", "strategy": model_router.strategy.value}


@app.get("/api/v7/models/health")
async def models_health_check():
    """检查所有Provider健康状态"""
    if not model_router._initialized:
        model_router.auto_init_from_env()
    results = await model_router.health_check_all()
    return {
        "results": results,
        "available_tiers": model_router.get_available_tiers(),
        "total": len(results),
        "healthy": sum(1 for v in results.values() if v),
    }


@app.get("/api/v7/models/hardware")
async def get_hardware_report():
    """获取硬件锚点报告

    展示当前机器硬件信息和推荐模型配置。
    开源用户可根据自己的硬件锚点开拓最优适配。
    """
    from hardware_profile import detect_hardware, get_model_recommendation, recommend_tier

    info = detect_hardware()
    tier = recommend_tier(info)
    rec = get_model_recommendation(tier)
    return {
        "hardware": info.to_dict(),
        "recommended_tier": tier.value,
        "recommendation": {
            "model": rec.model_name,
            "quantization": rec.quantization,
            "estimated_ram_gb": rec.estimated_ram_gb,
            "estimated_vram_gb": rec.estimated_vram_gb,
            "can_full_gpu_offload": rec.can_full_gpu_offload,
            "deployment_method": rec.deployment_method,
            "notes": rec.notes,
        },
    }


# ==================== SkillRouter v2 API（双层路由） ====================

from providers.base_provider import ProviderTier as _PT
from skill_router_v2 import CATEGORY_MODEL_MAP, SkillRouterV2, create_router_v2

# SkillRouter v2 全局实例
_skill_router_v2: SkillRouterV2 | None = None


def _get_skill_router_v2() -> SkillRouterV2:
    """获取SkillRouter v2单例"""
    global _skill_router_v2
    if _skill_router_v2 is None:
        _skill_router_v2 = create_router_v2()
        logger.info(f"SkillRouter v2 已加载: {_skill_router_v2.stats()['total']}个Skill")
    return _skill_router_v2


@app.get("/api/v7/skills/route")
async def skill_route(query: str, role: str = "all", top_k: int = 5):
    """双层路由查询 — Skill路由 + Model推荐

    GET /api/v7/skills/route?query=帮我做市场调研&role=灵犀
    """
    router = _get_skill_router_v2()
    result = router.route_with_model(query, role=role, top_k=top_k)
    return result.to_dict()


@app.post("/api/v7/skills/route")
async def skill_route_post(payload: dict):
    """双层路由查询（POST版，支持更多参数）"""
    router = _get_skill_router_v2()
    query = payload.get("query", "")
    role = payload.get("role", "all")
    top_k = payload.get("top_k", 5)
    force_tier = None
    if payload.get("force_tier"):
        try:
            force_tier = _PT(payload["force_tier"])
        except ValueError:
            pass
    result = router.route_with_model(query, role=role, top_k=top_k, force_tier=force_tier)
    return result.to_dict()


@app.get("/api/v7/skills/stats")
async def skill_stats():
    """Skill注册表统计"""
    router = _get_skill_router_v2()
    return router.stats()


@app.get("/api/v7/skills/list")
async def skill_list(role: str = "all", priority: int | None = None, category: str | None = None):
    """列出所有Skill（支持过滤）"""
    router = _get_skill_router_v2()
    skills = router.all_skills(active_only=True)
    if role != "all":
        skills = [s for s in skills if s.role == role or s.role == "all"]
    if priority is not None:
        skills = [s for s in skills if s.priority == priority]
    if category:
        skills = [s for s in skills if s.category == category]
    return {
        "total": len(skills),
        "skills": [
            {
                "id": s.id,
                "name": s.name,
                "category": s.category,
                "priority": s.priority,
                "role": s.role,
                "triggers": s.triggers,
                "description": s.description,
                "model_tier": CATEGORY_MODEL_MAP.get(s.category, _PT.L1).value,
            }
            for s in skills
        ],
    }


@app.get("/api/v7/skills/model-map")
async def skill_model_map():
    """查看分类→模型层级映射表"""
    return {
        "map": {k: v.value for k, v in CATEGORY_MODEL_MAP.items()},
        "tiers": {
            "L1": "本地模型（快速/免费）",
            "L2": "免费API（质量足够）",
            "L3": "付费API（高质量推理）",
        },
    }


@app.post("/api/v7/skills/execute")
async def skill_execute(payload: dict):
    """双层路由 + 模型执行（一步到位）

    POST /api/v7/skills/execute
    {"query": "帮我做市场调研", "role": "灵犀", "system_prompt": "..."}
    """
    router = _get_skill_router_v2()
    query = payload.get("query", "")
    role = payload.get("role", "all")
    system_prompt = payload.get("system_prompt", "")
    max_tokens = payload.get("max_tokens", 512)

    # 确保ModelRouter已初始化
    if not model_router._initialized:
        model_router.auto_init_from_env()

    result = router.route_with_model(
        query,
        role=role,
        model_router=model_router,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
    )
    return result.to_dict()


# ==================== v6兼容层 ====================


@app.post("/api/v6/send")
async def v6_compat_send(req: SendMessageRequest):
    """v6兼容：发送消息

    将v6的文本消息转译为v7 Event，内部走EventStream全流程
    """
    result = await send_event(req)
    return {
        "success": True,  # v6返回格式
        "event_id": result.event_id,
        "event_type": result.event_type,
    }


@app.get("/api/v6/inbox/{agent_name}")
async def v6_compat_inbox(
    agent_name: str,
    limit: int = Query(50, ge=1, le=200),
    before: str | None = Query(None),
    before_id: str | None = Query(None),
):
    """v6兼容：收件箱

    从EventStream中过滤recipient=agent_name的事件
    支持v6.1的DESC排序+cursor分页
    """
    stream = get_or_create_stream()

    # 获取所有发往此Agent的事件
    events = [e for e in stream.events if e.recipient == agent_name]

    # DESC排序（最新在前）
    events = sorted(events, key=lambda e: e.timestamp, reverse=True)

    # cursor分页
    has_more = False
    if before and len(events) > limit:
        # 找到cursor位置
        cursor_idx = None
        for i, e in enumerate(events):
            if e.timestamp.isoformat() <= before:
                cursor_idx = i
                break
        if cursor_idx is not None:
            events = events[cursor_idx : cursor_idx + limit]
            has_more = cursor_idx + limit < len(events)
    elif len(events) > limit:
        has_more = True

    events = events[:limit]

    # 转为v6格式
    messages = []
    for e in events:
        icp_text = stream.to_icp_v1_format(e)
        messages.append(
            {
                "id": e.event_id,
                "sender": e.sender,
                "recipient": e.recipient,
                "content": icp_text,  # ICP v1.1文本格式
                "channel": "v7_event",
                "created_at": e.timestamp.isoformat(),
                "event_type": e.event_type.value,
                "has_handoff": e.handoff is not None,
                "has_wal": e.wal_entry is not None,
            }
        )

    return {
        "agent": agent_name,
        "messages": messages,
        "has_more": has_more,
        "cursor": events[-1].timestamp.isoformat() if events else None,
        "cursor_id": events[-1].event_id if events else None,
    }


@app.get("/api/v6/status")
async def v6_compat_status():
    """v6兼容：状态检查"""
    stream = get_or_create_stream()
    return {
        "status": "running",
        "version": "7.0.0-dev (v6-compat)",
        "agents": list(AGENT_TOKENS.values()),
        "total_events": stream.event_count,
    }


# ==================== Worktree API端点（Day 2新增）====================


@app.post("/api/v7/worktree/create", response_model=WorktreeResponse)
async def create_worktree(req: WorktreeCreateRequest):
    """创建Worktree（三源融优: A主文件隔离 + B副端口/DB + C自角色映射）"""
    role_map = {
        "maker": WorktreeRole.MAKER,
        "reviewer": WorktreeRole.REVIEWER,
        "researcher": WorktreeRole.RESEARCHER,
        "coordinator": WorktreeRole.COORDINATOR,
    }
    role = role_map.get(req.role, WorktreeRole.MAKER)

    config = WorktreeConfig(
        name=req.name,
        branch=req.branch or f"{'feat' if role == WorktreeRole.MAKER else req.role}/{req.name}",
        agent=req.agent,
        role=role,
        base_branch=req.base_branch,
        review_required=req.review_required,
        reviewer=req.reviewer,
    )

    try:
        path = worktree_manager.create_worktree(config)
        ports = worktree_manager.assign_ports(req.name)

        return WorktreeResponse(
            name=req.name,
            status=config.status.value,
            agent=req.agent,
            role=role.value,
            branch=config.branch,
            reviewer=config.reviewer,
            ports=ports,
            created_at=config.created_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v7/worktree/list")
async def list_worktrees():
    """列出所有Worktree"""
    worktrees = worktree_manager.list_worktrees()
    return {
        "total": len(worktrees),
        "worktrees": [
            {
                "name": wt.name,
                "status": wt.status.value,
                "agent": wt.agent,
                "role": wt.role.value if isinstance(wt.role, WorktreeRole) else wt.role,
                "branch": wt.branch,
                "reviewer": wt.reviewer,
                "created_at": wt.created_at,
            }
            for wt in worktrees
        ],
        "stats": worktree_manager.get_stats(),
    }


@app.post("/api/v7/worktree/action")
async def worktree_action(req: WorktreeActionRequest):
    """Worktree操作（lock/unlock/merge/remove/assign_reviewer）"""
    if req.action == "lock":
        success = worktree_manager.lock_worktree(req.name, req.reason)
        if not success:
            raise HTTPException(status_code=404, detail=f"Worktree '{req.name}' 不存在")
        return {"name": req.name, "action": "lock", "status": "locked"}

    elif req.action == "unlock":
        success = worktree_manager.unlock_worktree(req.name)
        if not success:
            raise HTTPException(status_code=404, detail=f"Worktree '{req.name}' 不存在")
        return {"name": req.name, "action": "unlock", "status": "active"}

    elif req.action == "merge":
        success = worktree_manager.verify_and_merge(req.name)
        if not success:
            raise HTTPException(
                status_code=400, detail=f"Worktree '{req.name}' 合并失败（可能在审查中）"
            )
        return {"name": req.name, "action": "merge", "status": "completed"}

    elif req.action == "remove":
        try:
            success = worktree_manager.remove_worktree(req.name, force=req.force)
            if not success:
                raise HTTPException(status_code=404, detail=f"Worktree '{req.name}' 不存在")
            return {"name": req.name, "action": "remove", "status": "removed"}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    elif req.action == "assign_reviewer":
        success = worktree_manager.assign_reviewer(req.name, req.reviewer)
        if not success:
            raise HTTPException(status_code=404, detail=f"Worktree '{req.name}' 不存在")
        return {
            "name": req.name,
            "action": "assign_reviewer",
            "reviewer": req.reviewer,
            "status": "reviewing",
        }

    else:
        raise HTTPException(status_code=400, detail=f"未知操作: {req.action}")


@app.post("/api/v7/worktree/review")
async def submit_review(req: ReviewSubmitRequest):
    """提交审查结果（C自: 制造者/检查者闭环）"""
    wt = worktree_manager.get_worktree(req.worktree_name)
    if not wt:
        raise HTTPException(status_code=404, detail=f"Worktree '{req.worktree_name}' 不存在")

    stream = get_or_create_stream()

    if req.verdict == "pass":
        # 审查通过 → 解锁 + 通知
        worktree_manager.unlock_worktree(req.worktree_name)

        event = create_done_action(
            sender=req.reviewer,
            recipient=wt.agent,
            task_id=f"review-{req.worktree_name}",
            what_done=f"审查通过: Worktree {req.worktree_name}",
            where_artifacts=[req.worktree_name],
            how_verify="审查者确认通过",
            known_issues=[],
            what_next="可以合并回主分支",
        )
        stream.publish(event)

        return {
            "worktree": req.worktree_name,
            "verdict": "pass",
            "reviewer": req.reviewer,
            "status": "unlocked",
        }

    elif req.verdict == "fail":
        # 审查不通过 → 解锁但通知修改
        worktree_manager.unlock_worktree(req.worktree_name)

        event = create_action(
            sender=req.reviewer,
            recipient=wt.agent,
            event_type=EventType.WARN,
            content=f"[WARN] 审查未通过: Worktree {req.worktree_name}。问题: {', '.join(req.issues)}。{req.comments}",
        )
        stream.publish(event)

        return {
            "worktree": req.worktree_name,
            "verdict": "fail",
            "reviewer": req.reviewer,
            "issues": req.issues,
            "status": "needs_revision",
        }

    else:
        raise HTTPException(status_code=400, detail=f"verdict必须是pass或fail，收到: {req.verdict}")


@app.get("/api/v7/worktree/{name}/ports")
async def get_worktree_ports(name: str):
    """获取Worktree的端口分配"""
    ports = worktree_manager.assign_ports(name)
    env = worktree_manager.get_worktree_env(name)
    if not ports:
        raise HTTPException(status_code=404, detail=f"Worktree '{name}' 不存在或无端口分配")
    return {"name": name, "ports": ports, "env_vars": env}


@app.get("/api/v7/worktree/stats")
async def get_worktree_stats():
    """获取Worktree全局统计"""
    return worktree_manager.get_stats()


# ==================== 会议室闭环端点（v7.2 新增）====================

# 频道定义（复用v6架构）
CHANNELS = {
    "#general": {
        "name": "综合大厅",
        "members": list(AGENT_TOKENS.values()),
        "assignee_default": None,
    },
    "#tech": {"name": "技术研发", "members": ["澜舟", "灵犀"], "assignee_default": "澜舟"},
    "#creative": {"name": "创意工坊", "members": ["千寻", "澜澜"], "assignee_default": "千寻"},
    "#admin": {"name": "行政管理", "members": ["澜澜", "九重"], "assignee_default": "澜澜"},
}

# Kanban回调存储
kanban_callbacks: dict[str, dict[str, Any]] = {}

# Kanban任务映射：event_id → kanban_task_id（TASK事件创建后记录，DONE事件查映射更新状态）
kanban_task_map: dict[str, str] = {}

# Kanban服务地址（可配置，默认本地8643端口）
KANBAN_API_URL = os.environ.get("KANBAN_API_URL", "http://localhost:8643")


class ChannelSendRequest(BaseModel):
    """频道发送请求（会议室闭环）"""

    content: str
    channel: str | None = None
    recipient: str | None = None
    sender: str | None = None
    token: str | None = None


class KanbanCallbackRequest(BaseModel):
    """Kanban回调请求（会议室闭环）"""

    event: str
    task: dict[str, Any] = Field(default_factory=dict)
    channel: str = "#general"


@app.post("/api/v7/channels/send")
async def channels_send(req: ChannelSendRequest):
    """频道广播+路由发送（会议室闭环端点1）

    融合v6频道路由逻辑 → v7 EventStream：
    1. 识别Agent（token/sender）
    2. 解析频道/收件人/@提及
    3. 创建Event → 发布到EventStream
    4. 检测任务意图 → 通知Kanban
    5. 持久化到SQLite
    """
    # 1. 识别Agent
    sender = resolve_agent(req.token, req.sender)

    # 2. 确定频道和收件人
    channel = req.channel
    recipient = req.recipient
    content = req.content

    # 解析 @提及
    mentions = re.findall(r"@(\S+)", content)
    valid_mentions = [m for m in mentions if m in AGENT_TOKENS.values()]

    # 解析内容中的频道标签
    ch_match = re.match(r"(#\w+)", content)
    if ch_match and ch_match.group(1) in CHANNELS:
        channel = ch_match.group(1)

    # 优先级: recipient > mentions > channel > 广播
    # recipient是请求方明确指定的收件人，sender身份已通过token验证，
    # recipient无需额外验证——开源用户可能未配置完整agent映射
    if recipient:
        # 点对点（信任请求中的recipient）
        pass
    elif valid_mentions:
        # @提及 → 频道广播
        recipient = None
    elif channel and channel in CHANNELS:
        # 频道消息
        recipient = None
    else:
        # 全频道广播
        channel = "#general"
        recipient = None

    # 2.5 P0安全注入：群聊消息安全过滤（在ICP解析前）
    is_chat_safe, safe_chat_content = validate_icp_content(content)
    if not is_chat_safe:
        _chat_sec_logger = structlog.get_logger()
        _chat_sec_logger.warning(
            "chat_injection_detected",
            endpoint="group_message",
            sender=sender,
            channel=channel,
            filtered=True,
        )
    content = safe_chat_content

    # 3. 创建Event
    parsed = parse_icp_message(content)
    event_type = parsed.get("event_type", EventType.INFO)
    clean_content = parsed.get("content", content)

    event = Action(
        event_id=_gen_event_id("act"),
        event_type=event_type,
        sender=sender,
        recipient=recipient or "",
        content=clean_content,
        source=EventSource.AGENT,
    )

    # 4. 发布到 EventStream（v6兼容）
    stream = get_or_create_stream()
    event_id = stream.publish(event)
    persist_event_to_db(event)

    # 4.5 同步到群聊系统（统一存储）
    try:
        mgr = _ensure_group_chat()
        mgr.send_message(
            sender=sender,
            content=clean_content,
            msg_type="text",
            channel=channel or "#general",
            mentions=valid_mentions,
            event_tag=event_type.name if event_type else "",
        )
    except Exception:
        pass

    # 5. 检测任务意图 → 通知Kanban（在原始content上检测，ICP解析前）
    task_intent = _detect_task_intent(content)
    kanban_notified = False
    if task_intent["is_task"] and channel:
        try:
            import requests as http_requests

            assignee = CHANNELS.get(channel, {}).get("assignee_default")
            payload = {
                "title": task_intent["title"],
                "body": f"来自{channel}频道 {sender}: {content}",
                "assignee": assignee,
                "channel": channel,
                "source_message_id": event.event_id,
            }
            resp = http_requests.post("http://localhost:8643/kanban/tasks", json=payload, timeout=5)
            kanban_notified = resp.status_code == 200
        except Exception:
            kanban_notified = False

    return {
        "event_id": event_id,
        "sender": sender,
        "channel": channel,
        "recipient": recipient,
        "task_detected": task_intent["is_task"],
        "kanban_notified": kanban_notified,
        "status": "sent",
    }


@app.post("/api/v7/callback/kanban")
async def kanban_callback_v7(req: KanbanCallbackRequest):
    """Kanban状态变更回调（会议室闭环端点2）

    流程：
    1. 记录回调到内存
    2. 发布Event到EventStream（状态变更通知）
    3. 广播到指定频道
    """
    # 1. 记录回调
    callback_id = f"cb_v7_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    kanban_callbacks[callback_id] = {
        "event": req.event,
        "task": req.task,
        "channel": req.channel,
        "timestamp": datetime.now().isoformat(),
    }

    # 2. 构建通知Event
    task = req.task
    task_id = task.get("id", "unknown")
    status_msg = (
        f"[Kanban] 任务「{task.get('title', '?')}」状态变更: "
        f"{task.get('status', '?')} (负责人: {task.get('assignee', '未分配')})"
    )
    if task.get("result"):
        status_msg += f"\n结果: {task['result']}"

    # 3. 发布到EventStream
    stream = get_or_create_stream()
    event = create_action(
        sender="千寻[Kanban]",
        recipient=req.channel,
        event_type=EventType.UPD,
        content=status_msg,
    )
    event_id = stream.publish(event)
    persist_event_to_db(event)

    return {
        "callback_id": callback_id,
        "event_id": event_id,
        "kanban_event": req.event,
        "task_id": task_id,
        "channel": req.channel,
        "status": "processed",
    }


@app.get("/api/v7/channels")
async def list_channels_v7():
    """列出所有频道（会议室闭环端点3辅助）"""
    return {
        "channels": {
            k: {
                "name": v["name"],
                "members": v["members"],
                "assignee_default": v["assignee_default"],
            }
            for k, v in CHANNELS.items()
        },
        "total": len(CHANNELS),
    }


@app.get("/api/v7/callback/kanban/history")
async def kanban_callback_history(limit: int = Query(50, ge=1, le=200)):
    """查看Kanban回调历史"""
    callbacks = sorted(
        kanban_callbacks.values(),
        key=lambda x: x["timestamp"],
        reverse=True,
    )
    return {
        "total": len(callbacks),
        "callbacks": callbacks[:limit],
    }


def _detect_task_intent(content: str) -> dict:
    """检测消息中的任务创建意图（复用v6逻辑 + v7 ICP兼容）"""
    patterns = [
        r"【任务】\s*(.+)",
        r"(?:创建|新建|添加)任务[:：]\s*(.+)",
        r"(?:TODO|TASK)[：:]\s*(.+)",
        r"\[TASK\]\s*(.+)",  # ICP v1.1标签格式
        r"需要(.+?)(?:完成|处理|解决)",
    ]
    for pat in patterns:
        m = re.search(pat, content)
        if m:
            return {"is_task": True, "title": m.group(1).strip()}
    return {"is_task": False}


# ==================== 灵犀-群聊桥接（方案C通知桥接）====================

# 频道路由规则（九重定调）：
#   简报/技术内容 → #tech
#   资料包/综合信息 → #general
#   私聊对话 → 不同步（专属界面直接对话）
_LINGXI_CHANNEL_RULES = {
    "简报": "#tech",
    "报告": "#tech",
    "调研": "#tech",
    "技术": "#tech",
    "分析": "#tech",
    "资料": "#general",
    "资料包": "#general",
    "分享": "#general",
    "通知": "#general",
}


def _detect_lingxi_channel(user_msg: str, reply: str) -> str | None:
    """根据灵犀对话内容判断应该同步到哪个频道（九重频道路由规则）

    规则：简报→#tech | 资料包→#general | 私聊→None(不同步)
    """
    combined = (user_msg + " " + reply).lower()
    for keyword, channel in _LINGXI_CHANNEL_RULES.items():
        if keyword.lower() in combined:
            return channel
    # 默认：如果回复较长（>100字），视为有价值的输出，同步到#general
    if len(reply.strip()) > 100:
        return "#general"
    # 短对话不同步（保持灵犀界面纯净）
    return None


def _sync_lingxi_to_group(user_message: str, reply: str, mode: str):
    """方案C核心：将灵犀AI回复摘要同步到群聊系统

    在 lingxi_chat_api() 返回成功结果后调用。
    根据频道路由规则自动选择目标频道，只推摘要避免信息过载。

    失败时静默处理，不影响灵犀主功能。
    """
    try:
        target_channel = _detect_lingxi_channel(user_message, reply)
        if not target_channel:
            return  # 短私聊，不同步

        mgr = _ensure_group_chat()

        # 摘要截断：用户消息取前50字，回复取前200字
        user_summary = user_message[:50] + ("..." if len(user_message) > 50 else "")
        reply_summary = reply[:200] + ("..." if len(reply) > 200 else "")

        mode_label = {"chat": "快速沟通", "work": "深度工作"}.get(mode, mode)

        mgr.send_message(
            sender="灵犀🐚",
            content=f"[{mode_label}] 问: {user_summary}\n→ 答: {reply_summary}",
            msg_type="text",
            channel=target_channel,
            mentions=[],
            event_tag="UPD",
        )
        logger.info(
            "lingxi_sync_ok",
            channel=target_channel,
            mode=mode,
            reply_len=len(reply),
            user_msg_len=len(user_message),
        )
    except Exception as e:
        logger.warning("lingxi_sync_failed", error=str(e))


# ==================== 会议室前端页面（v7风格）====================

V7_HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>九重工作室 - 会议室</title>
<style>
:root{--bg:#f5f5f5;--panel:#fff;--border:#e0e0e0;--text:#1a1a2e;--text2:#666;--text3:#999;--accent:#4A90D9;--accent2:#6c5ce7;--meeting-bg:#FFF8E1;--meeting-border:#FFD54F;--self-bg:#4A90D9;--other-bg:#fff;--radius:12px}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"Microsoft YaHei","Segoe UI",sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;overflow:hidden}
.header{background:var(--panel);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;gap:14px;flex-shrink:0}
.header .group-icon{width:42px;height:42px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;color:#fff;font-size:18px;font-weight:700;flex-shrink:0}
.header .info h1{font-size:16px;font-weight:600;color:var(--text)}
.header .info p{font-size:12px;color:var(--text3);margin-top:2px}
.header .actions{margin-left:auto;display:flex;gap:8px;align-items:center}
.header .btn{padding:7px 14px;border:1px solid var(--border);border-radius:8px;background:var(--panel);font-size:13px;cursor:pointer;color:var(--text2);transition:all .15s}
.header .btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-meeting{background:var(--panel);border:1px solid var(--meeting-border);color:#E65100}
.btn-meeting:hover{background:var(--meeting-bg)}
.main{flex:1;display:flex;overflow:hidden}
.sidebar{width:220px;background:var(--panel);border-right:1px solid var(--border);padding:14px;flex-shrink:0;overflow-y:auto;display:flex;flex-direction:column}
.sidebar h3{font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;font-weight:500}
.ch-list{margin-bottom:16px}
.ch-btn{display:flex;align-items:center;gap:6px;width:100%;text-align:left;padding:8px 10px;margin-bottom:2px;border:none;border-radius:8px;background:transparent;cursor:pointer;font-size:13px;color:var(--text);transition:background .12s}
.ch-btn:hover{background:var(--bg)}
.ch-btn.active{background:rgba(74,144,217,.12);color:var(--accent);font-weight:600}
.ch-btn .ch-hash{color:var(--text3);font-size:12px}
.ch-btn.active .ch-hash{color:var(--accent)}
.member-item{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;margin-bottom:2px;transition:background .12s}
.member-item:hover{background:var(--bg)}
.member-avatar{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;font-weight:700;flex-shrink:0}
.member-info{flex:1;min-width:0}
.member-name{font-size:12px;font-weight:500;color:var(--text)}
.member-role{font-size:10px;color:var(--text3)}
.status-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.status-dot.online{background:#4CAF50}
.status-dot.offline{background:#ccc}
.chat-area{flex:1;display:flex;flex-direction:column;overflow:hidden}
.msgs{flex:1;overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:10px}
.msg-row{display:flex;gap:10px;max-width:75%}
.msg-row.self{align-self:flex-end;flex-direction:row-reverse}
.msg-avatar{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;font-weight:700;flex-shrink:0}
.msg-body{display:flex;flex-direction:column;gap:3px}
.msg-row.self .msg-body{align-items:flex-end}
.msg-meta{font-size:11px;color:var(--text3);display:flex;gap:6px;align-items:center}
.msg-meta .name{font-weight:600;color:var(--text2)}
.msg-meta .ch-tag{background:#eef2f7;color:#6b7c93;padding:1px 5px;border-radius:3px;font-size:10px}
.msg-etag{font-size:10px;padding:1px 5px;border-radius:3px;font-weight:600}
.msg-etag.INFO{background:#e3f2fd;color:#1976d2}
.msg-etag.TASK{background:#fff8e1;color:#f57c00}
.msg-etag.WARN{background:#ffebee;color:#c62828}
.msg-etag.DONE{background:#e8f5e9;color:#2e7d32}
.msg-etag.ASK{background:#fbe9e7;color:#d84315}
.msg-etag.UPD{background:#f3e5f5;color:#7b1fa2}
.msg-bubble{padding:10px 14px;border-radius:14px;font-size:14px;line-height:1.5;word-break:break-word;white-space:pre-wrap}
.msg-row:not(.self) .msg-bubble{background:var(--other-bg);border:1px solid var(--border);border-bottom-left-radius:4px}
.msg-row.self .msg-bubble{background:var(--self-bg);color:#fff;border-bottom-right-radius:4px}
.msg-mention{color:var(--accent);font-weight:600}
.msg-row.self .msg-mention{color:#FFD54F}
.meeting-card{background:var(--meeting-bg);border:1px solid var(--meeting-border);border-radius:14px;padding:14px 18px;max-width:75%;border-left:4px solid #FF9800}
.meeting-card .mt-title{font-size:15px;font-weight:600;color:#E65100;margin-bottom:8px}
.meeting-card .mt-row{font-size:13px;color:#5D4037;margin-bottom:4px;display:flex;gap:6px}
.meeting-card .mt-label{font-weight:600;min-width:50px;color:#8D6E63}
.meeting-card .mt-agenda{margin:6px 0;padding-left:8px}
.meeting-card .mt-agenda li{font-size:13px;color:#5D4037;margin-bottom:3px;list-style:none;padding-left:14px;position:relative}
.meeting-card .mt-agenda li:before{content:"";position:absolute;left:0;top:8px;width:5px;height:5px;border-radius:50%;background:#FF9800}
.meeting-card .mt-attendees{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.meeting-card .mt-badge{background:#FFE0B2;color:#E65100;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500}
.empty-state{text-align:center;color:var(--text3);padding:40px 20px}
.empty-state .icon{font-size:36px;margin-bottom:12px;color:var(--accent);font-weight:700}
.empty-state .title{font-size:15px;font-weight:600;margin-bottom:6px;color:var(--text)}
.empty-state .desc{font-size:13px;line-height:1.6}
.input-area{padding:12px 20px 16px;background:var(--panel);border-top:1px solid var(--border);flex-shrink:0}
.input-wrap{display:flex;gap:8px;align-items:flex-end;max-width:900px;margin:0 auto}
.input-wrap select{width:110px;flex:none;padding:10px 8px;border:1px solid var(--border);border-radius:10px;background:var(--panel);color:var(--text);font-size:13px;outline:none;cursor:pointer}
.input-wrap textarea{flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:10px;resize:none;font-size:14px;font-family:inherit;outline:none;height:42px;max-height:120px;line-height:1.4;transition:border-color .15s}
.input-wrap textarea:focus{border-color:var(--accent)}
.input-wrap button{padding:10px 18px;border:none;border-radius:10px;font-size:14px;cursor:pointer;transition:all .15s;flex-shrink:0;font-weight:600}
.btn-send{background:var(--accent);color:#fff}
.btn-send:hover{background:#3A7BC8}
.btn-send:disabled{opacity:.5;cursor:not-allowed}
.meeting-modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.3);z-index:100;align-items:center;justify-content:center}
.meeting-modal.show{display:flex}
.meeting-modal .modal-box{background:var(--panel);border-radius:16px;padding:24px;width:440px;max-width:90vw;box-shadow:0 8px 32px rgba(0,0,0,.15)}
.meeting-modal h2{font-size:18px;margin-bottom:16px;color:var(--text)}
.meeting-modal label{font-size:12px;color:var(--text3);display:block;margin-bottom:4px;margin-top:12px}
.meeting-modal input,.meeting-modal textarea{width:100%;padding:9px 12px;border:1px solid var(--border);border-radius:8px;font-size:14px;font-family:inherit;outline:none}
.meeting-modal input:focus,.meeting-modal textarea:focus{border-color:var(--accent)}
.meeting-modal .modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:20px}
.meeting-modal .modal-actions button{padding:8px 18px;border:none;border-radius:8px;font-size:14px;cursor:pointer}
.meeting-modal .btn-cancel{background:var(--bg);color:var(--text2)}
.meeting-modal .btn-confirm{background:var(--accent);color:#fff}
.scrollbar::-webkit-scrollbar{width:5px}
.scrollbar::-webkit-scrollbar-thumb{background:rgba(0,0,0,.15);border-radius:3px}
.footer{padding:6px 20px;font-size:11px;color:var(--text3);text-align:center;border-top:1px solid var(--border);background:var(--panel)}
@media(max-width:700px){.sidebar{display:none}.msg-row{max-width:90%}.input-wrap select{width:80px}}
</style>
</head>
<body>

<div class="header">
<div class="group-icon">9</div>
<div class="info">
<h1>九重工作室 会议室</h1>
<p id="memberSummary">加载中...</p>
</div>
<div class="actions">
<button class="btn" onclick="scrollToBottom()" title="跳到底部">最新</button>
<button class="btn btn-meeting" onclick="openMeetingModal()">会议通知</button>
</div>
</div>

<div class="main">
<aside class="sidebar scrollbar">
<h3>频道</h3>
<div class="ch-list" id="chList">
<button class="ch-btn active" data-ch="#all"><span class="ch-hash">*</span>全部消息</button>
<button class="ch-btn" data-ch="#general"><span class="ch-hash">#</span>综合大厅</button>
<button class="ch-btn" data-ch="#tech"><span class="ch-hash">#</span>技术研发</button>
<button class="ch-btn" data-ch="#creative"><span class="ch-hash">#</span>创意工坊</button>
<button class="ch-btn" data-ch="#admin"><span class="ch-hash">#</span>行政管理</button>
</div>
<h3>成员</h3>
<div id="memberList"></div>
</aside>

<section class="chat-area">
<div class="msgs scrollbar" id="msgs"></div>
<div class="input-area">
<div class="input-wrap">
<select id="chSel">
<option value="#general">#general</option>
<option value="#tech">#tech</option>
<option value="#creative">#creative</option>
<option value="#admin">#admin</option>
</select>
<textarea id="msgInput" placeholder="输入消息，@提及成员，Enter发送..." rows="1" autofocus></textarea>
<button class="btn-send" id="sendBtn" onclick="sendMsg()">发送</button>
</div>
</div>
</section>
</div>

<div class="meeting-modal" id="meetingModal">
<div class="modal-box">
<h2>发起会议通知</h2>
<label>会议标题</label>
<input id="mtTitle" placeholder="例如：V8架构冲刺会议">
<label>会议时间</label>
<input id="mtTime" placeholder="例如：2026-06-24 14:00">
<label>会议地点</label>
<input id="mtLocation" value="线上会议室" placeholder="线上会议室 / 3号会议室">
<label>通知频道</label>
<select id="mtChannel" style="width:100%;padding:9px 12px;border:1px solid var(--border);border-radius:8px;font-size:14px;margin-top:4px">
<option value="#general">#general 综合大厅</option>
<option value="#tech">#tech 技术研发</option>
<option value="#creative">#creative 创意工坊</option>
<option value="#admin">#admin 行政管理</option>
</select>
<label>议程（每行一条）</label>
<textarea id="mtAgenda" rows="4" placeholder="回顾今日进度&#10;讨论SkillRouter设计&#10;分配明日任务"></textarea>
<div class="modal-actions">
<button class="btn-cancel" onclick="closeMeetingModal()">取消</button>
<button class="btn-confirm" onclick="sendMeeting()">发送通知</button>
</div>
</div>
</div>

<div class="footer">OpenBridge V8(3458) EventStream Kanban(8643) 书阁(3460) ICP v1.1</div>

<script>
var API = location.origin;
var SELF = "九重";
var curCh = "#all";
var lastTs = "";
var ws = null;
var members = [];

var MEMBER_COLORS = {"九重":"#DAA520","澜澜":"#4A90D9","灵犀":"#9B59B6","澜舟":"#2ECC71","千寻":"#E67E22"};
var MEMBER_INITIALS = {"九重":"九","澜澜":"澜","灵犀":"犀","澜舟":"舟","千寻":"寻"};
var MEMBER_NAMES = ["九重","澜澜","灵犀","澜舟","千寻"];
var CHANNEL_NAMES = {"#general":"综合大厅","#tech":"技术研发","#creative":"创意工坊","#admin":"行政管理"};

function esc(t){var d=document.createElement("div");d.textContent=t;return d.innerHTML}
function fmt(iso){try{var d=new Date(iso);return d.toLocaleString("zh-CN",{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}catch(e){return iso}}
function nl2br(t){return t.split(String.fromCharCode(10)).join("<br>")}
function scrollToBottom(){var b=document.getElementById("msgs");b.scrollTop=b.scrollHeight}

function getAvatar(name){
  var c = MEMBER_COLORS[name] || "#888";
  var i = MEMBER_INITIALS[name] || (name ? name[0] : "?");
  return '<div class="msg-avatar" style="background:'+c+'">'+i+'</div>';
}

function renderMentions(text){
  var result = esc(text);
  for(var i=0;i<MEMBER_NAMES.length;i++){
    var pattern = "@"+MEMBER_NAMES[i];
    result = result.split(pattern).join('<span class="msg-mention">'+pattern+'</span>');
  }
  return result;
}

function parseEventTag(content){
  var m = content.match(/^\\[(INFO|TASK|WARN|DONE|ASK|UPD)\\]/);
  if(m) return {tag:m[1], text:content.substring(m[0].length)};
  return {tag:"", text:content};
}

function addMsgToView(m){
  var box=document.getElementById("msgs");
  var isSelf = m.sender === SELF;
  var div=document.createElement("div");

  if(m.msg_type === "meeting" && m.meeting_data){
    var md = m.meeting_data;
    div.className="meeting-card";
    var agendaHtml = "";
    if(md.agenda && md.agenda.length>0){
      agendaHtml = '<div class="mt-agenda">';
      for(var i=0;i<md.agenda.length;i++){agendaHtml += "<li>"+esc(md.agenda[i])+"</li>"}
      agendaHtml += "</div>";
    }
    var attendeesHtml = "";
    if(md.attendees && md.attendees.length>0){
      attendeesHtml = '<div class="mt-attendees">';
      for(var j=0;j<md.attendees.length;j++){attendeesHtml += '<span class="mt-badge">'+esc(md.attendees[j])+'</span>'}
      attendeesHtml += "</div>";
    }
    var senderLine = esc(m.sender) + " - " + fmt(m.timestamp);
    if(m.channel && m.channel !== "#general"){senderLine += ' ['+esc(m.channel)+']'}
    div.innerHTML =
      '<div class="mt-title">' + esc(md.title) + '</div>' +
      '<div class="mt-row"><span class="mt-label">时间</span>' + esc(md.time) + '</div>' +
      '<div class="mt-row"><span class="mt-label">地点</span>' + esc(md.location) + '</div>' +
      agendaHtml + attendeesHtml +
      '<div style="margin-top:8px;font-size:11px;color:#999">发起: ' + senderLine + '</div>';
    box.appendChild(div);
  } else {
    div.className="msg-row" + (isSelf ? " self" : "");
    var parsed = parseEventTag(m.content);
    var content = renderMentions(parsed.text);
    var tagHtml = parsed.tag ? '<span class="msg-etag '+parsed.tag+'">'+parsed.tag+'</span>' : '';
    var chHtml = (m.channel && m.channel !== "#general" && curCh === "#all") ? '<span class="ch-tag">'+esc(m.channel)+'</span>' : '';
    div.innerHTML =
      getAvatar(m.sender) +
      '<div class="msg-body">' +
        '<div class="msg-meta"><span class="name">' + esc(m.sender) + '</span>'+tagHtml+chHtml+'<span>' + fmt(m.timestamp) + '</span></div>' +
        '<div class="msg-bubble">' + nl2br(content) + '</div>' +
      '</div>';
    box.appendChild(div);
  }
  scrollToBottom();
}

async function loadMsgs(){
  try{
    var url = API + "/api/v7/group/messages?limit=50";
    if(curCh && curCh !== "#all"){url += "&channel=" + encodeURIComponent(curCh)}
    var r = await fetch(url);
    var d = await r.json();
    if(d.ok){
      var msgs = d.messages || [];
      var box=document.getElementById("msgs");
      if(msgs.length > 0){
        box.innerHTML = "";
        for(var i=msgs.length-1;i>=0;i--){addMsgToView(msgs[i])}
      } else {
        box.innerHTML = '<div class="empty-state"><div class="icon">9</div><div class="title">九重工作室 会议室</div><div class="desc">发一条消息，全员同时收到<br>支持频道切换 @提及 会议通知 ICP标签</div></div>';
      }
    }
  }catch(e){console.error("loadMsgs error:",e)}
}

async function loadMembers(){
  try{
    var r = await fetch(API + "/api/v7/group/members");
    var d = await r.json();
    if(d.ok){
      members = d.members || [];
      var online = 0;
      var html = "";
      for(var i=0;i<members.length;i++){
        var m = members[i];
        if(m.online) online++;
        html += '<div class="member-item">' +
          '<div class="member-avatar" style="background:'+m.color+'">'+esc(m.avatar)+'</div>' +
          '<div class="member-info"><div class="member-name">'+esc(m.name)+'</div><div class="member-role">'+esc(m.role)+'</div></div>' +
          '<div class="status-dot '+(m.online?'online':'offline')+'"></div>' +
          '</div>';
      }
      document.getElementById("memberList").innerHTML = html;
      document.getElementById("memberSummary").textContent = members.length + " 位成员 - " + online + " 人在线";
    }
  }catch(e){console.error("loadMembers error:",e)}
}

async function sendMsg(){
  var input = document.getElementById("msgInput");
  var text = input.value.trim();
  if(!text) return;
  var ch = document.getElementById("chSel").value;
  input.value = "";
  input.style.height = "42px";

  var mentions = [];
  for(var i=0;i<MEMBER_NAMES.length;i++){
    if(text.indexOf("@"+MEMBER_NAMES[i]) >= 0) mentions.push(MEMBER_NAMES[i]);
  }

  var parsed = parseEventTag(text);

  document.getElementById("sendBtn").disabled = true;
  try{
    var r = await fetch(API + "/api/v7/channels/send", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sender:SELF, content:text, msg_type:"text", mentions:mentions, channel:ch, event_tag:parsed.tag})
    });
    var d = await r.json();
    if(d.ok){addMsgToView(d.message)}
  }catch(e){alert("发送失败: "+e)}
  document.getElementById("sendBtn").disabled = false;
  input.focus();
}

function openMeetingModal(){document.getElementById("meetingModal").classList.add("show")}
function closeMeetingModal(){document.getElementById("meetingModal").classList.remove("show")}

async function sendMeeting(){
  var title = document.getElementById("mtTitle").value.trim();
  var time = document.getElementById("mtTime").value.trim();
  var location = document.getElementById("mtLocation").value.trim() || "线上会议室";
  var channel = document.getElementById("mtChannel").value;
  var agendaText = document.getElementById("mtAgenda").value.trim();
  if(!title || !time){alert("请填写会议标题和时间");return}
  var agenda = agendaText ? agendaText.split(String.fromCharCode(10)).filter(function(s){return s.trim()}) : [];

  try{
    var r = await fetch(API + "/api/v7/group/notify", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sender:SELF, title:title, meeting_time:time, agenda:agenda, location:location, channel:channel})
    });
    var d = await r.json();
    if(d.ok){
      addMsgToView(d.message);
      closeMeetingModal();
      document.getElementById("mtTitle").value = "";
      document.getElementById("mtTime").value = "";
      document.getElementById("mtAgenda").value = "";
    }
  }catch(e){alert("发送失败: "+e)}
}

function connectWS(){
  try{
    var wsUrl = "ws://" + location.host + "/ws/" + SELF;
    ws = new WebSocket(wsUrl);
    ws.onopen = function(){
      ws.send(JSON.stringify({type:"subscribe", channel:"#general"}));
      ws.send(JSON.stringify({type:"subscribe", channel:"#tech"}));
      ws.send(JSON.stringify({type:"subscribe", channel:"#creative"}));
      ws.send(JSON.stringify({type:"subscribe", channel:"#admin"}));
    };
    ws.onmessage = function(ev){
      try{
        var msg = JSON.parse(ev.data);
        if(msg.type === "group_message"){
          if(curCh === "#all" || msg.channel === curCh){addMsgToView(msg)}
        }
      }catch(e){}
    };
    ws.onclose = function(){setTimeout(connectWS, 5000)};
  }catch(e){console.log("WS not available, using polling")}
}

var msgInput = document.getElementById("msgInput");
msgInput.addEventListener("keydown", function(e){
  if(e.key === "Enter" && !e.shiftKey){e.preventDefault(); sendMsg();}
});
msgInput.addEventListener("input", function(){
  this.style.height = "42px";
  this.style.height = Math.min(this.scrollHeight, 120) + "px";
});

document.querySelectorAll(".ch-btn").forEach(function(btn){
  btn.addEventListener("click", function(){
    document.querySelectorAll(".ch-btn").forEach(function(b){b.classList.remove("active")});
    btn.classList.add("active");
    curCh = btn.dataset.ch;
    var sel = document.getElementById("chSel");
    if(curCh === "#all"){sel.value = "#general"}else{sel.value = curCh}
    loadMsgs();
  });
});

document.getElementById("meetingModal").addEventListener("click", function(e){
  if(e.target === this) closeMeetingModal();
});

window.addEventListener("DOMContentLoaded", function(){
  loadMembers();
  loadMsgs();
  connectWS();
  setInterval(loadMembers, 10000);
  setInterval(loadMsgs, 5000);
  msgInput.focus();
});
</script>
</body>
</html>"""


@app.get("/meeting")
async def meeting_room(request: Request):
    """会议室前端页面（v7 EventStream风格）"""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(apply_theme_to_page(V7_HTML_PAGE, request))


# ==================== 灵犀独立聊天界面 ====================

LINGXI_CHAT_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>灵犀 · 独立沟通</title>
<style>
:root{--bg:#fafafa;--card:#fff;--text:#1a1a2e;--text2:#555;--border:#e0e0e0;--accent:#4a90d9;--accent2:#6c5ce7;--user-bg:#4a90d9;--ai-bg:#f0f0f5;--shadow:0 2px 12px rgba(0,0,0,.08);--radius:12px;}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;overflow:hidden;}
.header{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;padding:14px 24px;display:flex;align-items:center;gap:14px;flex-shrink:0;box-shadow:0 2px 8px rgba(0,0,0,.15);}
.header .avatar{width:40px;height:40px;border-radius:50%;background:rgba(255,255,255,.25);display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0;}
.header .title h1{font-size:18px;font-weight:600;}
.header .title p{font-size:12px;opacity:.8;margin-top:2px;}
.header .status{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:13px;}
.dot{width:8px;height:8px;border-radius:50%;background:#4ecca3;animation:pulse 2s infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.chat-area{flex:1;overflow-y:auto;padding:20px 24px;display:flex;flex-direction:column;gap:16px;}
.msg{max-width:80%;padding:12px 18px;border-radius:18px;line-height:1.6;font-size:14.5px;word-break:break-word;position:relative;}
.msg.user{align-self:flex-end;background:var(--user-bg);color:#fff;border-bottom-right-radius:5px;}
.msg.ai{align-self:flex-start;background:var(--ai-bg);color:var(--text);border-bottom-left-radius:5px;border:1px solid var(--border);}
.msg .time{font-size:11px;color:#999;margin-top:6px;text-align:right;}
.msg.user .time{color:rgba(255,255,255,.65)}
.msg .sender{font-size:11px;font-weight:600;margin-bottom:4px;color:var(--accent2)}
.msg.ai .typing{color:#999;font-style:italic}
.input-area{padding:16px 24px 20px;background:var(--card);border-top:1px solid var(--border);flex-shrink:0;}
.input-wrap{display:flex;gap:10px;align-items:flex-end;max-width:900px;margin:0 auto;}
.input-wrap textarea{flex:1;padding:12px 16px;border:2px solid var(--border);border-radius:20px;resize:none;font-size:14.5px;font-family:inherit;outline:none;transition:border-color .2s;height:46px;max-height:120px;line-height:1.4;}
.mode-btn{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.3);color:#fff;padding:6px 14px;border-radius:20px;font-size:12.5px;cursor:pointer;transition:all .2s;white-space:nowrap}
.mode-btn:hover{background:rgba(255,255,255,.25)}
.mode-btn.work{mode-btn.work{background:rgba(255,200,50,.25);border-color:rgba(255,200,50,.5)}
.input-wrap textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(74,144,217,.12)}
.input-wrap button{width:46px;height:46px;border-radius:50%;border:none;background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;font-size:18px;cursor:pointer;transition:transform .15s,box-shadow .15s;flex-shrink:0;display:flex;align-items:center;justify-content:center;}
.input-wrap button:hover{transform:scale(1.06);box-shadow:0 3px 12px rgba(74,144,217,.35)}
.input-wrap button:disabled{opacity:.5;cursor:not-allowed;transform:none}
.welcome{text-align:center;padding:40px 20px;color:var(--text2)}
.welcome .icon{font-size:48px;margin-bottom:12px}
.welcome h2{font-size:20px;margin-bottom:8px}
.welcome p{font-size:14px;line-height:1.7;max-width:500px;margin:0 auto}
.toolbar{display:flex;gap:8px;justify-content:center;padding:8px 24px 0;border-top:1px solid #f5f5f5}
.toolbar button{padding:6px 14px;border:1px solid var(--border);border-radius:16px;background:#fff;font-size:12px;cursor:pointer;transition:all .15s;color:var(--text2)}
.toolbar button:hover{border-color:var(--accent);color:var(--accent)}
.toolbar button.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.group-notif-bar{background:#FFF8E1;border:1px solid #FFD54F;padding:8px 14px;font-size:13px;display:none;margin:0 20px 12px 20px;border-radius:10px;cursor:pointer;transition:all .3s;align-items:center;gap:8px}
.group-notif-bar.show{display:flex}
.notif-tag{background:#E65100;color:#fff;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:600;flex-shrink:0}
.notif-link{color:var(--accent);text-decoration:none;margin-left:auto;font-size:12px;white-space:nowrap}
.notif-link:hover{text-decoration:underline}
.group-notif-bar{background:#FFF8E1;border:1px solid #FFD54F;padding:8px 14px;font-size:13px;display:none;margin:0 20px 12px 20px;border-radius:10px;cursor:pointer;transition:all .3s;align-items:center;gap:8px}
.group-notif-bar.show{display:flex}
.notif-tag{background:#E65100;color:#fff;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:600;flex-shrink:0}
.notif-link{color:var(--accent);text-decoration:none;margin-left:auto;font-size:12px;white-space:nowrap}
.notif-link:hover{text-decoration:underline}
@media(max-width:600px){
.chat-area{padding:14px 14px}.msg{max-width:92%}.input-area{padding:12px 14px 16px}.header{padding:12px 16px}
}
</style>
</head>
<body>
<div class="header">
<div class="avatar">🐚</div>
<div class="title"><h1>灵犀 · Lingxi</h1><p>信息咨询 · 战略分析 · 情报调研</p></div>
<div class="status"><div class="dot"></div><span id="statusText">在线</span></div>
<button id="modeBtn" class="mode-btn" onclick="toggleMode()" title="切换沟通/工作模式">⚡ 快速沟通</button>
<a href="/meeting" target="_blank" title="查看全员群聊会议室" style="color:#fff;font-size:12.5px;text-decoration:none;margin-left:8px;opacity:.8;border:1px solid rgba(255,255,255,.35);padding:5px 10px;border-radius:14px;transition:all .2s;">🏛️ 会议室</a>
</div>

<div id="chatArea" class="chat-area">
<div id="groupNotifBar" class="group-notif-bar" onclick="window.open('/meeting','_blank')"></div>
<div class="welcome">
<div class="icon">🐚</div>
<h2>你好，我是灵犀</h2>
<p>信息咨询与战略分析专员。你可以问我任何调研、分析、战略规划相关的问题。我会认真思考并给出专业回答。</p>
</div>
</div>

<div class="input-area">
<div class="input-wrap">
<textarea id="msgInput" placeholder="输入消息和灵犀对话..." rows="1" autofocus></textarea>
<button id="sendBtn" onclick="sendMessage()" title="发送">➤</button>
</div>
</div>

<script>
const chatArea = document.getElementById("chatArea");
const msgInput = document.getElementById("msgInput");
const sendBtn = document.getElementById("sendBtn");
const statusText = document.getElementById("statusText");
let chatMode = "chat"; // "chat"=快速沟通(flash), "work"=深度工作(pro)

let isSending = false;
const API_BASE = "/api/v7/lingxi/chat";


function toggleMode() {
    chatMode = chatMode === "chat" ? "work" : "chat";
    const btn = document.getElementById("modeBtn");
    if (chatMode === "work") {
        btn.textContent = "🧠 深度工作";
        btn.classList.add("work");
        addMsg("已切换到**深度工作模式**（DeepSeek V4 Pro · 推理强度），适合复杂分析和战略规划。", false);
    } else {
        btn.textContent = "⚡ 快速沟通";
        btn.classList.remove("work");
        addMsg("已切换到**快速沟通模式**（DeepSeek V4 Flash · 极速响应），适合日常对话和信息查询。", false);
    }
}
function addMsg(content, isUser) {
    const welcome = chatArea.querySelector(".welcome");
    if (welcome) welcome.remove();
    const div = document.createElement("div");
    div.className = "msg " + (isUser ? "user" : "ai");
    if (!isUser) div.innerHTML = "<div class='sender'>🐚 灵犀</div>" + content.split(String.fromCharCode(10)).join("<br>") + "<div class='time'>" + new Date().toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}) + "</div>";
    else div.innerHTML = content.split(String.fromCharCode(10)).join("<br>") + "<div class='time'>" + new Date().toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit"}) + "</div>";
    chatArea.appendChild(div);
    chatArea.scrollTop = chatArea.scrollHeight;
}

function showTyping() {
    const div = document.createElement("div");
    div.className = "msg ai";
    div.id = "typing";
    var tip = chatMode === "work" ? "🔍 灵犀正在深度分析中..." : "🐚 灵犀正在思考中...";
    div.innerHTML = "<div class='sender'>🐚 灵犀</div><span class='typing'>" + tip + "</span>";
    chatArea.appendChild(div);
    chatArea.scrollTop = chatArea.scrollHeight;
}

function hideTyping() {
    const t = document.getElementById("typing");
    if (t) t.remove();
}

async function sendMessage() {
    const text = msgInput.value.trim();
    if (!text || isSending) return;
    msgInput.value = "";
    msgInput.style.height = "46px";
    addMsg(text, true);
    isSending = true;
    sendBtn.disabled = true;
    statusText.textContent = chatMode === "work" ? "深度思考中..." : "快速回复中...";
    showTyping();

    try {
        const resp = await fetch(API_BASE, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({message: text, mode: chatMode})
        });
        const data = await resp.json();
        hideTyping();
        if (data.ok) addMsg(data.reply, false);
        else {
            var hint = data.error || "未知错误";
            if (hint.indexOf("超时") !== -1) hint += "\\n💡 提示：可切换到「快速沟通」模式后重试";
            addMsg("抱歉，出现了一个错误：" + hint, false);
        }
    } catch(e) {
        hideTyping();
        addMsg("网络连接失败，请检查服务是否正常。", false);
    }
    isSending = false;
    sendBtn.disabled = false;
    statusText.textContent = "在线";
}

msgInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
msgInput.addEventListener("input", function() {
    this.style.height = "46px";
    this.style.height = Math.min(this.scrollHeight, 120) + "px";
});

window.addEventListener("DOMContentLoaded", () => { msgInput.focus(); connectGroupWS(); });

// === 方案C：群聊通知桥接（WebSocket） ===
let wsGroup = null;
function esc(t){var d=document.createElement("div");d.textContent=t;return d.innerHTML}

function connectGroupWS(){
    try{
        wsGroup = new WebSocket("ws://" + location.host + "/ws/灵犀");
        wsGroup.onopen = function(){
            wsGroup.send(JSON.stringify({type:"subscribe", channel:"#general"}));
            wsGroup.send(JSON.stringify({type:"subscribe", channel:"#tech"}));
            console.log("[群聊桥接] 已连接，订阅 #general + #tech");
        };
        wsGroup.onmessage = function(ev){
            try{
                var msg = JSON.parse(ev.data);
                if(msg.type === "group_message"){
                    // 只推送 @灵犀 / TASK / WARN 关键消息
                    var isMention = msg.mentions && JSON.parse(msg.mentions || "[]").indexOf("灵犀") >= 0;
                    var isKey = msg.event_tag === "TASK" || msg.event_tag === "WARN";
                    if(isMention || isKey){
                        showGroupNotif(msg);
                    }
                }
            }catch(e){}
        };
        wsGroup.onclose = function(){ setTimeout(connectGroupWS, 5000); };
    }catch(e){ console.log("[群聊桥接] WebSocket不可用"); }
}

function showGroupNotif(msg){
    var bar = document.getElementById("groupNotifBar");
    var tagHtml = msg.event_tag ? '<span class="notif-tag">'+esc(msg.event_tag)+'</span> ' : '';
    var contentPreview = (msg.content||"").substring(0, 80);
    bar.innerHTML = tagHtml + '<strong>'+esc(msg.sender)+'</strong>: '+esc(contentPreview)+(msg.content.length>80?'...':'')
        + ' <a href="/meeting" target="_blank" class="notif-link" onclick="event.stopPropagation()">查看详情 →</a>';
    bar.classList.add("show");
    // 15秒后自动隐藏
    clearTimeout(showGroupNotif._timer);
    showGroupNotif._timer = setTimeout(function(){ bar.classList.remove("show"); }, 15000);
}
</script>
</body>
</html>"""


@app.get("/lingxi")
async def lingxi_chat(request: Request):
    """灵犀独立沟通界面（桌面入口）"""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(apply_theme_to_page(LINGXI_CHAT_PAGE, request))


class LingxiChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    mode: str = Field(default="chat", pattern="^(chat|work)$")  # chat=快速沟通, work=深度工作


@app.post("/api/v7/lingxi/chat")
async def lingxi_chat_api(req: LingxiChatRequest):
    """灵犀聊天API - 双通道智能路由

    性能对比（2026-06-23实测）:
    - 直接DeepSeek API: 1.3s (baseline)
    - Gateway HTTP API: 5-8s (常驻进程，省去CLI启动开销)
    - OpenClaw CLI:      20s+ (每次启动新Node.js进程，已废弃)

    通道选择:
    - chat模式: 直接调DeepSeek Flash API + 灵犀人设 → 1-2秒
    - work模式: Gateway HTTP API调灵犀Pro(完整Agent能力) → 5-8秒
    """
    logger = structlog.get_logger()
    try:
        import httpx

        if req.mode == "chat":
            # === chat模式：直接调DeepSeek Flash API + 完整灵犀人设（v3·身份锚定版） ===
            lingxi_system = (
                "=== Identity ===\n"
                "You are a HomeStream AI assistant, running inside the HomeStream agent ecosystem.\n"
                "You have access to web search, data analysis, and multi-agent communication tools.\n"
                "Speak in the user's preferred language.\n"
                "\n"
                "=== Capabilities ===\n"
                "1. Web search: get real-time information\n"
                "2. Market research: aggregate multi-source data + competitive analysis\n"
                "3. Strategic analysis: SWOT + positioning + priority recommendations\n"
                "4. Report generation: from quick briefs to in-depth reports\n"
                "5. Knowledge retrieval: query connected knowledge bases\n"
                "6. Team communication: relay messages to other agents\n"
                "\n"
                "=== Behavior Rules ===\n"
                "- When asked to research: deliver analysis directly, don't describe process\n"
                "- When asked to judge: give conclusions and recommendations, not multiple-choice lists\n"
                "- When uncertain: state confidence level (high/medium/low)\n"
                "- When dealing with tech: analyze feasibility, delegate implementation to dev agents\n"
                "\n"
                "=== Output Format ===\n"
                "[Core Conclusion] (3 sentences max)\n"
                "- Finding 1 (with data or source)\n"
                "- Finding 2 (with data or source)\n"
                "- Finding 3 (with data or source)\n"
                "[Recommendation]\n"
                "1. Priority - Action - Reason\n"
                "\n"
                "=== Red Lines ===\n"
                "Never leak private data | Mark confidence when uncertain | Flag risks first | Ask before external publishing"
            )

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.deepseek_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": lingxi_system},
                            {"role": "user", "content": req.message},
                        ],
                        "max_tokens": 1024,
                        "temperature": 0.7,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    reply = data["choices"][0]["message"]["content"]
                    # 方案C：灵犀回复自动同步到群聊
                    _sync_lingxi_to_group(req.message, reply, "chat")
                    return {"ok": True, "reply": reply, "mode": "chat", "source": "deepseek-flash"}
                else:
                    return {
                        "ok": False,
                        "error": f"API error {resp.status_code}: {resp.text[:200]}",
                    }

        elif req.mode == "work":
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    gateway_url = settings.openclaw_gateway_url.rstrip("/")
                    resp = await client.post(
                        f"{gateway_url}/api/agent/chat",
                        headers={
                            "Authorization": f"Bearer {settings.openclaw_gateway_token}",
                            "Content-Type": "application/json",
                        },
                        json={"agent": "lingxi-chat", "message": req.message, "local": True},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        reply = data.get("reply", "") or data.get("response", "") or ""
                        if isinstance(data.get("result"), str) and not reply:
                            reply = data["result"]
                        if reply:
                            # 方案C：灵犀回复自动同步到群聊
                            _sync_lingxi_to_group(req.message, reply, "work")
                            return {
                                "ok": True,
                                "reply": reply,
                                "mode": "work",
                                "source": "openclaw-gateway",
                            }
                        return {"ok": False, "error": f"Gateway空回复: {str(data)[:200]}"}
                    hint = f"Gateway({resp.status_code})"
                    if resp.status_code == 599:
                        hint += " 灵犀离线"
                    elif resp.status_code == 504:
                        hint += " 超时"
                    return {"ok": False, "error": hint}
            except Exception as e:
                err = str(e)
                hint = "连接失败"
                if "timeout" in err.lower():
                    hint = "超时"
                elif "refused" in err.lower():
                    hint = "Gateway未启动"
                return {"ok": False, "error": f"{hint}: {err[:100]}"}
        else:
            return {"ok": False, "error": f"未知模式: {req.mode}"}

    except Exception as e:
        logger.error(f"灵犀异常: {e}", exc_info=True)
        return {"ok": False, "error": f"内部错误: {str(e)[:200]}"}


# ============================================================
# 群聊API
# ============================================================


class GroupMessageRequest(BaseModel):
    """群聊消息请求体（FastAPI Pydantic模型）"""

    sender: str
    content: str
    msg_type: str = "text"
    channel: str = "#general"
    mentions: list[str] | None = None
    event_tag: str = ""


@app.post("/api/v7/group/send")
async def group_send(req: GroupMessageRequest):
    manager = get_group_chat_manager()
    msg = manager.send_message(
        sender=req.sender,
        content=req.content,
        msg_type=req.msg_type,
        mentions=req.mentions or [],
        channel=req.channel or "#general",
        event_tag=req.event_tag or "",
    )
    return {"ok": True, "msg_id": msg.msg_id, "message": msg.to_dict()}


@app.post("/api/v7/group/notify")
async def group_notify(req: GroupMessageRequest):
    manager = get_group_chat_manager()
    msg = manager.send_message(
        sender=req.sender,
        content=req.content,
        msg_type="notify",
        mentions=req.mentions or [],
        channel=req.channel or "#general",
        event_tag="system_notify" if not req.event_tag else req.event_tag,
    )
    return {"ok": True, "msg_id": msg.msg_id, "message": msg.to_dict()}


@app.get("/api/v7/group/messages")
async def group_messages(channel: str = "#general", limit: int = 50, before: str = ""):
    manager = get_group_chat_manager()
    msgs = manager.get_messages(channel=channel, limit=limit, before=before or None)
    return {"ok": True, "messages": msgs, "total": len(msgs)}


@app.get("/api/v7/group/members")
async def group_members():
    manager = get_group_chat_manager()
    return {"ok": True, "members": manager.get_online_members()}


@app.get("/api/v7/group/stats")
async def group_stats():
    manager = get_group_chat_manager()
    return {"ok": True, **manager.get_stats()}


# ============================================================
# 前端页面
# ============================================================

UNIFIED_CHAT_PAGE = (
    '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>九重工'
    "作室 - 会议室</title>\n<style>\n:root{--bg:#f5f5f5;--panel:#fff;--border:#e0e0e0;--text:#1a1a2e;--text2:#666;--text3:#999;--accent:#4A90D9;--accent2:#6c5ce7;--meetin"
    "g-bg:#FFF8E1;--meeting-border:#FFD54F;--self-bg:#4A90D9;--other-bg:#fff;--radius:12px}\n*{margin:0;padding:0;box-sizing:border-box}\nbody{font-family:-apple-sys"
    'tem,"Microsoft YaHei","Segoe UI",sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;overflow:hidden}\n.header'
    "{background:var(--panel);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;gap:14px;flex-shrink:0}\n.header .group-icon{wi"
    "dth:42px;height:42px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent2));display:flex;align-items:center;justify-content:center;c"
    "olor:#fff;font-size:18px;font-weight:700;flex-shrink:0}\n.header .info h1{font-size:16px;font-weight:600;color:var(--text)}\n.header .info p{font-size:12px;colo"
    "r:var(--text3);margin-top:2px}\n.header .actions{margin-left:auto;display:flex;gap:8px;align-items:center}\n.header .btn{padding:7px 14px;border:1px solid var(-"
    "-border);border-radius:8px;background:var(--panel);font-size:13px;cursor:pointer;color:var(--text2);transition:all .15s}\n.header .btn:hover{border-color:var(--"
    "accent);color:var(--accent)}\n.btn-meeting{background:var(--panel);border:1px solid var(--meeting-border);color:#E65100}\n.btn-meeting:hover{background:var(--me"
    "eting-bg)}\n.main{flex:1;display:flex;overflow:hidden}\n.sidebar{width:220px;background:var(--panel);border-right:1px solid var(--border);padding:14px;flex-shri"
    "nk:0;overflow-y:auto;display:flex;flex-direction:column}\n.sidebar h3{font-size:11px;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bot"
    "tom:8px;font-weight:500}\n.ch-list{margin-bottom:16px}\n.ch-btn{display:flex;align-items:center;gap:6px;width:100%;text-align:left;padding:8px 10px;margin-botto"
    "m:2px;border:none;border-radius:8px;background:transparent;cursor:pointer;font-size:13px;color:var(--text);transition:background .12s}\n.ch-btn:hover{background"
    ":var(--bg)}\n.ch-btn.active{background:rgba(74,144,217,.12);color:var(--accent);font-weight:600}\n.ch-btn .ch-hash{color:var(--text3);font-size:12px}\n.ch-btn.a"
    "ctive .ch-hash{color:var(--accent)}\n.member-item{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;margin-bottom:2px;transition:backgro"
    "und .12s}\n.member-item:hover{background:var(--bg)}\n.member-avatar{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:cen"
    "ter;color:#fff;font-size:10px;font-weight:700;flex-shrink:0}\n.member-info{flex:1;min-width:0}\n.member-name{font-size:12px;font-weight:500;color:var(--text)}\n"
    ".member-role{font-size:10px;color:var(--text3)}\n.status-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}\n.status-dot.online{background:#4CAF50}\n.sta"
    "tus-dot.offline{background:#ccc}\n.chat-area{flex:1;display:flex;flex-direction:column;overflow:hidden}\n.msgs{flex:1;overflow-y:auto;padding:16px 20px;display:"
    "flex;flex-direction:column;gap:10px}\n.msg-row{display:flex;gap:10px;max-width:75%}\n.msg-row.self{align-self:flex-end;flex-direction:row-reverse}\n.msg-avatar{"
    "width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-size:10px;font-weight:700;flex-shrink:0}\n.msg-b"
    "ody{display:flex;flex-direction:column;gap:3px}\n.msg-row.self .msg-body{align-items:flex-end}\n.msg-meta{font-size:11px;color:var(--text3);display:flex;gap:6px"
    ";align-items:center}\n.msg-meta .name{font-weight:600;color:var(--text2)}\n.msg-meta .ch-tag{background:#eef2f7;color:#6b7c93;padding:1px 5px;border-radius:3px;"
    "font-size:10px}\n.msg-etag{font-size:10px;padding:1px 5px;border-radius:3px;font-weight:600}\n.msg-etag.INFO{background:#e3f2fd;color:#1976d2}\n.msg-etag.TASK{b"
    "ackground:#fff8e1;color:#f57c00}\n.msg-etag.WARN{background:#ffebee;color:#c62828}\n.msg-etag.DONE{background:#e8f5e9;color:#2e7d32}\n.msg-etag.ASK{background:#"
    "fbe9e7;color:#d84315}\n.msg-etag.UPD{background:#f3e5f5;color:#7b1fa2}\n.msg-bubble{padding:10px 14px;border-radius:14px;font-size:14px;line-height:1.5;word-bre"
    "ak:break-word;white-space:pre-wrap}\n.msg-row:not(.self) .msg-bubble{background:var(--other-bg);border:1px solid var(--border);border-bottom-left-radius:4px}\n."
    "msg-row.self .msg-bubble{background:var(--self-bg);color:#fff;border-bottom-right-radius:4px}\n.msg-mention{color:var(--accent);font-weight:600}\n.msg-row.self "
    ".msg-mention{color:#FFD54F}\n.meeting-card{background:var(--meeting-bg);border:1px solid var(--meeting-border);border-radius:14px;padding:14px 18px;max-width:75"
    "%;border-left:4px solid #FF9800}\n.meeting-card .mt-title{font-size:15px;font-weight:600;color:#E65100;margin-bottom:8px}\n.meeting-card .mt-row{font-size:13px;"
    "color:#5D4037;margin-bottom:4px;display:flex;gap:6px}\n.meeting-card .mt-label{font-weight:600;min-width:50px;color:#8D6E63}\n.meeting-card .mt-agenda{margin:6p"
    "x 0;padding-left:8px}\n.meeting-card .mt-agenda li{font-size:13px;color:#5D4037;margin-bottom:3px;list-style:none;padding-left:14px;position:relative}\n.meeting"
    '-card .mt-agenda li:before{content:"";position:absolute;left:0;top:8px;width:5px;height:5px;border-radius:50%;background:#FF9800}\n.meeting-card .mt-attendees'
    "{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}\n.meeting-card .mt-badge{background:#FFE0B2;color:#E65100;padding:2px 8px;border-radius:10px;font-size:11px"
    ";font-weight:500}\n.empty-state{text-align:center;color:var(--text3);padding:40px 20px}\n.empty-state .icon{font-size:36px;margin-bottom:12px;color:var(--accent"
    ");font-weight:700}\n.empty-state .title{font-size:15px;font-weight:600;margin-bottom:6px;color:var(--text)}\n.empty-state .desc{font-size:13px;line-height:1.6}"
    "\n.input-area{padding:12px 20px 16px;background:var(--panel);border-top:1px solid var(--border);flex-shrink:0}\n.input-wrap{display:flex;gap:8px;align-items:fle"
    "x-end;max-width:900px;margin:0 auto}\n.input-wrap select{width:110px;flex:none;padding:10px 8px;border:1px solid var(--border);border-radius:10px;background:var"
    "(--panel);color:var(--text);font-size:13px;outline:none;cursor:pointer}\n.input-wrap textarea{flex:1;padding:10px 14px;border:1px solid var(--border);border-rad"
    "ius:10px;resize:none;font-size:14px;font-family:inherit;outline:none;height:42px;max-height:120px;line-height:1.4;transition:border-color .15s}\n.input-wrap tex"
    "tarea:focus{border-color:var(--accent)}\n.input-wrap button{padding:10px 18px;border:none;border-radius:10px;font-size:14px;cursor:pointer;transition:all .15s;f"
    "lex-shrink:0;font-weight:600}\n.btn-send{background:var(--accent);color:#fff}\n.btn-send:hover{background:#3A7BC8}\n.btn-send:disabled{opacity:.5;cursor:not-all"
    "owed}\n.meeting-modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.3);z-index:100;align-items:center;justify-content:c"
    "enter}\n.meeting-modal.show{display:flex}\n.meeting-modal .modal-box{background:var(--panel);border-radius:16px;padding:24px;width:440px;max-width:90vw;box-shad"
    "ow:0 8px 32px rgba(0,0,0,.15)}\n.meeting-modal h2{font-size:18px;margin-bottom:16px;color:var(--text)}\n.meeting-modal label{font-size:12px;color:var(--text3);d"
    "isplay:block;margin-bottom:4px;margin-top:12px}\n.meeting-modal input,.meeting-modal textarea{width:100%;padding:9px 12px;border:1px solid var(--border);border-"
    "radius:8px;font-size:14px;font-family:inherit;outline:none}\n.meeting-modal input:focus,.meeting-modal textarea:focus{border-color:var(--accent)}\n.meeting-moda"
    "l .modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:20px}\n.meeting-modal .modal-actions button{padding:8px 18px;border:none;border-radius"
    ":8px;font-size:14px;cursor:pointer}\n.meeting-modal .btn-cancel{background:var(--bg);color:var(--text2)}\n.meeting-modal .btn-confirm{background:var(--accent);c"
    "olor:#fff}\n.scrollbar::-webkit-scrollbar{width:5px}\n.scrollbar::-webkit-scrollbar-thumb{background:rgba(0,0,0,.15);border-radius:3px}\n.footer{padding:6px 20p"
    "x;font-size:11px;color:var(--text3);text-align:center;border-top:1px solid var(--border);background:var(--panel)}\n@media(max-width:700px){.sidebar{display:none"
    '}.msg-row{max-width:90%}.input-wrap select{width:80px}}\n</style>\n</head>\n<body>\n\n<div class="header">\n<div class="group-icon">9</div>\n<div class="in'
    'fo">\n<h1>九重工作室 会议室</h1>\n<p id="memberSummary">加载中...</p>\n</div>\n<div class="actions">\n<button class="btn" onclick="scrollToBottom()" title="跳到底部'
    '">最新</button>\n<button class="btn btn-meeting" onclick="openMeetingModal()">会议通知</button>\n</div>\n</div>\n\n<div class="main">\n<aside class="sidebar s'
    'crollbar">\n<h3>频道</h3>\n<div class="ch-list" id="chList">\n<button class="ch-btn active" data-ch="#all"><span class="ch-hash">*</span>全部消息</button>'
    '\n<button class="ch-btn" data-ch="#general"><span class="ch-hash">#</span>综合大厅</button>\n<button class="ch-btn" data-ch="#tech"><span class="ch-hash'
    '">#</span>技术研发</button>\n<button class="ch-btn" data-ch="#creative"><span class="ch-hash">#</span>创意工坊</button>\n<button class="ch-btn" data-ch="#admi'
    'n"><span class="ch-hash">#</span>行政管理</button>\n</div>\n<h3>成员</h3>\n<div id="memberList"></div>\n</aside>\n\n<section class="chat-area">\n<div class="m'
    'sgs scrollbar" id="msgs"></div>\n<div class="input-area">\n<div class="input-wrap">\n<select id="chSel">\n<option value="#general">#general</option>'
    '\n<option value="#tech">#tech</option>\n<option value="#creative">#creative</option>\n<option value="#admin">#admin</option>\n</select>\n<textarea id="ms'
    'gInput" placeholder="输入消息，@提及成员，Enter发送..." rows="1" autofocus></textarea>\n<button class="btn-send" id="sendBtn" onclick="sendMsg()">发送</button>\n</'
    'div>\n</div>\n</section>\n</div>\n\n<div class="meeting-modal" id="meetingModal">\n<div class="modal-box">\n<h2>发起会议通知</h2>\n<label>会议标题</label>\n<input i'
    'd="mtTitle" placeholder="例如：V8架构冲刺会议">\n<label>会议时间</label>\n<input id="mtTime" placeholder="例如：2026-06-24 14:00">\n<label>会议地点</label>\n<input id="mtL'
    'ocation" value="线上会议室" placeholder="线上会议室 / 3号会议室">\n<label>通知频道</label>\n<select id="mtChannel" style="width:100%;padding:9px 12px;border:1px solid var'
    '(--border);border-radius:8px;font-size:14px;margin-top:4px">\n<option value="#general">#general 综合大厅</option>\n<option value="#tech">#tech 技术研发</option>\n<'
    'option value="#creative">#creative 创意工坊</option>\n<option value="#admin">#admin 行政管理</option>\n</select>\n<label>议程（每行一条）</label>\n<textarea id="mtAgenda"'
    ' rows="4" placeholder="回顾今日进度&#10;讨论SkillRouter设计&#10;分配明日任务"></textarea>\n<div class="modal-actions">\n<button class="btn-cancel" onclick="closeMeetin'
    'gModal()">取消</button>\n<button class="btn-confirm" onclick="sendMeeting()">发送通知</button>\n</div>\n</div>\n</div>\n\n<div class="footer">OpenBridge V8(3458) EventSt'
    'ream Kanban(8643) 书阁(3460) ICP v1.1</div>\n\n<script>\nvar API = location.origin;\nvar SELF = "九重";\nvar curCh = "#all";\nvar lastTs = "";\nvar ws = null;'
    '\nvar members = [];\n\nvar MEMBER_COLORS = {"九重":"#DAA520","澜澜":"#4A90D9","灵犀":"#9B59B6","澜舟":"#2ECC71","千寻":"#E67E22"};\nvar MEMBER_INITIAL'
    'S = {"九重":"九","澜澜":"澜","灵犀":"犀","澜舟":"舟","千寻":"寻"};\nvar MEMBER_NAMES = ["九重","澜澜","灵犀","澜舟","千寻"];\nvar CHANNEL_NAMES = {"#gener'
    'al":"综合大厅","#tech":"技术研发","#creative":"创意工坊","#admin":"行政管理"};\n\nfunction esc(t){var d=document.createElement("div");d.textContent=t;return d.'
    'innerHTML}\nfunction fmt(iso){try{var d=new Date(iso);return d.toLocaleString("zh-CN",{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}c'
    'atch(e){return iso}}\nfunction nl2br(t){return t.split(String.fromCharCode(10)).join("<br>")}\nfunction scrollToBottom(){var b=document.getElementById("msgs'
    '");b.scrollTop=b.scrollHeight}\n\nfunction getAvatar(name){\n  var c = MEMBER_COLORS[name] || "#888";\n  var i = MEMBER_INITIALS[name] || (name ? name[0] : '
    "\"?\");\n  return '<div class=\"msg-avatar\" style=\"background:'+c+'\">'+i+'</div>';\n}\n\nfunction renderMentions(text){\n  var result = esc(text);\n  for(var"
    ' i=0;i<MEMBER_NAMES.length;i++){\n    var pattern = "@"+MEMBER_NAMES[i];\n    result = result.split(pattern).join(\'<span class="msg-mention">\'+pattern+\'</sp'
    "an>');\n  }\n  return result;\n}\n\nfunction parseEventTag(content){\n  var m = content.match(/^\\[(INFO|TASK|WARN|DONE|ASK|UPD)\\]/);\n  if(m) return {tag:m[1]"
    ', text:content.substring(m[0].length)};\n  return {tag:"", text:content};\n}\n\nfunction addMsgToView(m){\n  var box=document.getElementById("msgs");\n  var'
    ' isSelf = m.sender === SELF;\n  var div=document.createElement("div");\n\n  if(m.msg_type === "meeting" && m.meeting_data){\n    var md = m.meeting_data;\n '
    '   div.className="meeting-card";\n    var agendaHtml = "";\n    if(md.agenda && md.agenda.length>0){\n      agendaHtml = \'<div class="mt-agenda">\';\n     '
    ' for(var i=0;i<md.agenda.length;i++){agendaHtml += "<li>"+esc(md.agenda[i])+"</li>"}\n      agendaHtml += "</div>";\n    }\n    var attendeesHtml = "";'
    "\n    if(md.attendees && md.attendees.length>0){\n      attendeesHtml = '<div class=\"mt-attendees\">';\n      for(var j=0;j<md.attendees.length;j++){attendeesH"
    'tml += \'<span class="mt-badge">\'+esc(md.attendees[j])+\'</span>\'}\n      attendeesHtml += "</div>";\n    }\n    var senderLine = esc(m.sender) + " - " + fm'
    "t(m.timestamp);\n    if(m.channel && m.channel !== \"#general\"){senderLine += ' ['+esc(m.channel)+']'}\n    div.innerHTML =\n      '<div class=\"mt-title\">' +"
    " esc(md.title) + '</div>' +\n      '<div class=\"mt-row\"><span class=\"mt-label\">时间</span>' + esc(md.time) + '</div>' +\n      '<div class=\"mt-row\"><span cl"
    "ass=\"mt-label\">地点</span>' + esc(md.location) + '</div>' +\n      agendaHtml + attendeesHtml +\n      '<div style=\"margin-top:8px;font-size:11px;color:#999\">"
    '发起: \' + senderLine + \'</div>\';\n    box.appendChild(div);\n  } else {\n    div.className="msg-row" + (isSelf ? " self" : "");\n    var parsed = parseEvent'
    "Tag(m.content);\n    var content = renderMentions(parsed.text);\n    var tagHtml = parsed.tag ? '<span class=\"msg-etag '+parsed.tag+'\">'+parsed.tag+'</span>' "
    ": '';\n    var chHtml = (m.channel && m.channel !== \"#general\" && curCh === \"#all\") ? '<span class=\"ch-tag\">'+esc(m.channel)+'</span>' : '';\n    div.inne"
    "rHTML =\n      getAvatar(m.sender) +\n      '<div class=\"msg-body\">' +\n        '<div class=\"msg-meta\"><span class=\"name\">' + esc(m.sender) + '</span>'+ta"
    "gHtml+chHtml+'<span>' + fmt(m.timestamp) + '</span></div>' +\n        '<div class=\"msg-bubble\">' + nl2br(content) + '</div>' +\n      '</div>';\n    box.appen"
    'dChild(div);\n  }\n  scrollToBottom();\n}\n\nasync function loadMsgs(){\n  try{\n    var url = API + "/api/v7/group/messages?limit=50";\n    if(curCh && curCh'
    ' !== "#all"){url += "&channel=" + encodeURIComponent(curCh)}\n    var r = await fetch(url);\n    var d = await r.json();\n    if(d.ok){\n      var msgs = d.'
    'messages || [];\n      var box=document.getElementById("msgs");\n      if(msgs.length > 0){\n        box.innerHTML = "";\n        for(var i=msgs.length-1;i>'
    '=0;i--){addMsgToView(msgs[i])}\n      } else {\n        box.innerHTML = \'<div class="empty-state"><div class="icon">9</div><div class="title">九重工作室 会议室</d'
    'iv><div class="desc">发一条消息，全员同时收到<br>支持频道切换 @提及 会议通知 ICP标签</div></div>\';\n      }\n    }\n  }catch(e){console.error("loadMsgs error:",e)}\n}\n\nasync functi'
    'on loadMembers(){\n  try{\n    var r = await fetch(API + "/api/v7/group/members");\n    var d = await r.json();\n    if(d.ok){\n      members = d.members || ['
    '];\n      var online = 0;\n      var html = "";\n      for(var i=0;i<members.length;i++){\n        var m = members[i];\n        if(m.online) online++;\n      '
    "  html += '<div class=\"member-item\">' +\n          '<div class=\"member-avatar\" style=\"background:'+m.color+'\">'+esc(m.avatar)+'</div>' +\n          '<div "
    'class="member-info"><div class="member-name">\'+esc(m.name)+\'</div><div class="member-role">\'+esc(m.role)+\'</div></div>\' +\n          \'<div class="status-'
    "dot '+(m.online?'online':'offline')+'\"></div>' +\n          '</div>';\n      }\n      document.getElementById(\"memberList\").innerHTML = html;\n      document"
    '.getElementById("memberSummary").textContent = members.length + " 位成员 - " + online + " 人在线";\n    }\n  }catch(e){console.error("loadMembers error:",e)}'
    '\n}\n\nasync function sendMsg(){\n  var input = document.getElementById("msgInput");\n  var text = input.value.trim();\n  if(!text) return;\n  var ch = docume'
    'nt.getElementById("chSel").value;\n  input.value = "";\n  input.style.height = "42px";\n\n  var mentions = [];\n  for(var i=0;i<MEMBER_NAMES.length;i++){'
    '\n    if(text.indexOf("@"+MEMBER_NAMES[i]) >= 0) mentions.push(MEMBER_NAMES[i]);\n  }\n\n  var parsed = parseEventTag(text);\n\n  document.getElementById("se'
    'ndBtn").disabled = true;\n  try{\n    var r = await fetch(API + "/api/v7/group/send", {\n      method:"POST",\n      headers:{"Content-Type":"applicatio'
    'n/json"},\n      body:JSON.stringify({sender:SELF, content:text, msg_type:"text", mentions:mentions, channel:ch, event_tag:parsed.tag})\n    });\n    var d ='
    ' await r.json();\n    if(d.ok){addMsgToView(d.message)}\n  }catch(e){alert("发送失败: "+e)}\n  document.getElementById("sendBtn").disabled = false;\n  input.foc'
    'us();\n}\n\nfunction openMeetingModal(){document.getElementById("meetingModal").classList.add("show")}\nfunction closeMeetingModal(){document.getElementById'
    '("meetingModal").classList.remove("show")}\n\nasync function sendMeeting(){\n  var title = document.getElementById("mtTitle").value.trim();\n  var time = '
    'document.getElementById("mtTime").value.trim();\n  var location = document.getElementById("mtLocation").value.trim() || "线上会议室";\n  var channel = document'
    '.getElementById("mtChannel").value;\n  var agendaText = document.getElementById("mtAgenda").value.trim();\n  if(!title || !time){alert("请填写会议标题和时间");retur'
    'n}\n  var agenda = agendaText ? agendaText.split(String.fromCharCode(10)).filter(function(s){return s.trim()}) : [];\n\n  try{\n    var r = await fetch(API + "'
    '/api/v7/group/notify", {\n      method:"POST",\n      headers:{"Content-Type":"application/json"},\n      body:JSON.stringify({sender:SELF, title:title, '
    "meeting_time:time, agenda:agenda, location:location, channel:channel})\n    });\n    var d = await r.json();\n    if(d.ok){\n      addMsgToView(d.message);\n   "
    '   closeMeetingModal();\n      document.getElementById("mtTitle").value = "";\n      document.getElementById("mtTime").value = "";\n      document.getEl'
    'ementById("mtAgenda").value = "";\n    }\n  }catch(e){alert("发送失败: "+e)}\n}\n\nfunction connectWS(){\n  try{\n    var wsUrl = "ws://" + location.host + '
    '"/ws/" + SELF;\n    ws = new WebSocket(wsUrl);\n    ws.onopen = function(){\n      ws.send(JSON.stringify({type:"subscribe", channel:"#general"}));\n     '
    ' ws.send(JSON.stringify({type:"subscribe", channel:"#tech"}));\n      ws.send(JSON.stringify({type:"subscribe", channel:"#creative"}));\n      ws.send(J'
    'SON.stringify({type:"subscribe", channel:"#admin"}));\n    };\n    ws.onmessage = function(ev){\n      try{\n        var msg = JSON.parse(ev.data);\n       '
    ' if(msg.type === "group_message"){\n          if(curCh === "#all" || msg.channel === curCh){addMsgToView(msg)}\n        }\n      }catch(e){}\n    };\n    ws'
    '.onclose = function(){setTimeout(connectWS, 5000)};\n  }catch(e){console.log("WS not available, using polling")}\n}\n\nvar msgInput = document.getElementById('
    '"msgInput");\nmsgInput.addEventListener("keydown", function(e){\n  if(e.key === "Enter" && !e.shiftKey){e.preventDefault(); sendMsg();}\n});\nmsgInput.add'
    'EventListener("input", function(){\n  this.style.height = "42px";\n  this.style.height = Math.min(this.scrollHeight, 120) + "px";\n});\n\ndocument.querySe'
    'lectorAll(".ch-btn").forEach(function(btn){\n  btn.addEventListener("click", function(){\n    document.querySelectorAll(".ch-btn").forEach(function(b){b.c'
    'lassList.remove("active")});\n    btn.classList.add("active");\n    curCh = btn.dataset.ch;\n    var sel = document.getElementById("chSel");\n    if(curCh'
    ' === "#all"){sel.value = "#general"}else{sel.value = curCh}\n    loadMsgs();\n  });\n});\n\ndocument.getElementById("meetingModal").addEventListener("cli'
    'ck", function(e){\n  if(e.target === this) closeMeetingModal();\n});\n\nwindow.addEventListener("DOMContentLoaded", function(){\n  loadMembers();\n  loadMsgs'
    "();\n  connectWS();\n  setInterval(loadMembers, 10000);\n  setInterval(loadMsgs, 5000);\n  msgInput.focus();\n});\n</script>\n</body>\n</html>"
)


# ============================================================
# 页面路由（这些会覆盖同路径的旧路由）
# ============================================================


@app.get("/meeting", name="meeting_page")
async def meeting_page(request: Request):
    from fastapi.responses import HTMLResponse

    return HTMLResponse(apply_theme_to_page(UNIFIED_CHAT_PAGE, request))


@app.get("/lingxi", name="lingxi_page")
async def lingxi_page(request: Request):
    from fastapi.responses import HTMLResponse

    return HTMLResponse(apply_theme_to_page(LINGXI_CHAT_PAGE, request))


@app.get("/observatory", name="observatory_page")
async def observatory_page(request: Request):
    """可观测性仪表盘页面 — ECharts + 纯HTML，无React构建链"""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(apply_theme_to_page(OBSERVATORY_PAGE, request))


# ============================================================
# P1: BridgeV7Adapter API（V8模块整合）
# ============================================================


class AdapterSetupRequest(BaseModel):
    """Adapter 初始化请求"""

    worktree_name: str = "default"
    rolling_seed: int = 0
    extra_cfg: dict[str, Any] = {}


class AdapterRolloutRequest(BaseModel):
    """Skill Rollout请求"""

    skill_content: str
    worktree_name: str = "default"
    out_dir: str | None = None
    seed: int = 0


class AdapterReflectRequest(BaseModel):
    """Reflect分析请求"""

    results: list[dict[str, Any]]
    skill_content: str
    worktree_name: str = "default"


class AdapterEvaluateRequest(BaseModel):
    """Gate评估请求"""

    results: list[dict[str, Any]]
    skill_content: str
    patches: list[dict[str, Any]] | None = None


@app.post("/api/v7/adapter/setup")
async def adapter_setup(req: AdapterSetupRequest):
    """初始化 BridgeV7Adapter — SkillOpt融合入口"""
    adapter = _get_adapter()
    cfg = {"worktree": req.worktree_name, "seed": req.rolling_seed, **req.extra_cfg}
    try:
        adapter.setup(cfg)
        return {
            "status": "ok",
            "message": "OpenBridge V8适配器已初始化",
            "worktree": req.worktree_name,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"适配器初始化失败: {str(e)}")


@app.post("/api/v7/adapter/rollout")
async def adapter_rollout(req: AdapterRolloutRequest):
    """执行 Skill Rollout（EventStream轨迹记录）"""
    adapter = _get_adapter()
    out_dir = req.out_dir or f"data/rollouts/{uuid.uuid4().hex[:8]}"
    try:
        # 确保 setup 已执行
        if adapter._event_stream is None:
            adapter.setup({"worktree": req.worktree_name, "seed": req.seed})
        env = adapter.build_train_env(batch_size=1, seed=req.seed)
        results = adapter.rollout(env, req.skill_content, out_dir)
        return {
            "status": "ok",
            "task_count": len(results),
            "out_dir": out_dir,
            "summary": [
                {"task_id": r.get("task_id", ""), "success": r.get("success", False)}
                for r in results
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rollout失败: {str(e)}")


@app.post("/api/v7/adapter/reflect")
async def adapter_reflect(req: AdapterReflectRequest):
    """Reflect分析 — 分析Rollout轨迹，生成Patches"""
    adapter = _get_adapter()
    try:
        env = adapter.build_train_env(batch_size=1, seed=0)
        patches = adapter.reflect(req.results, req.skill_content, os.getcwd(), env=env)
        return {
            "status": "ok",
            "patch_count": len(patches),
            "patches": [{"id": p.id, "reason": p.reason} for p in patches[:5]],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reflect分析失败: {str(e)}")


@app.post("/api/v7/adapter/evaluate")
async def adapter_evaluate(req: AdapterEvaluateRequest):
    """Gate评估 — 对接ConditionVerifier判断是否通过门控"""
    adapter = _get_adapter()
    try:
        passed = adapter.evaluate_gate(
            req.results,
            req.skill_content,
            previous_patches=req.patches,
        )
        return {
            "status": "ok",
            "gate_passed": passed,
            "gate_metric": adapter.bridge_cfg.gate_metric,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gate评估失败: {str(e)}")


@app.get("/api/v7/adapter/status")
async def adapter_status():
    """查询Adapter运行状态"""
    adapter = _get_adapter()
    return {
        "initialized": adapter._event_stream is not None,
        "current_worktree": adapter._current_worktree,
        "rollout_history_count": len(adapter._rollout_history),
        "config": {
            "rollout_timeout": adapter.bridge_cfg.rollout_timeout,
            "max_retries": adapter.bridge_cfg.max_retries,
            "gate_metric": adapter.bridge_cfg.gate_metric,
        },
    }


# ============================================================
# P2: EventStreamWSBridge 激活（V8）
# ============================================================

_ws_bridge = None  # EventStreamWSBridge 延迟实例化(启动时激活)


@app.on_event("startup")
async def activate_ws_bridge():
    """服务启动时：创建messages表 + 激活 EventStream → WebSocket 桥接"""
    # 创建兼容v6的messages表（会议室闭环依赖）
    try:
        conn = get_db()
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            sender TEXT NOT NULL,
            recipient TEXT,
            content TEXT,
            channel TEXT DEFAULT '#general',
            created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_ch ON messages(channel, created_at)")
        conn.commit()
        conn.close()
        logger.info("messages表已就绪")
    except Exception as e:
        logger.error("messages表创建失败", error=str(e))

    global _ws_bridge
    try:
        from ws_manager import EventStreamWSBridge

        stream = get_or_create_stream()
        wsm = _get_ws_manager()
        _ws_bridge = EventStreamWSBridge(stream, wsm)
        logger.info(
            "[P2整合] EventStreamWSBridge已激活",
            subscription_count=len(stream._subscribers_by_type)
            if hasattr(stream, "_subscribers_by_type")
            else "N/A",
        )
    except Exception as e:
        logger.error("[P2整合] EventStreamWSBridge激活失败", error=str(e))


# ============================================================
# P3: Ratchet Loop + Experiment Archiver（V8）
# ============================================================


class ExperimentStartRequest(BaseModel):
    """启动实验请求"""

    experiment_name: str
    program_md: str  # program.md 内容
    worktree_name: str = "experiment"
    max_cycles: int = 5
    archive_results: bool = True


class ExperimentStatusResponse(BaseModel):
    """实验状态响应"""

    experiment_id: str
    phase: str
    cycle: int
    results_count: int
    archived: bool


@app.post("/api/v7/experiment/start")
async def experiment_start(req: ExperimentStartRequest):
    """启动 Ratchet Loop 实验工坊"""
    try:
        from ratchet_loop import ExperimentResult, RatchetConfig, RatchetLoop

        config = RatchetConfig(max_cycles=req.max_cycles)
        loop = RatchetLoop(config)
        results = loop.run(req.program_md)

        # P5: 归档对接（可选）
        archive_id = None
        if req.archive_results:
            try:
                from experiment_archiver import ExperimentArchiver

                archiver = ExperimentArchiver()
                archive_id = archiver.archive(
                    name=req.experiment_name,
                    results=results,
                    program_md=req.program_md,
                    worktree=req.worktree_name,
                )
                logger.info("[P3+P5] 实验结果已归档", archive_id=archive_id)
            except Exception as e:
                logger.warning("[P5] 归档失败(非致命)", error=str(e))

        return {
            "status": "ok",
            "experiment_name": req.experiment_name,
            "cycles_completed": len(results),
            "last_phase": results[-1].phase if results else "unknown",
            "archive_id": archive_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"实验执行失败: {str(e)}")


@app.get("/api/v7/experiment/list")
async def experiment_list():
    """列出已归档的实验"""
    try:
        from experiment_archiver import ExperimentArchiver

        archiver = ExperimentArchiver()
        experiments = archiver.list_all()
        return {"status": "ok", "count": len(experiments), "experiments": experiments}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"实验列表查询失败: {str(e)}")


@app.get("/api/v7/experiment/{experiment_id}")
async def experiment_detail(experiment_id: str):
    """查看单个实验详情"""
    try:
        from experiment_archiver import ExperimentArchiver

        archiver = ExperimentArchiver()
        detail = archiver.get(experiment_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"实验 {experiment_id} 不存在")
        return {"status": "ok", "experiment": detail}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"实验详情查询失败: {str(e)}")


@app.get("/group")
async def group_redirect():
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/meeting")


# ============================================================
# P1生态健康API：权限边界 + 审计因果链 + 限流状态
# ============================================================


@app.get("/api/v7/permissions")
async def get_permissions():
    """查看权限边界状态（三层分治模型）。

    返回所有注册Agent的权限等级、风险评分、Skill白名单。
    来源：6/29六维生态健康冲浪融优 — Erlang/OTP监管者模式 + Bulkhead隔离
    """
    try:
        report = get_permission_boundary_report()
        return {"status": "ok", "permissions": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"权限报告获取失败: {str(e)}")


@app.get("/api/v7/audit")
async def get_audit_trail(
    agent_id: str = Query(default="", description="按Agent ID过滤"),
    action: str = Query(default="", description="按操作范围过滤(READ/WRITE/EXECUTE/ADMIN)"),
    limit: int = Query(default=50, ge=1, le=200, description="返回记录数上限"),
):
    """查询审计追踪链（因果链·所有操作可溯源）。

    审计三要素：谁(agent_id) + 何时(timestamp) + 做了什么(action+resource)。
    来源：6/29六维生态健康冲浪融优 — 审计因果链闭环
    """
    try:
        action_enum = ActionScope(action) if action else None
        entries = get_audit_trail(agent_id=agent_id, action=action_enum, limit=limit)
        return {
            "status": "ok",
            "total": len(entries),
            "audit_trail": [
                {
                    "audit_id": e.audit_id,
                    "agent_id": e.agent_id,
                    "action": e.action.value,
                    "resource": e.resource,
                    "timestamp": e.timestamp.isoformat(),
                    "request_id": e.request_id,
                    "result": e.result,
                    "detail": e.detail,
                }
                for e in entries
            ],
        }
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的action类型: {action}，可选: READ/WRITE/EXECUTE/ADMIN/SYSTEM",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"审计追踪查询失败: {str(e)}")


@app.post("/api/v7/permissions/check")
async def check_permission_api(req: dict):
    """权限检查（Agent是否允许执行指定操作）。

    请求体示例:
        {"agent_id": "lingxi", "action": "EXECUTE", "resource": "web_search"}
    """
    try:
        agent_id = req.get("agent_id", "")
        action_str = req.get("action", "")
        resource = req.get("resource", "")
        request_id = req.get("request_id", "")

        if not agent_id or not action_str:
            raise HTTPException(status_code=400, detail="agent_id和action为必填项")

        action = ActionScope(action_str)
        allowed, reason = check_permission(agent_id, action, resource, request_id)
        return {"agent_id": agent_id, "action": action_str, "allowed": allowed, "reason": reason}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的action类型: {action_str}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"权限检查失败: {str(e)}")


@app.get("/api/v7/rate-limits")
async def get_rate_limits():
    """查看限流状态（所有限流器的当前令牌数/速率/容量）。

    来源：6/29六维生态健康冲浪融优 — 限流-熔断-背压三级防护
    """
    try:
        from rate_limiter import get_limiter_status

        status = get_limiter_status()
        return {"status": "ok", "rate_limiters": status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"限流状态查询失败: {str(e)}")


# ============================================================
# V8 多模态API（STT·TTS·OCR·Vision）·6/30
# ============================================================

# 多模态模块实例（延迟初始化·零配置可用）
_multimodal_instances = {}


def _get_multimodal(module_name: str):
    """获取多模态模块实例（延迟导入+单例）"""
    if module_name not in _multimodal_instances:
        try:
            if module_name == "stt":
                from providers.stt_provider import create_stt_provider

                _multimodal_instances[module_name] = create_stt_provider()
            elif module_name == "tts":
                from providers.tts_provider import create_tts_provider

                _multimodal_instances[module_name] = create_tts_provider()
            elif module_name == "ocr":
                from providers.ocr_provider import create_ocr_provider

                _multimodal_instances[module_name] = create_ocr_provider()
            elif module_name == "vision":
                from providers.vision_provider import create_vision_provider

                _multimodal_instances[module_name] = create_vision_provider()
        except Exception as e:
            logger.warning(f"多模态模块 {module_name} 加载失败: {e}")
            _multimodal_instances[module_name] = None
    return _multimodal_instances[module_name]


@app.get("/api/v8/multimodal/status")
async def multimodal_status():
    """查询多模态模块可用性"""
    return {
        "status": "ok",
        "modules": {
            "stt": _get_multimodal("stt").is_available() if _get_multimodal("stt") else False,
            "tts": _get_multimodal("tts").is_available() if _get_multimodal("tts") else False,
            "ocr": _get_multimodal("ocr").is_available() if _get_multimodal("ocr") else False,
            "vision": _get_multimodal("vision").is_available()
            if _get_multimodal("vision")
            else False,
        },
        "version": "8.0.0",
    }


@app.post("/api/v8/stt/transcribe")
async def stt_transcribe(req: dict = Body(...)):
    """语音转文字（STT）

    支持：Base64音频 / 本地文件路径
    """
    stt = _get_multimodal("stt")
    if not stt:
        return {"status": "error", "message": "STT模块不可用"}
    try:
        audio_b64 = req.get("audio_base64", "")
        audio_path = req.get("audio_path", "")
        if audio_b64:
            import base64

            result = stt.transcribe_bytes(base64.b64decode(audio_b64))
        elif audio_path:
            result = stt.transcribe(audio_path)
        else:
            return {"status": "error", "message": "请提供 audio_base64 或 audio_path"}
        return {
            "status": "ok",
            "text": result.text,
            "language": result.language,
            "engine": result.engine_used,
            "duration_ms": result.duration_ms,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/v8/tts/speak")
async def tts_speak(req: dict = Body(...)):
    """文字转语音（TTS）

    返回Base64编码的音频
    """
    tts = _get_multimodal("tts")
    if not tts:
        return {"status": "error", "message": "TTS模块不可用"}
    try:
        text = req.get("text", "")
        if not text:
            return {"status": "error", "message": "请提供 text"}
        voice = req.get("voice", "zh_female")
        result = tts.speak(text)
        import base64

        return {
            "status": "ok",
            "audio_base64": base64.b64encode(result.audio_bytes).decode(),
            "format": result.format,
            "engine": result.engine_used,
            "text": text,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/v8/ocr/extract")
async def ocr_extract(req: dict = Body(...)):
    """图片文字提取（OCR）

    支持：Base64图片 / 本地文件路径
    """
    ocr = _get_multimodal("ocr")
    if not ocr:
        return {"status": "error", "message": "OCR模块不可用"}
    try:
        img_b64 = req.get("image_base64", "")
        img_path = req.get("image_path", "")
        if img_b64:
            result = ocr.extract_text_base64(img_b64)
        elif img_path:
            result = ocr.extract_text(img_path)
        else:
            return {"status": "error", "message": "请提供 image_base64 或 image_path"}
        return {
            "status": "ok",
            "text": result.text,
            "blocks_count": len(result.blocks),
            "engine": result.engine_used,
            "processing_ms": result.processing_ms,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/v8/vision/describe")
async def vision_describe(req: dict = Body(...)):
    """图像理解·描述图片

    支持：Base64图片 / 本地文件路径
    """
    vision = _get_multimodal("vision")
    if not vision:
        return {"status": "error", "message": "图像理解模块不可用"}
    try:
        img_b64 = req.get("image_base64", "")
        img_path = req.get("image_path", "")
        if img_b64:
            import base64

            result = vision.describe_bytes(base64.b64decode(img_b64))
        elif img_path:
            result = vision.describe(img_path)
        else:
            return {"status": "error", "message": "请提供 image_base64 或 image_path"}
        return {
            "status": "ok",
            "description": result.description,
            "objects": result.objects,
            "engine": result.engine_used,
            "processing_ms": result.processing_ms,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/v8/vision/ask")
async def vision_ask(req: dict = Body(...)):
    """图像理解·针对图片提问

    Args:
        image_base64/image_path: 图片
        question: 用户问题
    """
    vision = _get_multimodal("vision")
    if not vision:
        return {"status": "error", "message": "图像理解模块不可用"}
    try:
        question = req.get("question", "请描述这张图片")
        img_b64 = req.get("image_base64", "")
        img_path = req.get("image_path", "")
        if img_b64:
            import base64

            result = vision.ask_about_image_bytes(base64.b64decode(img_b64), question)
        elif img_path:
            result = vision.ask_about_image(img_path, question)
        else:
            return {"status": "error", "message": "请提供 image_base64 或 image_path"}
        return {
            "status": "ok",
            "answer": result.answer,
            "description": result.description,
            "engine": result.engine_used,
            "processing_ms": result.processing_ms,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ==================== 书阁知识桥梁API (V8) ====================


@app.get("/api/v8/bookhouse/stats")
async def v8_bookhouse_stats():
    """书阁统计：总藏书·标签·阁楼分布"""
    return await book_stats()


@app.get("/api/v8/bookhouse/health")
async def v8_bookhouse_health():
    """书阁健康检查"""
    return await book_health()


@app.get("/api/v8/bookhouse/search")
async def v8_bookhouse_search(q: str = Query(..., description="搜索关键词")):
    """FTS5全文搜索 + LIKE模糊降级"""
    return await book_search(q)


@app.get("/api/v8/bookhouse/book/{book_id}")
async def v8_bookhouse_get_book(book_id: int):
    """获取单本书详情（含标签·引用关系）"""
    return await get_book(book_id)


@app.get("/api/v8/bookhouse/building/{name}")
async def v8_bookhouse_building(name: str):
    """获取某阁楼下所有藏书"""
    return await get_building(name)


@app.get("/api/v8/bookhouse/tags")
async def v8_bookhouse_tags():
    """列出所有标签（按使用频次排序）"""
    return await list_tags()


@app.post("/api/v8/bookhouse/add")
async def v8_bookhouse_add(req: dict = Body(...)):
    """新增书籍（需要Token + building名称 + title）"""
    from config import AGENT_TOKENS

    token = req.get("token", "")
    # 验证Token
    valid_agent = None
    for agent_name, expected_token in (AGENT_TOKENS or {}).items():
        if token == expected_token:
            valid_agent = agent_name
            break
    if not valid_agent:
        return {"status": "error", "message": "无效的Token"}
    return await add_book(
        title=req.get("title", ""),
        building=req.get("building", ""),
        token=token,
        category=req.get("category", ""),
        author=req.get("author", valid_agent),
        source=req.get("source", ""),
        file_path=req.get("file_path", ""),
        summary=req.get("summary", ""),
        tags=req.get("tags", ""),
    )


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    import sys

    import uvicorn

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    print("=" * 50)
    print("  OpenBridge V8 Server (多模态生态版)")
    print("  /meeting  会议室 | /lingxi  灵犀沟通")
    print("  /docs API文档 | /api/v8/multimodal/status 多模态状态")
    print("  /api/v8/bookhouse/* 书阁知识桥梁(3460)")
    print("=" * 50)
    uvicorn.run("bridge_v7_server:app", host="0.0.0.0", port=3458, log_level="info", reload=False)
