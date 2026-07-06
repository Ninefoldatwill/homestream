"""
Provider抽象基类

所有模型Provider（本地/API）都继承这个基类，
实现统一接口，让ModelRouter可以无差别调度。

开源设计：第三方开发者只需继承BaseProvider并实现chat()方法，
然后通过ProviderRegistry注册即可接入。
"""

from __future__ import annotations

import abc
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class ProviderType(Enum):
    """Provider类型"""
    LOCAL = "local"     # 本地模型（llama.cpp/Ollama等）
    API = "api"         # 云端API（GLM/DeepSeek/OpenAI等）


class ProviderTier(Enum):
    """Provider层级（对应渐进式复杂度三层）"""
    L1 = "L1"   # 本地模型层（零配置，离线可用）
    L2 = "L2"   # 免费API层（单API，增强能力）
    L3 = "L3"   # 付费API层（多API，专业级）


class ProviderStatus(Enum):
    """Provider健康状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass
class ChatMessage:
    """统一消息格式"""
    role: str           # "system" / "user" / "assistant"
    content: str

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class ChatResponse:
    """统一响应格式"""
    content: str
    model: str
    provider: str
    tier: ProviderTier
    latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    cost_estimate: float = 0.0    # 估算费用（元），本地为0
    raw: Optional[Dict] = None    # 原始响应（调试用）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "provider": self.provider,
            "tier": self.tier.value,
            "latency_ms": round(self.latency_ms, 1),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_estimate": round(self.cost_estimate, 6),
        }


@dataclass
class ProviderConfig:
    """Provider配置"""
    name: str                           # Provider唯一名称
    display_name: str                   # 显示名称
    provider_type: ProviderType         # LOCAL / API
    tier: ProviderTier                  # L1 / L2 / L3
    enabled: bool = True                # 是否启用
    priority: int = 100                 # 优先级（数字越小优先级越高）
    api_base: str = ""                  # API地址
    api_key: str = ""                   # API密钥
    model_name: str = ""                # 模型名称
    max_tokens: int = 512               # 最大输出token
    temperature: float = 0.7            # 温度
    timeout: int = 30                   # 超时秒数
    extra: Dict[str, Any] = field(default_factory=dict)  # 额外配置

    # 费用估算（API类用）
    cost_per_1k_input: float = 0.0      # 每1K输入token费用（元）
    cost_per_1k_output: float = 0.0     # 每1K输出token费用（元）


class BaseProvider(abc.ABC):
    """Provider抽象基类"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._status = ProviderStatus.UNKNOWN
        self._last_check: float = 0.0
        self._request_count: int = 0
        self._error_count: int = 0
        self._total_latency_ms: float = 0.0

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def status(self) -> ProviderStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        """HEALTHY/DEGRADED/UNKNOWN都算可用——UNKNOWN表示尚未检查，应被尝试"""
        return self._status in (
            ProviderStatus.HEALTHY,
            ProviderStatus.DEGRADED,
            ProviderStatus.UNKNOWN,
        )

    @property
    def stats(self) -> Dict[str, Any]:
        avg_latency = self._total_latency_ms / max(self._request_count, 1)
        return {
            "name": self.name,
            "status": self._status.value,
            "requests": self._request_count,
            "errors": self._error_count,
            "avg_latency_ms": round(avg_latency, 1),
        }

    @abc.abstractmethod
    async def chat(
        self,
        messages: List[ChatMessage],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> ChatResponse:
        """
        发送聊天请求（核心方法）

        Args:
            messages: 消息列表
            max_tokens: 最大输出token（None用配置默认值）
            temperature: 温度（None用配置默认值）

        Returns:
            ChatResponse: 统一响应

        Raises:
            ProviderError: 请求失败时抛出
        """
        ...

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """健康检查，返回是否可用"""
        ...

    def _estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """估算单次请求费用"""
        cost = (
            (tokens_in / 1000.0) * self.config.cost_per_1k_input
            + (tokens_out / 1000.0) * self.config.cost_per_1k_output
        )
        return round(cost, 6)

    def _record_request(self, latency_ms: float, success: bool):
        """记录请求统计"""
        self._request_count += 1
        self._total_latency_ms += latency_ms
        if not success:
            self._error_count += 1

    def _mark_status(self, status: ProviderStatus):
        """更新状态"""
        if self._status != status:
            logger.info(f"[{self.name}] 状态变更: {self._status.value} -> {status.value}")
        self._status = status
        self._last_check = time.time()

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"name={self.name} "
            f"type={self.config.provider_type.value} "
            f"tier={self.config.tier.value} "
            f"status={self._status.value}>"
        )


class ProviderError(Exception):
    """Provider错误"""
    def __init__(self, provider: str, message: str, status_code: int = 0):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")
