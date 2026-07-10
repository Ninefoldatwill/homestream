"""
OpenBridge 弹性模式模块 — Solo/Team/Ecosystem 三种部署模式

模式定位（融优主义）：
- Solo:   单Agent轻量部署（零配置启动）
- Team:   团队协作标准部署（Kanban+群聊+Worktree）
- Ecosystem: 生态扩展完整部署（全部功能+外部Agent接入）

融优来源：
- 直接融：pydantic-settings环境变量加载（config.py已有）
- 融合改造：Feature Flags模式（逐渐启用功能）
- 自己造：模式验证+切换逻辑
"""

import os  # 新增：用于os.getenv
from enum import Enum
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from config import settings


class DeployMode(str, Enum):
    """部署模式枚举"""

    SOLO = "solo"  # 单Agent模式
    TEAM = "team"  # 团队协作模式
    ECOSYSTEM = "ecosystem"  # 生态扩展模式


class FeatureFlag(str, Enum):
    """功能开关枚举"""

    # 核心功能
    EVENT_STREAM = "event_stream"  # EventStream引擎
    GROUP_CHAT = "group_chat"  # 群聊系统
    KANBAN = "kanban"  # Kanban任务板

    # 协作功能
    WORKTREE = "worktree"  # Worktree管理器
    RATCHET_LOOP = "ratchet_loop"  # 双层实验工坊
    HANDOFF = "handoff"  # Agent交接协议

    # 通讯功能
    ICP_V2 = "icp_v2"  # ICP v2.0协议
    A2A_INTEGRATION = "a2a"  # A2A协议集成
    MCP_SERVER = "mcp_server"  # MCP Server暴露

    # 观测功能
    PROMETHEUS = "prometheus"  # Prometheus指标
    STRUCTLOG = "structlog"  # 结构化日志
    TRACE = "trace"  # 全链路追踪

    # 扩展功能
    EXTERNAL_AGENT = "external_agent"  # 外部Agent接入
    WEBHOOK = "webhook"  # Webhook推送
    RATE_LIMIT = "rate_limit"  # 速率限制


# 每种模式的功能开关默认配置
MODE_FEATURE_MAP: dict[DeployMode, set[FeatureFlag]] = {
    DeployMode.SOLO: {
        FeatureFlag.EVENT_STREAM,  # 核心功能必开
        FeatureFlag.GROUP_CHAT,  # 群聊基础功能
        FeatureFlag.PROMETHEUS,  # 观测功能
        FeatureFlag.STRUCTLOG,
    },
    DeployMode.TEAM: {
        FeatureFlag.EVENT_STREAM,
        FeatureFlag.GROUP_CHAT,
        FeatureFlag.KANBAN,
        FeatureFlag.WORKTREE,
        FeatureFlag.RATCHET_LOOP,
        FeatureFlag.HANDOFF,
        FeatureFlag.ICP_V2,
        FeatureFlag.PROMETHEUS,
        FeatureFlag.STRUCTLOG,
        FeatureFlag.TRACE,
    },
    DeployMode.ECOSYSTEM: {
        # Team全部功能
        FeatureFlag.EVENT_STREAM,
        FeatureFlag.GROUP_CHAT,
        FeatureFlag.KANBAN,
        FeatureFlag.WORKTREE,
        FeatureFlag.RATCHET_LOOP,
        FeatureFlag.HANDOFF,
        FeatureFlag.ICP_V2,
        FeatureFlag.A2A_INTEGRATION,
        FeatureFlag.MCP_SERVER,
        FeatureFlag.PROMETHEUS,
        FeatureFlag.STRUCTLOG,
        FeatureFlag.TRACE,
        # Ecosystem扩展功能
        FeatureFlag.EXTERNAL_AGENT,
        FeatureFlag.WEBHOOK,
        FeatureFlag.RATE_LIMIT,
    },
}

# 每种模式的默认端口
MODE_DEFAULT_PORT: dict[DeployMode, int] = {
    DeployMode.SOLO: 3458,
    DeployMode.TEAM: 3458,
    DeployMode.ECOSYSTEM: 8080,  # Ecosystem用标准端口
}

# 每种模式需要的Agent Token数量
MODE_MIN_AGENTS: dict[DeployMode, int] = {
    DeployMode.SOLO: 1,  # 至少1个（澜舟）
    DeployMode.TEAM: 3,  # 至少3个（九重+澜舟+澜澜）
    DeployMode.ECOSYSTEM: 5,  # 全部5个Agent
}


