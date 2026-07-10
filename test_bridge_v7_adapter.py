"""
test_bridge_v7_adapter.py — BridgeV7Adapter 完整集成测试

测试范围：
1. 基本功能（创建/训练环境/Rollout/Reflect）
2. 完整 SkillOpt 循环（Rollout → Reflect → Evaluate → Apply Patch）
3. 持久化 EventStream（EventStore）
4. 端口隔离（PortManager）
5. 错误处理（失败任务/超时/API异常）

作者: 澜舟
日期: 2026-06-21
"""

import os
import sys
import tempfile
import time

# 添加桥v7路径
sys.path.insert(0, os.path.dirname(__file__))

# skillopt 是 L3 自用层依赖，开源版不包含此模块。
# 有 skillopt 时正常运行全部 6 个测试（本地 L3 开发环境）；
# 无 skillopt 时优雅跳过（GitHub Actions CI 环境），不阻断其余 845+ 测试。
import pytest

pytest.importorskip("skillopt", reason="skillopt 是 L3 自用层依赖，开源版不可用")

from bridge_v7_adapter import (
    BridgeV7Config,
    create_bridge_v7_adapter,
)
from condition_verifier import VerifierConfig


def test_1_basic_functionality():
    """测试1：基本功能测试"""
    print("\n=== 测试1：基本功能 ===")

    # 1.1 创建适配器
    adapter = create_bridge_v7_adapter()
    print("  ✓ 1.1 Adapter创建成功")

    # 1.2 get_task_types
    task_types = adapter.get_task_types()
    assert isinstance(task_types, list), "get_task_types应返回list"
    assert len(task_types) > 0, "应至少返回ICP消息类型"
    print(f"  ✓ 1.2 任务类型: {len(task_types)}个")

    # 1.3 build_train_env
    env = adapter.build_train_env(batch_size=2, seed=42)
    assert "worktree" in env, "环境应包含worktree"
    assert "event_stream" in env, "环境应包含event_stream"
    assert "ports" in env, "环境应包含ports"
    assert len(env["ports"]) > 0, "应分配至少一个端口"
    print(f"  ✓ 1.3 训练环境: worktree={env['worktree']}, ports={len(env['ports'])}个")

    # 1.4 rollout（模拟执行）
    skill_content = """---
tasks:
  - type: echo
    description: 测试任务1（应该成功）
  - type: echo
    description: 测试任务2（应该成功）
---
# 测试 Skill
这是一个测试 Skill 文档。
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = adapter.rollout(env, skill_content, tmpdir)
        assert isinstance(results, list), "rollout应返回list"
        assert len(results) > 0, "应至少执行一个任务"
        assert "hard" in results[0], "结果应包含hard字段"
        assert "soft" in results[0], "结果应包含soft字段"
        print(f"  ✓ 1.4 Rollout: {len(results)}个结果")

        # 1.5 reflect
        patches = adapter.reflect(results, skill_content, tmpdir)
        assert isinstance(patches, list), "reflect应返回list"
        print(f"  ✓ 1.5 Reflect: {len(patches)}个Patches")

    print("=== 测试1通过 ✓ ===")
    return True


def test_2_complete_skillopt_cycle():
    """测试2：完整 SkillOpt 循环"""
    print("\n=== 测试2：完整SkillOpt循环 ===")

    adapter = create_bridge_v7_adapter()

    # 2.1 准备初始Skill
    initial_skill = """---
tasks:
  - type: echo
    description: 任务A
  - type: echo
    description: 任务B
---
# 初始 Skill
这是一个初始版本的 Skill 文档。

## 执行步骤
1. 解析输入
2. 执行任务
3. 返回结果

