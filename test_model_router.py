"""
ModelRouter 测试套件

覆盖：
  1. 硬件检测
  2. Provider基类
  3. ProviderRegistry
  4. LlamaCppProvider（实际连接llama-server）
  5. ModelRouter路由策略
  6. ModelRouter降级机制
  7. ModelRouter统一接口
"""

import pytest
import asyncio
import os
import sys
from unittest.mock import Mock, patch, MagicMock
from typing import List

# 确保项目根目录在path中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hardware_profile import (
    detect_hardware, recommend_tier, get_model_recommendation,
    HardwareInfo, HardwareTier,
)
from providers.base_provider import (
    BaseProvider, ProviderConfig, ProviderType, ProviderTier,
    ProviderStatus, ChatMessage, ChatResponse, ProviderError,
)
from providers.llama_cpp_provider import LlamaCppProvider, create_default_llama_cpp_provider
from providers.glm_provider import GLMProvider, create_glm_flash_provider
from providers.deepseek_provider import DeepSeekProvider, create_deepseek_flash_provider
from model_router import ModelRouter, ProviderRegistry, RouterStrategy


# ==================== 1. 硬件检测测试 ====================

class TestHardwareProfile:
    """硬件检测测试"""

    @pytest.mark.skipif(
        os.environ.get("GITHUB_ACTIONS") == "true",
        reason="GitHub Actions runner 无法检测物理内存（total_ram_gb=0）",
    )
    def test_detect_hardware(self):
        """测试硬件自动检测"""
        info = detect_hardware()
        assert info.total_ram_gb > 0, "总内存应大于0"
        assert info.cpu_cores > 0, "CPU核心数应大于0"
        assert info.os_type in ("windows", "linux", "macos", "unknown")

    def test_detect_hardware_returns_dict(self):
        """测试硬件信息转dict"""
        info = detect_hardware()
        d = info.to_dict()
        assert "total_ram_gb" in d
        assert "gpu_name" in d
        assert "cpu_cores" in d

    def test_recommend_tier_nano(self):
        """测试Nano档位推荐（8GB无GPU）"""
        info = HardwareInfo(total_ram_gb=8, gpu_vram_total_mb=0)
        tier = recommend_tier(info)
        assert tier == HardwareTier.NANO

    def test_recommend_tier_lite(self):
        """测试Lite档位推荐（16GB+6GB VRAM）"""
        info = HardwareInfo(total_ram_gb=16, gpu_vram_total_mb=6144)
        tier = recommend_tier(info)
        assert tier == HardwareTier.LITE

    def test_recommend_tier_std(self):
        """测试Std档位推荐（32GB+8GB VRAM）"""
        info = HardwareInfo(total_ram_gb=32, gpu_vram_total_mb=8192)
        tier = recommend_tier(info)
        assert tier == HardwareTier.STD

    def test_recommend_tier_max(self):
        """测试Max档位推荐（256GB+48GB VRAM）"""
        info = HardwareInfo(total_ram_gb=256, gpu_vram_total_mb=49152)
        tier = recommend_tier(info)
        assert tier == HardwareTier.MAX

    def test_get_model_recommendation(self):
        """测试模型推荐"""
        for tier in HardwareTier:
            rec = get_model_recommendation(tier)
            assert rec.tier == tier
            assert rec.model_name != ""
            assert rec.deployment_method != ""

    def test_lite_recommendation_matches_jiuzhong_hardware(self):
        """测试九重硬件锚点应推荐Lite档位"""
        info = HardwareInfo(
            total_ram_gb=15.7,   # 16GB物理内存实际显示15.7GB
            gpu_vram_total_mb=6141,  # 6GB VRAM
            gpu_name="NVIDIA GeForce RTX 4050 Laptop GPU",
        )
        tier = recommend_tier(info)
        assert tier == HardwareTier.LITE
        rec = get_model_recommendation(tier)
        assert "Qwen2.5-7B" in rec.model_name
        assert rec.can_full_gpu_offload is True

    def test_micro_recommendation(self):
        """测试Micro档位（16GB RAM + 4GB VRAM）"""
        info = HardwareInfo(
            total_ram_gb=15.7,
            gpu_vram_total_mb=4096,
        )
        tier = recommend_tier(info)
        assert tier == HardwareTier.MICRO

    def test_nano_recommendation(self):
        """测试Nano档位（8GB RAM无GPU）"""
        info = HardwareInfo(
            total_ram_gb=7.5,
            gpu_vram_total_mb=0,
        )
        tier = recommend_tier(info)
        assert tier == HardwareTier.NANO


