"""
Ollama Provider - Ollama 本地模型适配器

对应渐进式复杂度 L1层（零配置，离线可用）
通过 Ollama 原生 API 与本地 Ollama 服务通信

为什么用原生 API 而非 OpenAI 兼容端点？
  - 原生 /api/chat 支持 keep_alive（模型常驻内存）、format（结构化输出）
  - 原生 /api/tags 支持模型自动发现和元信息查询
  - 与 LlamaCppProvider（走 OpenAI 兼容 API）形成互补，覆盖更广场景

开源用户可配置：
  - Ollama 服务地址（默认 http://localhost:11434）
  - 模型名称（如 qwen2.5:3b, llama3.2:3b, mistral:7b）
  - keep_alive 时长（模型常驻内存时间）
  - 温度、最大token等

商标声明：
  "Ollama" 是 Ollama Inc. 的商标。
  本文件是独立开发的 API 客户端适配器，不包含任何 Ollama
  模型权重或源代码，仅通过 HTTP 调用 Ollama REST API。
  本项目与 Ollama Inc. 无关联、未获背书。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

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


@dataclass
class OllamaModelInfo:
    """Ollama 模型元信息"""

    name: str  # 模型名（如 qwen2.5:3b）
    size: int = 0  # 文件大小（字节）
    digest: str = ""  # 摘要
    family: str = ""  # 模型族（如 llama, qwen）
    parameter_size: str = ""  # 参数量（如 3B, 7B）
    quantization: str = ""  # 量化等级（如 Q4_K_M）

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size_mb": round(self.size / 1024 / 1024, 1) if self.size else 0,
            "digest": self.digest[:12],
            "family": self.family,
            "parameter_size": self.parameter_size,
            "quantization": self.quantization,
        }


class OllamaProvider(BaseProvider):
    """Ollama 本地模型 Provider

    使用 Ollama 原生 API (/api/chat) 进行对话，
    支持 keep_alive（模型常驻内存）、format（结构化输出）等特性。

    与 LlamaCppProvider 的区别：
      - LlamaCppProvider: 通过 llama-server 的 OpenAI 兼容 API 通信
      - OllamaProvider: 通过 Ollama 原生 API 通信，支持更多 Ollama 特有功能
    """

    DEFAULT_API_BASE = "http://localhost:11434"

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        if not config.api_base:
            config.api_base = self.DEFAULT_API_BASE
        # 本地模型不收费
        self.config.cost_per_1k_input = 0.0
        self.config.cost_per_1k_output = 0.0
        # Ollama 特有配置（从 extra 字典读取）
        self._keep_alive: str = config.extra.get("keep_alive", "5m")
        self._format: str = config.extra.get("format", "")  # 空字符串=普通文本

    async def chat(
        self,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        """调用 Ollama /api/chat 端点

        Args:
            messages: 消息列表
            max_tokens: 最大输出token（映射到 Ollama 的 num_predict）
            temperature: 温度

        Returns:
            ChatResponse: 统一响应

        Raises:
            ProviderError: 请求失败时抛出
        """
        url = f"{self.config.api_base}/api/chat"
        max_tok = max_tokens or self.config.max_tokens
        temp = temperature if temperature is not None else self.config.temperature

        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": [m.to_dict() for m in messages],
            "stream": False,
            "options": {
                "temperature": temp,
                "num_predict": max_tok,
            },
            "keep_alive": self._keep_alive,
        }

        # 结构化输出（如 format="json"）
        if self._format:
            payload["format"] = self._format

        headers = {"Content-Type": "application/json"}

        start = time.time()
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            latency = (time.time() - start) * 1000

            content = result.get("message", {}).get("content", "")
            # Ollama 返回的 token 计数
            tokens_in = result.get("prompt_eval_count", 0)
            tokens_out = result.get("eval_count", 0)

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
                cost_estimate=0.0,  # 本地模型免费
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

            if e.code == 404:
                self._mark_status(ProviderStatus.OFFLINE)
                raise ProviderError(
                    self.name,
                    f"模型 '{self.config.model_name}' 未找到 (404)。"
                    f"请先运行: ollama pull {self.config.model_name}",
                    404,
                ) from e
            elif e.code == 500:
                self._mark_status(ProviderStatus.DEGRADED)
                # 尝试解析 Ollama 错误信息
                try:
                    err_data = json.loads(error_body)
                    err_msg = err_data.get("error", error_body)
                except Exception:
                    err_msg = error_body
                raise ProviderError(self.name, f"Ollama内部错误: {err_msg}", 500) from e
            else:
                self._mark_status(ProviderStatus.DEGRADED)
                raise ProviderError(self.name, f"HTTP {e.code}: {error_body}", e.code) from e

        except urllib.error.URLError as e:
            latency = (time.time() - start) * 1000
            self._record_request(latency, False)
            self._mark_status(ProviderStatus.OFFLINE)
            raise ProviderError(
                self.name,
                f"无法连接Ollama服务 ({self.config.api_base})。"
                f"请确认Ollama已启动: {e.reason}",
            ) from e

        except Exception as e:
            latency = (time.time() - start) * 1000
            self._record_request(latency, False)
            self._mark_status(ProviderStatus.DEGRADED)
            raise ProviderError(self.name, f"请求异常: {e}") from e

    async def health_check(self) -> bool:
        """检查 Ollama 服务是否在线 + 模型是否可用

        工作流程：
          1. 调用 /api/tags 确认 Ollama 服务在线
          2. 检查配置的模型是否在已安装列表中

        Returns:
            bool: 服务在线且模型可用时返回 True
        """
        # 1. 检查 Ollama 服务是否运行
        try:
            models = self.list_models(api_base=self.config.api_base)
        except ProviderError:
            self._mark_status(ProviderStatus.OFFLINE)
            return False
        except Exception as e:
            self._mark_status(ProviderStatus.OFFLINE)
            logger.debug(f"[{self.name}] 健康检查失败: {e}")
            return False

        if not models:
            self._mark_status(ProviderStatus.OFFLINE)
            logger.warning(f"[{self.name}] Ollama在线但无已安装模型")
            return False

        # 2. 检查配置的模型是否存在
        model_names = [m.name for m in models]

        # 精确匹配
        if self.config.model_name in model_names:
            self._mark_status(ProviderStatus.HEALTHY)
            logger.debug(f"[{self.name}] 健康检查通过，模型: {self.config.model_name}")
            return True

        # 模糊匹配（用户可能写 qwen2.5 但实际是 qwen2.5:latest）
        for name in model_names:
            if name.startswith(self.config.model_name):
                self._mark_status(ProviderStatus.HEALTHY)
                logger.debug(f"[{self.name}] 健康检查通过（模糊匹配），模型: {name}")
                return True

        # 模型不存在
        self._mark_status(ProviderStatus.OFFLINE)
        logger.warning(
            f"[{self.name}] 模型 '{self.config.model_name}' 未安装。"
            f"已安装: {model_names[:5]}..."
        )
        return False

    @staticmethod
    def list_models(
        api_base: str = "http://localhost:11434",
    ) -> list[OllamaModelInfo]:
        """列出 Ollama 已安装的所有模型

        Args:
            api_base: Ollama API 地址

        Returns:
            已安装模型列表

        Raises:
            ProviderError: 无法连接 Ollama 时抛出
        """
        url = f"{api_base}/api/tags"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            models = []
            for m in data.get("models", []):
                details = m.get("details", {})
                models.append(
                    OllamaModelInfo(
                        name=m.get("name", m.get("model", "")),
                        size=m.get("size", 0),
                        digest=m.get("digest", ""),
                        family=details.get("family", ""),
                        parameter_size=details.get("parameter_size", ""),
                        quantization=details.get("quantization_level", ""),
                    )
                )
            return models

        except urllib.error.URLError as e:
            raise ProviderError(
                "Ollama",
                f"无法连接Ollama服务 ({api_base}): {e.reason}",
            ) from e
        except Exception as e:
            raise ProviderError("Ollama", f"列出模型失败: {e}") from e

    @staticmethod
    def is_running(api_base: str = "http://localhost:11434") -> bool:
        """快速检查 Ollama 服务是否在运行（不检查具体模型）

        Args:
            api_base: Ollama API 地址

        Returns:
            bool: Ollama 服务在线时返回 True
        """
        try:
            OllamaProvider.list_models(api_base)
            return True
        except Exception:
            return False


# ==================== 工厂函数 ====================


def create_ollama_provider(
    model_name: str = "qwen2.5:3b",
    api_base: str = "http://localhost:11434",
    display_name: str = "",
    keep_alive: str = "5m",
    priority: int = 10,
) -> OllamaProvider:
    """创建 Ollama Provider（通用工厂）

    Args:
        model_name: Ollama 模型名称（如 qwen2.5:3b, llama3.2:3b）
        api_base: Ollama API 地址
        display_name: 显示名称（默认自动生成）
        keep_alive: 模型常驻内存时间（如 5m, 30m, -1=永久）
        priority: 优先级（数字越小优先级越高）

    Returns:
        OllamaProvider 实例
    """
    # 生成安全的 provider name（去掉特殊字符）
    safe_name = (
        model_name.replace(":", "_").replace(".", "_").replace("-", "_").replace("/", "_")
    )
    config = ProviderConfig(
        name=f"ollama_{safe_name}",
        display_name=display_name or f"Ollama {model_name} (本地)",
        provider_type=ProviderType.LOCAL,
        tier=ProviderTier.L1,
        priority=priority,
        api_base=api_base,
        model_name=model_name,
        max_tokens=512,
        temperature=0.7,
        timeout=60,
        extra={"keep_alive": keep_alive},
    )
    return OllamaProvider(config)


def create_ollama_qwen_provider(
    model_name: str = "qwen2.5:3b",
    api_base: str = "http://localhost:11434",
) -> OllamaProvider:
    """创建 Qwen 模型 Ollama Provider

    Qwen2.5 系列是通义千问开源模型，中文能力优秀。
    九重当前使用 qwen2.5:3b 作为 L1 本地层模型。
    """
    return create_ollama_provider(
        model_name=model_name,
        api_base=api_base,
        display_name=f"Ollama Qwen ({model_name})",
        keep_alive="5m",
    )


def create_ollama_llama_provider(
    model_name: str = "llama3.2:3b",
    api_base: str = "http://localhost:11434",
) -> OllamaProvider:
    """创建 Llama 模型 Ollama Provider

    Llama 3.2 是 Meta 开源模型，英文能力优秀。
    适合需要英文推理的场景。
    """
    return create_ollama_provider(
        model_name=model_name,
        api_base=api_base,
        display_name=f"Ollama Llama ({model_name})",
        keep_alive="5m",
    )


def create_ollama_mistral_provider(
    model_name: str = "mistral:7b",
    api_base: str = "http://localhost:11434",
) -> OllamaProvider:
    """创建 Mistral 模型 Ollama Provider

    Mistral 7B 是欧洲 Mistral AI 开源模型，推理能力强。
    """
    return create_ollama_provider(
        model_name=model_name,
        api_base=api_base,
        display_name=f"Ollama Mistral ({model_name})",
        keep_alive="5m",
    )
