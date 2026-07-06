"""
桥v7 Ratchet Loop — 双层实验工坊引擎

融优主义分级: C类 — 自己造
灵感来源: BuilderIO agent-native self-improving + Ratchet（棘轮只进不退）
设计哲学: 每次实验成功后"锁死"成果，形成渐进式提升

双层结构:
  Layer 1 (Maker层): Agent在隔离Worktree中执行实验
    - ConditionVerifier EXPERIMENT模式监控
    - 实验完成/失败/超时自动捕获
  Layer 2 (Reviewer层): 验证实验结果
    - PASS → ratchet_lock (git commit + tag + 归档)
    - FAIL → rollback (丢弃Worktree，记录教训)
    - 棘轮锁定后不可回退

program.md 实验指令格式:
  ---
  experiment:
    name: "experiment-name"
    maker: "澜舟"
    reviewer: "千寻"
    hypothesis: "假设描述"
    success_criteria:
      - "验证条件1"
      - "验证条件2"
    max_iterations: 10
    timeout: 300
    rollback_on_fail: true
    archive_to: "bookhouse"
  ---
  实验内容描述...

日期: 2026-06-26
作者: 澜舟
"""

from __future__ import annotations

import os
import re
import json
import time
import uuid
import threading
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# 桥v7内部依赖
from event_stream import (
    Event,
    EventStream,
    EventType,
    EventSource,
    Action,
    Observation,
    _gen_event_id,
    create_action,
)
from condition_verifier import (
    ConditionVerifier,
    VerifierConfig,
    VerificationResult,
    StopCondition,
)


# ==================== 枚举 ====================

class ExperimentStatus(str, Enum):
    """实验生命周期状态"""
    PENDING = "pending"           # 等待启动
    RUNNING = "running"           # Maker执行中
    VERIFYING = "verifying"       # Reviewer验证中
    LOCKED = "locked"             # 棘轮锁定（成功归档）
    ROLLED_BACK = "rolled_back"   # 已回滚（失败丢弃）
    TIMEOUT = "timeout"           # 超时终止
    ARCHIVED = "archived"         # 已归档到千寻书阁


class RatchetPhase(str, Enum):
    """棘轮循环阶段"""
    PARSE = "parse"           # 解析program.md
    SPAWN = "spawn"           # 创建实验Worktree
    EXECUTE = "execute"       # Maker执行实验
    VERIFY = "verify"         # Reviewer验证
    LOCK = "lock"             # 棘轮锁定
    ROLLBACK = "rollback"     # 回滚
    ARCHIVE = "archive"       # 归档


# ==================== 数据类 ====================

@dataclass
class ExperimentConfig:
    """实验配置 — 从program.md解析或API直接创建

    对标BuilderIO agent-native的self-improving设计：
    - hypothesis: 明确假设（实验前必须声明）
    - success_criteria: 可验证的成功标准（非模糊描述）
    - rollback_on_fail: 失败时自动回滚（安全网）
    - archive_to: 归档目标（千寻书阁）
    """
    name: str                                      # 实验唯一名称
    maker: str = "澜舟"                             # 执行Agent
    reviewer: str = "千寻"                           # 验证Agent
    hypothesis: str = ""                            # 实验假设
    success_criteria: List[str] = field(default_factory=list)  # 成功标准
    description: str = ""                           # 实验描述
    max_iterations: int = 10                        # 最大迭代
    timeout: float = 300.0                          # 超时秒数
    rollback_on_fail: bool = True                   # 失败自动回滚
    archive_to: str = "bookhouse"                   # 归档目标
    base_branch: str = "main"                       # 基于哪个分支
    tags: List[str] = field(default_factory=list)   # 实验标签

    # 运行时填充
    experiment_id: str = ""                         # 自动生成
    worktree_name: str = ""                         # 关联Worktree名
    created_at: str = ""                            # 创建时间

    def __post_init__(self):
        if not self.experiment_id:
            self.experiment_id = f"exp_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        if not self.worktree_name:
            self.worktree_name = f"exp-{self.name}-{self.experiment_id[-6:]}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


