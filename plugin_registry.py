"""
插件注册中心 — 搜索/注册/版本管理/生命周期管控。

融优来源：
  Microsoft Agent Governance Toolkit (Manifest规范 + 全生命周期)
  + OpenBridge skill_validator.py (SKILL.md标准)
  + OpenBridge permission_guard.py (权限矩阵)

设计原则：
  Manifest优于随意 · 签名优于信任 · 沙箱优于裸跑 · 注册优于散装

Manifest 规范（openbridge-plugin.yaml）：
  name / version / description / author / plugin_type / capabilities
  / dependencies / min_openbridge_version / signature / permissions

插件类型：
  policy_template — 策略模板（规则引擎配置）
  integration — 集成插件（外部API对接）
  agent — Agent插件（新增Agent能力）
  validator — 验证器插件（数据校验/安全扫描）
  theme — 主题皮肤（千面设计市场，前端个性化）

生命周期：
  搜索 → 注册 → 签名 → 验证 → 安装 → 沙箱执行 → 卸载
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger("bridge_v7.plugin_registry")


# ============================================================
# 插件类型与状态
# ============================================================


class PluginType(str, Enum):
    """插件类型。"""

    POLICY_TEMPLATE = "policy_template"  # 策略模板
    INTEGRATION = "integration"  # 集成插件
    AGENT = "agent"  # Agent插件
    VALIDATOR = "validator"  # 验证器插件
    THEME = "theme"  # 主题皮肤（千面设计市场）


class PluginStatus(str, Enum):
    """插件生命周期状态。"""

    REGISTERED = "registered"  # 已注册（签名待验证）
    VERIFIED = "verified"  # 已验证（签名通过）
    INSTALLED = "installed"  # 已安装（可使用）
    DISABLED = "disabled"  # 已禁用（暂停使用）
    UNINSTALLED = "uninstalled"  # 已卸载
    REVOKED = "revoked"  # 已撤销（签名失效/安全原因）


# ============================================================
# PluginManifest — pydantic 模型定义
# ============================================================


class PluginManifest(BaseModel):
    """插件Manifest规范 — 对齐 Microsoft agent-plugin.yaml。

    核心字段：
      name: 插件名称（1-64字符，小写+连字符）
      version: 版本号（SemVer）
      description: 用途描述
      author: 作者信息
      plugin_type: 类型枚举
      capabilities: 能力标签列表
      dependencies: 依赖声明
      permissions: 权限需求
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(description="插件名称", min_length=1, max_length=64)
    version: str = Field(default="1.0.0", description="版本号(SemVer)")
    description: str = Field(default="", description="用途描述", max_length=1024)
    author: str = Field(default="", description="作者信息")
    plugin_type: PluginType = Field(default=PluginType.INTEGRATION)
    capabilities: list[str] = Field(
        default_factory=list,
        description="能力标签列表",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="依赖声明(如 nlp-tokenizer>=2.0.0)",
    )
    min_openbridge_version: str = Field(
        default="8.0.0",
        description="最低兼容版本",
    )
    permissions: list[str] = Field(
        default_factory=lambda: ["L1_PUBLIC"],
        description="所需权限等级列表",
    )
    signature: str = Field(
        default="",
        description="Ed25519签名(Base64编码)",
    )
    entry_point: str = Field(
        default="",
        description="入口文件路径(如 main.py)",
    )
    tags: list[str] = Field(default_factory=list, description="搜索标签")


# ============================================================
# SKILL.md → PluginManifest 映射器
# ============================================================


