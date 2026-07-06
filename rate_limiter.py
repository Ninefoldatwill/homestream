"""
桥v7 基础限流保护 — 令牌桶 + 滑动窗口计数器（v7.3+P1韧性）

融优来源：6/29六维生态健康冲浪 — RPM→TPM范式转移 + 限流-熔断-背压三级防护

设计决策：
- 令牌桶（Token Bucket）: 允许短时突发，长期平均限流 — 适合AI API调用
- 滑动窗口计数器: 简单直观，适合HTTP接口频控
- 内存实现: 种子阶段够用，生产可替换Redis

使用:
    limiter = create_token_bucket("model_chat", rate=30, burst=50)
    if limiter.consume():  # 允许
        do_work()
    else:  # 限流
        raise HTTPException(429, "Rate limit exceeded")
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional
from collections import deque
import structlog

logger = structlog.get_logger("bridge_v7.rate_limiter")


# ============================================================
# 令牌桶（Token Bucket）
# ============================================================

@dataclass
class TokenBucket:
    """令牌桶限流器 — 支持短时突发+长期平均限流"""
    name: str
    rate: float                            # 每秒填充速率（令牌/秒）
    burst: int                             # 桶容量（突发上限）
    _tokens: float = 0.0                   # 当前令牌数
    _last_refill: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self._tokens = float(self.burst)   # 初始化满桶

    def _refill(self):
        """按时间差补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
        self._last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        """消费令牌。返回 True=允许, False=限流。"""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    @property
    def available(self) -> float:
        """当前可用令牌数"""
        with self._lock:
            self._refill()
            return self._tokens


# ============================================================
# 滑动窗口计数器（Sliding Window）
# ============================================================

@dataclass
class SlidingWindow:
    """滑动窗口计数器 — 简单直观的接口频控"""
    name: str
    window_seconds: float                   # 窗口大小（秒）
    max_requests: int                       # 窗口内最大请求数
    _timestamps: deque = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _trim(self):
        """清理过期时间戳"""
        now = time.monotonic()
        while self._timestamps and self._timestamps[0] < now - self.window_seconds:
            self._timestamps.popleft()

    def allow(self) -> bool:
        """检查是否允许请求。返回 True=允许, False=限流。"""
        with self._lock:
            now = time.monotonic()
            self._trim()
            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(now)
                return True
            return False

    @property
    def current_count(self) -> int:
        """当前窗口内的请求数"""
        with self._lock:
            self._trim()
            return len(self._timestamps)


# ============================================================
# 限流器注册表（线程安全单例）
# ============================================================

_limiter_registry: Dict[str, TokenBucket] = {}
_registry_lock = threading.Lock()


def create_token_bucket(
    name: str,
    rate: float = 30,
    burst: int = 50,
) -> TokenBucket:
    """创建或获取令牌桶限流器。

    参数:
        name: 限流器名称（同一名称返回同实例）
        rate: 每秒填充速率（默认30令牌/秒 = ~1800 RPM）
        burst: 桶容量（默认50 = 允许瞬时50并发）
    """
    with _registry_lock:
        if name not in _limiter_registry:
            _limiter_registry[name] = TokenBucket(name=name, rate=rate, burst=burst)
            logger.info("rate_limiter_created", name=name, rate=rate, burst=burst)
        return _limiter_registry[name]


def get_limiter(name: str) -> Optional[TokenBucket]:
    """获取已注册的限流器"""
    with _registry_lock:
        return _limiter_registry.get(name)


# ============================================================
# 预注册关键路径限流器
# ============================================================

# 模型聊天接口 — 最重要的限流点（防AI模型费用爆增）
MODEL_CHAT_LIMITER = create_token_bucket(
    "model_chat",
    rate=30,       # 30 QPS（~1800 RPM）
    burst=50,      # 允许50突发
)

# ICP消息发送 — 防消息风暴
ICP_EVENT_LIMITER = create_token_bucket(
    "icp_event",
    rate=100,      # 100 QPS
    burst=200,
)

# 群聊消息 — 防刷屏
GROUP_CHAT_LIMITER = create_token_bucket(
    "group_chat",
    rate=50,       # 50 TPS
    burst=100,
)

# Skill调用 — 防工具滥用
SKILL_INVOKE_LIMITER = create_token_bucket(
    "skill_invoke",
    rate=20,       # 20 QPS
    burst=40,
)

# 通用API — 默认限流
GENERAL_API_LIMITER = create_token_bucket(
    "general_api",
    rate=60,       # 60 QPS
    burst=120,
)


def get_limiter_for_endpoint(endpoint: str) -> TokenBucket:
    """根据API端点返回对应的限流器"""
    endpoint_lower = endpoint.lower()
    if "chat" in endpoint_lower or "model" in endpoint_lower:
        return MODEL_CHAT_LIMITER
    if "event" in endpoint_lower or "icp" in endpoint_lower:
        return ICP_EVENT_LIMITER
    if "group" in endpoint_lower:
        return GROUP_CHAT_LIMITER
    if "skill" in endpoint_lower or "adapter" in endpoint_lower:
        return SKILL_INVOKE_LIMITER
    return GENERAL_API_LIMITER


# ============================================================
# 限流状态查询
# ============================================================

# ============================================================
# FastAPI/Starlette 限流中间件
# ============================================================

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from fastapi.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """全局限流中间件 — 自动匹配端点对应的限流器。

    白名单:
    - /health — 不限制（健康检查）
    - /metrics — 不限制（Prometheus指标）
    - /docs, /openapi.json — 不限制（API文档）
    """

    WHITELIST = {"/health", "/metrics", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(self, request: Request, call_next):
        # 白名单放行
        if request.url.path in self.WHITELIST or request.url.path.startswith("/docs"):
            return await call_next(request)

        limiter = get_limiter_for_endpoint(request.url.path)
        if not limiter.consume():
            logger.warning(
                "rate_limit_hit",
                path=request.url.path,
                limiter_name=limiter.name,
                available=limiter.available,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": f"请求频率过高，请稍后重试",
                    "retry_after": 1,
                },
            )
        return await call_next(request)


def get_limiter_status() -> dict:
    """获取所有限流器状态"""
    return {
        name: {
            "available": limiter.available,
            "rate": limiter.rate,
            "burst": limiter.burst,
        }
        for name, limiter in _limiter_registry.items()
    }