# ==================== 2. Provider基类测试 ====================

class TestBaseProvider:
    """Provider基类测试"""

    def test_chat_message_creation(self):
        """测试ChatMessage创建"""
        msg = ChatMessage(role="user", content="你好")
        assert msg.role == "user"
        assert msg.content == "你好"
        assert msg.to_dict() == {"role": "user", "content": "你好"}

    def test_chat_response_creation(self):
        """测试ChatResponse创建"""
        resp = ChatResponse(
            content="回复内容",
            model="test-model",
            provider="test",
            tier=ProviderTier.L1,
            latency_ms=100.5,
        )
        d = resp.to_dict()
        assert d["content"] == "回复内容"
        assert d["model"] == "test-model"
        assert d["tier"] == "L1"
        assert d["latency_ms"] == 100.5

    def test_provider_config_defaults(self):
        """测试ProviderConfig默认值"""
        config = ProviderConfig(
            name="test",
            display_name="Test Provider",
            provider_type=ProviderType.LOCAL,
            tier=ProviderTier.L1,
        )
        assert config.enabled is True
        assert config.priority == 100
        assert config.max_tokens == 512
        assert config.temperature == 0.7

    def test_provider_cost_estimation(self):
        """测试费用估算"""
        config = ProviderConfig(
            name="test",
            display_name="Test",
            provider_type=ProviderType.API,
            tier=ProviderTier.L3,
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.002,
        )

        # 创建一个简单的测试Provider
        class TestProvider(BaseProvider):
            async def chat(self, messages, max_tokens=None, temperature=None):
                pass
            async def health_check(self):
                return True

        provider = TestProvider(config)
        cost = provider._estimate_cost(tokens_in=1000, tokens_out=500)
        assert cost == pytest.approx(0.002)  # 0.001 + 0.001


# ==================== 3. ProviderRegistry测试 ====================

class TestProviderRegistry:
    """ProviderRegistry测试"""

    def test_register_and_get(self):
        """测试注册和获取"""
        registry = ProviderRegistry()
        config = ProviderConfig(
            name="test_provider",
            display_name="Test",
            provider_type=ProviderType.LOCAL,
            tier=ProviderTier.L1,
        )

        class DummyProvider(BaseProvider):
            async def chat(self, messages, max_tokens=None, temperature=None):
                pass
            async def health_check(self):
                return True

        provider = DummyProvider(config)
        registry.register(provider)

        assert registry.get("test_provider") is not None
        assert len(registry.get_all()) == 1

    def test_unregister(self):
        """测试注销"""
        registry = ProviderRegistry()
        config = ProviderConfig(
            name="test_provider",
            display_name="Test",
            provider_type=ProviderType.LOCAL,
            tier=ProviderTier.L1,
        )

        class DummyProvider(BaseProvider):
            async def chat(self, messages, max_tokens=None, temperature=None):
                pass
            async def health_check(self):
                return True

        provider = DummyProvider(config)
        registry.register(provider)
        assert len(registry.get_all()) == 1

        registry.unregister("test_provider")
        assert len(registry.get_all()) == 0
        assert registry.get("test_provider") is None

    def test_get_by_tier(self):
        """测试按层级获取"""
        registry = ProviderRegistry()

        for i, tier in enumerate([ProviderTier.L1, ProviderTier.L2, ProviderTier.L3]):
            config = ProviderConfig(
                name=f"provider_{i}",
                display_name=f"Provider {i}",
                provider_type=ProviderType.LOCAL if tier == ProviderTier.L1 else ProviderType.API,
                tier=tier,
            )

            class DummyProvider(BaseProvider):
                async def chat(self, messages, max_tokens=None, temperature=None):
                    pass
                async def health_check(self):
                    return True

            registry.register(DummyProvider(config))

        l1 = registry.get_by_tier(ProviderTier.L1)
        l2 = registry.get_by_tier(ProviderTier.L2)
        l3 = registry.get_by_tier(ProviderTier.L3)

        assert len(l1) == 1
        assert len(l2) == 1
        assert len(l3) == 1

    def test_list_status(self):
        """测试状态列表"""
        registry = ProviderRegistry()
        config = ProviderConfig(
            name="test",
            display_name="Test",
            provider_type=ProviderType.LOCAL,
            tier=ProviderTier.L1,
        )

        class DummyProvider(BaseProvider):
            async def chat(self, messages, max_tokens=None, temperature=None):
                pass
            async def health_check(self):
                return True

        registry.register(DummyProvider(config))
        status_list = registry.list()
        assert len(status_list) == 1
        assert status_list[0]["name"] == "test"