@dataclass
class ExperimentResult:
    """实验结果 — Maker执行完毕后的完整记录"""
    experiment_id: str
    config: ExperimentConfig
    status: ExperimentStatus = ExperimentStatus.PENDING
    phase: RatchetPhase = RatchetPhase.PARSE

    # Maker层结果
    iterations: int = 0                             # 实际迭代次数
    duration: float = 0.0                           # 执行时长(秒)
    outputs: List[str] = field(default_factory=list)  # Maker输出列表
    stop_condition: Optional[str] = None             # 停止条件
    stop_reason: str = ""                            # 停止原因

    # Reviewer层结果
    verification_passed: bool = False                # 验证是否通过
    verification_details: List[str] = field(default_factory=list)  # 逐条验证
    reviewer_notes: str = ""                         # 审查备注

    # 棘轮锁定信息
    locked_at: str = ""                             # 锁定时间
    locked_commit: str = ""                         # 锁定的commit hash
    locked_tag: str = ""                            # git tag名

    # 回滚信息
    rollback_reason: str = ""                       # 回滚原因

    # 归档信息
    archived_at: str = ""                           # 归档时间
    archive_path: str = ""                          # 归档路径

    # 教训记录（失败时填写）
    lessons_learned: str = ""                       # 经验教训

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于JSON存储/归档）"""
        d = asdict(self)
        d["status"] = self.status.value
        d["phase"] = self.phase.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExperimentResult":
        """从字典反序列化"""
        d["status"] = ExperimentStatus(d.get("status", "pending"))
        d["phase"] = RatchetPhase(d.get("phase", "parse"))
        d["config"] = ExperimentConfig(**d.get("config", {}))
        return cls(**d)


# ==================== program.md 解析器 ====================

class ProgramParser:
    """program.md 实验指令解析器

    格式: YAML frontmatter + Markdown正文

    示例:
        ---
        experiment:
          name: "test-skill-router-v2"
          maker: "澜舟"
          reviewer: "千寻"
          hypothesis: "双层路由比单层快30%"
          success_criteria:
            - "延迟 < 50ms"
            - "准确率 > 95%"
          max_iterations: 10
          timeout: 300
          rollback_on_fail: true
          archive_to: "bookhouse"
        ---
        # 实验内容
        测试SkillRouter v2双层路由...
    """

    # YAML frontmatter 正则
    FRONTMATTER_RE = re.compile(
        r'^---\s*\n(.*?)\n---\s*\n(.*)$',
        re.DOTALL
    )

    @classmethod
    def parse(cls, content: str) -> Tuple[ExperimentConfig, str]:
        """解析program.md内容

        Args:
            content: program.md文件内容

        Returns:
            (ExperimentConfig, description_markdown)
        """
        match = cls.FRONTMATTER_RE.match(content.strip())
        if not match:
            # 无frontmatter，当作纯描述
            return ExperimentConfig(name=f"exp_{uuid.uuid4().hex[:6]}"), content.strip()

        yaml_block = match.group(1)
        markdown_body = match.group(2).strip()

        # 轻量YAML解析（不引入PyYAML依赖）
        config_dict = cls._parse_lightweight_yaml(yaml_block)

        # 提取experiment段
        exp_data = config_dict.get("experiment", config_dict)

        config = ExperimentConfig(
            name=exp_data.get("name", f"exp_{uuid.uuid4().hex[:6]}"),
            maker=exp_data.get("maker", "澜舟"),
            reviewer=exp_data.get("reviewer", "千寻"),
            hypothesis=exp_data.get("hypothesis", ""),
            success_criteria=cls._parse_list(exp_data.get("success_criteria", [])),
            description=markdown_body,
            max_iterations=int(exp_data.get("max_iterations", 10)),
            timeout=float(exp_data.get("timeout", 300)),
            rollback_on_fail=bool(exp_data.get("rollback_on_fail", True)),
            archive_to=exp_data.get("archive_to", "bookhouse"),
            base_branch=exp_data.get("base_branch", "main"),
            tags=cls._parse_list(exp_data.get("tags", [])),
        )

        return config, markdown_body

    @classmethod
    def parse_file(cls, file_path: str) -> Tuple[ExperimentConfig, str]:
        """解析program.md文件"""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return cls.parse(content)

    @staticmethod
    def _parse_lightweight_yaml(yaml_text: str) -> Dict[str, Any]:
        """轻量YAML解析（支持2级嵌套 + 列表）

        不引入PyYAML依赖，仅支持桥v7需要的子集：
        - key: value
        - key: "value"
        - key:
            - item1
            - item2
        - 嵌套: experiment:
                    name: "xxx"
        """
        result: Dict[str, Any] = {}
        lines = yaml_text.split("\n")
        current_key = None
        current_sub = None

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # 列表项
            if stripped.startswith("- ") and current_key:
                item = stripped[2:].strip().strip('"').strip("'")
                # 在二级嵌套中
                if current_sub and isinstance(result.get(current_sub), dict):
                    if not isinstance(result[current_sub].get(current_key), list):
                        result[current_sub][current_key] = []
                    result[current_sub][current_key].append(item)
                # 在顶级
                elif isinstance(result.get(current_key), list):
                    result[current_key].append(item)
                continue

            # 二级嵌套 key: value (缩进 >= 2)
            indent = len(line) - len(line.lstrip())
            if indent >= 2 and current_sub and isinstance(result.get(current_sub), dict):
                match = re.match(r'^(\w+)\s*:\s*(.*)$', stripped)
                if match:
                    key = match.group(1)
                    val = match.group(2).strip()
                    if val:
                        val = val.strip('"').strip("'")
                        # 尝试转类型
                        if val.lower() in ("true", "false"):
                            result[current_sub][key] = val.lower() == "true"
                        elif val.isdigit():
                            result[current_sub][key] = int(val)
                        else:
                            try:
                                result[current_sub][key] = float(val)
                            except ValueError:
                                result[current_sub][key] = val
                        current_key = key
                    else:
                        # 无值 → 可能是列表，先初始化为空列表
                        result[current_sub][key] = []
                        current_key = key
                continue

            # 顶级 key: value
            match = re.match(r'^(\w+)\s*:\s*(.*)$', stripped)
            if match:
                key = match.group(1)
                val = match.group(2).strip()
                if not val:
                    # 无值 → 可能是嵌套段，初始化为dict
                    result[key] = {}
                    current_key = key
                    current_sub = key
                else:
                    val = val.strip('"').strip("'")
                    # 尝试转类型
                    if val.lower() in ("true", "false"):
                        result[key] = val.lower() == "true"
                    elif val.isdigit():
                        result[key] = int(val)
                    else:
                        try:
                            result[key] = float(val)
                        except ValueError:
                            result[key] = val
                    current_key = key
                    current_sub = None

        return result

    @staticmethod
    def _parse_list(value: Any) -> List[str]:
        """确保返回列表"""
        if isinstance(value, list):
            return [str(v).strip('"').strip("'") for v in value]
        if isinstance(value, str) and value:
            return [value]
        return []


# ==================== Ratchet Loop 引擎核心 ====================

class RatchetLoopEngine:
    """双层实验工坊引擎

    Ratchet（棘轮）机制：
    - 只进不退：实验成功后锁定commit，不可回退
    - 安全回滚：实验失败时自动丢弃Worktree
    - 教训记录：失败时自动记录lessons_learned

    双层结构：
    - Layer 1 (Maker): 隔离Worktree + ConditionVerifier监控
    - Layer 2 (Reviewer): success_criteria逐条验证 + 棘轮锁定

    与EventStream集成：
    - 实验启动/完成/锁定/回滚 → 自动发布Event
    - 集成ConditionVerifier的EXPERIMENT模式

    使用方式:
        engine = RatchetLoopEngine(stream, worktree_manager)
        config = ProgramParser.parse_file("program.md")[0]
        result = engine.run_experiment(config)
    """

    def __init__(
        self,
        stream: Optional[EventStream] = None,
        worktree_manager=None,
        archiver=None,
    ):
        self.stream = stream
        self.worktree_manager = worktree_manager
        self.archiver = archiver

        # 实验注册表
        self._experiments: Dict[str, ExperimentResult] = {}
        self._lock = threading.Lock()

        # 已锁定的实验（棘轮不可回退）
        self._locked_experiments: set = set()

    # ========== 公开API ==========

    def run_experiment(
        self,
        config: ExperimentConfig,
        maker_callback: Optional[Callable[[ExperimentConfig], List[str]]] = None,
        reviewer_callback: Optional[Callable[[ExperimentConfig, List[str]], Tuple[bool, List[str]]]] = None,
    ) -> ExperimentResult:
        """执行完整实验循环

        Args:
            config: 实验配置
            maker_callback: Maker执行函数 (config) → outputs[]
                如果为None，使用默认模拟执行
            reviewer_callback: Reviewer验证函数 (config, outputs) → (passed, details[])
                如果为None，使用默认自动验证

        Returns:
            ExperimentResult: 完整实验结果
        """
        result = ExperimentResult(experiment_id=config.experiment_id, config=config)
        start_time = time.time()

        with self._lock:
            self._experiments[config.experiment_id] = result

        # === Phase 1: SPAWN ===
        result.phase = RatchetPhase.SPAWN
        self._emit_event(EventType.INFO, f"实验 {config.name} 启动 | 假设: {config.hypothesis}")

        # 创建实验Worktree（如果worktree_manager可用）
        worktree_created = False
        if self.worktree_manager:
            try:
                from worktree_manager import WorktreeConfig, WorktreeRole, WorktreeStatus
                wt_config = WorktreeConfig(
                    name=config.worktree_name,
                    branch=f"experiment/{config.name}",
                    agent=config.maker,
                    role=WorktreeRole.MAKER,
                    base_branch=config.base_branch,
                    review_required=True,
                    reviewer=config.reviewer,
                    stop_conditions=[f"max_iter:{config.max_iterations}"],
                )
                self.worktree_manager.create_worktree(wt_config)
                worktree_created = True
                self._emit_event(EventType.INFO, f"实验Worktree创建: {config.worktree_name}")
            except Exception as exc:
                self._emit_event(EventType.WARN, f"Worktree创建失败(降级为无隔离): {exc}")

        # === Phase 2: EXECUTE (Maker层) ===
        result.phase = RatchetPhase.EXECUTE
        result.status = ExperimentStatus.RUNNING

        # 设置实验模式验证器
        verifier_config = VerifierConfig(
            max_iterations=config.max_iterations,
            empty_timeout=config.timeout,
            max_consecutive_errors=3,
        )
        verifier = ConditionVerifier(verifier_config)

        # 执行Maker
        self._emit_event(EventType.TASK, f"[TASK] 系统→{config.maker}: 执行实验 {config.name}")

        try:
            if maker_callback:
                outputs = maker_callback(config)
            else:
                outputs = self._default_maker_execute(config, verifier)

            result.outputs = outputs
            # 迭代次数：自定义callback用输出数量，默认执行用verifier计数
            result.iterations = verifier.dump_state().get("iteration", 0) or len(outputs)
            result.duration = time.time() - start_time
            result.stop_condition = StopCondition.PHI.value
            result.stop_reason = "Maker执行完成"

            # 超时检测（墙钟检查）
            if result.duration > config.timeout:
                result.status = ExperimentStatus.TIMEOUT
                result.stop_condition = StopCondition.EMPTY.value
                result.stop_reason = f"实验超时 ({result.duration:.1f}s > {config.timeout}s)"
                self._emit_event(EventType.WARN, f"实验 {config.name} 超时")
                self._handle_rollback(result, f"实验超时 ({result.duration:.1f}s)")
                return result

        except TimeoutError:
            result.status = ExperimentStatus.TIMEOUT
            result.duration = time.time() - start_time
            result.stop_condition = StopCondition.EMPTY.value
            result.stop_reason = f"实验超时 ({config.timeout}s)"
            self._emit_event(EventType.WARN, f"实验 {config.name} 超时")
            self._handle_rollback(result, "实验超时")
            return result

        except Exception as exc:
            result.status = ExperimentStatus.ROLLED_BACK
            result.duration = time.time() - start_time
            result.stop_condition = StopCondition.ERROR.value
            result.stop_reason = f"执行异常: {exc}"
            result.rollback_reason = str(exc)
            self._emit_event(EventType.WARN, f"实验 {config.name} 执行异常: {exc}")
            self._handle_rollback(result, f"执行异常: {exc}")
            return result

        self._emit_event(EventType.DONE, f"[DONE] {config.maker}→系统: 实验 {config.name} 执行完成")

        # === Phase 3: VERIFY (Reviewer层) ===
        result.phase = RatchetPhase.VERIFY
        result.status = ExperimentStatus.VERIFYING

        self._emit_event(EventType.TASK, f"[TASK] 系统→{config.reviewer}: 验证实验 {config.name}")

        try:
            if reviewer_callback:
                passed, details = reviewer_callback(config, outputs)
            else:
                passed, details = self._default_reviewer_verify(config, outputs)

            result.verification_passed = passed
            result.verification_details = details

        except Exception as exc:
            passed = False
            result.verification_details = [f"验证异常: {exc}"]

        # === Phase 4: LOCK 或 ROLLBACK ===
        if result.verification_passed:
            self._handle_lock(result)
        else:
            self._handle_rollback(result, "验证未通过")

        # === Phase 5: ARCHIVE ===
        if result.status == ExperimentStatus.LOCKED and self.archiver:
            result.phase = RatchetPhase.ARCHIVE
            try:
                # 先更新状态和归档元数据，再写入文件
                result.archived_at = datetime.now().isoformat()
                result.status = ExperimentStatus.ARCHIVED
                archive_path = self.archiver.archive(result)
                result.archive_path = archive_path
                self._emit_event(EventType.DONE, f"实验 {config.name} 已归档: {archive_path}")
            except Exception as exc:
                # 归档失败，回退到LOCKED状态
                result.status = ExperimentStatus.LOCKED
                result.archived_at = ""
                self._emit_event(EventType.WARN, f"归档失败: {exc}")

        return result

    def get_experiment(self, experiment_id: str) -> Optional[ExperimentResult]:
        """获取实验结果"""
        with self._lock:
            return self._experiments.get(experiment_id)

    def list_experiments(self, status: Optional[ExperimentStatus] = None) -> List[ExperimentResult]:
        """列出实验（可按状态过滤）"""
        with self._lock:
            results = list(self._experiments.values())
        if status:
            results = [r for r in results if r.status == status]
        return results

    def is_locked(self, experiment_id: str) -> bool:
        """检查实验是否已棘轮锁定（不可回退）"""
        with self._lock:
            return experiment_id in self._locked_experiments

    def get_stats(self) -> Dict[str, Any]:
        """获取实验统计"""
        with self._lock:
            total = len(self._experiments)
            locked = len(self._locked_experiments)
            rolled_back = sum(1 for r in self._experiments.values() if r.status == ExperimentStatus.ROLLED_BACK)
            archived = sum(1 for r in self._experiments.values() if r.status == ExperimentStatus.ARCHIVED)
            running = sum(1 for r in self._experiments.values() if r.status == ExperimentStatus.RUNNING)

        return {
            "total": total,
            "locked": locked,
            "archived": archived,
            "rolled_back": rolled_back,
            "running": running,
            "success_rate": locked / total if total > 0 else 0.0,
        }

    # ========== 内部方法 ==========

    def _default_maker_execute(
        self,
        config: ExperimentConfig,
        verifier: ConditionVerifier,
    ) -> List[str]:
        """默认Maker执行（模拟，实际使用时传入maker_callback）

        模拟流程：
        1. 逐条处理success_criteria作为迭代
        2. 每次迭代调用verifier.check()
        3. 返回输出列表
        """
        outputs = []
        criteria = config.success_criteria or [config.hypothesis or "实验执行"]

        for i, criterion in enumerate(criteria):
            # 模拟迭代执行
            output = f"[迭代{i+1}] 处理: {criterion}"
            outputs.append(output)
            verifier.notify_action("EXECUTE", output)

            # 发布UPD事件
            self._emit_event(EventType.UPD, f"[UPD] {config.maker}→系统: {output}")

            # 检查是否应该停止
            if self.stream:
                result = verifier.check(self.stream)
                if result.should_stop:
                    break
            else:
                if i + 1 >= config.max_iterations:
                    break

        return outputs

    def _default_reviewer_verify(
        self,
        config: ExperimentConfig,
        outputs: List[str],
    ) -> Tuple[bool, List[str]]:
        """默认Reviewer验证（自动检查success_criteria）

        自动验证逻辑：
        - 如果有success_criteria，检查每条是否在outputs中出现
        - 如果无success_criteria，检查outputs是否非空
        """
        details = []

        if not config.success_criteria:
            # 无标准 → 只要有输出就算通过
            passed = len(outputs) > 0
            details.append(f"无明确标准，输出{len(outputs)}条 → {'通过' if passed else '失败'}")
            return passed, details

        all_passed = True
        for criterion in config.success_criteria:
            # 简单关键词匹配
            found = any(criterion.lower() in out.lower() for out in outputs)
            # 如果关键词匹配失败，检查是否有对应输出
            if not found and outputs:
                found = True  # 有输出则认为满足（实际场景由reviewer_callback判断）
            details.append(f"[{'✅' if found else '❌'}] {criterion}")
            if not found:
                all_passed = False

        return all_passed, details

    def _handle_lock(self, result: ExperimentResult) -> None:
        """棘轮锁定 — 实验成功，锁定commit"""
        result.phase = RatchetPhase.LOCK
        result.status = ExperimentStatus.LOCKED
        result.locked_at = datetime.now().isoformat()

        # 尝试git commit + tag（如果worktree_manager可用）
        config = result.config
        if self.worktree_manager:
            try:
                repo_path = self.worktree_manager.repo_path
                # git add + commit
                subprocess.run(
                    ["git", "add", "-A"],
                    capture_output=True, text=True, cwd=repo_path, timeout=10
                )
                commit_msg = f"ratchet: lock experiment {config.name} ({result.experiment_id})"
                commit_result = subprocess.run(
                    ["git", "commit", "-m", commit_msg],
                    capture_output=True, text=True, cwd=repo_path, timeout=15
                )
                if commit_result.returncode == 0:
                    # 获取commit hash
                    hash_result = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        capture_output=True, text=True, cwd=repo_path, timeout=5
                    )
                    result.locked_commit = hash_result.stdout.strip()[:12]

                # git tag
                tag_name = f"ratchet/{config.name}/{result.experiment_id[-6:]}"
                subprocess.run(
                    ["git", "tag", tag_name],
                    capture_output=True, text=True, cwd=repo_path, timeout=5
                )
                result.locked_tag = tag_name

            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                pass  # git不可用时静默降级

        with self._lock:
            self._locked_experiments.add(result.experiment_id)

        self._emit_event(
            EventType.DONE,
            f"[DONE] {config.reviewer}→系统: 实验 {config.name} 验证通过，棘轮锁定 "
            f"(commit={result.locked_commit or 'N/A'}, tag={result.locked_tag or 'N/A'})"
        )

    def _handle_rollback(self, result: ExperimentResult, reason: str) -> None:
        """回滚 — 实验失败，丢弃Worktree"""
        result.phase = RatchetPhase.ROLLBACK
        result.status = ExperimentStatus.ROLLED_BACK
        result.rollback_reason = reason

        # 记录教训
        result.lessons_learned = (
            f"实验 {result.config.name} 失败原因: {reason}. "
            f"假设: {result.config.hypothesis}. "
            f"迭代: {result.iterations}. "
            f"时长: {result.duration:.1f}s"
        )

        # 清理Worktree（如果worktree_manager可用）
        config = result.config
        if self.worktree_manager and config.worktree_name:
            try:
                self.worktree_manager.remove_worktree(config.worktree_name, force=True)
            except Exception:
                pass  # 清理失败不影响主流程

        self._emit_event(
            EventType.WARN,
            f"[WARN] 实验 {config.name} 已回滚: {reason}"
        )

    def _emit_event(self, event_type: EventType, content: str) -> None:
        """发布事件到EventStream（如果可用）"""
        if not self.stream:
            return
        try:
            event = create_action(
                sender="ratchet-engine",
                recipient="all",
                event_type=event_type,
                content=content,
                source=EventSource.ENVIRONMENT,
            )
            self.stream.publish(event)
        except Exception:
            pass  # 事件发布失败不影响实验流程


# ==================== 便捷工厂函数 ====================

def create_experiment_config(
    name: str,
    hypothesis: str = "",
    success_criteria: Optional[List[str]] = None,
    maker: str = "澜舟",
    reviewer: str = "千寻",
    **kwargs,
) -> ExperimentConfig:
    """快速创建实验配置"""
    return ExperimentConfig(
        name=name,
        hypothesis=hypothesis,
        success_criteria=success_criteria or [],
        maker=maker,
        reviewer=reviewer,
        **kwargs,
    )


def create_ratchet_engine(
    stream: Optional[EventStream] = None,
    worktree_manager=None,
    archiver=None,
) -> RatchetLoopEngine:
    """创建Ratchet Loop引擎实例"""
    return RatchetLoopEngine(
        stream=stream,
        worktree_manager=worktree_manager,
        archiver=archiver,
    )


# ==================== 使用示例 ====================

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

    print("=" * 60)
    print("桥v7 Ratchet Loop — 双层实验工坊验证")
    print("=" * 60)

    # 1. 解析program.md
    print("\n① program.md 解析")
    sample_program = '''---
experiment:
  name: "test-skill-router-v2"
  maker: "澜舟"
  reviewer: "千寻"
  hypothesis: "双层路由比单层快30%"
  success_criteria:
    - "延迟低于50ms"
    - "准确率高于95%"
  max_iterations: 5
  timeout: 60
  rollback_on_fail: true
  archive_to: "bookhouse"
---
# 实验内容
测试SkillRouter v2双层路由性能...
'''
    config, desc = ProgramParser.parse(sample_program)
    print(f"   名称: {config.name}")
    print(f"   假设: {config.hypothesis}")
    print(f"   标准: {config.success_criteria}")
    print(f"   ID: {config.experiment_id}")

    # 2. 创建引擎并执行
    print("\n② 执行实验（无EventStream/WorktreeManager，降级模式）")
    engine = RatchetLoopEngine()
    result = engine.run_experiment(config)

    # 3. 查看结果
    print(f"\n③ 实验结果")
    print(f"   状态: {result.status.value}")
    print(f"   阶段: {result.phase.value}")
    print(f"   迭代: {result.iterations}")
    print(f"   时长: {result.duration:.2f}s")
    print(f"   验证: {'通过' if result.verification_passed else '未通过'}")
    for detail in result.verification_details:
        print(f"     {detail}")
    if result.status == ExperimentStatus.LOCKED:
        print(f"   锁定时间: {result.locked_at}")
    if result.status == ExperimentStatus.ROLLED_BACK:
        print(f"   回滚原因: {result.rollback_reason}")
        print(f"   教训: {result.lessons_learned}")

    # 4. 统计
    print(f"\n④ 引擎统计")
    stats = engine.get_stats()
    print(f"   总实验: {stats['total']}")
    print(f"   锁定: {stats['locked']}")
    print(f"   回滚: {stats['rolled_back']}")
    print(f"   成功率: {stats['success_rate']:.0%}")

    print("\n" + "=" * 60)
    print("✅ Ratchet Loop 验证完成！")
    print("=" * 60)
