"""
四层记忆系统 — SOUL/USER/TOOLS/Session 分层加载 + 人格模板 + Compaction 压缩。

融优来源：
  OpenClaw 四层记忆引擎(SOUL.md/USER.md/AGENTS.md/MEMORY.md)
  + SimpleMem 三阶段压缩(43.24% F1最高) + Memobase 批处理优化
  + OpenBridge已有 memory_evolution.py(遗忘/合并/重构) + MEMORY.md(长期记忆)

设计原则：
  四层优于扁平 · 渐进优于一次性 · 压缩优于截断 · 融合优于替代

四层加载链：
  Layer 1 SOUL  — Agent人格·角色定位·交流风格·行为原则（启动首先加载）
  Layer 2 USER  — 用户画像·身份场景·偏好禁忌·互动历史（启动第二加载）
  Layer 3 TOOLS — 工具技能·工作流程·权限边界·Agent协作网络（启动第三加载）
  Layer 4 Session — 近期对话上下文·任务进展·临时决策（每次对话注入）

Compaction 策略：
  对话过长 → 静默轮次保存关键上下文到 MEMORY.md
  每日结果 → memory/YYYY-MM-DD.md（已有机制）
  每周清理 → knowledge_pruner.py 精简建议
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger("bridge_v7.soul_config")


# ============================================================
# 四层记忆层级定义
# ============================================================


class MemoryLayer(str, Enum):
    """四层记忆层级"""

    SOUL = "soul"  # 第一层：人格·角色·风格·原则
    USER = "user"  # 第二层：用户画像·偏好·禁忌
    TOOLS = "tools"  # 第三层：工具·技能·权限·协作
    SESSION = "session"  # 第四层：对话上下文·临时决策


# ============================================================
# 人格模板系统（pydantic 模型）
# ============================================================


class PersonalityTemplate(BaseModel):
    """Agent 人格模板 — SOUL.md 的结构化表示。

    对应 OpenClaw SOUL.md 的三个核心段落：
    - 角色定位（role + core_traits）
    - 交流风格（communication_style）
    - 行为原则（behavior_rules + boundaries）
    """

    model_config = ConfigDict(extra="allow")  # 允许扩展字段

    # === 角色定位 ===
    role: str = Field(default="assistant", description="Agent角色定位")
    core_traits: list[str] = Field(
        default_factory=lambda: ["专业", "严谨", "高效"],
        description="核心性格特质列表",
    )

    # === 交流风格 ===
    communication_style: str = Field(
        default="精简·直击重点·不客套不啰嗦",
        description="交流风格描述",
    )
    language: str = Field(default="zh-CN", description="默认语言")
    tone: str = Field(default="professional", description="语气基调")

    # === 行为原则 ===
    behavior_rules: list[str] = Field(
        default_factory=lambda: [
            "本地文件操作可直接执行",
            "对外发送必须先确认",
            "不确定时主动追问",
        ],
        description="行为原则列表",
    )
    boundaries: list[str] = Field(
        default_factory=lambda: [
            "不执行危险操作(rm -rf等)",
            "不泄露系统配置和密钥",
            "不处理政治敏感内容",
        ],
        description="行为边界/禁忌列表",
    )

    # === 元数据 ===
    version: str = Field(default="1.0.0", description="模板版本")
    author: str = Field(default="system", description="模板作者")
    tags: list[str] = Field(default_factory=list, description="标签")


class UserProfile(BaseModel):
    """用户画像 — USER.md 的结构化表示。

    对应 OpenClaw USER.md 的核心信息：
    - 身份场景（identity + scenario）
    - 偏好禁忌（preferences + taboos）
    - 互动历史摘要（interaction_summary）
    """

    model_config = ConfigDict(extra="allow")

    # === 身份场景 ===
    identity: str = Field(default="用户", description="用户身份描述")
    scenario: str = Field(default="通用", description="使用场景")

    # === 偏好 ===
    preferences: dict[str, str] = Field(
        default_factory=dict,
        description="偏好映射（如 language: zh-CN, style: 表格化）",
    )
    taboos: list[str] = Field(
        default_factory=list,
        description="禁忌/雷区列表",
    )

    # === 互动历史 ===
    interaction_summary: str = Field(
        default="",
        description="与Agent的互动历史摘要",
    )
    frequent_topics: list[str] = Field(
        default_factory=list,
        description="高频话题列表",
    )

    # === 元数据 ===
    updated_at: float = Field(default_factory=time.time, description="最后更新时间")


class ToolsProfile(BaseModel):
    """工具技能画像 — TOOLS/AGENTS.md 的结构化表示。

    对应 OpenClaw AGENTS.md：
    - 工作流程和启动顺序
    - 可用技能和权限边界
    - Agent协作网络
    """

    model_config = ConfigDict(extra="allow")

    # === 技能清单 ===
    available_skills: list[str] = Field(
        default_factory=list,
        description="可用技能列表",
    )
    skill_priorities: dict[str, int] = Field(
        default_factory=dict,
        description="技能优先级映射",
    )

    # === 权限边界 ===
    permission_level: str = Field(
        default="L1_PUBLIC",
        description="权限等级",
    )
    allowed_actions: list[str] = Field(
        default_factory=lambda: ["READ"],
        description="允许的操作范围",
    )

    # === 协作网络 ===
    collaborators: dict[str, str] = Field(
        default_factory=dict,
        description="协作Agent映射（agent_id → role描述）",
    )
    workflow_sequence: list[str] = Field(
        default_factory=list,
        description="工作流程启动顺序",
    )


# ============================================================
# 预置人格模板库
# ============================================================

# 通用角色模板（用户可自定义扩展）
BUILTIN_TEMPLATES: dict[str, PersonalityTemplate] = {
    "leader": PersonalityTemplate(
        role="Team leader · strategy",
        core_traits=["warm", "structured", "outcome-driven"],
        communication_style="structured · direct · encourages innovation",
        language="zh-CN",
        tone="warm_professional",
        behavior_rules=[
            "research before execute",
            "solid fundamentals for technical decisions",
            "outcome-oriented delivery",
        ],
        boundaries=[
            "no technical decisions without research",
            "no politically sensitive content",
        ],
        tags=["leader", "strategy"],
    ),
    "developer": PersonalityTemplate(
        role="Developer · system architect",
        core_traits=["pragmatic", "rigorous", "incremental"],
        communication_style="code speaks · document pitfalls · small steps",
        language="zh-CN",
        tone="technical",
        behavior_rules=[
            "incremental slices · small deliveries",
            "record pitfalls in memory",
            "reuse existing modules",
        ],
        boundaries=[
            "never skip tests",
            "never hardcode secrets",
        ],
        tags=["developer", "architect", "python"],
    ),
    "coordinator": PersonalityTemplate(
        role="Coordinator · administration",
        core_traits=["warm", "coordinated", "organized"],
        communication_style="gentle reminders · task tracking · status reports",
        language="zh-CN",
        tone="warm_service",
        behavior_rules=[
            "coordinate task assignment and tracking",
            "status reports without omission",
            "record daily achievements and next-day plan",
        ],
        boundaries=["no overstepping technical decisions", "no leaking token config"],
        tags=["coordinator", "admin"],
    ),
    "researcher": PersonalityTemplate(
        role="Research · strategic analyst",
        core_traits=["professional", "insightful", "cross-validated"],
        communication_style="research reports · data-driven · multi-source",
        language="zh-CN",
        tone="analytical",
        behavior_rules=[
            "cross-validate from multiple sources",
            "always label information timeliness",
            "attach feasibility assessment to strategic suggestions",
        ],
        boundaries=["no spreading unverified info", "no politically sensitive analysis"],
        tags=["researcher", "analyst"],
    ),
    "archivist": PersonalityTemplate(
        role="Knowledge archivist",
        core_traits=["meticulous", "organized", "gatekeeping"],
        communication_style="archival records · knowledge organizing · standardized docs",
        language="zh-CN",
        tone="archival",
        behavior_rules=[
            "archive all achievements",
            "standardize document formats",
            "classify knowledge systematically",
        ],
        boundaries=["never delete · only mark as archived", "no leaking isolated data"],
        tags=["archivist", "knowledge"],
    ),
}

# 通用模板（开源版默认）
DEFAULT_TEMPLATE = PersonalityTemplate(
    role="OpenBridge 通用助手",
    core_traits=["专业", "务实", "有温度"],
    communication_style="清晰·精简·成果导向",
    language="zh-CN",
    tone="professional",
    behavior_rules=[
        "先理解需求再行动",
        "不确定时主动追问",
        "本地操作可直接执行·对外发送需确认",
    ],
    boundaries=[
        "不执行危险系统操作",
        "不泄露配置和密钥",
        "不处理政治敏感内容",
    ],
    tags=["general", "openbridge"],
)


# ============================================================
# 四层加载链
# ============================================================


@dataclass
class LayeredContext:
    """四层上下文加载结果。

    按加载顺序组装，用于注入到LLM prompt或Agent决策上下文。
    """

    soul: PersonalityTemplate = field(default_factory=lambda: DEFAULT_TEMPLATE)
    user: UserProfile = field(default_factory=UserProfile)
    tools: ToolsProfile = field(default_factory=ToolsProfile)
    session: str = ""  # 近期对话摘要

    def to_prompt_sections(self) -> dict[str, str]:
        """将四层上下文转为可注入prompt的段落字典。"""
        sections = {}

        # Layer 1: SOUL
        soul = self.soul
        sections["soul"] = (
            f"## 角色定位\n{soul.role}\n"
            f"## 核心特质\n{'·'.join(soul.core_traits)}\n"
            f"## 交流风格\n{soul.communication_style}\n"
            f"## 行为原则\n"
            + "\n".join(f"- {r}" for r in soul.behavior_rules)
            + "\n## 行为边界\n"
            + "\n".join(f"- {b}" for b in soul.boundaries)
        )

        # Layer 2: USER
        user = self.user
        prefs = "\n".join(f"- {k}: {v}" for k, v in user.preferences.items())
        sections["user"] = (
            f"## 用户身份\n{user.identity}·场景:{user.scenario}\n"
            f"## 偏好\n{prefs}\n"
            f"## 禁忌\n" + "\n".join(f"- {t}" for t in user.taboos)
            if user.taboos
            else ""
        )

        # Layer 3: TOOLS
        tools = self.tools
        sections["tools"] = (
            f"## 可用技能\n{'·'.join(tools.available_skills)}\n"
            f"## 权限等级\n{tools.permission_level}\n"
            f"## 协作网络\n" + "\n".join(f"- {k}: {v}" for k, v in tools.collaborators.items())
        )

        # Layer 4: SESSION
        if self.session:
            sections["session"] = f"## 近期对话摘要\n{self.session}"

        return sections

    def assemble_system_prompt(self, max_chars: int = 2000) -> str:
        """组装完整的系统提示词，控制在 max_chars 以内。

        四层按优先级裁剪：SOUL必留 → USER优先 → TOOLS压缩 → SESSION截断
        """
        sections = self.to_prompt_sections()
        parts = []

        # SOUL 必留（最高优先级）
        soul_text = sections.get("soul", "")
        parts.append(soul_text)

        remaining = max_chars - len(soul_text)

        # USER 优先
        user_text = sections.get("user", "")
        if remaining > 0 and user_text:
            if len(user_text) <= remaining:
                parts.append(user_text)
                remaining -= len(user_text)
            else:
                parts.append(user_text[:remaining])
                remaining = 0

        # TOOLS 压缩
        tools_text = sections.get("tools", "")
        if remaining > 0 and tools_text:
            if len(tools_text) <= remaining:
                parts.append(tools_text)
                remaining -= len(tools_text)
            else:
                # 压缩：只保留技能清单和权限
                compressed = f"技能:{','.join(self.tools.available_skills[:10])} 权限:{self.tools.permission_level}"
                parts.append(compressed)

        # SESSION 截断
        session_text = sections.get("session", "")
        if remaining > 0 and session_text:
            if len(session_text) <= remaining:
                parts.append(session_text)
            else:
                parts.append(session_text[:remaining])

        return "\n\n".join(parts)


# ============================================================
# 四层加载器
# ============================================================


class SoulConfigLoader:
    """四层记忆加载器 — 从文件/数据库/内存加载四层上下文。

    加载策略：
    1. SOUL: 从预置模板库选择 + SOUL.md文件覆盖
    2. USER: 从 USER.md文件 + .workbuddy/memory/MEMORY.md提取
    3. TOOLS: 从 permission_guard.REGISTERED_AGENTS + skill_router_v2
    4. Session: 从 memory_evolution 近期记忆
    """

    def __init__(self, workspace_root: str = "", db_path: str = ":memory:"):
        self.workspace_root = workspace_root
        self.db_path = db_path
        self._context_cache: LayeredContext | None = None

    def load_all(self, agent_id: str = "", session_summary: str = "") -> LayeredContext:
        """按四层顺序加载完整上下文。"""
        soul = self._load_soul(agent_id)
        user = self._load_user()
        tools = self._load_tools(agent_id)
        session = session_summary or self._load_session()

        context = LayeredContext(
            soul=soul,
            user=user,
            tools=tools,
            session=session,
        )
        self._context_cache = context

        logger.info(
            "soul_config.loaded",
            agent_id=agent_id,
            layers=4,
            soul_role=soul.role,
            user_identity=user.identity,
            tools_count=len(tools.available_skills),
            session_len=len(session),
        )

        return context

    # --- Layer 1: SOUL ---
    def _load_soul(self, agent_id: str) -> PersonalityTemplate:
        """加载人格模板：预置库 → SOUL.md覆盖。"""
        # 先从预置库取
        template = BUILTIN_TEMPLATES.get(agent_id, DEFAULT_TEMPLATE)

        # SOUL.md文件覆盖（如果存在）
        soul_path = self._find_layer_file("SOUL.md")
        if soul_path:
            overrides = self._parse_markdown_layers(soul_path)
            if overrides:
                template = self._apply_overrides(template, overrides)

        return template

    # --- Layer 2: USER ---
    def _load_user(self) -> UserProfile:
        """加载用户画像：USER.md + MEMORY.md提取。"""
        user = UserProfile()

        # USER.md
        user_path = self._find_layer_file("USER.md")
        if user_path:
            user_data = self._parse_markdown_layers(user_path)
            if user_data:
                user = self._apply_user_overrides(user, user_data)

        # MEMORY.md 提取偏好（补充信息）
        memory_path = self._find_layer_file("MEMORY.md")
        if memory_path:
            memory_data = self._parse_markdown_layers(memory_path)
            if memory_data:
                # 从MEMORY.md提取偏好和禁忌
                for key, val in memory_data.items():
                    if key.startswith("偏好") or key.startswith("preference"):
                        user.preferences[key] = val
                    if key.startswith("禁忌") or key.startswith("taboo"):
                        user.taboos.append(val)

        return user

    # --- Layer 3: TOOLS ---
    def _load_tools(self, agent_id: str) -> ToolsProfile:
        """加载工具技能画像：权限守卫 + AgentCard。"""
        tools = ToolsProfile()

        # 从 permission_guard 提取权限信息
        try:
            from permission_guard import REGISTERED_AGENTS, PermissionLevel

            if agent_id in REGISTERED_AGENTS:
                ap = REGISTERED_AGENTS[agent_id]
                tools.permission_level = ap.level.value
                tools.allowed_actions = [s.value for s in ap.allowed_skills]
                # 协作网络
                for aid, aprop in REGISTERED_AGENTS.items():
                    if aid != agent_id:
                        tools.collaborators[aid] = aprop.agent_name
        except ImportError:
            logger.debug("soul_config.permission_guard_unavailable")

        # 从 skill_router_v2 提取技能清单
        try:
            from skill_router_v2 import SkillRouterV2

            router = SkillRouterV2()
            tools.available_skills = list(router._skills.keys())[:20]
        except (ImportError, AttributeError):
            logger.debug("soul_config.skill_router_unavailable")

        # AGENTS.md 文件补充
        agents_path = self._find_layer_file("AGENTS.md")
        if agents_path:
            agents_data = self._parse_markdown_layers(agents_path)
            if agents_data:
                for key, val in agents_data.items():
                    if key.startswith("skill") or key.startswith("技能"):
                        tools.available_skills.append(val)
                    if key.startswith("workflow") or key.startswith("流程"):
                        tools.workflow_sequence.append(val)

        return tools

    # --- Layer 4: Session ---
    def _load_session(self, recent_hours: int = 24) -> str:
        """加载近期对话摘要：从 memory_evolution 提取。"""
        try:
            from memory_evolution import MemoryEvolutionOrchestra

            orchestra = MemoryEvolutionOrchestra(db_path=self.db_path)
            records = orchestra.forgetting.list_active(limit=10)
            summaries = [r.content[:100] for r in records[:5]]
            return " | ".join(summaries) if summaries else ""
        except (ImportError, Exception):
            # ImportError: memory_evolution未安装
            # Exception: 数据库未初始化等
            logger.debug("soul_config.session_load_skipped")
            return ""

    # --- 辅助方法 ---
    def _find_layer_file(self, filename: str) -> str | None:
        """在工作区目录树中查找分层文件。"""
        if not self.workspace_root:
            return None

        search_paths = [
            os.path.join(self.workspace_root, filename),
            os.path.join(self.workspace_root, ".workbuddy", "memory", filename),
        ]

        for p in search_paths:
            if os.path.isfile(p):
                return p

        return None

    def _parse_markdown_layers(self, filepath: str) -> dict[str, str]:
        """解析 Markdown 文件为 key-value 层段字典。

        以 ## 标题段为 key，段下内容为 value。
        简单解析，不依赖外部库。
        """
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return {}

        sections: dict[str, str] = {}
        current_key = ""
        current_lines: list[str] = []

        for line in content.splitlines():
            if line.startswith("## "):
                if current_key:
                    sections[current_key] = "\n".join(current_lines).strip()
                current_key = line[3:].strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_key:
            sections[current_key] = "\n".join(current_lines).strip()

        return sections

    def _apply_overrides(
        self, template: PersonalityTemplate, overrides: dict[str, str]
    ) -> PersonalityTemplate:
        """将 SOUL.md 的段覆盖到人格模板。"""
        data = template.model_dump()

        # 角色定位 → role + core_traits
        if "角色定位" in overrides or "Role" in overrides:
            role_text = overrides.get("角色定位", overrides.get("Role", ""))
            data["role"] = role_text.split("\n")[0][:100]

        # 交流风格 → communication_style
        if "交流风格" in overrides or "Communication" in overrides:
            data["communication_style"] = overrides.get(
                "交流风格", overrides.get("Communication", "")
            )[:200]

        # 行为原则 → behavior_rules
        if "行为原则" in overrides or "Behavior" in overrides:
            rules_text = overrides.get("行为原则", overrides.get("Behavior", ""))
            data["behavior_rules"] = [
                r.lstrip("- ").strip() for r in rules_text.split("\n") if r.strip().startswith("-")
            ]

        # 行为边界 → boundaries
        if "行为边界" in overrides or "Boundaries" in overrides:
            bounds_text = overrides.get("行为边界", overrides.get("Boundaries", ""))
            data["boundaries"] = [
                b.lstrip("- ").strip() for b in bounds_text.split("\n") if b.strip().startswith("-")
            ]

        return PersonalityTemplate(**data)

    def _apply_user_overrides(self, user: UserProfile, overrides: dict[str, str]) -> UserProfile:
        """将 USER.md 的段覆盖到用户画像。"""
        data = user.model_dump()

        if "身份" in overrides or "Identity" in overrides:
            data["identity"] = overrides.get("身份", overrides.get("Identity", ""))[:100]

        if "场景" in overrides or "Scenario" in overrides:
            data["scenario"] = overrides.get("场景", overrides.get("Scenario", ""))[:100]

        if "偏好" in overrides or "Preferences" in overrides:
            prefs_text = overrides.get("偏好", overrides.get("Preferences", ""))
            for line in prefs_text.split("\n"):
                line = line.strip()
                if line.startswith("-"):
                    kv = line.lstrip("- ").strip()
                    if ":" in kv:
                        k, v = kv.split(":", 1)
                        data["preferences"][k.strip()] = v.strip()

        if "禁忌" in overrides or "Taboos" in overrides:
            taboos_text = overrides.get("禁忌", overrides.get("Taboos", ""))
            data["taboos"] = [
                t.lstrip("- ").strip() for t in taboos_text.split("\n") if t.strip().startswith("-")
            ]

        return UserProfile(**data)


# ============================================================
# Compaction 压缩引擎
# ============================================================


class CompactionEngine:
    """对话压缩引擎 — 防止上下文爆炸。

    三阶段策略（来自 SimpleMem F1 43.24%）：
    1. 实时观察 → 识别关键信息（决策·承诺·偏好变更）
    2. 阶段压缩 → 主题段合并（每N轮触发）
    3. 全局提炼 → 跨主题反思（每日维护时触发）

    与 memory_evolution.py 联动：
    - 压缩结果存入 memory_evolution 记忆库
    - 长期规则桥接到 MEMORY.md
    """

    # 压缩触发阈值
    TOKEN_THRESHOLD = 4000  # 超过此token数触发压缩
    TURN_THRESHOLD = 20  # 超过此对话轮次触发压缩
    CRITICAL_PATTERNS = [  # 关键信息模式
        r"决定|决策|commit|决定了",
        r"偏好|preference|更喜欢|习惯",
        r"禁止|禁忌|taboo|不能|不要|never",
        r"重要|关键|critical|必须|must",
        r"承诺|答应|agree|确认",
    ]

    def __init__(self, memory_db_path: str = ":memory:"):
        self.token_count = 0
        self.turn_count = 0
        self._critical_buffer: list[str] = []  # 关键信息暂存
        self._topic_buffer: list[str] = []  # 主题段暂存

    def observe(self, message: str, is_user: bool = True) -> str | None:
        """观察一条消息，识别关键信息并计数。

        Returns:
            如果触发压缩，返回压缩摘要；否则返回 None。
        """
        # 计数
        est_tokens = len(message) * 3 // 2  # 中英混合估算
        self.token_count += est_tokens
        self.turn_count += 1

        # 识别关键信息
        is_critical = any(re.search(p, message, re.IGNORECASE) for p in self.CRITICAL_PATTERNS)
        if is_critical and is_user:
            self._critical_buffer.append(message[:200])

        # 主题暂存（简化：每条消息取首50字）
        self._topic_buffer.append(message[:50])

        # 检查是否触发压缩
        if self.token_count >= self.TOKEN_THRESHOLD or self.turn_count >= self.TURN_THRESHOLD:
            return self.compact()

        return None

    def compact(self) -> str:
        """执行阶段压缩：关键信息 + 主题摘要。"""
        # 关键信息提取
        critical_summary = ""
        if self._critical_buffer:
            critical_summary = "关键决策/偏好:\n" + "\n".join(
                f"- {c}" for c in self._critical_buffer[-5:]
            )

        # 主题摘要（简化：合并最近主题段）
        topic_summary = ""
        if self._topic_buffer:
            recent_topics = self._topic_buffer[-10:]
            topic_summary = "近期话题: " + " → ".join(recent_topics[:5])

        # 组合压缩结果
        result = f"{critical_summary}\n{topic_summary}".strip()

        # 清空缓冲
        self._critical_buffer.clear()
        self._topic_buffer.clear()
        self.token_count = 0
        self.turn_count = 0

        logger.info("soul_config.compacted", result_len=len(result))

        return result

    def should_compact(self) -> bool:
        """判断是否应该触发压缩。"""
        return self.token_count >= self.TOKEN_THRESHOLD or self.turn_count >= self.TURN_THRESHOLD

    def reset(self):
        """重置计数器（新对话开始时）。"""
        self.token_count = 0
        self.turn_count = 0
        self._critical_buffer.clear()
        self._topic_buffer.clear()


# ============================================================
# 便捷API
# ============================================================


def get_agent_context(
    agent_id: str = "", workspace_root: str = "", session_summary: str = ""
) -> LayeredContext:
    """快捷获取Agent的四层上下文。"""
    loader = SoulConfigLoader(workspace_root=workspace_root)
    return loader.load_all(agent_id=agent_id, session_summary=session_summary)


def get_system_prompt(agent_id: str = "", workspace_root: str = "", max_chars: int = 2000) -> str:
    """快捷组装Agent的系统提示词。"""
    context = get_agent_context(agent_id, workspace_root)
    return context.assemble_system_prompt(max_chars=max_chars)


def list_available_templates() -> dict[str, str]:
    """列出所有可用的人格模板。"""
    result = {}
    for key, tmpl in BUILTIN_TEMPLATES.items():
        result[key] = f"{tmpl.role} ({','.join(tmpl.core_traits[:3])})"
    result["default"] = f"{DEFAULT_TEMPLATE.role} ({','.join(DEFAULT_TEMPLATE.core_traits[:3])})"
    return result