# ==================== 4. LlamaCppProvider测试 ====================

class TestLlamaCppProvider:
    """LlamaCppProvider测试"""

    def test_create_default_provider(self):
        """测试默认Provider创建"""
        provider = create_default_llama_cpp_provider()
        assert provider.name == "llama_cpp_local"
        assert provider.config.tier == ProviderTier.L1
        assert provider.config.provider_type == ProviderType.LOCAL
        assert provider.config.cost_per_1k_input == 0.0

    @pytest.mark.asyncio
    async def test_health_check_online(self):
        """测试健康检查（llama-server应在线）- 如果服务不在线则跳过"""
        provider = create_default_llama_cpp_provider(
            api_base="http://localhost:1342/v1",
            api_key="jan-local",
            model_name="qwen_model.gguf",
        )
        result = await provider.health_check()
        # llama-server可能不在线，接受False结果
        if result is False:
            pytest.skip("llama-server不在线，跳过在线健康检查测试")
        assert provider.status == ProviderStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_health_check_offline(self):
        """测试健康检查（端口不存在的服务应离线）"""
        provider = create_default_llama_cpp_provider(
            api_base="http://localhost:9999/v1",
            api_key="jan-local",
            model_name="test",
        )
        result = await provider.health_check()
        assert result is False
        assert provider.status == ProviderStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_chat_with_real_server(self):
        """测试实际聊天（需要llama-server在线）"""
        provider = create_default_llama_cpp_provider(
            api_base="http://localhost:1342/v1",
            api_key="jan-local",
            model_name="qwen_model.gguf",
        )

        # 先检查服务是否在线
        is_online = await provider.health_check()
        if not is_online:
            pytest.skip("llama-server未运行，跳过实际聊天测试")

        messages = [
            ChatMessage(role="system", content="你是一个简洁的助手，用一句话回答。"),
            ChatMessage(role="user", content="1+1等于几？"),
        ]
        response = await provider.chat(messages, max_tokens=50, temperature=0.1)

        assert response.content != ""
        assert response.provider == "llama_cpp_local"
        assert response.tier == ProviderTier.L1
        assert response.cost_estimate == 0.0  # 本地模型免费
        assert response.latency_ms > 0


# ==================== 5. ModelRouter路由策略测试 ====================

class TestModelRouterStrategy:
    """ModelRouter路由策略测试"""

    def _create_mock_provider(self, name, tier, available=True, latency=100):
        """创建Mock Provider"""
        config = ProviderConfig(
            name=name,
            display_name=name,
            provider_type=ProviderType.LOCAL if tier == ProviderTier.L1 else ProviderType.API,
            tier=tier,
            priority=10 + list(ProviderTier).index(tier) * 10,
        )

        class MockProvider(BaseProvider):
            async def chat(self, messages, max_tokens=None, temperature=None):
                self._record_request(latency, True)
                self._mark_status(ProviderStatus.HEALTHY)
                return ChatResponse(
                    content=f"Response from {self.name}",
                    model=self.config.model_name,
                    provider=self.name,
                    tier=self.config.tier,
                    latency_ms=latency,
                )
            async def health_check(self):
                if available:
                    self._mark_status(ProviderStatus.HEALTHY)
                else:
                    self._mark_status(ProviderStatus.OFFLINE)
                return available

        provider = MockProvider(config)
        if available:
            provider._mark_status(ProviderStatus.HEALTHY)
        else:
            provider._mark_status(ProviderStatus.OFFLINE)
        return provider

    def test_cost_first_strategy(self):
        """测试成本优先策略（L1 > L2 > L3）"""
        router = ModelRouter(strategy=RouterStrategy.COST_FIRST)
        router.init_for_testing([
            self._create_mock_provider("l3_provider", ProviderTier.L3),
            self._create_mock_provider("l1_provider", ProviderTier.L1),
            self._create_mock_provider("l2_provider", ProviderTier.L2),
        ])

        ordered = router._get_ordered_providers()
        assert ordered[0].name == "l1_provider"
        assert ordered[1].name == "l2_provider"
        assert ordered[2].name == "l3_provider"

    def test_quality_first_strategy(self):
        """测试质量优先策略（L3 > L2 > L1）"""
        router = ModelRouter(strategy=RouterStrategy.QUALITY_FIRST)
        router.init_for_testing([
            self._create_mock_provider("l1_provider", ProviderTier.L1),
            self._create_mock_provider("l3_provider", ProviderTier.L3),
            self._create_mock_provider("l2_provider", ProviderTier.L2),
        ])

        ordered = router._get_ordered_providers()
        assert ordered[0].name == "l3_provider"
        assert ordered[1].name == "l2_provider"
        assert ordered[2].name == "l1_provider"

    def test_tier_specified_strategy(self):
        """测试指定层级策略"""
        router = ModelRouter()
        router.init_for_testing([
            self._create_mock_provider("l1_provider", ProviderTier.L1),
            self._create_mock_provider("l2_provider", ProviderTier.L2),
            self._create_mock_provider("l3_provider", ProviderTier.L3),
        ])

        router.set_tier(ProviderTier.L2)
        ordered = router._get_ordered_providers()
        assert len(ordered) == 1
        assert ordered[0].name == "l2_provider"

    def test_get_available_tiers(self):
        """测试获取可用层级"""
        router = ModelRouter()
        router.init_for_testing([
            self._create_mock_provider("l1_provider", ProviderTier.L1),
            self._create_mock_provider("l2_provider", ProviderTier.L2),
        ])

        tiers = router.get_available_tiers()
        assert "L1" in tiers
        assert "L2" in tiers