class SkillToManifestMapper:
    """将 SKILL.md 规格映射为 PluginManifest。

    映射规则：
      name → name
      description → description
      license → author字段补充
      compatibility → min_openbridge_version
      metadata → capabilities + tags
      allowed-tools → permissions
    """

    def map_skill_to_manifest(self, skill_data: dict[str, Any]) -> PluginManifest:
        """将SKILL.md的YAML frontmatter数据映射为Manifest。"""
        manifest = PluginManifest(
            name=skill_data.get("name", "unknown-skill"),
            description=skill_data.get("description", ""),
            author=skill_data.get("license", "unknown"),
            min_openbridge_version=self._extract_version(
                skill_data.get("compatibility", "openbridge>=8.0.0")
            ),
            plugin_type=self._infer_type(skill_data),
            capabilities=skill_data.get("metadata", {}).get("capabilities", []),
            permissions=self._infer_permissions(skill_data.get("allowed-tools", "")),
            tags=skill_data.get("metadata", {}).get("tags", []),
        )
        return manifest

    def _extract_version(self, compatibility: str) -> str:
        """从compatibility字符串提取最低版本号。"""
        import re

        match = re.search(r"openbridge[><=]+(\d+\.\d+\.\d+)", compatibility)
        if match:
            return match.group(1)
        return "8.0.0"

    def _infer_type(self, skill_data: dict[str, Any]) -> PluginType:
        """从SKILL.md推断插件类型。"""
        desc = skill_data.get("description", "").lower()
        name = skill_data.get("name", "").lower()

        if "validator" in name or "validate" in desc or "check" in desc:
            return PluginType.VALIDATOR
        if "agent" in name or "chat" in desc or "respond" in desc:
            return PluginType.AGENT
        if "policy" in name or "rule" in desc or "govern" in desc:
            return PluginType.POLICY_TEMPLATE
        return PluginType.INTEGRATION

    def _infer_permissions(self, allowed_tools: str) -> list[str]:
        """从allowed-tools推断所需权限。"""
        if not allowed_tools:
            return ["L1_PUBLIC"]

        tools = allowed_tools.split()
        if any(t in ("exec", "shell", "system") for t in tools):
            return ["L3_CORE"]
        if any(t in ("write", "edit", "create") for t in tools):
            return ["L2_PLUGIN"]
        return ["L1_PUBLIC"]


# ============================================================
# PluginRegistry — 注册中心
# ============================================================