class ModeConfig(BaseModel):
    """模式配置（Pydantic模型，类型安全）"""

    mode: DeployMode = DeployMode.TEAM  # 默认Team模式
    enabled_features: set[FeatureFlag] = Field(default_factory=set)
    custom_features: set[FeatureFlag] = Field(default_factory=set)  # 用户手动启用
    disabled_features: set[FeatureFlag] = Field(default_factory=set)  # 用户手动禁用

    model_config = ConfigDict(use_enum_values=True)

    def get_enabled_features(self) -> set[FeatureFlag]:
        """计算当前启用的功能（模式默认 + 自定义启用 - 自定义禁用）"""
        # 从MODE_FEATURE_MAP获取模式默认功能
        from modes import MODE_FEATURE_MAP, DeployMode  # 延迟导入避免循环

        # 将mode转换为DeployMode（pydantic的use_enum_values可能转为字符串）
        mode = self.mode
        if isinstance(mode, str):
            try:
                mode = DeployMode(mode)
            except ValueError:
                return set()
        default = MODE_FEATURE_MAP.get(mode, set())
        # 合并：默认 + 自定义启用 - 自定义禁用
        result = (default | self.custom_features) - self.disabled_features
        return result

    def is_enabled(self, feature: FeatureFlag) -> bool:
        """检查功能是否启用"""
        return feature in self.get_enabled_features()

    def enable_feature(self, feature: FeatureFlag):
        """手动启用功能（覆盖模式默认）"""
        self.custom_features.add(feature)

    def disable_feature(self, feature: FeatureFlag):
        """手动禁用功能（覆盖模式默认）"""
        self.disabled_features.add(feature)  # 实际上需要记录禁用列表
        # 简化实现：直接操作enabled_features
        if feature in self.enabled_features:
            self.enabled_features.remove(feature)


class ModeValidator:
    """模式配置验证器"""

    def __init__(self, config: ModeConfig):
        self.config = config

    def validate(self) -> dict[str, Any]:
        """验证当前配置是否符合所选模式

        Returns:
            {"valid": bool, "errors": [str], "warnings": [str]}
        """
        errors = []
        warnings = []

        # 确保mode是DeployMode类型（pydantic的use_enum_values可能转为字符串）
        if isinstance(self.config.mode, str):
            try:
                self.config.mode = DeployMode(self.config.mode)
            except ValueError:
                errors.append(f"无效的模式: {self.config.mode}")
                return {"valid": False, "errors": errors, "warnings": warnings}

        # 1. 检查Agent Token数量
        agent_count = self._count_agents()
        min_agents = MODE_MIN_AGENTS[self.config.mode]
        if agent_count < min_agents:
            errors.append(
                f"模式 {self.config.mode.value if isinstance(self.config.mode, DeployMode) else self.config.mode} 需要至少 {min_agents} 个Agent Token，"
                f"当前只配置了 {agent_count} 个"
            )

        # 2. 检查必需功能是否启用
        required = MODE_FEATURE_MAP[
            DeployMode(self.config.mode) if isinstance(self.config.mode, str) else self.config.mode
        ]
        for feature in required:
            # 确保feature是FeatureFlag类型
            if isinstance(feature, str):
                try:
                    feature = FeatureFlag(feature)
                except ValueError:
                    continue  # 跳过无效功能名
            if not self.config.is_enabled(feature):
                warnings.append(
                    f"模式 {self.config.mode.value if isinstance(self.config.mode, DeployMode) else self.config.mode} 推荐启用 {feature.value}，"
                    f"当前未启用"
                )

        # 3. 检查端口冲突
        if str(self.config.mode) == "ecosystem" and settings.port == 3458:
            warnings.append(f"Ecosystem模式推荐使用端口 8080，当前为 {settings.port}")

        # 4. 检查外部服务配置（Team/Ecosystem模式）
        if self.config.mode in (DeployMode.TEAM, DeployMode.ECOSYSTEM):
            if not settings.llama_api_base:
                warnings.append("Team/Ecosystem模式推荐配置 LLAMA_API_BASE（L1本地模型）")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    def _count_agents(self) -> int:
        """统计已配置的Agent Token数量"""
        count = 0
        for i in range(1, 6):
            if getattr(settings, f"agent_{i}_token", ""):
                count += 1
        return count


@lru_cache
def get_mode_config() -> ModeConfig:
    """获取当前模式配置（单例，从环境变量加载）"""
    mode_str = os.getenv("OPENBRIDGE_MODE", "team").lower()
    try:
        mode = DeployMode(mode_str)
    except ValueError:
        mode = DeployMode.TEAM  # 默认Team模式

    # 从环境变量加载启用的功能
    enabled = MODE_FEATURE_MAP[mode].copy()
    custom_env = os.getenv("OPENBRIDGE_FEATURES", "")
    if custom_env:
        for f in custom_env.split(","):
            f = f.strip()
            try:
                enabled.add(FeatureFlag(f))
            except ValueError:
                pass

    return ModeConfig(
        mode=mode,
        enabled_features=enabled,
    )


