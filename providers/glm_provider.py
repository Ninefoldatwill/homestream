"""
GLM Provider - 智谱GLM API适配器

对应渐进式复杂度 L2层（单API，免费增强能力）
GLM-4.7-Flash: 免费，30B参数(3B激活)，适合日常任务

开源用户可配置：
  - API Key（从 https://open.bigmodel.cn 获取）
  - 模型选择（GLM-4.7-Flash免费 / GLM-4-Plus付费）
  - 温度、最大token等

商标声明：
  "GLM"和"智谱"是北京智谱华章科技有限公司（Zhipu AI）的商标。
  本文件是独立开发的API客户端适配器，不包含任何GLM模型权重
  或源代码，仅通过HTTP调用智谱开放平台API。
  本项目与智谱华章无关联、未获背书。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from .base_provider import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderConfig,
    ProviderError,
    ProviderStatus,
    ProviderTier,
    ProviderType,
)

logger = logging.getLogger(__name__)


class GLMProvider(BaseProvider):
    """智谱GLM API Provider"""

    DEFAULT_API_BASE = "https://open.bigmodel.cn/api/paas/v4"

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        if not config.api_base:
            config.api_base = self.DEFAULT_API_BASE

    async def chat(
        self,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        """调用GLM API"""
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

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }

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

            cost = self._estimate_cost(tokens_in, tokens_out)
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
                cost_estimate=cost,
                raw=result,
            )

        except urllib.error.HTTPError as e:
            latency = (time.time() - start) * 1000
            self._record_request(latency, False)
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            if e.code == 401:
                self._mark_status(ProviderStatus.OFFLINE)
                raise ProviderError(self.name, f"API Key无效 (401): {error_body}", 401) from e
            elif e.code == 429:
                self._mark_status(ProviderStatus.DEGRADED)
                raise ProviderError(self.name, f"请求频率超限 (429): {error_body}", 429) from e
            else:
                self._mark_status(ProviderStatus.DEGRADED)
                raise ProviderError(self.name, f"HTTP {e.code}: {error_body}", e.code) from e
        except urllib.error.URLError as e:
            latency = (time.time() - start) * 1000
            self._record_request(latency, False)
            self._mark_status(ProviderStatus.OFFLINE)
            raise ProviderError(self.name, f"网络错误: {e.reason}") from e
        except Exception as e:
            latency = (time.time() - start) * 1000
            self._record_request(latency, False)
            self._mark_status(ProviderStatus.DEGRADED)
            raise ProviderError(self.name, f"请求异常: {e}") from e

    async def health_check(self) -> bool:
        """检查GLM API是否可用（发送最小请求）"""
        if not self.config.api_key:
            self._mark_status(ProviderStatus.OFFLINE)
            return False
        try:
            test_msg = ChatMessage(role="user", content="hi")
            response = await self.chat([test_msg], max_tokens=5)
            return bool(response.content)
        except ProviderError as e:
            if e.status_code == 401:
                logger.warning(f"[{self.name}] API Key无效")
                return False
            elif e.status_code == 429:
                logger.info(f"[{self.name}] 频率限制但API可用")
                self._mark_status(ProviderStatus.DEGRADED)
                return True
            logger.warning(f"[{self.name}] 健康检查失败: {e}")
            return False
        except Exception as e:
            logger.warning(f"[{self.name}] 健康检查异常: {e}")
            return False


def create_glm_flash_provider(api_key: str) -> GLMProvider:
    """创建GLM-4.7-Flash免费Provider"""
    config = ProviderConfig(
        name="glm_flash",
        display_name="GLM-4.7-Flash (免费API)",
        provider_type=ProviderType.API,
        tier=ProviderTier.L2,
        priority=20,
        api_base=GLMProvider.DEFAULT_API_BASE,
        api_key=api_key,
        model_name="glm-4-flash",
        max_tokens=1024,
        temperature=0.7,
        timeout=30,
        cost_per_1k_input=0.0,  # Flash免费
        cost_per_1k_output=0.0,
    )
    return GLMProvider(config)


def create_glm_plus_provider(api_key: str) -> GLMProvider:
    """创建GLM-4-Plus付费Provider（更强能力）"""
    config = ProviderConfig(
        name="glm_plus",
        display_name="GLM-4-Plus (付费API)",
        provider_type=ProviderType.API,
        tier=ProviderTier.L3,
        priority=50,
        api_base=GLMProvider.DEFAULT_API_BASE,
        api_key=api_key,
        model_name="glm-4-plus",
        max_tokens=2048,
        temperature=0.7,
        timeout=30,
        cost_per_1k_input=0.05,  # 约0.05元/千token
        cost_per_1k_output=0.05,
    )
    return GLMProvider(config)
