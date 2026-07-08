"""
HomeStream 架构可视化引擎

从 EventStore + ModelRouter 数据动态生成 SVG 架构图。
完全原创实现，纯 Python SVG 字符串生成，零外部依赖。

输出三种架构图：
  1. Agent 拓扑图 — 谁在和谁通信，通信频率如何
  2. 事件流向图 — 最近事件的因果链时间线
  3. 路由状态图 — 三层模型路由的实时状态

设计原则：
  - 纯 Python SVG 生成，零外部依赖
  - 数据驱动：图形大小/颜色/布局都由实际数据决定
  - 降级安全：数据不足时返回占位 SVG
  - 铸钥匠精神：让每个人都能一眼看懂自己的AI生态园

灵感来源：受可视化引擎理念启发，但完全基于 HomeStream 自有架构实现。
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from event_store import EventStore
    from model_router import ModelRouter

logger = logging.getLogger(__name__)

# ==================== 常量 ====================

SVG_WIDTH = 680

# 颜色方案（浅色主题，适配亮色界面）
C = {
    "bg": "#ffffff",
    "border": "#e0e0e0",
    "text_primary": "#2c2c2a",
    "text_secondary": "#888780",
    "text_muted": "#b4b2a9",
    "node_fill": "#e6f1fb",
    "node_stroke": "#185fa5",
    "node_text": "#0c447c",
    "edge": "#888780",
    "edge_active": "#378add",
    "active": "#1d9e75",
    "inactive": "#b4b2a9",
    "error": "#e24b4a",
    "warn": "#ef9f27",
    "l1_fill": "#e6f1fb",
    "l1_stroke": "#185fa5",
    "l1_text": "#0c447c",
    "l2_fill": "#eaf3de",
    "l2_stroke": "#3b6d11",
    "l2_text": "#27500a",
    "l3_fill": "#fbeaf0",
    "l3_stroke": "#993556",
    "l3_text": "#72243e",
}

# EventType 颜色映射
EVENT_COLORS: Dict[str, str] = {
    "INFO": "#378add",
    "ASK": "#ba7517",
    "TASK": "#639922",
    "UPD": "#534ab7",
    "DONE": "#1d9e75",
    "WARN": "#e24b4a",
    "ACK": "#888780",
    "PING": "#d4537e",
    "LOG": "#5f5e5a",
}

# 安全的 EventType 值集合
VALID_EVENT_TYPES = set(EVENT_COLORS.keys())


# ==================== 工具函数 ====================

def _escape_xml(text: str) -> str:
    """转义 XML 特殊字符，限制长度防止 SVG 膨胀"""
    if not text:
        return ""
    escaped = (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
    return escaped[:60]


def _truncate(text: str, max_len: int = 12) -> str:
    """截断文本到指定长度"""
    text = str(text) if text else ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def _placeholder_svg(message: str, height: int = 200) -> str:
    """生成占位 SVG（数据不足时使用）"""
    return (
        f'<svg viewBox="0 0 {SVG_WIDTH} {height}" width="100%" role="img">'
        f'<title>{_escape_xml(message)}</title>'
        f'<rect x="0" y="0" width="{SVG_WIDTH}" height="{height}" '
        f'fill="{C["bg"]}" rx="8"/>'
        f'<text x="{SVG_WIDTH // 2}" y="{height // 2}" '
        f'text-anchor="middle" dominant-baseline="central" '
        f'font-size="13" fill="{C["text_muted"]}">'
        f'{_escape_xml(message)}</text>'
        f"</svg>"
    )


# ==================== 1. Agent 拓扑图 ====================

def generate_agent_topology(
    event_store: Optional["EventStore"] = None,
    session_id: str = "default",
    max_events: int = 200,
) -> str:
    """生成 Agent 拓扑图 SVG

    从 EventStore 提取最近的 sender->recipient 通信关系，
    以圆形布局展示 Agent 节点和通信连线。

    节点大小 = Agent 活跃度（发送+接收事件数）
    连线粗细 = 通信频率
    """
    if not event_store:
        return _placeholder_svg("EventStore 未初始化，无法生成拓扑图")

    try:
        events = event_store.query_by_session(
            session_id, limit=max_events, newest_first=False
        )
    except Exception as e:
        logger.warning(f"拓扑图数据查询失败: {e}")
        return _placeholder_svg(f"数据查询失败: {_escape_xml(str(e)[:30])}")

    if not events:
        return _placeholder_svg("暂无事件数据，启动对话后即可看到 Agent 拓扑")

    # 构建通信图
    agent_activity: Counter = Counter()
    edge_weights: Dict[str, int] = defaultdict(int)

    for ev in events:
        sender = getattr(ev, "sender", None) or "unknown"
        recipient = getattr(ev, "recipient", None) or "unknown"
        agent_activity[sender] += 1
        agent_activity[recipient] += 1
        edge_key = f"{sender}->{recipient}"
        edge_weights[edge_key] += 1

    # 限制节点数量（最多12个）
    top_agents = [a for a, _ in agent_activity.most_common(12)]
    if not top_agents:
        return _placeholder_svg("未检测到活跃 Agent")

    n = len(top_agents)
    # 圆形布局参数
    cx, cy = SVG_WIDTH // 2, 190
    radius = min(130, 40 + n * 8)

    # 计算节点坐标
    positions: Dict[str, tuple] = {}
    for i, agent in enumerate(top_agents):
        angle = 2 * math.pi * i / n - math.pi / 2  # 从顶部开始
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        positions[agent] = (x, y)

    # 构建有效边（只保留两端都在 top_agents 中的边）
    valid_edges = []
    for edge_key, weight in sorted(
        edge_weights.items(), key=lambda x: -x[1]
    )[:30]:
        sender, recipient = edge_key.split("->", 1)
        if sender in positions and recipient in positions:
            valid_edges.append((sender, recipient, weight))

    max_weight = max((w for _, _, w in valid_edges), default=1)

    # 生成 SVG
    parts: List[str] = []
    svg_height = 400

    parts.append(
        f'<svg viewBox="0 0 {SVG_WIDTH} {svg_height}" width="100%" role="img">'
    )
    parts.append("<title>Agent 通信拓扑图</title>")
    parts.append(
        f'<desc>{n} 个 Agent，{len(valid_edges)} 条通信链路</desc>'
    )

    # 背景
    parts.append(
        f'<rect x="0" y="0" width="{SVG_WIDTH}" height="{svg_height}" '
        f'fill="{C["bg"]}" rx="8"/>'
    )

    # 标题
    parts.append(
        f'<text x="{SVG_WIDTH // 2}" y="24" text-anchor="middle" '
        f'font-size="14" font-weight="500" fill="{C["text_primary"]}">'
        f'Agent 通信拓扑 ({n} agents, {len(valid_edges)} links)</text>'
    )

    # 绘制边（在节点下方）
    for sender, recipient, weight in valid_edges:
        x1, y1 = positions[sender]
        x2, y2 = positions[recipient]
        stroke_width = 0.5 + 2.5 * (weight / max_weight)
        opacity = 0.2 + 0.6 * (weight / max_weight)
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" '
            f'x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{C["edge"]}" stroke-width="{stroke_width:.1f}" '
            f'opacity="{opacity:.2f}"/>'
        )

    # 绘制节点
    max_activity = max(agent_activity.values()) if agent_activity else 1
    for agent in top_agents:
        x, y = positions[agent]
        activity = agent_activity[agent]
        node_r = 16 + 14 * (activity / max_activity)

        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{node_r:.1f}" '
            f'fill="{C["node_fill"]}" stroke="{C["node_stroke"]}" '
            f'stroke-width="0.5"/>'
        )
        # Agent 名称
        label = _truncate(agent, 10)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="11" '
            f'font-weight="500" fill="{C["node_text"]}">'
            f'{_escape_xml(label)}</text>'
        )
        # 事件计数
        parts.append(
            f'<text x="{x:.1f}" y="{y + node_r + 12:.1f}" '
            f'text-anchor="middle" font-size="10" '
            f'fill="{C["text_secondary"]}">{activity}</text>'
        )

    # 图例
    legend_y = svg_height - 24
    parts.append(
        f'<text x="20" y="{legend_y}" font-size="11" '
        f'fill="{C["text_secondary"]}">'
        f'\u25cf 节点大小 = 活跃度  |  线条粗细 = 通信频率</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


# ==================== 2. 事件流向图 ====================

def generate_event_flow(
    event_store: Optional["EventStore"] = None,
    session_id: str = "default",
    max_events: int = 15,
) -> str:
    """生成事件流向图 SVG

    展示最近事件的垂直时间线，包括事件类型颜色编码和因果链连接。
    """
    if not event_store:
        return _placeholder_svg("EventStore 未初始化，无法生成事件流向图")

    try:
        events = event_store.query_by_session(
            session_id, limit=max_events, newest_first=True
        )
    except Exception as e:
        logger.warning(f"事件流向图查询失败: {e}")
        return _placeholder_svg(f"数据查询失败: {_escape_xml(str(e)[:30])}")

    if not events:
        return _placeholder_svg("暂无事件数据")

    # 按时间正序排列（旧->新）
    events = list(reversed(events))
    n = len(events)

    svg_height = max(250, n * 32 + 60)
    left_margin = 50
    timeline_x = 80
    box_width = 480
    box_height = 24
    start_y = 40

    # 构建 event_id -> y 坐标映射（用于因果链箭头）
    event_positions: Dict[str, tuple] = {}

    parts: List[str] = []
    parts.append(
        f'<svg viewBox="0 0 {SVG_WIDTH} {svg_height}" width="100%" role="img">'
    )
    parts.append("<title>事件因果链流向图</title>")
    parts.append(
        f'<desc>最近 {n} 个事件的时间线</desc>'
    )

    # 背景
    parts.append(
        f'<rect x="0" y="0" width="{SVG_WIDTH}" height="{svg_height}" '
        f'fill="{C["bg"]}" rx="8"/>'
    )

    # 标题
    parts.append(
        f'<text x="{SVG_WIDTH // 2}" y="24" text-anchor="middle" '
        f'font-size="14" font-weight="500" fill="{C["text_primary"]}">'
        f'EventStream 因果链 (最近 {n} 事件)</text>'
    )

    # 时间线竖线
    parts.append(
        f'<line x1="{timeline_x}" y1="{start_y}" '
        f'x2="{timeline_x}" y2="{start_y + n * 32}" '
        f'stroke="{C["border"]}" stroke-width="1"/>'
    )

    # 绘制每个事件
    for i, ev in enumerate(events):
        y = start_y + i * 32
        ev_type = str(getattr(ev, "event_type", "LOG"))
        ev_color = EVENT_COLORS.get(ev_type, C["text_muted"])
        sender = getattr(ev, "sender", "?")
        recipient = getattr(ev, "recipient", "?")
        ev_id = getattr(ev, "event_id", str(i))
        cause = getattr(ev, "cause", None)
        ts = getattr(ev, "timestamp", None)

        event_positions[ev_id] = (timeline_x, y)

        # 时间线节点
        parts.append(
            f'<circle cx="{timeline_x}" cy="{y + box_height // 2}" '
            f'r="4" fill="{ev_color}"/>'
        )

        # 事件框
        box_x = timeline_x + 15
        parts.append(
            f'<rect x="{box_x}" y="{y}" width="{box_width}" '
            f'height="{box_height}" rx="4" '
            f'fill="{ev_color}" fill-opacity="0.08" '
            f'stroke="{ev_color}" stroke-width="0.5"/>'
        )

        # 类型标签
        parts.append(
            f'<text x="{box_x + 8}" y="{y + box_height // 2}" '
            f'dominant-baseline="central" font-size="11" '
            f'font-weight="500" fill="{ev_color}">'
            f'{_escape_xml(ev_type)}</text>'
        )

        # 发送者 -> 接收者
        label = f"{_truncate(sender, 8)} -> {_truncate(recipient, 8)}"
        parts.append(
            f'<text x="{box_x + 55}" y="{y + box_height // 2}" '
            f'dominant-baseline="central" font-size="11" '
            f'fill="{C["text_primary"]}">{_escape_xml(label)}</text>'
        )

        # 时间戳
        time_str = ""
        if ts:
            try:
                time_str = ts.strftime("%H:%M:%S")
            except (AttributeError, ValueError):
                time_str = "?"
        parts.append(
            f'<text x="{box_x + box_width - 8}" y="{y + box_height // 2}" '
            f'text-anchor="end" dominant-baseline="central" '
            f'font-size="10" fill="{C["text_secondary"]}">'
            f'{_escape_xml(time_str)}</text>'
        )

    # 绘制因果链箭头
    arrow_count = 0
    for i, ev in enumerate(events):
        if arrow_count >= 10:
            break
        cause = getattr(ev, "cause", None)
        if cause and cause in event_positions:
            _, from_y = event_positions[cause]
            _, to_y = event_positions[getattr(ev, "event_id", "")]
            if from_y < to_y:
                # 曲线箭头
                mid_y = (from_y + to_y) / 2
                parts.append(
                    f'<path d="M {timeline_x - 10},{from_y + 12} '
                    f'Q {timeline_x - 35},{mid_y} '
                    f'{timeline_x - 10},{to_y - 4}" '
                    f'fill="none" stroke="{C["edge_active"]}" '
                    f'stroke-width="0.8" stroke-dasharray="3,2" '
                    f'opacity="0.5"/>'
                )
                arrow_count += 1

    # 图例
    legend_y = svg_height - 16
    legend_items = [
        ("INFO", EVENT_COLORS["INFO"]),
        ("TASK", EVENT_COLORS["TASK"]),
        ("DONE", EVENT_COLORS["DONE"]),
        ("WARN", EVENT_COLORS["WARN"]),
    ]
    lx = 20
    for name, color in legend_items:
        parts.append(
            f'<rect x="{lx}" y="{legend_y - 8}" width="10" height="10" '
            f'rx="2" fill="{color}" fill-opacity="0.3" '
            f'stroke="{color}" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{lx + 14}" y="{legend_y}" font-size="10" '
            f'fill="{C["text_secondary"]}">{name}</text>'
        )
        lx += 60

    parts.append(
        f'<text x="{SVG_WIDTH - 20}" y="{legend_y}" '
        f'text-anchor="end" font-size="10" fill="{C["text_secondary"]}">'
        f'\u2192 虚线 = 因果链</text>'
    )

    parts.append("</svg>")
    return "".join(parts)


# ==================== 3. 路由状态图 ====================

def generate_router_status(
    model_router: Optional["ModelRouter"] = None,
) -> str:
    """生成三层模型路由状态图 SVG

    展示 L1/L2/L3 三层路由的 Provider 状态、模型名称和请求量。
    """
    if not model_router:
        return _placeholder_svg("ModelRouter 未初始化", height=150)

    try:
        if not getattr(model_router, "_initialized", False):
            model_router.auto_init_from_env()
        status = model_router.get_status()
    except Exception as e:
        logger.warning(f"路由状态图获取失败: {e}")
        return _placeholder_svg(f"路由状态获取失败: {_escape_xml(str(e)[:30])}", height=150)

    providers = status.get("providers", [])
    if not providers:
        return _placeholder_svg("无已配置 Provider", height=150)

    # 按 tier 分组
    tier_groups: Dict[str, list] = {"L1": [], "L2": [], "L3": []}
    for p in providers:
        tier = p.get("tier", "L1")
        if tier not in tier_groups:
            tier_groups[tier] = []
        tier_groups[tier].append(p)

    tier_config = [
        ("L1", "L1 \u7845\u57fa (Silicon-based)", C["l1_fill"], C["l1_stroke"], C["l1_text"]),
        ("L2", "L2 DeepSeek", C["l2_fill"], C["l2_stroke"], C["l2_text"]),
        ("L3", "L3 Ollama \u672c\u5730", C["l3_fill"], C["l3_stroke"], C["l3_text"]),
    ]

    svg_height = 60 + len(tier_config) * 70
    parts: List[str] = []

    parts.append(
        f'<svg viewBox="0 0 {SVG_WIDTH} {svg_height}" width="100%" role="img">'
    )
    parts.append("<title>三层模型路由状态图</title>")
    parts.append(
        f'<desc>策略: {status.get("strategy", "unknown")}</desc>'
    )

    # 背景
    parts.append(
        f'<rect x="0" y="0" width="{SVG_WIDTH}" height="{svg_height}" '
        f'fill="{C["bg"]}" rx="8"/>'
    )

    # 标题
    strategy = status.get("strategy", "unknown")
    parts.append(
        f'<text x="{SVG_WIDTH // 2}" y="24" text-anchor="middle" '
        f'font-size="14" font-weight="500" fill="{C["text_primary"]}">'
        f'\u4e09\u5c42\u6a21\u578b\u8def\u7531\u72b6\u6001 '
        f'(\u7b56\u7565: {_escape_xml(strategy)})</text>'
    )

    # 绘制每一层
    for idx, (tier_key, tier_label, fill, stroke, text_color) in enumerate(
        tier_config
    ):
        y = 45 + idx * 70
        provs = tier_groups.get(tier_key, [])

        # 层背景
        parts.append(
            f'<rect x="20" y="{y}" width="{SVG_WIDTH - 40}" height="60" '
            f'rx="6" fill="{fill}" fill-opacity="0.4" '
            f'stroke="{stroke}" stroke-width="0.5"/>'
        )

        # 层标签
        parts.append(
            f'<text x="32" y="{y + 18}" font-size="12" '
            f'font-weight="500" fill="{text_color}">'
            f'{_escape_xml(tier_label)}</text>'
        )

        if not provs:
            parts.append(
                f'<text x="32" y="{y + 40}" font-size="11" '
                f'fill="{C["text_muted"]}">\u672a\u914d\u7f6e</text>'
            )
            continue

        # Provider 框
        box_x = 180
        box_w = min(
            140, (SVG_WIDTH - 220) // max(len(provs), 1)
        )
        for j, p in enumerate(provs):
            px = box_x + j * (box_w + 10)
            py = y + 12
            p_status = p.get("status", "unknown")
            p_enabled = p.get("enabled", True)

            if not p_enabled:
                status_color = C["inactive"]
            elif p_status == "healthy":
                status_color = C["active"]
            elif p_status in ("error", "unhealthy"):
                status_color = C["error"]
            else:
                status_color = C["warn"]

            # Provider 框
            parts.append(
                f'<rect x="{px}" y="{py}" width="{box_w}" height="40" '
                f'rx="4" fill="{C["bg"]}" '
                f'stroke="{status_color}" stroke-width="0.5"/>'
            )

            # 状态点
            parts.append(
                f'<circle cx="{px + 8}" cy="{py + 10}" r="3" '
                f'fill="{status_color}"/>'
            )

            # Provider 名称
            name = _truncate(
                p.get("display_name", p.get("name", "?")), 14
            )
            parts.append(
                f'<text x="{px + 16}" y="{py + 14}" font-size="10" '
                f'font-weight="500" fill="{C["text_primary"]}">'
                f'{_escape_xml(name)}</text>'
            )

            # 模型名
            model = _truncate(p.get("model", ""), 16)
            parts.append(
                f'<text x="{px + 8}" y="{py + 28}" font-size="9" '
                f'fill="{C["text_secondary"]}">'
                f'{_escape_xml(model)}</text>'
            )

            # 请求量
            requests = p.get("stats", {}).get("requests", 0)
            parts.append(
                f'<text x="{px + box_w - 6}" y="{py + 28}" '
                f'text-anchor="end" font-size="9" '
                f'fill="{C["text_secondary"]}">{requests} req</text>'
            )

    parts.append("</svg>")
    return "".join(parts)


# ==================== 统一聚合接口 ====================

def collect_architecture_data(
    event_store: Optional["EventStore"] = None,
    model_router: Optional["ModelRouter"] = None,
    session_id: str = "default",
) -> Dict[str, Any]:
    """聚合所有架构可视化数据，供 API 端点返回。

    Returns:
        包含三张 SVG 图和元数据的字典:
        - topology_svg: Agent 通信拓扑图
        - flow_svg: 事件因果链流向图
        - router_svg: 三层路由状态图
        - meta: 数据统计信息
    """
    topology_svg = generate_agent_topology(event_store, session_id)
    flow_svg = generate_event_flow(event_store, session_id)
    router_svg = generate_router_status(model_router)

    # 收集元数据
    agent_count = 0
    event_count = 0
    link_count = 0

    if event_store:
        try:
            stats = event_store.stats(session_id)
            event_count = stats.get("total_events", 0)
            agent_count = len(stats.get("top_senders", {}))
        except Exception:
            pass

    return {
        "topology_svg": topology_svg,
        "flow_svg": flow_svg,
        "router_svg": router_svg,
        "meta": {
            "agent_count": agent_count,
            "event_count": event_count,
            "session_id": session_id,
        },
    }
