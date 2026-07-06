"""
LlamaCpp Provider - 本地llama-server适配器

对应渐进式复杂度 L1层（零配置，离线可用）
通过OpenAI兼容API与本地llama-server通信

开源用户可配置：
  - llama-server路径和端口
  - 模型文件路径
  - GPU offload层数
  - 上下文长度
"""

from __future__ import annotations

import json
import time
import logging
import urllib.request
import urllib.error
from typing import Optional, List

from .base_provider import (
    BaseProvider, ProviderConfig, ProviderType, ProviderTier,
    ChatMessage, ChatResponse, ProviderError, ProviderStatus,
)

logger = logging.getLogger(__name__)


class LlamaCppProvider(BaseProvider):
    """本地llama-server Provider"""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        # 本地模型不收费
        self.config.cost_per_1k_input = 0.0
        self.config.cost_per_1k_output = 0.0

    async def chat(
        self,
        messages: List[ChatMessage],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> ChatResponse:
        """调用本地llama-server的OpenAI兼容API"""
        url = f"{self.config.api_base}/chat/completions"
        max_tok = max_tokens or self.config.max_tokens
        temp = temperature if temperature is not None else self.config.temperature

        payload = {
            "model": self.config.model_name,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": max_tok,
            "temperature": temp,
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        start = time.time()
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            latency = (time.time() - start) * 1000

            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = result.get("usage", {})
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)

            self._record_request(latency, True)
            self._mark_status(ProviderStatus.HEALTHY)

            return ChatResponse(
                content=content,
                model=self.config.model_name,
                provider=self.name,
                tier=self.config.tier,
                latency_ms=latency,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_estimate=0.0,
                raw=result,
            )

        except urllib.error.URLError as e:
            latency = (time.time() - start) * 1000
            self._record_request(latency, False)
            self._mark_status(ProviderStatus.OFFLINE)
            raise ProviderError(self.name, f"连接失败: {e.reason}") from e
        except Exception as e:
            latency = (time.time() - start) * 1000
            self._record_request(latency, False)
            self._mark_status(ProviderStatus.DEGRADED)
            raise ProviderError(self.name, f"请求异常: {e}") from e

    async def health_check(self) -> bool:
        """检查llama-server是否在线"""
        url = f"{self.config.api_base}/models"
        try:
            req = urllib.request.Request(url)
            if self.config.api_key:
                req.add_header("Authorization", f"Bearer {self.config.api_key}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("data", [])
                if models:
                    self._mark_status(ProviderStatus.HEALTHY)
                    logger.debug(f"[{self.name}] 健康检查通过，模型: {models[0].get('id', 'unknown')}")
                    return True
                else:
                    self._mark_status(ProviderStatus.DEGRADED)
                    return False
        except Exception as e:
            self._mark_status(ProviderStatus.OFFLINE)
            logger.debug(f"[{self.name}] 健康检查失败: {e}")
            return False


def create_default_llama_cpp_provider(
    api_base: str = "http://localhost:1342/v1",
    api_key: str = "jan-local",
    model_name: str = "qwen2.5-7b",
) -> LlamaCppProvider:
    """创建默认的llama.cpp Provider（九重当前配置）"""
    config = ProviderConfig(
        name="llama_cpp_local",
        display_name="本地Qwen2.5-7B (llama.cpp)",
        provider_type=ProviderType.LOCAL,
        tier=ProviderTier.L1,
        priority=10,       # L1优先级最高（成本为零）
        api_base=api_base,
        api_key=api_key,
        model_name=model_name,
        max_tokens=512,
        temperature=0.7,
        timeout=60,        # 本地模型推理可能较慢
    )
    return LlamaCppProvider(config)