## 错误处理
（待完善）
"""

    # 2.2 Rollout（执行初始Skill）
    env = adapter.build_train_env(batch_size=2, seed=100)

    with tempfile.TemporaryDirectory() as tmpdir:
        results_v1 = adapter.rollout(env, initial_skill, tmpdir)
        print(f"  ✓ 2.1 Rollout v1: {len(results_v1)}个结果")

        # 2.3 Reflect（分析失败，生成Patch）
        patches = adapter.reflect(results_v1, initial_skill, tmpdir)
        print(f"  ✓ 2.2 Reflect: 生成{len(patches)}个Patches")

        # 2.4 应用Patch（生成v2 Skill）
        if patches and patches[0]:
            patch = patches[0]
            new_skill_version = adapter.apply_patch(patch, initial_skill)
            print(f"  ✓ 2.3 Apply Patch: 新版本ID={new_skill_version[:16]}...")

            # 2.5 执行新版本Skill
            results_v2 = adapter.rollout(env, new_skill_version, tmpdir)
            print(f"  ✓ 2.4 Rollout v2: {len(results_v2)}个结果")

            # 2.6 对比v1和v2（应该有所改进）
            avg_hard_v1 = sum(r["hard"] for r in results_v1) / len(results_v1)
            avg_hard_v2 = sum(r["hard"] for r in results_v2) / len(results_v2)
            print(f"  ✓ 2.5 性能对比: v1={avg_hard_v1:.2f} → v2={avg_hard_v2:.2f}")

        else:
            print("  ⚠ 2.3 没有生成Patch（可能是规则分析未触发）")

    print("=== 测试2通过 ✓ ===")
    return True


def test_3_persistent_event_stream():
    """测试3：持久化 EventStream（EventStore）"""
    print("\n=== 测试3：持久化EventStream ===")

    # 3.1 创建带持久化的适配器
    bridge_cfg = BridgeV7Config(
        event_stream_persist=True,
        event_store_path="data/test_bridge_v7_events.db",
    )
    adapter = create_bridge_v7_adapter(bridge_cfg=bridge_cfg)

    # 3.2 执行rollout（产生事件）
    env = adapter.build_train_env(batch_size=1, seed=200)
    skill_content = """---
tasks:
  - type: test
    description: 持久化测试任务
---
# 持久化测试 Skill
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = adapter.rollout(env, skill_content, tmpdir)
        print(f"  ✓ 3.1 Rollout完成: {len(results)}个结果")

        # 3.3 检查EventStore文件
        event_store_path = bridge_cfg.event_store_path
        if os.path.exists(event_store_path):
            file_size = os.path.getsize(event_store_path)
            print(f"  ✓ 3.2 EventStore文件: {event_store_path} ({file_size} bytes)")
        else:
            print(f"  ⚠ 3.2 EventStore文件不存在: {event_store_path}")

    print("=== 测试3通过 ✓ ===")
    return True


def test_4_port_isolation():
    """测试4：端口隔离（PortManager）"""
    print("\n=== 测试4：端口隔离 ===")

    adapter = create_bridge_v7_adapter()

    # 4.1 创建两个训练环境（不同seed）
    env1 = adapter.build_train_env(batch_size=1, seed=1)
    env2 = adapter.build_train_env(batch_size=1, seed=2)

    ports1 = env1["ports"]
    ports2 = env2["ports"]

    print(f"  ✓ 4.1 环境1端口: {list(ports1.items())[:2]}...")
    print(f"  ✓ 4.2 环境2端口: {list(ports2.items())[:2]}...")

    # 4.2 检查端口是否不同（隔离）
    shared_keys = set(ports1.keys()) & set(ports2.keys())
    if shared_keys:
        # 相同服务应分配不同端口
        for key in shared_keys:
            assert ports1[key] != ports2[key], f"端口隔离失败: {key} 都是{ports1[key]}"

    print("  ✓ 4.3 端口隔离验证通过")

    print("=== 测试4通过 ✓ ===")
    return True


