"""
HomeStream 可观测性数据聚合模块

聚合三大数据源，为 /api/v7/observatory 端点提供10面板仪表盘数据：
  1. Prometheus指标 (middleware.py) — HTTP请求/延迟/ICP消息/技能调用
  2. EventStore (event_store.py) — 事件统计/类型分布/会话
  3. ModelRouter (model_router.py) — Provider状态/Token/成本估算
  4. 架构可视化 (arch_visualizer.py) — Agent拓扑/事件流向/路由状态 SVG
  5. 数据质量守卫 (data_guardian.py) — 因果链/时间戳/类型/身份校验

设计原则：
  - 零侵入：不修改现有模块，只读取数据
  - 降级安全：任一数据源异常不影响整体返回
  - 铸钥匠精神：让每个用户都能看懂自己的AI生态园运行状态
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from prometheus_client import REGISTRY

if TYPE_CHECKING:
    from event_store import EventStore
    from model_router import ModelRouter

# 延迟导入架构可视化和数据质量模块（避免循环依赖）
try:
    from arch_visualizer import collect_architecture_data

    _HAS_ARCH_VIZ = True
except ImportError:
    _HAS_ARCH_VIZ = False

try:
    from data_guardian import run_full_audit

    _HAS_DATA_GUARDIAN = True
except ImportError:
    _HAS_DATA_GUARDIAN = False

logger = logging.getLogger(__name__)


# ==================== Prometheus 指标读取 ====================


def _read_counter_total(name: str) -> float:
    """读取 Prometheus Counter 总值（所有 label 合计）"""
    total = 0.0
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name == name or sample.name == name + "_total":
                total += sample.value
    return total


def _read_counter_by_label(name: str, label_key: str) -> dict[str, float]:
    """读取 Counter 按某个 label 分组的值"""
    result: dict[str, float] = {}
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name == name or sample.name == name + "_total":
                val = sample.labels.get(label_key, "")
                result[val] = result.get(val, 0) + sample.value
    return result


def _read_counter_multi_label(
    name: str,
) -> dict[str, float]:
    """读取 Counter 所有 label 组合，key='k=v|k=v' 格式"""
    result: dict[str, float] = {}
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name == name or sample.name == name + "_total":
                label_str = "|".join(f"{k}={v}" for k, v in sorted(sample.labels.items()))
                result[label_str] = result.get(label_str, 0) + sample.value
    return result


def _read_histogram(name: str) -> dict[str, Any]:
    """读取 Histogram 的 bucket/count/sum"""
    buckets: dict[str, float] = {}
    total_count = 0.0
    total_sum = 0.0
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name.endswith("_bucket") and name in sample.name:
                le = sample.labels.get("le", "+Inf")
                buckets[le] = sample.value
            elif sample.name.endswith("_count") and name in sample.name:
                total_count = sample.value
            elif sample.name.endswith("_sum") and name in sample.name:
                total_sum = sample.value
    return {
        "buckets": buckets,
        "count": total_count,
        "sum": total_sum,
        "avg_seconds": total_sum / max(total_count, 1),
    }


def _read_gauge(name: str) -> float:
    """读取 Gauge 当前值"""
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name == name:
                return sample.value
    return 0.0


def _calc_percentiles(buckets: dict[str, float], total: float) -> dict[str, float]:
    """从 Histogram bucket 计算百分位延迟（秒）"""
    if not buckets or total <= 0:
        return {"p50": 0, "p75": 0, "p90": 0, "p95": 0, "p99": 0}

    sorted_buckets = sorted(buckets.items(), key=lambda x: float(x[0].replace("+Inf", "999")))
    cumulative = 0
    percentiles = {}
    targets = [("p50", 0.50), ("p75", 0.75), ("p90", 0.90), ("p95", 0.95), ("p99", 0.99)]

    for le, count in sorted_buckets:
        for pname, ratio in targets:
            if pname not in percentiles and count / total >= ratio:
                percentiles[pname] = float(le)

    for pname, _ in targets:
        if pname not in percentiles:
            percentiles[pname] = 0.0

    return percentiles


# ==================== 分组辅助 ====================


def _parse_label_str(label_str: str) -> dict[str, str]:
    """解析 'k=v|k=v' 格式的 label 字符串"""
    result = {}
    for part in label_str.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k] = v
    return result


def _group_http_by_endpoint(raw: dict[str, float]) -> list[dict[str, Any]]:
    """按端点分组 HTTP 请求统计"""
    endpoint_map: dict[str, dict[str, float]] = {}
    for label_str, val in raw.items():
        labels = _parse_label_str(label_str)
        endpoint = labels.get("endpoint", "unknown")
        status = labels.get("status", "0")
        if endpoint not in endpoint_map:
            endpoint_map[endpoint] = {"total": 0, "success": 0, "error": 0}
        endpoint_map[endpoint]["total"] += val
        if status.startswith("2") or status.startswith("3"):
            endpoint_map[endpoint]["success"] += val
        else:
            endpoint_map[endpoint]["error"] += val
    return [
        {"endpoint": k, **v} for k, v in sorted(endpoint_map.items(), key=lambda x: -x[1]["total"])
    ]


def _group_skill_invocations(raw: dict[str, float]) -> list[dict[str, Any]]:
    """按技能名分组调用统计"""
    skill_map: dict[str, dict[str, float]] = {}
    for label_str, val in raw.items():
        labels = _parse_label_str(label_str)
        skill = labels.get("skill_name", "unknown")
        status = labels.get("status", "unknown")
        if skill not in skill_map:
            skill_map[skill] = {"total": 0, "success": 0, "error": 0}
        skill_map[skill]["total"] += val
        if status == "success":
            skill_map[skill]["success"] += val
        else:
            skill_map[skill]["error"] += val
    return [{"skill": k, **v} for k, v in sorted(skill_map.items(), key=lambda x: -x[1]["total"])]


# ==================== 核心聚合函数 ====================


def collect_observatory_data(
    event_store: EventStore | None = None,
    model_router: ModelRouter | None = None,
    session_id: str = "default",
) -> dict[str, Any]:
    """聚合所有可观测性数据源，返回8面板仪表盘数据。

    Args:
        event_store: EventStore 实例（可为 None，降级返回空）
        model_router: ModelRouter 实例（可为 None，降级返回空）
        session_id: 会话ID

    Returns:
        包含 summary + panels + providers 的完整字典
    """
    timestamp = datetime.now().isoformat()

    # === Panel 1: HTTP 成功率 ===
    http_raw = _read_counter_multi_label("bridge_http_requests_total")
    success_count = sum(v for k, v in http_raw.items() if "status=2" in k or "status=3" in k)
    error_count = sum(v for k, v in http_raw.items() if "status=4" in k or "status=5" in k)
    total_http = success_count + error_count
    success_rate = success_count / max(total_http, 1)

    # === Panel 2: 延迟百分位 ===
    latency_data = _read_histogram("bridge_http_request_duration_seconds")
    percentiles = _calc_percentiles(latency_data["buckets"], latency_data["count"])

    # === Panel 3 & 7: Token & 成本估算 ===
    providers_stats: list[dict[str, Any]] = []
    total_tokens_in = 0
    total_tokens_out = 0
    total_cost = 0.0
    cost_by_tier: dict[str, float] = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    hardware_info = None
    strategy = "unknown"

    if model_router:
        try:
            if not model_router._initialized:
                model_router.auto_init_from_env()
            status = model_router.get_status()
            strategy = status.get("strategy", "unknown")
            hardware_info = status.get("hardware")

            for p in status.get("providers", []):
                stats = p.get("stats", {})
                requests = stats.get("requests", 0)
                errors = stats.get("errors", 0)
                avg_latency = stats.get("avg_latency_ms", 0)
                tier = p.get("tier", "L1")

                # 从 Provider 对象获取费用配置
                provider_obj = model_router.registry.get(p["name"])
                cost_in_rate = 0.0
                cost_out_rate = 0.0
                if provider_obj:
                    cost_in_rate = provider_obj.config.cost_per_1k_input
                    cost_out_rate = provider_obj.config.cost_per_1k_output

                # 估算 Token（BaseProvider 只记录了 request_count，没有记录 tokens）
                # 保守估算：每请求平均输入200 token，输出为配置 max_tokens 的一半
                max_tok = provider_obj.config.max_tokens if provider_obj else 512
                est_tokens_in = requests * 200
                est_tokens_out = requests * (max_tok // 2)

                # 估算成本
                est_cost = (est_tokens_in / 1000.0) * cost_in_rate + (
                    est_tokens_out / 1000.0
                ) * cost_out_rate

                total_tokens_in += est_tokens_in
                total_tokens_out += est_tokens_out
                total_cost += est_cost
                if tier in cost_by_tier:
                    cost_by_tier[tier] += est_cost

                providers_stats.append(
                    {
                        "name": p["name"],
                        "display_name": p.get("display_name", p["name"]),
                        "tier": tier,
                        "model": p.get("model", ""),
                        "status": p.get("status", "unknown"),
                        "enabled": p.get("enabled", True),
                        "requests": requests,
                        "errors": errors,
                        "error_rate": round(errors / max(requests, 1), 4),
                        "avg_latency_ms": avg_latency,
                        "est_tokens_in": est_tokens_in,
                        "est_tokens_out": est_tokens_out,
                        "est_cost": round(est_cost, 6),
                        "cost_per_1k_input": cost_in_rate,
                        "cost_per_1k_output": cost_out_rate,
                    }
                )
        except Exception as e:
            logger.warning(f"ModelRouter 数据聚合失败: {e}")

    # === Panel 4: 事件类型分布 ===
    event_stats: dict[str, Any] = {}
    if event_store:
        try:
            event_stats = event_store.stats(session_id)
        except Exception as e:
            logger.warning(f"EventStore stats 失败: {e}")

    # === Panel 5: ICP 消息统计 ===
    icp_by_type = _read_counter_by_label("bridge_icp_messages_sent_total", "message_type")

    # === Panel 6: 技能调用统计 ===
    skill_raw = _read_counter_multi_label("bridge_skill_router_invocations_total")
    skill_success = sum(v for k, v in skill_raw.items() if "status=success" in k)
    skill_total = sum(skill_raw.values())
    skill_error = skill_total - skill_success

    # === Panel 8: 活跃连接 & 事件吞吐 ===
    active_connections = _read_gauge("bridge_active_connections")
    events_by_type = _read_counter_by_label("bridge_events_processed_total", "event_type")
    events_total = sum(events_by_type.values())

    # === Panel 9: 架构可视化 ===
    arch_data: dict[str, Any] = {}
    if _HAS_ARCH_VIZ:
        try:
            arch_data = collect_architecture_data(event_store, model_router, session_id)
        except Exception as e:
            logger.warning(f"\u67b6\u6784\u53ef\u89c6\u5316\u751f\u6210\u5931\u8d25: {e}")
            arch_data = {"error": str(e)[:200]}

    # === Panel 10: 数据质量审计 ===
    quality_data: dict[str, Any] = {}
    if _HAS_DATA_GUARDIAN:
        try:
            quality_data = run_full_audit(event_store, session_id)
        except Exception as e:
            logger.warning(f"\u6570\u636e\u8d28\u91cf\u5ba1\u8ba1\u5931\u8d25: {e}")
            quality_data = {"error": str(e)[:200]}

    # === 构建响应 ===
    return {
        "timestamp": timestamp,
        "server_version": "8.0.0",
        "summary": {
            "http_success_rate": round(success_rate, 4),
            "http_total_requests": int(total_http),
            "total_events": event_stats.get("total_events", 0),
            "active_sessions": len(event_stats.get("sessions", {})),
            "active_connections": int(active_connections),
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "total_cost": round(total_cost, 6),
            "skill_success_rate": round(skill_success / max(skill_total, 1), 4),
            "strategy": strategy,
        },
        "panels": {
            "success_rate": {
                "success": int(success_count),
                "error": int(error_count),
                "total": int(total_http),
                "rate": round(success_rate, 4),
                "by_endpoint": _group_http_by_endpoint(http_raw),
            },
            "latency": {
                "avg_ms": round(latency_data["avg_seconds"] * 1000, 1),
                "total_count": int(latency_data["count"]),
                "buckets": {k: v for k, v in latency_data["buckets"].items()},
                "percentiles_ms": {k: round(v * 1000, 1) for k, v in percentiles.items()},
            },
            "token_cost": {
                "total_tokens_in": total_tokens_in,
                "total_tokens_out": total_tokens_out,
                "total_cost": round(total_cost, 6),
                "by_provider": [
                    {
                        "name": p["display_name"],
                        "tier": p["tier"],
                        "tokens_in": p["est_tokens_in"],
                        "tokens_out": p["est_tokens_out"],
                        "cost": p["est_cost"],
                    }
                    for p in providers_stats
                ],
            },
            "per_request_cost": {
                "total_cost": round(total_cost, 6),
                "total_requests": int(total_http),
                "avg_cost_per_request": round(total_cost / max(total_http, 1), 6),
            },
            "event_distribution": {
                "total": event_stats.get("total_events", 0),
                "by_type": event_stats.get("by_type", {}),
                "top_senders": event_stats.get("top_senders", {}),
                "sessions": event_stats.get("sessions", {}),
            },
            "tool_execution": {
                "success": int(skill_success),
                "error": int(skill_error),
                "total": int(skill_total),
                "rate": round(skill_success / max(skill_total, 1), 4),
                "by_skill": _group_skill_invocations(skill_raw),
            },
            "cost_breakdown": {
                "by_tier": {k: round(v, 6) for k, v in cost_by_tier.items()},
                "total": round(total_cost, 6),
            },
            "active_throughput": {
                "events_processed": int(events_total),
                "by_type": dict(events_by_type),
                "active_connections": int(active_connections),
                "icp_messages": {
                    "total": int(sum(icp_by_type.values())),
                    "by_type": dict(icp_by_type),
                },
            },
            "architecture": arch_data,
            "data_quality": quality_data,
        },
        "providers": providers_stats,
        "hardware": hardware_info,
    }
