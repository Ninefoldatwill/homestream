"""
ModelRouter - 硬件自适应多模型路由器

九重定调："作为技术开源，未来大家可以根据每个人的硬件锚点
          开拓最优适配自己的AI生态园"

核心设计：
  1. 硬件自适应：启动时自动检测硬件，推荐最优本地模型
  2. ProviderRegistry：动态注册，第三方可扩展
  3. 渐进式复杂度：
     - 零配置：只启动本地llama.cpp（无API Key也能用）
     - 单API：填一个GLM Key → L2层可用
     - 多API：填多个Key → L3层可用+自动降级
  4. 路由策略：成本优先(默认) / 质量优先 / 速度优先 / 指定层级
  5. 降级机制：L1失败 → L2 → L3 → 全失败报错
  6. 双保障：主线路(L1+L2)失败/超时 → 自动切换到复线(DeepSeek L3)

架构：
  ModelRouter
  ├── ProviderRegistry (动态注册表)
  ├── RouterStrategy (路由策略)
  ├── DualRedundancyConfig (双保障配置)  ← 新增 6/27
  ├── auto_init() (根据.env自动初始化)
  └── chat() (统一入口，自动路由+降级+双保障)  ← 增强 6/27
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

from hardware_profile import HardwareInfo, detect_hardware, get_model_recommendation, recommend_tier
from providers.base_provider import (
    BaseProvider,
    ChatMessage,
    ChatResponse,
    ProviderConfig,
    ProviderError,
    ProviderStatus,
    ProviderTier,
    ProviderType,
)
from providers.deepseek_provider import DeepSeekProvider, create_deepseek_flash_provider
from providers.glm_provider import GLMProvider, create_glm_flash_provider
from providers.llama_cpp_provider import LlamaCppProvider, create_default_llama_cpp_provider
from providers.ollama_provider import OllamaProvider, create_ollama_provider

logger = logging.getLogger(__name__)


class RouterStrategy(Enum):
    """路由策略"""

    COST_FIRST = "cost_first"  # 成本优先：本地 > 免费API > 付费API
    QUALITY_FIRST = "quality_first"  # 质量优先：付费Pro > 免费Flash > 本地
    SPEED_FIRST = "speed_first"  # 速度优先：按历史延迟排序
    TIER_SPECIFIED = "tier_specified"  # 指定层级
    SMART = "smart"  # v5.1.0: 6维度智能评分（延迟/成本/健康度/新鲜度/成功率/负载）


@dataclass
class DualRedundancyConfig:
    """双保障配置（6/27新增）

    主线路失败/超时后，自动切换到复线（DeepSeek L3）
    """

    enabled: bool = True  # 是否启用双保障
    primary_tiers: list[ProviderTier] = field(
        default_factory=lambda: [ProviderTier.L1, ProviderTier.L2]
    )  # 主线路
    backup_tier: ProviderTier = ProviderTier.L3  # 复线（DeepSeek）
    primary_timeout: int = 10  # 主线路单Provider超时（秒）
    backup_timeout: int = 15  # 复线超时（秒）
    fail_fast: bool = True  # 是否快速失败（主线路全部失败后立即用复线）


class ProviderRegistry:
    """Provider动态注册表

    开源设计：第三方开发者只需调用register()即可接入自己的Provider
    """

    def __init__(self):
        self._providers: dict[str, BaseProvider] = {}
        self._provider_classes: dict[str, type[BaseProvider]] = {}

    def register(self, provider: BaseProvider) -> bool:
        """注册Provider"""
        if provider.name in self._providers:
            logger.warning(f"Provider '{provider.name}' 已存在，将被覆盖")
        self._providers[provider.name] = provider
        logger.info(f"注册Provider: {provider}")
        return True

    def unregister(self, name: str) -> bool:
        """注销Provider"""
        if name in self._providers:
            del self._providers[name]
            logger.info(f"注销Provider: {name}")
            return True
        return False

    def get(self, name: str) -> BaseProvider | None:
        """获取Provider"""
        return self._providers.get(name)

    def get_all(self) -> dict[str, BaseProvider]:
        """获取所有Provider"""
        return dict(self._providers)

    def get_by_tier(self, tier: ProviderTier) -> builtins.list[BaseProvider]:
        """按层级获取Provider"""
        return [p for p in self._providers.values() if p.config.tier == tier and p.config.enabled]

    def get_available(self) -> builtins.list[BaseProvider]:
        """获取所有已启用的Provider"""
        return [p for p in self._providers.values() if p.config.enabled]

    def register_class(self, type_name: str, cls: type[BaseProvider]):
        """注册Provider类（供工厂模式使用）"""
        self._provider_classes[type_name] = cls

    def list(self) -> builtins.list[dict[str, Any]]:
        """列出所有Provider状态"""
        return [p.stats for p in self._providers.values()]


class ModelRouter:
    """硬件自适应多模型路由器"""

    def __init__(self, strategy: RouterStrategy = RouterStrategy.COST_FIRST):
        self.registry = ProviderRegistry()
        self.strategy = strategy
        self.hardware: HardwareInfo | None = None
        self._initialized = False
        self._specified_tier: ProviderTier | None = None
        self.dual_redundancy = DualRedundancyConfig()  # 双保障配置（6/27新增）
        self.scorer = None  # v5.1.0: 多维度评分器（延迟初始化）

        # 从环境变量加载双保障配置
        self._load_dual_redundancy_config()
        self._ensure_scorer()  # v5.1.0: 初始化评分器

    def _ensure_scorer(self):
        """确保评分器已初始化（v5.1.0）"""
        if self.scorer is None:
            try:
                from router_score import RouterScore

                self.scorer = RouterScore()
            except ImportError:
                logger.debug("router_score 模块不可用，跳过评分器初始化")
                self.scorer = None

    # ==================== 初始化 ====================

    def _load_dual_redundancy_config(self):
        """从环境变量加载双保障配置（6/27新增）"""
        cfg = self.dual_redundancy

        # 是否启用双保障
        enabled = os.getenv("MODEL_ROUTER_DUAL_REDUNDANCY", "true").lower()
        cfg.enabled = enabled in ("true", "1", "yes")

        # 主线路层级
        primary_tiers_str = os.getenv("MODEL_ROUTER_PRIMARY_TIERS", "L1,L2")
        cfg.primary_tiers = []
        for t in primary_tiers_str.split(","):
            t = t.strip()
            if t == "L1":
                cfg.primary_tiers.append(ProviderTier.L1)
            elif t == "L2":
                cfg.primary_tiers.append(ProviderTier.L2)
            elif t == "L3":
                cfg.primary_tiers.append(ProviderTier.L3)

        # 复线层级
        backup_tier_str = os.getenv("MODEL_ROUTER_BACKUP_TIER", "L3")
        if backup_tier_str.strip() == "L3":
            cfg.backup_tier = ProviderTier.L3
        elif backup_tier_str.strip() == "L2":
            cfg.backup_tier = ProviderTier.L2
        elif backup_tier_str.strip() == "L1":
            cfg.backup_tier = ProviderTier.L1

        # 超时配置
        cfg.primary_timeout = int(os.getenv("MODEL_ROUTER_TIMEOUT_PRIMARY", "10"))
        cfg.backup_timeout = int(os.getenv("MODEL_ROUTER_TIMEOUT_BACKUP", "15"))

        logger.info(
            f"双保障配置加载: enabled={cfg.enabled}, "
            f"primary_tiers={[t.value for t in cfg.primary_tiers]}, "
            f"backup_tier={cfg.backup_tier.value}, "
            f"primary_timeout={cfg.primary_timeout}s, "
            f"backup_timeout={cfg.backup_timeout}s"
        )

    def auto_init_from_env(self):
        """根据.env和环境自动初始化Provider

        渐进式复杂度：
          - 零配置：检测到llama-server → 注册L1
          - 单API：检测到GLM_API_KEY → 注册L2
          - 多API：检测到DEEPSEEK_API_KEY → 注册L3
        """
        if self._initialized:
            return

        # 1. 硬件检测
        self.hardware = detect_hardware()
        tier = recommend_tier(self.hardware)
        rec = get_model_recommendation(tier)
        logger.info(f"硬件检测完成: {self.hardware.to_dict()}")
        logger.info(f"推荐档位: {tier.value} → {rec.model_name} ({rec.quantization})")

        # 2. L1: 本地llama.cpp（零配置）
        llama_base = os.getenv("LLAMA_API_BASE", "http://localhost:1342/v1")
        llama_key = os.getenv("LLAMA_API_KEY", "jan-local")
        llama_model = os.getenv(
            "LLAMA_MODEL_NAME", rec.model_name.lower().replace(".", "").replace("-", "")
        )

        llama_provider = create_default_llama_cpp_provider(
            api_base=llama_base,
            api_key=llama_key,
            model_name=llama_model,
        )
        self.registry.register(llama_provider)

        # 2b. L1: Ollama 本地模型（如果Ollama在运行）
        # 与 llama.cpp 互补：Ollama 更易安装，支持模型自动发现
        ollama_base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
        ollama_model = os.getenv("OLLAMA_MODEL_NAME", "")

        if ollama_model:
            # 用户指定了模型名 → 直接注册
            ollama_provider = create_ollama_provider(
                model_name=ollama_model,
                api_base=ollama_base,
            )
            self.registry.register(ollama_provider)
            logger.info(f"Ollama Provider已注册: {ollama_model}")
        else:
            # 用户没指定模型 → 自动检测Ollama是否在运行
            try:
                if OllamaProvider.is_running(ollama_base):
                    models = OllamaProvider.list_models(api_base=ollama_base)
                    if models:
                        auto_model = models[0].name
                        ollama_provider = create_ollama_provider(
                            model_name=auto_model,
                            api_base=ollama_base,
                        )
                        self.registry.register(ollama_provider)
                        logger.info(f"Ollama自动发现模型: {auto_model} (共{len(models)}个已安装)")
            except Exception as e:
                logger.debug(f"Ollama检测跳过: {e}")

        # 3. L2: GLM免费API（有Key才注册）
        glm_key = os.getenv("GLM_API_KEY", "")
        if glm_key:
            glm_provider = create_glm_flash_provider(glm_key)
            self.registry.register(glm_provider)
            logger.info("GLM-4.7-Flash (免费) 已注册")

            # GLM Plus（付费，可选）
            glm_plus_key = os.getenv("GLM_PLUS_API_KEY", "")
            if glm_plus_key:
                from providers.glm_provider import create_glm_plus_provider

                self.registry.register(create_glm_plus_provider(glm_plus_key))

        # 4. L3: DeepSeek付费API（有Key才注册）
        ds_key = os.getenv("DEEPSEEK_API_KEY", "")
        if ds_key:
            ds_provider = create_deepseek_flash_provider(ds_key)
            self.registry.register(ds_provider)
            logger.info("DeepSeek V4-Flash 已注册")

            # DeepSeek Reasoner（可选）
            ds_reasoner_key = os.getenv("DEEPSEEK_REASONER_API_KEY", "")
            if ds_reasoner_key:
                from providers.deepseek_provider import create_deepseek_reasoner_provider

                self.registry.register(create_deepseek_reasoner_provider(ds_reasoner_key))

        # 5. 汇报初始化结果
        providers = self.registry.get_available()
        logger.info(f"ModelRouter初始化完成: {len(providers)}个Provider已注册")
        for p in providers:
            logger.info(f"  - {p.name} ({p.config.tier.value}) → {p.config.model_name}")

        self._initialized = True

    def init_for_testing(self, providers: list[BaseProvider]):
        """测试用：手动注入Provider"""
        for p in providers:
            self.registry.register(p)
        self._initialized = True

    # ==================== 路由策略 ====================

    def set_strategy(self, strategy: RouterStrategy):
        """切换路由策略"""
        self.strategy = strategy
        logger.info(f"路由策略切换为: {strategy.value}")

    def set_tier(self, tier: ProviderTier | None):
        """指定使用哪个层级（仅TIER_SPECIFIED策略有效）"""
        self._specified_tier = tier
        if tier:
            self.strategy = RouterStrategy.TIER_SPECIFIED
            logger.info(f"指定层级: {tier.value}")

    def _get_ordered_providers(self) -> list[BaseProvider]:
        """根据策略获取排序后的Provider列表"""
        available = [p for p in self.registry.get_available() if p.is_available]

        if not available:
            return []

        if self.strategy == RouterStrategy.COST_FIRST:
            # 成本优先：L1 > L2 > L3，同层级按priority
            tier_order = {ProviderTier.L1: 0, ProviderTier.L2: 1, ProviderTier.L3: 2}
            available.sort(key=lambda p: (tier_order.get(p.config.tier, 99), p.config.priority))

        elif self.strategy == RouterStrategy.QUALITY_FIRST:
            # 质量优先：L3 > L2 > L1
            tier_order = {ProviderTier.L3: 0, ProviderTier.L2: 1, ProviderTier.L1: 2}
            available.sort(key=lambda p: (tier_order.get(p.config.tier, 99), p.config.priority))

        elif self.strategy == RouterStrategy.SPEED_FIRST:
            # 速度优先：按历史平均延迟排序
            def avg_latency(p):
                stats = p.stats
                return stats.get("avg_latency_ms", 9999) if stats.get("requests", 0) > 0 else 9999

            available.sort(key=avg_latency)

        elif self.strategy == RouterStrategy.TIER_SPECIFIED:
            # 指定层级
            if self._specified_tier:
                available = [p for p in available if p.config.tier == self._specified_tier]
                available.sort(key=lambda p: p.config.priority)
            else:
                available.sort(key=lambda p: p.config.priority)

        elif self.strategy == RouterStrategy.SMART:
            # v5.1.0: 6维度智能评分排序
            if self.scorer is None:
                from router_score import RouterScore

                self.scorer = RouterScore()
            available = self.scorer.rank_providers(available)

        return available

    # ==================== 核心方法 ====================

    async def chat(
        self,
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
        prefer_tier: ProviderTier | None = None,
    ) -> ChatResponse:
        """统一聊天接口 - 自动路由 + 降级 + 双保障

        Args:
            messages: 消息列表
            max_tokens: 最大输出token
            temperature: 温度
            prefer_tier: 临时指定层级（不改变全局策略）

        Returns:
            ChatResponse: 统一响应

        Raises:
            ProviderError: 所有Provider都失败时抛出
        """
        if not self._initialized:
            self.auto_init_from_env()

        # 临时指定层级
        if prefer_tier:
            old_strategy = self.strategy
            old_tier = self._specified_tier
            self.set_tier(prefer_tier)
            ordered = self._get_ordered_providers()
            self.strategy = old_strategy
            self._specified_tier = old_tier
        else:
            ordered = self._get_ordered_providers()

        if not ordered:
            raise ProviderError("ModelRouter", "没有可用的Provider")

        # 双保障模式：主线路 → 复线
        if self.dual_redundancy.enabled:
            return await self._chat_with_dual_redundancy(ordered, messages, max_tokens, temperature)

        # 原逻辑：串行降级（双保障未启用时）
        errors: list[str] = []
        for provider in ordered:
            try:
                timeout = self._get_timeout_for_provider(provider)
                logger.debug(
                    f"尝试Provider: {provider.name} ({provider.config.tier.value}), timeout={timeout}s"
                )
                if self.scorer:
                    self.scorer.on_request_start(provider.name)
                response = await asyncio.wait_for(
                    provider.chat(messages, max_tokens, temperature), timeout=timeout
                )
                if self.scorer:
                    self.scorer.on_request_success(provider.name)
                logger.info(
                    f"路由成功: {provider.name} "
                    f"({response.latency_ms:.0f}ms, "
                    f"in={response.tokens_in} out={response.tokens_out})"
                )
                return response
            except TimeoutError:
                if self.scorer:
                    self.scorer.on_request_failure(provider.name)
                errors.append(f"{provider.name}: 超时({timeout}s)")
                logger.warning(f"Provider {provider.name} 超时({timeout}s)，尝试降级...")
                continue
            except ProviderError as e:
                if self.scorer:
                    self.scorer.on_request_failure(provider.name)
                errors.append(f"{provider.name}: {e}")
                logger.warning(f"Provider {provider.name} 失败: {e}，尝试降级...")
                continue
            except Exception as e:
                if self.scorer:
                    self.scorer.on_request_failure(provider.name)
                errors.append(f"{provider.name}: {e}")
                logger.warning(f"Provider {provider.name} 异常: {e}，尝试降级...")
                continue

        # 全部失败
        raise ProviderError(
            "ModelRouter", "所有Provider均失败:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    async def _chat_with_dual_redundancy(
        self,
        ordered: list[BaseProvider],
        messages: list[ChatMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        """双保障聊天：主线路失败/超时 → 自动切换到复线

        工作流程：
          1. 尝试主线路（L1 → L2），每个Provider有独立超时
          2. 如果主线路全部失败/超时，切换到复线（DeepSeek L3）
          3. 复线也失败，抛出错误
        """
        cfg = self.dual_redundancy
        errors: list[str] = []

        # 1. 尝试主线路
        primary_providers = [p for p in ordered if p.config.tier in cfg.primary_tiers]
        for provider in primary_providers:
            try:
                timeout = cfg.primary_timeout
                logger.debug(
                    f"[主线路] 尝试Provider: {provider.name} ({provider.config.tier.value}), timeout={timeout}s"
                )
                if self.scorer:
                    self.scorer.on_request_start(provider.name)
                response = await asyncio.wait_for(
                    provider.chat(messages, max_tokens, temperature), timeout=timeout
                )
                if self.scorer:
                    self.scorer.on_request_success(provider.name)
                logger.info(f"[主线路] 路由成功: {provider.name} ({response.latency_ms:.0f}ms)")
                return response
            except TimeoutError:
                if self.scorer:
                    self.scorer.on_request_failure(provider.name)
                errors.append(f"{provider.name}: 超时({timeout}s)")
                logger.warning(f"[主线路] Provider {provider.name} 超时({timeout}s)，降级...")
                continue
            except ProviderError as e:
                if self.scorer:
                    self.scorer.on_request_failure(provider.name)
                errors.append(f"{provider.name}: {e}")
                logger.warning(f"[主线路] Provider {provider.name} 失败: {e}，降级...")
                continue
            except Exception as e:
                if self.scorer:
                    self.scorer.on_request_failure(provider.name)
                errors.append(f"{provider.name}: {e}")
                logger.warning(f"[主线路] Provider {provider.name} 异常: {e}，降级...")
                continue

        # 2. 主线路全部失败，切换到复线
        logger.warning(
            f"[双保障] 主线路全部失败({len(errors)}个错误)，切换到复线({cfg.backup_tier.value})..."
        )
        backup_providers = [p for p in ordered if p.config.tier == cfg.backup_tier]
        for provider in backup_providers:
            try:
                timeout = cfg.backup_timeout
                logger.info(
                    f"[复线] 尝试Provider: {provider.name} ({provider.config.tier.value}), timeout={timeout}s"
                )
                if self.scorer:
                    self.scorer.on_request_start(provider.name)
                response = await asyncio.wait_for(
                    provider.chat(messages, max_tokens, temperature), timeout=timeout
                )
                if self.scorer:
                    self.scorer.on_request_success(provider.name)
                logger.info(f"[复线] 路由成功: {provider.name} ({response.latency_ms:.0f}ms)")
                return response
            except TimeoutError:
                if self.scorer:
                    self.scorer.on_request_failure(provider.name)
                errors.append(f"{provider.name}(复线): 超时({timeout}s)")
                logger.warning(f"[复线] Provider {provider.name} 超时({timeout}s)")
                continue
            except ProviderError as e:
                if self.scorer:
                    self.scorer.on_request_failure(provider.name)
                errors.append(f"{provider.name}(复线): {e}")
                logger.warning(f"[复线] Provider {provider.name} 失败: {e}")
                continue
            except Exception as e:
                if self.scorer:
                    self.scorer.on_request_failure(provider.name)
                errors.append(f"{provider.name}(复线): {e}")
                logger.warning(f"[复线] Provider {provider.name} 异常: {e}")
                continue

        # 3. 全部失败
        raise ProviderError(
            "ModelRouter",
            f"双保障全部失败(主线路{len(cfg.primary_tiers)}层 + 复线1层):\n"
            + "\n".join(f"  - {e}" for e in errors),
        )

    def _get_timeout_for_provider(self, provider: BaseProvider) -> int:
        """根据Provider层级获取超时配置"""
        tier = provider.config.tier
        if tier == ProviderTier.L1:
            return self.dual_redundancy.primary_timeout
        elif tier == ProviderTier.L2:
            return self.dual_redundancy.primary_timeout + 5  # L2比L1多5秒
        elif tier == ProviderTier.L3:
            return self.dual_redundancy.backup_timeout
        else:
            return provider.config.timeout  # 使用Provider自己的配置

    async def chat_simple(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 512,
    ) -> str:
        """简化版聊天接口 - 直接传字符串

        Args:
            prompt: 用户输入
            system: 系统提示词（可选）
            max_tokens: 最大输出token

        Returns:
            str: 模型回复文本
        """
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))

        response = await self.chat(messages, max_tokens=max_tokens)
        return response.content

    # ==================== 状态管理 ====================

    async def health_check_all(self) -> dict[str, bool]:
        """检查所有Provider健康状态"""
        results = {}
        tasks = []
        names = []

        for name, provider in self.registry.get_all().items():
            tasks.append(provider.health_check())
            names.append(name)

        check_results = await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(names, check_results):
            if isinstance(result, Exception):
                results[name] = False
            else:
                results[name] = result

        return results

    def get_status(self) -> dict[str, Any]:
        """获取路由器完整状态"""
        providers_status = []
        for p in self.registry.get_all().values():
            providers_status.append(
                {
                    "name": p.name,
                    "display_name": p.config.display_name,
                    "type": p.config.provider_type.value,
                    "tier": p.config.tier.value,
                    "model": p.config.model_name,
                    "status": p.status.value,
                    "enabled": p.config.enabled,
                    "priority": p.config.priority,
                    "stats": p.stats,
                }
            )

        return {
            "strategy": self.strategy.value,
            "specified_tier": self._specified_tier.value if self._specified_tier else None,
            "hardware": self.hardware.to_dict() if self.hardware else None,
            "providers": providers_status,
            "total_providers": len(providers_status),
            "available_providers": sum(
                1 for p in providers_status if p["status"] in ("healthy", "degraded", "unknown")
            ),
        }

    def get_available_tiers(self) -> list[str]:
        """获取当前可用的层级列表"""
        tiers = set()
        for p in self.registry.get_available():
            if p.is_available:
                tiers.add(p.config.tier.value)
        return sorted(tiers)