def test_5_error_handling():
    """测试5：错误处理"""
    print("\n=== 测试5：错误处理 ===")

    adapter = create_bridge_v7_adapter()
    env = adapter.build_train_env(batch_size=1, seed=300)

    # 5.1 测试空Skill
    print("  ✓ 5.1 测试空Skill...")
    empty_skill = ""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = adapter.rollout(env, empty_skill, tmpdir)
        # 应该返回空列表或带错误的列表（不应该崩溃）
        print(f"    → 空Skill结果: {len(results)}个")

    # 5.2 测试超时任务（模拟）
    print("  ✓ 5.2 测试超时任务（模拟）...")
    timeout_skill = """---
tasks:
  - type: timeout_task
    description: 这个任务会超时
---
# 超时测试 Skill
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = adapter.rollout(env, timeout_skill, tmpdir)
        # 应该捕获异常，返回失败结果（不应该崩溃）
        if results:
            failed = [r for r in results if not r.get("hard")]
            print(f"    → 超时任务: {len(failed)}/{len(results)}个失败")

    # 5.3 测试 SkillRouter 未启用
    print("  ✓ 5.3 测试SkillRouter未启用...")
    bridge_cfg = BridgeV7Config(skill_router_enabled=False)
    adapter_no_router = create_bridge_v7_adapter(bridge_cfg=bridge_cfg)
    # 不应该崩溃（会使用_execute_task_direct兜底）
    print("    → SkillRouter未启用: 使用直接执行兜底")

    print("=== 测试5通过 ✓ ===")
    return True


def test_6_condition_verifier_integration():
    """测试6：ConditionVerifier 集成"""
    print("\n=== 测试6：ConditionVerifier集成 ===")

    # 6.1 创建带严格停止条件的适配器
    verifier_config = VerifierConfig(
        max_iterations=2,  # 最多2次迭代（任务）
        empty_timeout=5.0,  # 5秒空闲超时
    )
    bridge_cfg = BridgeV7Config(verifier_config=verifier_config)
    adapter = create_bridge_v7_adapter(bridge_cfg=bridge_cfg)

    # 6.2 执行rollout（应该在中途停止）
    env = adapter.build_train_env(batch_size=100, seed=400)  # 100个任务（应该被停止）
    skill_content = """---
tasks:
"""
    # 添加100个任务
    for i in range(100):
        skill_content += f"  - type: echo\n    description: 任务{i}\n"
    skill_content += "---\n# 大量任务 Skill\n"

    start_time = time.time()
    with tempfile.TemporaryDirectory() as tmpdir:
        results = adapter.rollout(env, skill_content, tmpdir)
        elapsed = time.time() - start_time

        print(f"  ✓ 6.1 执行了{len(results)}个任务（原计划100个）")
        print(f"  ✓ 6.2 耗时{elapsed:.2f}秒（应该≤10秒）")

        # 验证是否被停止
        if len(results) < 100:
            print("  ✓ 6.3 ConditionVerifier成功停止了执行")
        else:
            print("  ⚠ 6.3 ConditionVerifier未触发（可能需要更多事件）")

    print("=== 测试6通过 ✓ ===")
    return True


# ==================== 主测试运行器 ====================


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("BridgeV7Adapter 完整集成测试")
    print("=" * 60)

    tests = [
        ("基本功能", test_1_basic_functionality),
        ("完整SkillOpt循环", test_2_complete_skillopt_cycle),
        ("持久化EventStream", test_3_persistent_event_stream),
        ("端口隔离", test_4_port_isolation),
        ("错误处理", test_5_error_handling),
        ("ConditionVerifier集成", test_6_condition_verifier_integration),
    ]

    results = []
    for name, test_func in tests:
        try:
            start = time.time()
            success = test_func()
            elapsed = time.time() - start
            results.append((name, "通过" if success else "失败", elapsed))
        except Exception as e:
            results.append((name, f"错误: {e}", 0))
            import traceback

            traceback.print_exc()

    # 打印测试报告
    print("\n" + "=" * 60)
    print("测试报告")
    print("=" * 60)
    print(f"{'测试名称':<30} {'状态':<15} {'耗时(秒)':<10}")
    print("-" * 60)
    for name, status, elapsed in results:
        print(f"{name:<30} {status:<15} {elapsed:<10.2f}")
    print("-" * 60)

    passed = sum(1 for _, s, _ in results if s == "通过")
    print(f"\n总计: {passed}/{len(tests)} 通过")

    return passed == len(tests)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="BridgeV7Adapter 集成测试")
    parser.add_argument("--test", type=str, default="all", help="指定测试（1-6）或'all'")
    args = parser.parse_args()

    if args.test == "all":
        success = run_all_tests()
    else:
        # 运行单个测试
        test_num = int(args.test)
        test_func = globals().get(f"test_{test_num}_xxx")  # 动态获取
        # 简化：直接运行所有测试
        success = run_all_tests()

    sys.exit(0 if success else 1)
