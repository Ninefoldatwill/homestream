"""
桥v7 Worktree并行隔离管理器（Day 2 核心模块）

三源融优设计：
- A主: Claude Code Worktree（文件隔离+配置复制+自动清理）
- B副: Coasts运行时隔离（双端口模型+DB隔离，融合改造：去Docker用Python进程）
- C自: Agent Loop审查分离（制造者/检查者角色映射）

设计原则：
- 不用Docker，用Python进程管理（融优主义：部分匹配→融合改造）
- 不用Redis，用SQLite做状态持久化
- 与EventStream引擎深度集成
- 与ICP v1.1协议天然映射
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ==================== 常量定义 ====================

# 默认仓库路径（可通过环境变量 WORKTREE_REPO_PATH 配置）
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO_PATH = os.environ.get("WORKTREE_REPO_PATH", _PROJECT_DIR)

# Worktree存储目录（可通过环境变量 WORKTREE_BASE_DIR 配置）
WORKTREE_BASE_DIR = os.environ.get(
    "WORKTREE_BASE_DIR",
    os.path.join(_PROJECT_DIR, "worktrees"),
)

# 规范端口映射（B副: Coasts双端口模型）
CANONICAL_PORTS = {
    "bridge_v7": 3459,
    "kanban": 8643,
    "bookhouse": 3460,
    "openclaw": 28790,
}

# Agent→角色默认映射（C自: 审查分离）
AGENT_ROLE_DEFAULTS = {
    "澜舟": "maker",
    "千寻": "reviewer",
    "灵犀": "researcher",
    "澜澜": "coordinator",
}


# ==================== 数据类 ====================


class WorktreeRole(str, Enum):
    """Worktree角色（C自: Agent Loop审查分离映射 + v7.3实验角色）"""

    MAKER = "maker"  # 制造者（澜舟）
    REVIEWER = "reviewer"  # 审查者（千寻）
    RESEARCHER = "researcher"  # 调研者（灵犀）
    COORDINATOR = "coordinator"  # 调度者（澜澜）
    EXPERIMENTER = "experimenter"  # v7.3: 实验者（Ratchet Loop）


class WorktreeStatus(str, Enum):
    """Worktree生命周期状态"""

    CREATING = "creating"
    ACTIVE = "active"
    REVIEWING = "reviewing"  # 审查中（C自: 审查分离）
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"
    LOCKED = "locked"  # A主: git worktree lock保护
    EXPERIMENTING = "experimenting"  # v7.3: Ratchet Loop实验中
    RATCHET_LOCKED = "ratchet_locked"  # v7.3: 棘轮锁定（不可回退）
    ROLLED_BACK = "rolled_back"  # v7.3: 实验失败已回滚


@dataclass
class WorktreeConfig:
    """Worktree配置 — 三源融优: A主(Claude Code) + B副(Coasts) + C自(审查分离)"""

    # === 基础配置（A主: git worktree）===
    name: str  # Worktree唯一名称
    branch: str  # Git分支名
    agent: str  # 分配的Agent
    role: WorktreeRole = WorktreeRole.MAKER  # 角色类型
    base_branch: str = "main"  # 基于哪个分支创建
    repo_path: str = DEFAULT_REPO_PATH  # 主仓库路径

    # === 端口配置（B副: Coasts双端口模型，融合改造）===
    port_base: int = 3459  # 规范端口
    port_offset: int = 0  # 动态偏移(worktree_index)

    # === 数据库配置（B副: Per-worktree SQLite）===
    db_path: str = ""  # 自动生成

    # === 配置复制（A主: .worktreeinclude）===
    worktreeinclude_patterns: list[str] = field(
        default_factory=lambda: [
            ".env",
            "*.json",
            "*.yaml",
            "*.yml",
            "*.toml",
            "requirements.txt",
            "pyproject.toml",
        ]
    )

    # === 审查配置（C自: 制造者/检查者分离）===
    review_required: bool = True  # 是否需要审查
    reviewer: str = ""  # 审查者Agent名
    stop_conditions: list[str] = field(default_factory=list)  # Stop Hook条件

    # === 运行时状态 ===
    status: WorktreeStatus = WorktreeStatus.CREATING
    worktree_path: str = ""  # 实际路径（自动生成）
    created_at: str = ""  # 创建时间
    completed_at: str = ""  # 完成时间

    def __post_init__(self):
        """自动生成路径和时间"""
        if not self.worktree_path:
            self.worktree_path = os.path.join(WORKTREE_BASE_DIR, self.name)
        if not self.db_path:
            db_dir = os.path.join(self.worktree_path, ".worktree")
            self.db_path = os.path.join(db_dir, f"{self.name}.db")
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        # 自动分配审查者（C自: 角色映射）
        if self.review_required and not self.reviewer:
            if self.role == WorktreeRole.MAKER:
                self.reviewer = "千寻"  # 默认千寻审查澜舟
            elif self.role == WorktreeRole.RESEARCHER:
                self.reviewer = "澜舟"  # 澜舟审查灵犀的调研


# ==================== PortManager（B副融合改造）====================


class PortManager:
    """端口管理器 — Coasts双端口模型的Python进程版

    融优主义判定：部分匹配→融合改造
    - 原方案: Docker网络映射 + 规范端口
    - 改造方案: Python环境变量 + 动态偏移

    双端口模型:
    - 规范端口(canonical): 签出实例获得(3000, 3459...)
    - 动态端口(dynamic): 每个worktree实例获得偏移端口(4000, 4459...)
    """

    # 端口偏移步长（每个worktree偏移1000）
    PORT_STEP = 1000

    # 端口分配范围
    MIN_PORT = 3000
    MAX_PORT = 60000

    def __init__(self):
        self._allocations: dict[str, dict[str, int]] = {}  # worktree_name → {service: port}
        self._canonical_owner: str | None = None  # 当前持有规范端口的worktree
        self._lock = threading.RLock()  # 可重入锁：set_canonical持锁后调allocate不死锁

    def allocate(self, worktree_name: str, worktree_index: int) -> dict[str, int]:
        """为Worktree分配端口 — 动态偏移模型

        Args:
            worktree_name: Worktree名称
            worktree_index: Worktree索引(0, 1, 2...)，决定偏移量

        Returns:
            {service_name: port} 端口映射字典
        """
        with self._lock:
            ports = {}
            for service, base_port in CANONICAL_PORTS.items():
                if worktree_index == 0:
                    # 索引0 = 主实例，获得规范端口
                    ports[service] = base_port
                else:
                    # 索引1+ = 并行实例，动态偏移
                    ports[service] = base_port + worktree_index * self.PORT_STEP

                # 安全校验：端口范围
                if not (self.MIN_PORT <= ports[service] <= self.MAX_PORT):
                    raise ValueError(
                        f"端口 {ports[service]} 超出范围 ({self.MIN_PORT}-{self.MAX_PORT})"
                    )

                # 冲突检测
                for wt_name, wt_ports in self._allocations.items():
                    if wt_name != worktree_name:
                        for svc, port in wt_ports.items():
                            if port == ports[service]:
                                raise ValueError(
                                    f"端口冲突: {service}={ports[service]} 已被 {wt_name} 占用"
                                )

            self._allocations[worktree_name] = ports
            return ports

    def set_canonical(self, worktree_name: str):
        """将指定Worktree设为规范端口持有者

        签出(checkout)时调用，使其获得"localhost肌肉记忆"的规范端口

        注意：必须先释放目标worktree的旧分配，再降级其他规范端口持有者，最后分配
        降级时自动寻找可用 index，避免端口冲突
        """
        with self._lock:
            # 1. 先清除目标worktree的旧分配（释放其动态端口给其他worktree复用）
            if worktree_name in self._allocations:
                del self._allocations[worktree_name]

            # 2. 找到所有当前持有规范端口的worktree，降级为动态端口
            for wt_name in list(self._allocations.keys()):
                if wt_name == worktree_name:
                    continue
                wt_ports = self._allocations[wt_name]
                holds_canonical = any(
                    port == CANONICAL_PORTS.get(svc) for svc, port in wt_ports.items()
                )
                if holds_canonical:
                    del self._allocations[wt_name]
                    # 寻找可用 index（避免与其他worktree端口冲突）
                    new_idx = self._find_available_index()
                    self.allocate(wt_name, new_idx)

            # 3. 分配规范端口给目标
            self._canonical_owner = worktree_name
            self.allocate(worktree_name, 0)

    def _find_available_index(self) -> int:
        """寻找可用的 worktree index（端口不冲突的最小 index >= 1）"""
        used_ports = set()
        for wt_ports in self._allocations.values():
            used_ports.update(wt_ports.values())

        idx = 1
        while True:
            # 计算该 index 下所有服务的端口
            would_use = {base + idx * self.PORT_STEP for base in CANONICAL_PORTS.values()}
            if not would_use & used_ports:
                return idx
            idx += 1
            if idx > 100:  # 安全上限
                return idx

    def release(self, worktree_name: str):
        """释放Worktree的端口分配"""
        with self._lock:
            if worktree_name in self._allocations:
                del self._allocations[worktree_name]
            if self._canonical_owner == worktree_name:
                self._canonical_owner = None

    def get_ports(self, worktree_name: str) -> dict[str, int]:
        """获取Worktree的端口分配"""
        return self._allocations.get(worktree_name, {})

    def get_env_vars(self, worktree_name: str) -> dict[str, str]:
        """生成Worktree的环境变量（注入到Agent进程）"""
        ports = self.get_ports(worktree_name)
        env = {}
        for service, port in ports.items():
            env[f"JIUCHONG_PORT_{service.upper()}"] = str(port)
        env["JIUCHONG_WORKTREE"] = worktree_name
        return env

    def _get_worktree_index(self, worktree_name: str) -> int:
        """获取Worktree的当前索引（从分配的端口反推）"""
        ports = self._allocations.get(worktree_name, {})
        if not ports:
            return 0
        bridge_port = ports.get("bridge_v7", CANONICAL_PORTS["bridge_v7"])
        return (bridge_port - CANONICAL_PORTS["bridge_v7"]) // self.PORT_STEP

    def list_allocations(self) -> dict[str, dict[str, int]]:
        """列出所有端口分配"""
        return dict(self._allocations)


# ==================== SQLiteManager（B副融合改造）====================


class SQLiteManager:
    """Per-worktree SQLite数据库隔离管理器

    融优主义判定：部分匹配→融合改造
    - 原方案: Docker容器内独立DB
    - 改造方案: 每个Worktree独立.db文件
    """

    def __init__(self):
        self._connections: dict[str, sqlite3.Connection] = {}
        self._lock = threading.Lock()

    def create_database(self, worktree_name: str, db_path: str) -> str:
        """为Worktree创建独立SQLite数据库

        Returns:
            数据库文件路径
        """
        with self._lock:
            # 确保目录存在
            db_dir = os.path.dirname(db_path)
            os.makedirs(db_dir, exist_ok=True)

            # 创建数据库并初始化表结构（check_same_thread=False 支持跨线程访问）
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS worktree_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    target TEXT,
                    data TEXT,
                    cause_id TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'inbox',
                    assigned_to TEXT,
                    worktree TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()

            self._connections[worktree_name] = conn

            # 写入初始状态
            self._set_state(conn, "worktree_name", worktree_name)
            self._set_state(conn, "created_at", datetime.now().isoformat())

            return db_path

    def get_connection(self, worktree_name: str) -> sqlite3.Connection | None:
        """获取Worktree的数据库连接"""
        return self._connections.get(worktree_name)

    def execute(self, worktree_name: str, sql: str, params: tuple = ()) -> list[dict]:
        """在Worktree数据库上执行SQL查询"""
        conn = self._connections.get(worktree_name)
        if not conn:
            raise ValueError(f"Worktree '{worktree_name}' 的数据库未初始化")

        cursor = conn.execute(sql, params)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        conn.commit()

        return [dict(zip(columns, row)) for row in rows]

    def close(self, worktree_name: str):
        """关闭Worktree的数据库连接"""
        with self._lock:
            conn = self._connections.pop(worktree_name, None)
            if conn:
                conn.close()

    def close_all(self):
        """关闭所有数据库连接"""
        with self._lock:
            for conn in self._connections.values():
                conn.close()
            self._connections.clear()

    def _set_state(self, conn: sqlite3.Connection, key: str, value: str):
        """写入状态键值"""
        conn.execute(
            "INSERT OR REPLACE INTO worktree_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key, value),
        )
        conn.commit()


# ==================== WorktreeManager（A主+B副+C自核心）====================


class WorktreeManager:
    """桥v7 Worktree并行隔离管理器

    三源融优核心实现：
    - A主(Claude Code): 文件隔离 + 配置复制 + 自动清理 + lock保护
    - B副(Coasts): 端口隔离 + DB隔离（Python进程版，去Docker）
    - C自(Agent Loop): 制造者/审查者角色分离

    与EventStream引擎集成：
    - Worktree创建/销毁 → Event发布
    - 审查完成 → DONE事件 → 自动创建review Worktree
    - Handoff 5要素 → Worktree间交接
    """

    def __init__(
        self,
        repo_path: str = DEFAULT_REPO_PATH,
        worktree_base: str = WORKTREE_BASE_DIR,
    ):
        self.repo_path = repo_path
        self.worktree_base = worktree_base
        self.port_manager = PortManager()
        self.db_manager = SQLiteManager()

        # Worktree注册表
        self._worktrees: dict[str, WorktreeConfig] = {}
        self._lock = threading.Lock()

        # Worktree计数器（用于端口偏移）
        self._counter = 0

        # 确保worktree_base目录存在
        os.makedirs(worktree_base, exist_ok=True)

    # === A主: CRUD核心 ===

    def create_worktree(self, config: WorktreeConfig) -> str:
        """创建隔离Worktree（A主: git worktree add + B副: 端口/DB + C自: 角色）

        Returns:
            worktree路径
        """
        with self._lock:
            if config.name in self._worktrees:
                raise ValueError(f"Worktree '{config.name}' 已存在")

            # 1. A主: git worktree add 创建文件隔离
            worktree_path = config.worktree_path
            branch_name = config.branch

            try:
                # 检查分支是否存在
                result = subprocess.run(
                    ["git", "branch", "--list", branch_name],
                    capture_output=True,
                    text=True,
                    cwd=self.repo_path,
                    timeout=10,
                )

                if result.stdout.strip():
                    # 分支已存在，用现有分支创建
                    cmd = ["git", "worktree", "add", worktree_path, branch_name]
                else:
                    # 创建新分支
                    cmd = [
                        "git",
                        "worktree",
                        "add",
                        "-b",
                        branch_name,
                        worktree_path,
                        config.base_branch,
                    ]

                result = subprocess.run(
                    cmd, capture_output=True, text=True, cwd=self.repo_path, timeout=30
                )

                if result.returncode != 0:
                    # git worktree add失败，创建普通目录作为fallback
                    os.makedirs(worktree_path, exist_ok=True)
                    # 写入.git占位标记
                    with open(os.path.join(worktree_path, ".worktree_marker"), "w") as f:
                        f.write(f"worktree: {config.name}\nbranch: {branch_name}\n")

            except (subprocess.TimeoutExpired, FileNotFoundError):
                # git不可用，创建普通目录
                os.makedirs(worktree_path, exist_ok=True)
                with open(os.path.join(worktree_path, ".worktree_marker"), "w") as f:
                    f.write(f"worktree: {config.name}\nbranch: {branch_name}\n")

            # 2. B副: 分配端口（动态偏移）
            try:
                ports = self.port_manager.allocate(config.name, self._counter)
            except ValueError:
                # 端口冲突，降级创建
                ports = self.port_manager.allocate(config.name, 0)

            config.port_offset = self._counter

            # 3. B副: 创建独立数据库
            self.db_manager.create_database(config.name, config.db_path)

            # 4. A主: 复制配置文件（.worktreeinclude）
            self._copy_config_files(config)

            # 5. 写入Worktree元数据
            self._save_worktree_meta(config)

            # 6. 更新状态
            config.status = WorktreeStatus.ACTIVE
            config.created_at = datetime.now().isoformat()

            # 7. 注册
            self._worktrees[config.name] = config
            self._counter += 1

            return worktree_path

    def list_worktrees(self) -> list[WorktreeConfig]:
        """列出所有活跃Worktree"""
        return list(self._worktrees.values())

    def get_worktree(self, name: str) -> WorktreeConfig | None:
        """获取指定Worktree配置"""
        return self._worktrees.get(name)

    def remove_worktree(self, name: str, force: bool = False) -> bool:
        """清理Worktree（A主: git worktree remove + lock检查）

        Args:
            name: Worktree名称
            force: 是否强制删除（忽略未提交变更）

        Returns:
            是否成功删除
        """
        with self._lock:
            config = self._worktrees.get(name)
            if not config:
                return False

            # A主: lock保护（正在审查中的worktree不可删除）
            if config.status == WorktreeStatus.REVIEWING and not force:
                raise ValueError(
                    f"Worktree '{name}' 正在审查中，不能删除。使用 force=True 强制删除。"
                )

            if config.status == WorktreeStatus.LOCKED and not force:
                raise ValueError(f"Worktree '{name}' 已锁定，不能删除。使用 force=True 强制删除。")

            # 1. 关闭DB连接
            self.db_manager.close(name)

            # 2. 释放端口
            self.port_manager.release(name)

            # 3. A主: git worktree remove
            try:
                cmd = ["git", "worktree", "remove", config.worktree_path]
                if force:
                    cmd.append("--force")
                subprocess.run(cmd, capture_output=True, text=True, cwd=self.repo_path, timeout=15)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                # git不可用，手动删除目录
                if os.path.exists(config.worktree_path):
                    shutil.rmtree(config.worktree_path, ignore_errors=True)

            # 4. 清理元数据
            meta_path = os.path.join(config.worktree_path, ".worktree", "meta.json")
            if os.path.exists(meta_path):
                os.remove(meta_path)

            # 5. 从注册表移除
            del self._worktrees[name]

            return True

    def lock_worktree(self, name: str, reason: str = "") -> bool:
        """锁定Worktree（A主: git worktree lock保护）"""
        with self._lock:
            config = self._worktrees.get(name)
            if not config:
                return False

            config.status = WorktreeStatus.LOCKED

            try:
                subprocess.run(
                    [
                        "git",
                        "worktree",
                        "lock",
                        config.worktree_path,
                        "--reason",
                        reason or "locked by WorktreeManager",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=self.repo_path,
                    timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

            self._save_worktree_meta(config)
            return True

    def unlock_worktree(self, name: str) -> bool:
        """解锁Worktree"""
        with self._lock:
            config = self._worktrees.get(name)
            if not config:
                return False

            config.status = WorktreeStatus.ACTIVE

            try:
                subprocess.run(
                    ["git", "worktree", "unlock", config.worktree_path],
                    capture_output=True,
                    text=True,
                    cwd=self.repo_path,
                    timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

            self._save_worktree_meta(config)
            return True

    # === B副: 端口/DB操作 ===

    def assign_ports(self, worktree_name: str) -> dict[str, int]:
        """获取Worktree的端口分配"""
        return self.port_manager.get_ports(worktree_name)

    def get_worktree_env(self, worktree_name: str) -> dict[str, str]:
        """获取Worktree的环境变量（注入到Agent进程）"""
        return self.port_manager.get_env_vars(worktree_name)

    def assign_reviewer(self, worktree_name: str, reviewer: str) -> bool:
        """分配审查者（C自: 制造者/检查者分离）"""
        with self._lock:
            config = self._worktrees.get(worktree_name)
            if not config:
                return False

            config.reviewer = reviewer
            config.status = WorktreeStatus.REVIEWING
            self._save_worktree_meta(config)
            return True

    def verify_and_merge(self, worktree_name: str) -> bool:
        """验证条件并合并（C自: Stop Hook验证）

        当制造者完成工作后，审查者验证通过后合并回主分支
        """
        with self._lock:
            config = self._worktrees.get(worktree_name)
            if not config:
                return False

            # 检查审查是否完成
            if config.status == WorktreeStatus.REVIEWING:
                # 审查中不能合并
                return False

            try:
                # 合并分支回base_branch
                subprocess.run(
                    ["git", "checkout", config.base_branch],
                    capture_output=True,
                    text=True,
                    cwd=self.repo_path,
                    timeout=10,
                )
                subprocess.run(
                    ["git", "merge", config.branch],
                    capture_output=True,
                    text=True,
                    cwd=self.repo_path,
                    timeout=30,
                )

                config.status = WorktreeStatus.COMPLETED
                config.completed_at = datetime.now().isoformat()
                self._save_worktree_meta(config)
                return True

            except (subprocess.TimeoutExpired, FileNotFoundError):
                return False

    # === A主: 配置复制 ===

    def _copy_config_files(self, config: WorktreeConfig):
        """复制配置文件到Worktree（A主: .worktreeinclude机制）"""
        if not os.path.exists(config.worktree_path):
            return

        # 创建.worktree目录
        worktree_dir = os.path.join(config.worktree_path, ".worktree")
        os.makedirs(worktree_dir, exist_ok=True)

        # 复制匹配的配置文件
        for pattern in config.worktreeinclude_patterns:
            # 在主仓库查找匹配文件
            for root, dirs, files in os.walk(self.repo_path):
                # 跳过.git和worktree目录
                dirs[:] = [
                    d for d in dirs if d not in {".git", "worktrees", "__pycache__", ".worktree"}
                ]

                for filename in files:
                    # 简单glob匹配
                    if self._match_pattern(filename, pattern):
                        src = os.path.join(root, filename)
                        rel = os.path.relpath(src, self.repo_path)
                        dst = os.path.join(config.worktree_path, rel)

                        dst_dir = os.path.dirname(dst)
                        if dst_dir and not os.path.exists(dst_dir):
                            os.makedirs(dst_dir, exist_ok=True)

                        if os.path.exists(src) and not os.path.exists(dst):
                            shutil.copy2(src, dst)

    def _match_pattern(self, filename: str, pattern: str) -> bool:
        """简单的文件名模式匹配"""
        if pattern.startswith("*."):
            return filename.endswith(pattern[1:])
        return filename == pattern

    # === 元数据持久化 ===

    def _save_worktree_meta(self, config: WorktreeConfig):
        """保存Worktree元数据到JSON"""
        meta_dir = os.path.join(config.worktree_path, ".worktree")
        os.makedirs(meta_dir, exist_ok=True)

        meta_path = os.path.join(meta_dir, "meta.json")
        meta = {
            "name": config.name,
            "branch": config.branch,
            "agent": config.agent,
            "role": config.role.value if isinstance(config.role, WorktreeRole) else config.role,
            "base_branch": config.base_branch,
            "status": config.status.value
            if isinstance(config.status, WorktreeStatus)
            else config.status,
            "port_base": config.port_base,
            "port_offset": config.port_offset,
            "db_path": config.db_path,
            "review_required": config.review_required,
            "reviewer": config.reviewer,
            "stop_conditions": config.stop_conditions,
            "created_at": config.created_at,
            "completed_at": config.completed_at,
        }

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def _load_worktree_meta(self, worktree_path: str) -> dict | None:
        """加载Worktree元数据"""
        meta_path = os.path.join(worktree_path, ".worktree", "meta.json")
        if not os.path.exists(meta_path):
            return None

        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)

    # === 统计信息 ===

    def get_stats(self) -> dict[str, Any]:
        """获取Worktree统计信息"""
        stats = {
            "total_worktrees": len(self._worktrees),
            "active": sum(1 for w in self._worktrees.values() if w.status == WorktreeStatus.ACTIVE),
            "reviewing": sum(
                1 for w in self._worktrees.values() if w.status == WorktreeStatus.REVIEWING
            ),
            "locked": sum(1 for w in self._worktrees.values() if w.status == WorktreeStatus.LOCKED),
            "completed": sum(
                1 for w in self._worktrees.values() if w.status == WorktreeStatus.COMPLETED
            ),
            "experimenting": sum(
                1 for w in self._worktrees.values() if w.status == WorktreeStatus.EXPERIMENTING
            ),
            "ratchet_locked": sum(
                1 for w in self._worktrees.values() if w.status == WorktreeStatus.RATCHET_LOCKED
            ),
            "rolled_back": sum(
                1 for w in self._worktrees.values() if w.status == WorktreeStatus.ROLLED_BACK
            ),
            "port_allocations": self.port_manager.list_allocations(),
            "agents": {},
        }

        # 按Agent统计
        for config in self._worktrees.values():
            agent = config.agent
            if agent not in stats["agents"]:
                stats["agents"][agent] = {"worktrees": 0, "roles": []}
            stats["agents"][agent]["worktrees"] += 1
            role = config.role.value if isinstance(config.role, WorktreeRole) else config.role
            stats["agents"][agent]["roles"].append(role)

        return stats


# ==================== 便捷工厂函数 ====================


def create_maker_worktree(
    name: str,
    agent: str = "澜舟",
    branch: str = "",
    base_branch: str = "main",
    reviewer: str = "千寻",
) -> WorktreeConfig:
    """创建制造者Worktree（C自: 澜舟开发→千寻审查）"""
    if not branch:
        branch = f"feat/{name}"

    return WorktreeConfig(
        name=name,
        branch=branch,
        agent=agent,
        role=WorktreeRole.MAKER,
        base_branch=base_branch,
        reviewer=reviewer,
        review_required=True,
    )


def create_reviewer_worktree(
    source_name: str,
    reviewer: str = "千寻",
    source_branch: str = "",
) -> WorktreeConfig:
    """创建审查者Worktree（C自: 千寻审查→澜舟合并）"""
    name = f"review-{source_name}"
    branch = f"review/{source_name}"

    return WorktreeConfig(
        name=name,
        branch=branch,
        agent=reviewer,
        role=WorktreeRole.REVIEWER,
        base_branch=source_branch or f"feat/{source_name}",
        review_required=False,  # 审查者不需要被审查
    )


def create_researcher_worktree(
    name: str,
    agent: str = "灵犀",
    base_branch: str = "main",
) -> WorktreeConfig:
    """创建调研者Worktree（灵犀深度调研用）"""
    return WorktreeConfig(
        name=name,
        branch=f"research/{name}",
        agent=agent,
        role=WorktreeRole.RESEARCHER,
        base_branch=base_branch,
        review_required=False,  # 调研成果由澜舟审查
        reviewer="澜舟",
    )


def create_coordinator_worktree(
    name: str,
    agent: str = "澜澜",
    base_branch: str = "main",
) -> WorktreeConfig:
    """创建调度者Worktree（澜澜统筹用）"""
    return WorktreeConfig(
        name=name,
        branch=f"coord/{name}",
        agent=agent,
        role=WorktreeRole.COORDINATOR,
        base_branch=base_branch,
        review_required=False,
    )


def create_experiment_worktree(
    name: str,
    agent: str = "澜舟",
    reviewer: str = "千寻",
    base_branch: str = "main",
    max_iterations: int = 10,
) -> WorktreeConfig:
    """创建实验Worktree（v7.3: Ratchet Loop双层实验工坊）

    实验Worktree特点：
    - 角色为EXPERIMENTER
    - 分支前缀为experiment/
    - 启用审查（Reviewer验证实验结果）
    - stop_conditions包含实验迭代上限

    Args:
        name: 实验名称
        agent: 执行Agent
        reviewer: 验证Agent
        base_branch: 基于哪个分支
        max_iterations: 最大迭代次数
    """
    return WorktreeConfig(
        name=name,
        branch=f"experiment/{name}",
        agent=agent,
        role=WorktreeRole.EXPERIMENTER,
        base_branch=base_branch,
        review_required=True,
        reviewer=reviewer,
        stop_conditions=[f"max_iter:{max_iterations}", "experiment_mode"],
    )
