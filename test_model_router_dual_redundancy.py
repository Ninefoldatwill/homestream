"""
测试 ModelRouter 双保障功能（6/27新增）

测试覆盖：
  1. 超时控制：Provider卡住时超时异常 + 降级
  2. 双保障切换：主线路失败 → 自动切换到复线
  3. 配置加载：从环境变量读取双保障配置
  4. 正常流程：主线路成功，不触发复线
"""

import asyncio
import logging
import os
import sys
from unittest.mock import AsyncMock, patch

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

from model_router import DualRedundancyConfig, ModelRouter, ProviderTier, RouterStrategy
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

logging.basicConfig(level=logging.DEBUG)


class MockProvider(BaseProvider):
    """模拟Provider，用于测试"""

    def __init__(
        self, name: str, tier: ProviderTier, should_fail: bool = False, should_timeout: bool = False
    ):
        config = ProviderConfig(
            name=name,
            display_name=name,
            provider_type=ProviderType.API,
            tier=tier,
            timeout=5,
        )
        super().__init__(config)
        self.should_fail = should_fail
        self.should_timeout = should_timeout
        self.call_count = 0

    async def chat(self, messages, max_tokens=None, temperature=None):
        self.call_count += 1
        if self.should_timeout:
            # 模拟卡住（睡眠比超时更长的时间）
            await asyncio.sleep(10)
        if self.should_fail:
            raise ProviderError(self.name, "模拟失败")
        # 正常返回
        await asyncio.sleep(0.1)  # 模拟延迟
        return ChatResponse(
            content=f"Response from {self.name}",
            model=self.config.model_name,
            provider=self.name,
            tier=self.config.tier,
            latency_ms=100,
            tokens_in=10,
            tokens_out=20,
        )

    async def health_check(self):
        return not self.should_fail


async def test_dual_redundancy_primary_success():
    """测试1：主线路成功，不触发复线"""
    print("\n=== 测试1：主线路成功 ===")
    router = ModelRouter()
    router.dual_redundancy = DualRedundancyConfig(
        enabled=True,
        primary_tiers=[ProviderTier.L1, ProviderTier.L2],
        backup_tier=ProviderTier.L3,
        primary_timeout=1,
        backup_timeout=1,
    )

    # 注册模拟Provider：L1成功
    provider_l1 = MockProvider("L1-Success", ProviderTier.L1, should_fail=False)
    router.registry.register(provider_l1)
    router._initialized = True

    response = await router.chat([ChatMessage(role="user", content="test")])
    assert response.provider == "L1-Success", f"期望L1-Success，实际{response.provider}"
    print(f"✅ 主线路成功：{response.provider}")


async def test_dual_redundancy_primary_fail_backup_success():
    """测试2：主线路失败，复线成功"""
    print("\n=== 测试2：主线路失败 → 复线成功 ===")
    router = ModelRouter()
    router.dual_redundancy = DualRedundancyConfig(
        enabled=True,
        primary_tiers=[ProviderTier.L1, ProviderTier.L2],
        backup_tier=ProviderTier.L3,
        primary_timeout=1,
        backup_timeout=1,
    )

    # 注册模拟Provider：L1失败，L2失败，L3成功
    provider_l1 = MockProvider("L1-Fail", ProviderTier.L1, should_fail=True)
    provider_l2 = MockProvider("L2-Fail", ProviderTier.L2, should_fail=True)
    provider_l3 = MockProvider("L3-Success", ProviderTier.L3, should_fail=False)
    router.registry.register(provider_l1)
    router.registry.register(provider_l2)
    router.registry.register(provider_l3)
    router._initialized = True

    response = await router.chat([ChatMessage(role="user", content="test")])
    assert response.provider == "L3-Success", f"期望L3-Success，实际{response.provider}"
    print(f"✅ 复线成功：{response.provider}")
    print(f"   L1调用次数: {provider_l1.call_count}")
    print(f"   L2调用次数: {provider_l2.call_count}")
    print(f"   L3调用次数: {provider_l3.call_count}")


async def test_dual_redundancy_timeout():
    """测试3：主线路超时，降级到下一个"""
    print("\n=== 测试3：主线路超时 ===")
    router = ModelRouter()
    router.dual_redundancy = DualRedundancyConfig(
        enabled=True,
        primary_tiers=[ProviderTier.L1, ProviderTier.L2],
        backup_tier=ProviderTier.L3,
        primary_timeout=0.5,  # 短超时
        backup_timeout=1,
    )

    # 注册模拟Provider：L1超时，L2成功
    provider_l1 = MockProvider("L1-Timeout", ProviderTier.L1, should_timeout=True)
    provider_l2 = MockProvider("L2-Success", ProviderTier.L2, should_fail=False)
    router.registry.register(provider_l1)
    router.registry.register(provider_l2)
    router._initialized = True

    response = await router.chat([ChatMessage(role="user", content="test")])
    assert response.provider == "L2-Success", f"期望L2-Success，实际{response.provider}"
    print(f"✅ 超时后降级成功：{response.provider}")


async def test_dual_redundancy_all_fail():
    """测试4：主线路和复线都失败，抛出错误"""
    print("\n=== 测试4：全部失败 ===")
    router = ModelRouter()
    router.dual_redundancy = DualRedundancyConfig(
        enabled=True,
        primary_tiers=[ProviderTier.L1],
        backup_tier=ProviderTier.L3,
        primary_timeout=1,
        backup_timeout=1,
    )

    # 注册模拟Provider：L1失败，L3失败
    provider_l1 = MockProvider("L1-Fail", ProviderTier.L1, should_fail=True)
    provider_l3 = MockProvider("L3-Fail", ProviderTier.L3, should_fail=True)
    router.registry.register(provider_l1)
    router.registry.register(provider_l3)
    router._initialized = True

    try:
        await router.chat([ChatMessage(role="user", content="test")])
        assert False, "应该抛出异常"
    except ProviderError as e:
        print(f"✅ 全部失败，正确抛出异常: {e}")


async def test_config_load():
    """测试5：从环境变量加载配置"""
    print("\n=== 测试5：配置加载 ===")
    # 设置环境变量
    os.environ["MODEL_ROUTER_DUAL_REDUNDANCY"] = "true"
    os.environ["MODEL_ROUTER_PRIMARY_TIERS"] = "L1,L2"
    os.environ["MODEL_ROUTER_BACKUP_TIER"] = "L3"
    os.environ["MODEL_ROUTER_TIMEOUT_PRIMARY"] = "10"
    os.environ["MODEL_ROUTER_TIMEOUT_BACKUP"] = "15"

    router = ModelRouter()
    cfg = router.dual_redundancy
    print(f"   enabled: {cfg.enabled}")
    print(f"   primary_tiers: {[t.value for t in cfg.primary_tiers]}")
    print(f"   backup_tier: {cfg.backup_tier.value}")
    print(f"   primary_timeout: {cfg.primary_timeout}")
    print(f"   backup_timeout: {cfg.backup_timeout}")
    assert cfg.enabled == True
    assert len(cfg.primary_tiers) == 2
    assert cfg.backup_tier == ProviderTier.L3
    print("✅ 配置加载成功")


async def main():
    """运行所有测试"""
    print("开始测试 ModelRouter 双保障功能...")
    await test_dual_redundancy_primary_success()
    await test_dual_redundancy_primary_fail_backup_success()
    await test_dual_redundancy_timeout()
    await test_dual_redundancy_all_fail()
    await test_config_load()
    print("\n✅ 所有测试通过！")


if __name__ == "__main__":
    asyncio.run(main())