# ==================== 6. ModelRouter降级机制测试 ====================

class TestModelRouterFallback:
    """ModelRouter降级机制测试"""

    def _create_failing_provider(self, name, tier):
        """创建会失败的Provider"""
        config = ProviderConfig(
            name=name,
            display_name=name,
            provider_type=ProviderType.LOCAL if tier == ProviderTier.L1 else ProviderType.API,
            tier=tier,
            priority=10,
        )

        class FailingProvider(BaseProvider):
            async def chat(self, messages, max_tokens=None, temperature=None):
                self._record_request(50, False)
                self._mark_status(ProviderStatus.OFFLINE)
                raise ProviderError(self.name, "模拟失败")
            async def health_check(self):
                self._mark_status(ProviderStatus.HEALTHY)
                return True

        provider = FailingProvider(config)
        provider._mark_status(ProviderStatus.HEALTHY)
        return provider

    def _create_success_provider(self, name, tier):
        """创建成功的Provider"""
        config = ProviderConfig(
            name=name,
            display_name=name,
            provider_type=ProviderType.LOCAL if tier == ProviderTier.L1 else ProviderType.API,
            tier=tier,
            priority=20,
        )

        class SuccessProvider(BaseProvider):
            async def chat(self, messages, max_tokens=None, temperature=None):
                self._record_request(100, True)
                self._mark_status(ProviderStatus.HEALTHY)
                return ChatResponse(
                    content=f"OK from {self.name}",
                    model="test",
                    provider=self.name,
                    tier=self.config.tier,
                    latency_ms=100,
                )
            async def health_check(self):
                self._mark_status(ProviderStatus.HEALTHY)
                return True

        provider = SuccessProvider(config)
        provider._mark_status(ProviderStatus.HEALTHY)
        return provider

    @pytest.mark.asyncio
    async def test_fallback_to_next_provider(self):
        """测试L1失败后降级到L2"""
        router = ModelRouter(strategy=RouterStrategy.COST_FIRST)
        router.init_for_testing([
            self._create_failing_provider("l1_fail", ProviderTier.L1),
            self._create_success_provider("l2_ok", ProviderTier.L2),
        ])

        response = await router.chat([ChatMessage(role="user", content="test")])
        assert response.provider == "l2_ok"
        assert response.content == "OK from l2_ok"

    @pytest.mark.asyncio
    async def test_all_providers_fail(self):
        """测试所有Provider都失败"""
        router = ModelRouter(strategy=RouterStrategy.COST_FIRST)
        router.init_for_testing([
            self._create_failing_provider("l1_fail", ProviderTier.L1),
            self._create_failing_provider("l2_fail", ProviderTier.L2),
        ])

        with pytest.raises(ProviderError) as exc_info:
            await router.chat([ChatMessage(role="user", content="test")])
        assert "双保障全部失败" in str(exc_info.value) or "所有Provider均失败" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_prefer_tier_override(self):
        """测试临时指定层级"""
        router = ModelRouter(strategy=RouterStrategy.COST_FIRST)
        router.init_for_testing([
            self._create_success_provider("l1_ok", ProviderTier.L1),
            self._create_success_provider("l2_ok", ProviderTier.L2),
        ])

        # 默认成本优先 → L1
        response = await router.chat([ChatMessage(role="user", content="test")])
        assert response.provider == "l1_ok"

        # 临时指定L2
        response = await router.chat(
            [ChatMessage(role="user", content="test")],
            prefer_tier=ProviderTier.L2,
        )
        assert response.provider == "l2_ok"

    @pytest.mark.asyncio
    async def test_no_available_provider(self):
        """测试没有可用Provider"""
        router = ModelRouter()
        # 不初始化任何Provider
        router._initialized = True

        with pytest.raises(ProviderError) as exc_info:
            await router.chat([ChatMessage(role="user", content="test")])
        assert "没有可用的Provider" in str(exc_info.value)


