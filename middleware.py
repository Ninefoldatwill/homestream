"""
桥v7 可观测性中间件 — 请求上下文 + Prometheus指标。

来源：SkillJect可观测性模式 + RED方法 + 融优实践配置模板
特性：request_id全链路追踪 + HTTP指标 + 业务指标
"""

import time
import uuid
import structlog
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from fastapi import FastAPI, Response

logger = structlog.get_logger("bridge_v7.middleware")

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# === RED方法三指标 ===
REQUEST_COUNT = Counter(
    "bridge_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

REQUEST_LATENCY = Histogram(
    "bridge_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

# === 业务指标 ===
ACTIVE_CONNECTIONS = Gauge(
    "bridge_active_connections",
    "Number of active WebSocket connections",
)

ICP_MESSAGES_SENT = Counter(
    "bridge_icp_messages_sent_total",
    "ICP messages sent",
    ["message_type"],
)

SKILL_ROUTER_INVOCATIONS = Counter(
    "bridge_skill_router_invocations_total",
    "SkillRouter invocations",
    ["skill_name", "status"],
)

EVENTS_PROCESSED = Counter(
    "bridge_events_processed_total",
    "Events processed",
    ["event_type", "status"],
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个HTTP请求绑定request_id，贯穿日志全链路。"""

    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
        request_id_var.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start_time = time.perf_counter()

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000

            logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()


class MetricsMiddleware(BaseHTTPMiddleware):
    """Prometheus指标采集中间件。"""

    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        REQUEST_COUNT.labels(
            method=request.method,
            endpoint=request.url.path,
            status=response.status_code,
        ).inc()
        REQUEST_LATENCY.labels(
            method=request.method,
            endpoint=request.url.path,
        ).observe(duration)

        return response


def setup_observability(app: FastAPI):
    """注册可观测性中间件和指标端点。"""
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(MetricsMiddleware)

    @app.get("/metrics")
    async def metrics():
        """Prometheus指标端点。"""
        return Response(content=generate_latest(), media_type="text/plain")