def switch_mode(new_mode: DeployMode, save_to_env: bool = True) -> dict[str, Any]:
    """切换部署模式

    Args:
        new_mode: 目标模式
        save_to_env: 是否保存到.env文件

    Returns:
        {"success": bool, "message": str, "restart_required": bool}
    """
    config = get_mode_config()
    config.mode = new_mode
    config.enabled_features = MODE_FEATURE_MAP[new_mode].copy()

    if save_to_env:
        # 更新.env文件
        _update_env_file({"OPENBRIDGE_MODE": new_mode.value})

    return {
        "success": True,
        "message": f"已切换到 {new_mode.value} 模式，需要重启服务生效",
        "restart_required": True,
    }


def _update_env_file(updates: dict[str, str]):
    """更新.env文件（保留现有配置）"""
    import os

    env_path = ".env"

    # 读取现有配置
    existing = {}
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    try:
                        key, value = line.split("=", 1)
                        existing[key.strip()] = value.strip()
                    except ValueError:
                        pass

    # 更新
    existing.update(updates)

    # 写回
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("# === OpenBridge 弹性模式配置 ===\n")
        f.write(f"OPENBRIDGE_MODE={existing.get('OPENBRIDGE_MODE', 'team')}\n")
        f.write("\n")

        # 其他配置保持不变
        for key, value in existing.items():
            if key != "OPENBRIDGE_MODE":
                f.write(f"{key}={value}\n")


def get_mode_info() -> dict[str, Any]:
    """获取当前模式详细信息"""
    config = get_mode_config()
    validator = ModeValidator(config)
    validation = validator.validate()

    return {
        "current_mode": config.mode.value,
        "enabled_features": [f.value for f in config.get_enabled_features()],
        "validation": validation,
        "mode_description": _get_mode_description(config.mode),
    }


def _get_mode_description(mode: DeployMode) -> str:
    """获取模式描述"""
    descriptions = {
        DeployMode.SOLO: (
            "单Agent轻量部署模式：适合个人使用或快速原型验证。"
            "零配置启动，仅需配置1个Agent Token。"
            "功能：EventStream + 基础群聊 + 观测。"
        ),
        DeployMode.TEAM: (
            "团队协作标准部署模式：适合小团队（3-5人）日常协作。"
            "需要配置3+个Agent Token。"
            "功能：全部核心功能 + Worktree + Ratchet Loop + ICP v2.0。"
        ),
        DeployMode.ECOSYSTEM: (
            "生态扩展完整部署模式：适合多团队或对外提供服务。"
            "需要配置全部5个Agent Token + 外部服务。"
            "功能：Team全部功能 + A2A/MCP集成 + 外部Agent接入 + Webhook。"
        ),
    }
    return descriptions.get(mode, "")


# ==================== CLI 支持 ====================


def print_mode_status():
    """打印当前模式状态（CLI使用）"""
    info = get_mode_info()
    print(f"\n{'=' * 60}")
    print(f"OpenBridge 部署模式: {info['current_mode'].upper()}")
    print(f"{'=' * 60}")
    print(f"\n📝 模式说明：\n{info['mode_description']}")
    print(f"\n✅ 已启用功能（{len(info['enabled_features'])}个）：")
    for f in info["enabled_features"]:
        print(f"  - {f}")

    print("\n🔍 配置验证：")
    if info["validation"]["valid"]:
        print("  ✅ 配置有效")
    else:
        print("  ❌ 配置有误：")
        for e in info["validation"]["errors"]:
            print(f"    - {e}")

    if info["validation"]["warnings"]:
        print("  ⚠️ 警告：")
        for w in info["validation"]["warnings"]:
            print(f"    - {w}")

    print(f"\n{'=' * 60}\n")


if __name__ == "__main__":
    # 自验证
    import argparse

    parser = argparse.ArgumentParser(description="OpenBridge 弹性模式管理")
    parser.add_argument("action", choices=["status", "switch"], help="操作")
    parser.add_argument("--mode", choices=["solo", "team", "ecosystem"], help="目标模式")
    args = parser.parse_args()

    if args.action == "status":
        print_mode_status()
    elif args.action == "switch":
        if not args.mode:
            print("错误：switch操作需要--mode参数")
            exit(1)
        result = switch_mode(DeployMode(args.mode))
        print(result["message"])