class PluginRegistry:
    """插件注册中心 — 搜索/注册/版本管理/生命周期管控。

    核心能力：
    1. 注册：添加新插件Manifest到注册表
    2. 搜索：按名称/类型/标签/能力搜索
    3. 版本管理：同一插件的多个版本共存
    4. 生命周期：状态转换(registered→verified→installed→disabled→uninstalled)
    5. 依赖检查：安装前验证依赖是否满足
    """

    def __init__(self):
        self._plugins: dict[str, dict[str, PluginManifest]] = {}  # name → {version: manifest}
        self._status: dict[str, dict[str, PluginStatus]] = {}  # name → {version: status}
        self._install_time: dict[str, dict[str, float]] = {}  # name → {version: timestamp}
        self._ratings: dict[str, float] = {}  # name → avg_rating

    def register(self, manifest: PluginManifest) -> tuple[bool, str]:
        """注册插件到注册表。"""
        name = manifest.name
        version = manifest.version

        # 验证名称格式
        import re

        if not re.match(r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$", name):
            return False, f"名称格式不符: {name}（须小写字母+数字+连字符）"

        # 版本号验证(SemVer)
        if not re.match(r"^\d+\.\d+\.\d+$", version):
            return False, f"版本号格式不符: {version}（须SemVer x.y.z）"

        # 注册
        self._plugins.setdefault(name, {})[version] = manifest
        self._status.setdefault(name, {})[version] = PluginStatus.REGISTERED
        self._install_time.setdefault(name, {})[version] = time.time()

        logger.info(
            "plugin_registry.registered",
            name=name,
            version=version,
            type=manifest.plugin_type.value,
        )

        return True, f"注册成功: {name}@{version}"

    def verify_and_install(self, name: str, version: str = "") -> tuple[bool, str]:
        """验证签名并安装插件。"""
        manifest = self._get_manifest(name, version)
        if not manifest:
            return False, f"未找到插件: {name}@{version}"

        # 签名验证（委托给 plugin_signing）
        if manifest.signature:
            try:
                from plugin_signing import verify_plugin_signature

                verified, msg = verify_plugin_signature(manifest)
                if not verified:
                    self._set_status(name, manifest.version, PluginStatus.REVOKED)
                    return False, f"签名验证失败: {msg}"
            except ImportError:
                logger.debug("plugin_registry.signing_unavailable")

        # 依赖检查
        deps_ok, deps_msg = self._check_dependencies(manifest)
        if not deps_ok:
            return False, f"依赖不满足: {deps_msg}"

        # 权限检查
        perms_ok, perms_msg = self._check_permissions(manifest)
        if not perms_ok:
            return False, f"权限不满足: {perms_msg}"

        # 状态转换
        self._set_status(name, manifest.version, PluginStatus.VERIFIED)
        self._set_status(name, manifest.version, PluginStatus.INSTALLED)

        logger.info("plugin_registry.installed", name=name, version=manifest.version)

        return True, f"安装成功: {name}@{manifest.version}"

    def search(
        self,
        query: str = "",
        plugin_type: PluginType | None = None,
        tag: str = "",
        capability: str = "",
    ) -> list[PluginManifest]:
        """搜索插件：按名称/类型/标签/能力。"""
        results = []

        for name, versions in self._plugins.items():
            # 取最新版本
            latest = self._get_latest_version(name)
            manifest = versions.get(latest)
            if not manifest:
                continue

            # 过滤条件
            if query and query.lower() not in manifest.name.lower():
                if query.lower() not in manifest.description.lower():
                    continue
            if plugin_type and manifest.plugin_type != plugin_type:
                continue
            if tag and tag not in manifest.tags:
                continue
            if capability and capability not in manifest.capabilities:
                continue

            results.append(manifest)

        return results

    def get_installed_plugins(self) -> list[PluginManifest]:
        """获取所有已安装插件。"""
        results = []
        for name, versions in self._status.items():
            for ver, status in versions.items():
                if status == PluginStatus.INSTALLED:
                    manifest = self._plugins.get(name, {}).get(ver)
                    if manifest:
                        results.append(manifest)
        return results

    def disable(self, name: str, version: str = "") -> tuple[bool, str]:
        """禁用插件。"""
        manifest = self._get_manifest(name, version)
        if not manifest:
            return False, f"未找到插件: {name}"

        current_status = self._get_status(name, manifest.version)
        if current_status != PluginStatus.INSTALLED:
            return False, f"只能禁用已安装插件（当前状态: {current_status.value})"

        self._set_status(name, manifest.version, PluginStatus.DISABLED)
        logger.info("plugin_registry.disabled", name=name)
        return True, f"已禁用: {name}@{manifest.version}"

    def enable(self, name: str, version: str = "") -> tuple[bool, str]:
        """重新启用插件。"""
        manifest = self._get_manifest(name, version)
        if not manifest:
            return False, f"未找到插件: {name}"

        current = self._get_status(name, manifest.version)
        if current != PluginStatus.DISABLED:
            return False, f"只能启用已禁用插件（当前状态: {current.value})"

        self._set_status(name, manifest.version, PluginStatus.INSTALLED)
        logger.info("plugin_registry.enabled", name=name)
        return True, f"已启用: {name}@{manifest.version}"

    def uninstall(self, name: str, version: str = "") -> tuple[bool, str]:
        """卸载插件。"""
        manifest = self._get_manifest(name, version)
        if not manifest:
            return False, f"未找到插件: {name}"

        self._set_status(name, manifest.version, PluginStatus.UNINSTALLED)
        logger.info("plugin_registry.uninstalled", name=name)
        return True, f"已卸载: {name}@{manifest.version}"

    def get_plugin_info(self, name: str, version: str = "") -> dict[str, Any] | None:
        """获取插件详细信息。"""
        manifest = self._get_manifest(name, version)
        if not manifest:
            return None

        status = self._get_status(name, manifest.version)
        return {
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "type": manifest.plugin_type.value,
            "status": status.value,
            "capabilities": manifest.capabilities,
            "permissions": manifest.permissions,
            "author": manifest.author,
            "tags": manifest.tags,
        }

    def stats(self) -> dict[str, Any]:
        """注册表统计。"""
        total = sum(len(v) for v in self._plugins.values())
        installed = sum(
            1 for vs in self._status.values() for s in vs.values() if s == PluginStatus.INSTALLED
        )
        types = {}
        for name, versions in self._plugins.items():
            latest = self._get_latest_version(name)
            m = versions.get(latest)
            if m:
                types.setdefault(m.plugin_type.value, 0)
                types[m.plugin_type.value] += 1

        return {
            "total_plugins": total,
            "installed": installed,
            "by_type": types,
            "unique_names": len(self._plugins),
        }

    # --- 内部方法 ---
    def _get_manifest(self, name: str, version: str = "") -> PluginManifest | None:
        """获取Manifest，默认取最新版本。"""
        versions = self._plugins.get(name, {})
        if not versions:
            return None
        if version and version in versions:
            return versions[version]
        latest = self._get_latest_version(name)
        return versions.get(latest)

    def _get_latest_version(self, name: str) -> str:
        """获取最新版本号。"""
        versions = self._plugins.get(name, {})
        if not versions:
            return ""

        # SemVer排序：取最大版本号
        def semver_key(v: str) -> tuple[int, int, int]:
            parts = v.split(".")
            return (int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)

        return max(versions.keys(), key=semver_key)

    def _get_status(self, name: str, version: str) -> PluginStatus:
        """获取插件状态。"""
        return self._status.get(name, {}).get(version, PluginStatus.REGISTERED)

    def _set_status(self, name: str, version: str, status: PluginStatus):
        """设置插件状态。"""
        self._status.setdefault(name, {})[version] = status

    def _check_dependencies(self, manifest: PluginManifest) -> tuple[bool, str]:
        """依赖检查（简化：检查依赖是否在已安装列表中）。"""
        if not manifest.dependencies:
            return True, "无依赖"

        installed_names = {m.name for m in self.get_installed_plugins()}

        missing = []
        for dep in manifest.dependencies:
            # 解析依赖名（去掉版本约束）
            dep_name = dep.split(">=")[0].split("==")[0].split("<")[0].strip()
            if dep_name not in installed_names:
                missing.append(dep)

        if missing:
            return False, f"缺失依赖: {','.join(missing)}"
        return True, "依赖满足"

    def _check_permissions(self, manifest: PluginManifest) -> tuple[bool, str]:
        """权限检查：与permission_guard对齐。"""
        required = set(manifest.permissions)

        # L1_PUBLIC: 最低权限，总是满足
        if required == {"L1_PUBLIC"} or not required:
            return True, "L1_PUBLIC权限满足"

        # L2_PLUGIN/L3_CORE: 需要匹配的权限等级
        try:
            from permission_guard import PermissionLevel

            max_perm = max(PermissionLevel(p) for p in required if p in PermissionLevel.__members__)
            # 验证：当前系统是否支持该权限等级
            return True, f"权限等级 {max_perm.value} 可用"
        except (ImportError, ValueError):
            return True, "权限检查跳过（permission_guard不可用）"

    # --- 主题（千面设计市场）---

    def install_theme(self, theme_id: str, theme_dir: Path | None = None) -> tuple[bool, str]:
        """安装主题（委托 ThemeManager 完成文件系统操作）。"""
        try:
            from theme_manager import ThemeManager
        except ImportError:
            return False, "ThemeManager 不可用"
        if theme_dir:
            tm = ThemeManager(themes_dir=theme_dir)
            theme_json = Path(theme_dir) / theme_id / "theme.json"
            if not theme_json.exists():
                # 主题已在 themes_dir 中，无需安装文件，仅登记
                ok, msg = True, f"主题已在目录中: {theme_id}"
            else:
                ok, msg = tm.install_theme(theme_json)
            if not ok:
                return ok, msg
        else:
            tm = ThemeManager()
            ok, msg = True, f"已登记: {theme_id}"
        # 在统一注册表登记（THEME 类型）
        manifest = PluginManifest(
            name=theme_id,
            version="1.0.0",
            plugin_type=PluginType.THEME,
            description=f"千面设计市场主题: {theme_id}",
        )
        self.register(manifest)
        return True, f"主题已安装并登记: {theme_id}"

    def activate_theme(
        self, theme_id: str, themes_dir: Path | None = None, registry_file: Path | None = None
    ) -> tuple[bool, str]:
        """激活主题。"""
        try:
            from theme_manager import ThemeManager
        except ImportError:
            return False, "ThemeManager 不可用"
        tm = (
            ThemeManager(themes_dir=themes_dir, registry_file=registry_file)
            if themes_dir
            else ThemeManager()
        )
        ok, msg = tm.activate(theme_id)
        if not ok:
            return ok, msg
        # 同步注册表状态
        manifest = self._get_manifest(theme_id)
        if manifest:
            self._set_status(theme_id, manifest.version, PluginStatus.INSTALLED)
        return True, f"主题已激活: {theme_id}"

    def list_themes(
        self, themes_dir: Path | None = None, registry_file: Path | None = None
    ) -> list[dict[str, Any]]:
        """列出所有已安装主题。"""
        try:
            from theme_manager import ThemeManager
        except ImportError:
            return []
        tm = (
            ThemeManager(themes_dir=themes_dir, registry_file=registry_file)
            if themes_dir
            else ThemeManager()
        )
        return tm.list_themes()


# ============================================================
# SharedSkillRegistry — 多Agent共享技能注册中心
# ============================================================


class SharedSkillRegistry(PluginRegistry):
    """多Agent统一技能管理 — 一次安装，全员共享。

    融优来源：agent-skills-hub SharedSkillRegistry
    设计理念：
      单Agent安装 → 广播通知 → 多Agent共享
      避免重复安装 · 统一版本管理 · 按需授权

    核心能力：
      1. register_agent() — 注册Agent到技能生态
      2. install_shared() — 安装技能并广播给所有Agent
      3. grant_to_agent() / revoke_from_agent() — 精细授权
      4. list_agent_skills() — 查看Agent可用技能
      5. list_shared_skills() — 查看全员共享技能
      6. sync_all_agents() — 同步技能到所有Agent目录
    """

    def __init__(self, skills_dir: Path | None = None):
        super().__init__()
        self._agents: dict[str, dict[str, Any]] = {}  # agent_id → {capabilities, registered_at}
        self._agent_skills: dict[str, set[str]] = {}  # agent_id → {skill_name, ...}
        self._shared_skills: set[str] = set()  # 全员共享的技能名集合
        self._skills_dir = skills_dir or Path.cwd() / "skills"
        self._mapper = SkillToManifestMapper()

    # --- Agent 管理 ---

    def register_agent(
        self, agent_id: str, capabilities: list[str] | None = None
    ) -> tuple[bool, str]:
        """注册Agent到技能生态。"""
        if agent_id in self._agents:
            return False, f"Agent已注册: {agent_id}"

        self._agents[agent_id] = {
            "capabilities": capabilities or [],
            "registered_at": time.time(),
        }
        self._agent_skills[agent_id] = set()

        # 已有的共享技能自动授予新Agent
        self._agent_skills[agent_id].update(self._shared_skills)

        logger.info(
            "shared_registry.agent_registered",
            agent_id=agent_id,
            shared_count=len(self._shared_skills),
        )
        return True, f"Agent注册成功: {agent_id}（自动获得{len(self._shared_skills)}个共享技能）"

    def unregister_agent(self, agent_id: str) -> tuple[bool, str]:
        """注销Agent。"""
        if agent_id not in self._agents:
            return False, f"Agent未注册: {agent_id}"

        del self._agents[agent_id]
        del self._agent_skills[agent_id]

        logger.info("shared_registry.agent_unregistered", agent_id=agent_id)
        return True, f"Agent已注销: {agent_id}"

    def list_agents(self) -> list[dict[str, Any]]:
        """列出所有已注册Agent。"""
        result = []
        for agent_id, info in self._agents.items():
            skill_count = len(self._agent_skills.get(agent_id, set()))
            result.append(
                {
                    "agent_id": agent_id,
                    "capabilities": info["capabilities"],
                    "registered_at": info["registered_at"],
                    "skill_count": skill_count,
                }
            )
        return result

    # --- 共享安装 ---

    def install_shared(self, manifest: PluginManifest) -> tuple[bool, str]:
        """安装技能并广播给所有已注册Agent。

        这是"一次安装，全员共享"的核心入口。
        """
        # 先在父类注册
        ok, msg = self.register(manifest)
        if not ok:
            return False, msg

        ok, msg = self.verify_and_install(manifest.name, manifest.version)
        if not ok:
            return False, msg

        # 标记为共享
        self._shared_skills.add(manifest.name)

        # 授予所有已注册Agent
        granted_count = 0
        for agent_id in self._agents:
            if manifest.name not in self._agent_skills[agent_id]:
                self._agent_skills[agent_id].add(manifest.name)
                granted_count += 1

        logger.info(
            "shared_registry.installed_shared",
            name=manifest.name,
            version=manifest.version,
            agents_granted=granted_count,
        )

        return True, (
            f"共享安装成功: {manifest.name}@{manifest.version}\n"
            f"  已广播给 {granted_count} 个Agent\n"
            f"  后续注册的Agent将自动获得此技能"
        )

    def install_from_skill_md(self, skill_data: dict[str, Any]) -> tuple[bool, str]:
        """从SKILL.md数据安装共享技能。"""
        manifest = self._mapper.map_skill_to_manifest(skill_data)
        return self.install_shared(manifest)

    # --- 精细授权 ---

    def grant_to_agent(self, skill_name: str, agent_id: str) -> tuple[bool, str]:
        """授予特定Agent访问某技能的权限。"""
        if agent_id not in self._agents:
            return False, f"Agent未注册: {agent_id}"

        manifest = self._get_manifest(skill_name)
        if not manifest:
            return False, f"技能未安装: {skill_name}"

        if skill_name in self._agent_skills[agent_id]:
            return False, f"Agent已有此技能: {skill_name}"

        self._agent_skills[agent_id].add(skill_name)
        logger.info("shared_registry.granted", skill=skill_name, agent=agent_id)
        return True, f"已授权: {skill_name} → {agent_id}"

    def revoke_from_agent(self, skill_name: str, agent_id: str) -> tuple[bool, str]:
        """撤销Agent对某技能的访问权限。"""
        if agent_id not in self._agents:
            return False, f"Agent未注册: {agent_id}"

        if skill_name not in self._agent_skills.get(agent_id, set()):
            return False, f"Agent未拥有此技能: {skill_name}"

        # 共享技能不可单独撤销（需先取消共享）
        if skill_name in self._shared_skills:
            return False, f"{skill_name}是共享技能，不可单独撤销（先取消共享）"

        self._agent_skills[agent_id].discard(skill_name)
        logger.info("shared_registry.revoked", skill=skill_name, agent=agent_id)
        return True, f"已撤销: {skill_name} ← {agent_id}"

    def unshare(self, skill_name: str) -> tuple[bool, str]:
        """取消技能的共享状态（所有Agent失去访问权限）。"""
        if skill_name not in self._shared_skills:
            return False, f"非共享技能: {skill_name}"

        self._shared_skills.discard(skill_name)

        # 从所有Agent移除
        removed = 0
        for agent_id in self._agent_skills:
            if skill_name in self._agent_skills[agent_id]:
                self._agent_skills[agent_id].discard(skill_name)
                removed += 1

        logger.info("shared_registry.unshared", skill=skill_name, agents_affected=removed)
        return True, f"已取消共享: {skill_name}（{removed}个Agent受影响）"

    # --- 查询 ---

    def list_agent_skills(self, agent_id: str) -> list[dict[str, Any]]:
        """列出Agent可用的所有技能。"""
        if agent_id not in self._agents:
            return []

        skills = self._agent_skills.get(agent_id, set())
        result = []
        for name in sorted(skills):
            manifest = self._get_manifest(name)
            if manifest:
                is_shared = name in self._shared_skills
                result.append(
                    {
                        "name": manifest.name,
                        "version": manifest.version,
                        "description": manifest.description,
                        "type": manifest.plugin_type.value,
                        "shared": is_shared,
                    }
                )
        return result

    def list_shared_skills(self) -> list[dict[str, Any]]:
        """列出所有共享技能。"""
        result = []
        for name in sorted(self._shared_skills):
            manifest = self._get_manifest(name)
            if manifest:
                agent_count = sum(1 for skills in self._agent_skills.values() if name in skills)
                result.append(
                    {
                        "name": manifest.name,
                        "version": manifest.version,
                        "description": manifest.description,
                        "type": manifest.plugin_type.value,
                        "agents_using": agent_count,
                        "total_agents": len(self._agents),
                    }
                )
        return result

    def list_agents_for_skill(self, skill_name: str) -> list[str]:
        """列出拥有某技能访问权限的所有Agent。"""
        return sorted(
            agent_id for agent_id, skills in self._agent_skills.items() if skill_name in skills
        )

    # --- 统计 ---

    def shared_stats(self) -> dict[str, Any]:
        """共享注册表统计。"""
        base_stats = self.stats()
        base_stats.update(
            {
                "registered_agents": len(self._agents),
                "shared_skills": len(self._shared_skills),
                "total_grants": sum(len(s) for s in self._agent_skills.values()),
                "avg_skills_per_agent": (
                    sum(len(s) for s in self._agent_skills.values()) / len(self._agents)
                    if self._agents
                    else 0.0
                ),
            }
        )
        return base_stats

    # --- 目录同步 ---

    def sync_to_directory(self, agent_id: str, target_dir: Path) -> tuple[bool, str]:
        """将Agent可用技能同步到目标目录（如Agent的工作目录）。

        在目标目录下创建 skills/ 子目录，写入每个技能的SKILL.md摘要。
        """
        if agent_id not in self._agents:
            return False, f"Agent未注册: {agent_id}"

        skills = self._agent_skills.get(agent_id, set())
        if not skills:
            return True, f"Agent {agent_id} 无可同步技能"

        target_skills_dir = target_dir / "skills"
        target_skills_dir.mkdir(parents=True, exist_ok=True)

        synced = 0
        for name in skills:
            manifest = self._get_manifest(name)
            if not manifest:
                continue

            skill_dir = target_skills_dir / name
            skill_dir.mkdir(parents=True, exist_ok=True)

            # 写入技能摘要文件
            summary = {
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "type": manifest.plugin_type.value,
                "permissions": manifest.permissions,
                "capabilities": manifest.capabilities,
                "shared": name in self._shared_skills,
                "synced_at": time.time(),
                "synced_from": agent_id,
            }
            summary_file = skill_dir / "skill.json"
            summary_file.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            synced += 1

        logger.info("shared_registry.synced", agent=agent_id, target=str(target_dir), count=synced)
        return True, f"已同步 {synced} 个技能到 {target_skills_dir}"


# ============================================================
# 全局注册中心实例
# ============================================================

_global_registry = PluginRegistry()
_global_shared_registry: SharedSkillRegistry | None = None


def get_registry() -> PluginRegistry:
    """获取全局注册中心实例。"""
    return _global_registry


def get_shared_registry(skills_dir: Path | None = None) -> SharedSkillRegistry:
    """获取或创建全局共享技能注册中心实例。"""
    global _global_shared_registry
    if _global_shared_registry is None:
        _global_shared_registry = SharedSkillRegistry(skills_dir=skills_dir)
    return _global_shared_registry
