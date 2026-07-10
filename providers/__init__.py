"""
providers - 模型Provider适配器包

支持的Provider：
  - LlamaCppProvider: 本地llama.cpp (L1, 零配置, 离线可用)
  - GLMProvider: 智谱GLM API (L2, 免费API)
  - DeepSeekProvider: DeepSeek API (L3, 付费API)
  - QwenProvider: 通义千问 API (L2/L3, 国产备选, 抗卡脖)

技术主权保障（详见 TECH_SOVEREIGNTY_ASSESSMENT.md）：
  通义千问为阿里云国产API，数据中心在国内，
  不受GFW和国际网络波动影响，是抗卡脖的优选备选。
  当GLM/DeepSeek不可用时，可降级到通义千问保障连续性。

开源扩展：
  第三方开发者只需继承BaseProvider并实现chat()方法，
  然后通过ModelRouter.registry.register()注册即可。
"""

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
from .deepseek_provider import (
    DeepSeekProvider,
    create_deepseek_flash_provider,
    create_deepseek_reasoner_provider,
)
from .glm_provider import GLMProvider, create_glm_flash_provider, create_glm_plus_provider
from .llama_cpp_provider import LlamaCppProvider, create_default_llama_cpp_provider
from .qwen_provider import (
    QwenProvider,
    create_qwen_max_provider,
    create_qwen_plus_provider,
    create_qwen_turbo_provider,
)

__all__ = [
    # 基类
    "BaseProvider",
    "ProviderConfig",
    "ProviderType",
    "ProviderTier",
    "ProviderStatus",
    "ChatMessage",
    "ChatResponse",
    "ProviderError",
    # LlamaCpp (L1 本地)
    "LlamaCppProvider",
    "create_default_llama_cpp_provider",
    # GLM (L2 免费API)
    "GLMProvider",
    "create_glm_flash_provider",
    "create_glm_plus_provider",
    # DeepSeek (L3 付费API)
    "DeepSeekProvider",
    "create_deepseek_flash_provider",
    "create_deepseek_reasoner_provider",
    # Qwen (L2/L3 国产备选, 抗卡脖)
    "QwenProvider",
    "create_qwen_turbo_provider",
    "create_qwen_plus_provider",
    "create_qwen_max_provider",
]