# ==================== 7. ModelRouter状态测试 ====================

class TestModelRouterStatus:
    """ModelRouter状态管理测试"""

    def test_get_status(self):
        """测试获取状态"""
        router = ModelRouter(strategy=RouterStrategy.COST_FIRST)
        router.init_for_testing([
            self._create_mock("l1", ProviderTier.L1),
            self._create_mock("l2", ProviderTier.L2),
        ])

        status = router.get_status()
        assert status["strategy"] == "cost_first"
        assert status["total_providers"] == 2
        assert len(status["providers"]) == 2

    def _create_mock(self, name, tier):
        config = ProviderConfig(
            name=name,
            display_name=name,
            provider_type=ProviderType.LOCAL if tier == ProviderTier.L1 else ProviderType.API,
            tier=tier,
        )

        class MockP(BaseProvider):
            async def chat(self, messages, max_tokens=None, temperature=None):
                pass
            async def health_check(self):
                return True

        p = MockP(config)
        p._mark_status(ProviderStatus.HEALTHY)
        return p

    @pytest.mark.asyncio
    async def test_health_check_all(self):
        """测试批量健康检查"""
        router = ModelRouter()
        router.init_for_testing([
            self._create_mock("l1", ProviderTier.L1),
            self._create_mock("l2", ProviderTier.L2),
        ])

        results = await router.health_check_all()
        assert "l1" in results
        assert "l2" in results

    def test_set_strategy(self):
        """测试切换策略"""
        router = ModelRouter(strategy=RouterStrategy.COST_FIRST)
        assert router.strategy == RouterStrategy.COST_FIRST

        router.set_strategy(RouterStrategy.QUALITY_FIRST)
        assert router.strategy == RouterStrategy.QUALITY_FIRST


# ==================== 8. 实际集成测试（需llama-server在线） ====================

class TestModelRouterIntegration:
    """ModelRouter实际集成测试"""

    @pytest.mark.asyncio
    async def test_auto_init_and_chat(self):
        """测试自动初始化并聊天"""
        router = ModelRouter(strategy=RouterStrategy.COST_FIRST)

        # 手动注入（模拟.env配置）
        from providers.llama_cpp_provider import create_default_llama_cpp_provider
        provider = create_default_llama_cpp_provider(
            api_base="http://localhost:1342/v1",
            api_key="jan-local",
            model_name="qwen_model.gguf",
        )
        router.init_for_testing([provider])

        # 检查健康
        is_healthy = await provider.health_check()
        if not is_healthy:
            pytest.skip("llama-server未运行")

        # 实际聊天
        response = await router.chat(
            [ChatMessage(role="user", content="说'你好'两个字")],
            max_tokens=20,
            temperature=0.1,
        )
        assert response.content != ""
        assert response.provider == "llama_cpp_local"
        assert response.tier == ProviderTier.L1
        print(f"\n本地模型回复: {response.content}")
        print(f"延迟: {response.latency_ms:.0f}ms")

    @pytest.mark.asyncio
    async def test_chat_simple(self):
        """测试简化版聊天接口"""
        router = ModelRouter()
        provider = create_default_llama_cpp_provider(
            api_base="http://localhost:1342/v1",
            api_key="jan-local",
            model_name="qwen_model.gguf",
        )
        router.init_for_testing([provider])

        is_healthy = await provider.health_check()
        if not is_healthy:
            pytest.skip("llama-server未运行")

        result = await router.chat_simple(
            prompt="1+1=?",
            system="你是数学助手，只回答数字",
            max_tokens=10,
        )
        assert result != ""
        print(f"\n简化接口回复: {result}")
