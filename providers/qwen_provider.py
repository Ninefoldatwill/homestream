"""
Qwen Provider - 通义千问 API适配器（国内备选，防卡脖）

对应渐进式复杂度 L2/L3层。
通义千问兼容OpenAI格式，适合作为GLM/DeepSeek的备选API，
当主API不可用时自动降级到通义千问，保障服务连续性。

开源用户可配置：
  - API Key（从 https://dashscope.console.aliyun.com 获取）
  - 模型选择（turbo便宜 / plus均衡 / max最强）
  - 温度、最大token等

技术主权保障（参考: TECH_SOVEREIGNTY_ASSESSMENT.md）：
  通义千问为阿里云国产API，数据中心在国内，
  不受GFW和国际网络波动影响，是抗卡脖的优选备选。

商标声明：
  "通义千问"和"Qwen"是阿里巴巴集团（Alibaba Cloud）的商标。
  本文件是独立开发的API客户端适配器，不包含任何Qwen模型权重、
  参数或源代码（非Qwen许可证定义的"Materials"），仅通过HTTP
  调用DashScope API。本项目与阿里巴巴集团无关联、未获背书。
  参见 NOTICE 文件和 IP_QWEN_PROVIDER_ASSESSMENT.md 评估报告。
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


class QwenProvider(BaseProvider):
    """通义千问 API Provider（国产备选，抗卡脖）"""

    # 通义千问OpenAI兼容模式端点
    DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

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
        """调用通义千问 API（OpenAI兼容格式）"""
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
                raise ProviderError(self.name, "API Key无效 (401)", 401) from e
            elif e.code == 429:
                self._mark_status(ProviderStatus.DEGRADED)
                raise ProviderError(self.name, "请求频率超限 (429)", 429) from e
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
        """检查通义千问 API是否可用"""
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
                self._mark_status(ProviderStatus.DEGRADED)
                return True
            logger.warning(f"[{self.name}] 健康检查失败: {e}")
            return False
        except Exception as e:
            logger.warning(f"[{self.name}] 健康检查异常: {e}")
            return False


# ==================== 工厂函数 ====================


def create_qwen_turbo_provider(api_key: str) -> QwenProvider:
    """创建通义千问Turbo Provider（轻量快速，适合L2备选）

    qwen-turbo: 最低成本，响应快，适合日常对话
    """
    config = ProviderConfig(
        name="qwen_turbo",
        display_name="通义千问 Turbo (国产备选API)",
        provider_type=ProviderType.API,
        tier=ProviderTier.L2,
        priority=25,  # 优先级略低于GLM，作为L2备选
        api_base=QwenProvider.DEFAULT_API_BASE,
        api_key=api_key,
        model_name="qwen-turbo",
        max_tokens=2048,
        temperature=0.7,
        timeout=30,
        cost_per_1k_input=0.0005,  # ¥0.5/百万token（极低）
        cost_per_1k_output=0.001,
    )
    return QwenProvider(config)


def create_qwen_plus_provider(api_key: str) -> QwenProvider:
    """创建通义千问Plus Provider（均衡能力，适合L3备选）

    qwen-plus: 能力均衡，适合复杂对话和推理
    """
    config = ProviderConfig(
        name="qwen_plus",
        display_name="通义千问 Plus (国产备选API)",
        provider_type=ProviderType.API,
        tier=ProviderTier.L3,
        priority=35,  # L3层备选
        api_base=QwenProvider.DEFAULT_API_BASE,
        api_key=api_key,
        model_name="qwen-plus",
        max_tokens=4096,
        temperature=0.7,
        timeout=30,
        cost_per_1k_input=0.004,
        cost_per_1k_output=0.012,
    )
    return QwenProvider(config)


def create_qwen_max_provider(api_key: str) -> QwenProvider:
    """创建通义千问Max Provider（最强能力，适合专业场景）

    qwen-max: 通义千问最强模型，适合高难度推理
    """
    config = ProviderConfig(
        name="qwen_max",
        display_name="通义千问 Max (国产最强API)",
        provider_type=ProviderType.API,
        tier=ProviderTier.L3,
        priority=45,
        api_base=QwenProvider.DEFAULT_API_BASE,
        api_key=api_key,
        model_name="qwen-max",
        max_tokens=4096,
        temperature=0.7,
        timeout=60,
        cost_per_1k_input=0.02,
        cost_per_1k_output=0.06,
    )
    return QwenProvider(config)
