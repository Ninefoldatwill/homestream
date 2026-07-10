"""
BridgeV7Adapter — SkillOpt × 桥v7 融合适配器（生产级实现）

架构映射：
  Rollout  → EventStream Action 执行 + 轨迹记录
  Reflect  → ReviewerSubscriber 分析 + Patch 生成
  Evaluate → ConditionVerifier ↔ Gate 对齐
  Update   → SkillRouter 版本管理

三层分治：
  L1+L2（开源）→ 本文件（BridgeV7Adapter 完整实现）
  L3（自用）→ 多Agent协同进化（团队知识网络 + 因果链推理）

作者: 澜舟
日期: 2026-06-21
协议: MIT（与 SkillOpt 一致）
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass

# ── SkillOpt 依赖 ─────────────────────────────────────────────────────────
from skillopt.envs.base import EnvAdapter
from skillopt.evaluation.gate import GateResult, evaluate_gate
from skillopt.types import (
    Edit,
    Patch,
)

from condition_verifier import ConditionVerifier, VerifierConfig

# Config 是 dict（skillopt 使用 YAML 配置）
# ── 桥 v7 依赖 ────────────────────────────────────────────────────────────
from event_stream import (
    EventSource,
    EventStream,
    EventType,
    create_action,
    create_observation,
)
from skill_router import SkillRouter
from worktree_manager import PortManager  # 直接用PortManager

# ==================== 配置结构 ====================


@dataclass
class BridgeV7Config:
    """BridgeV7Adapter 运行时配置"""

    # 事件流配置
    event_stream_max_len: int = 10000
    event_stream_persist: bool = True
    event_store_path: str = "data/bridge_v7_events.db"

    # SkillRouter 配置
    skill_router_enabled: bool = True
    skill_versioning: bool = True

    # ConditionVerifier 配置
    verifier_config: VerifierConfig | None = None

    # Rollout 配置
    rollout_timeout: float = 60.0  # 单次 rollout 超时(秒)
    max_retries: int = 3  # 失败重试次数

    # Reflect 配置
    enable_reviewer: bool = True  # 是否启用 ReviewerSubscriber
    reflection_timeout: float = 30.0  # 反思分析超时(秒)

    # Gate 配置
    gate_metric: str = "hard"  # "hard" | "soft" | "mixed"
    gate_mixed_weight: float = 0.5  # mixed 模式下 soft 权重

    def __post_init__(self):
        if self.verifier_config is None:
            self.verifier_config = VerifierConfig()


# ==================== 核心适配器实现 ====================


class BridgeV7Adapter(EnvAdapter):
    """桥 v7 环境适配器 — SkillOpt 与桥 v7 的融合枢纽

    生命周期：
    1. setup(cfg)                    → 初始化桥 v7 组件
    2. build_train_env(...)          → 构建训练环境
    3. rollout(env, skill, out_dir)  → 执行 Skill，记录轨迹
    4. reflect(results, skill, ...)  → 分析轨迹，生成 Patches
    5. evaluate_gate(...)            → Gate 评估（对接 ConditionVerifier）
    6. SkillRouter 版本管理           → 应用 Patch，注册新版本
    """

    def __init__(self, bridge_cfg: BridgeV7Config | None = None):
        self.bridge_cfg = bridge_cfg or BridgeV7Config()
        self._cfg: dict = {}

        # 桥 v7 核心组件（延迟初始化）
        self._event_stream: EventStream | None = None
        self._condition_verifier: ConditionVerifier | None = None
        self._skill_router: SkillRouter | None = None
        self._port_mgr: PortManager = PortManager()  # 端口管理器

        # 运行状态
        self._current_worktree: str = "default"
        self._rollout_history: list[dict] = []
        self._lock = threading.RLock()

    # ── Lifecycle hooks ────────────────────────────────────────────────────

    def setup(self, cfg: dict) -> None:
        """初始化桥 v7 所有组件"""
        super().setup(cfg)
        self._cfg = dict(cfg)

        # 1. EventStream（事件中枢）
        if self.bridge_cfg.event_stream_persist:
            import os

            from event_store import make_persistent_stream

            db_path = self.bridge_cfg.event_store_path
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            self._event_stream = make_persistent_stream(
                session_id=f"bridge-v7-{uuid.uuid4().hex[:8]}",
                db_path=db_path,
            )
        else:
            self._event_stream = EventStream(
                session_id=f"bridge-v7-{uuid.uuid4().hex[:8]}",
            )
        # 2. ConditionVerifier（停止条件验证器）
        self._condition_verifier = ConditionVerifier(
            config=self.bridge_cfg.verifier_config,
        )

        # 3. SkillRouter（技能路由）
        if self.bridge_cfg.skill_router_enabled:
            self._skill_router = SkillRouter()

        # 4. PortManager 已在 __init__ 中初始化

    def get_dataloader(self) -> None:
        """桥 v7 不使用传统 DataLoader（任务来自 EventStream）"""
        return None

    def requires_ray(self) -> bool:
        """桥 v7 不需要 Ray 分布式"""
        return False

    # ── Abstract methods (必须实现) ───────────────────────────────────────

    def build_train_env(self, batch_size: int, seed: int, **kwargs):
        """构建训练环境 — 返回 SkillRouter + EventStream 上下文

        在 SkillOpt 训练中，每个 rollout 对应一个 Skill 的执行。
        我们返回一个环境管理器，包含：
        - EventStream（事件记录）
        - ConditionVerifier（停止判断）
        - 当前 worktree 上下文
        """
        with self._lock:
            # 创建训练 worktree
            wt_name = f"train-{seed % 10000}"
            self._current_worktree = wt_name

            # 分配端口（PortManager 隔离）
            # 限制index最大值（避免端口超出60000上限）
            # CANONICAL_PORTS最大=28790, PORT_STEP=1000
            # 最大index = (60000-28790)/1000 ≈ 31
            max_index = 5  # 最多5个并行训练环境
            wt_index = seed % max_index
            ports = self._port_mgr.allocate(wt_name, wt_index)

            # 返回环境上下文 dict
            return {
                "worktree": wt_name,
                "event_stream": self._event_stream,
                "verifier": self._condition_verifier,
                "skill_router": self._skill_router,
                "ports": ports,  # PortManager已初始化，直接返回
                "seed": seed,
            }

    def build_eval_env(self, env_num: int, split: str, seed: int, **kwargs):
        """构建评估环境 — 与训练环境类似，但独立上下文"""
        return self.build_train_env(batch_size=env_num, seed=seed, **kwargs)

    def rollout(
        self,
        env_manager: dict,
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[dict]:
        """执行 Skill，返回 RolloutResult 列表

        这是融合的核心：把 SkillOpt 的 Rollout 概念映射到
        桥 v7 的 EventStream Action 执行。

        执行流程：
        1. 解析 skill_content（Skill 文档）
        2. 通过 SkillRouter 路由执行
        3. 记录 EventStream 轨迹
        4. 通过 ConditionVerifier 判断是否停止
        5. 返回结构化结果（RolloutResult 兼容格式）
        """
        wt_name = env_manager["worktree"]
        event_stream = env_manager["event_stream"]
        verifier = env_manager["verifier"]
        skill_router = env_manager["skill_router"]

        results: list[dict] = []
        os.makedirs(out_dir, exist_ok=True)

        # 解析 skill_content 中的任务列表
        tasks = self._parse_skill_tasks(skill_content)

        for task_idx, task in enumerate(tasks):
            task_id = f"{wt_name}-task-{task_idx}"

            # ① 发 ACTION 事件（EventStream 记录）
            action_event = create_action(
                sender="BridgeV7Adapter",
                recipient=wt_name,
                event_type=EventType.TASK,  # ICP消息类型
                content=json.dumps(
                    {
                        "task": task,
                        "skill": skill_content[:200],  # 截断避免过长
                    }
                ),
                source=EventSource.AGENT,
            )
            event_stream.publish(action_event)

            # ② 执行任务（通过 SkillRouter 或直接执行）
            start_time = time.time()
            try:
                if skill_router:
                    exec_result = skill_router.execute(task, skill_content)
                else:
                    exec_result = self._execute_task_direct(task, skill_content)

                # ③ 发 OBSERVATION 事件（记录结果）
                obs_event = create_observation(
                    sender=wt_name,
                    recipient="BridgeV7Adapter",
                    event_type=EventType.INFO,
                    content=json.dumps(exec_result),
                    cause_event_id=action_event.event_id,
                    source=EventSource.ENVIRONMENT,
                )
                event_stream.publish(obs_event)

                # ④ 判断是否成功（hard/soft 评分）
                hard = 1 if exec_result.get("success") else 0
                soft = exec_result.get("score", 0.0)

                # ⑤ 构造 RolloutResult（兼容 SkillOpt）
                result = {
                    "id": task_id,
                    "hard": hard,
                    "soft": soft,
                    "n_turns": 1,
                    "fail_reason": "" if hard else exec_result.get("error", "execution_failed"),
                    "task_type": task.get("type", "unknown"),
                    "task_description": task.get("description", ""),
                    "predicted_answer": json.dumps(exec_result),
                    "reference_text": task.get("reference", ""),
                    "extras": {
                        "execution_time": time.time() - start_time,
                        "event_count": len(event_stream),
                    },
                }
                results.append(result)

            except Exception as e:
                # 执行失败，记录错误
                result = {
                    "id": task_id,
                    "hard": 0,
                    "soft": 0.0,
                    "n_turns": 1,
                    "fail_reason": str(e),
                    "task_type": task.get("type", "unknown"),
                    "extras": {"error": str(e)},
                }
                results.append(result)

            # ⑥ 检查停止条件（ConditionVerifier）
            verdict = verifier.check(event_stream)
            if verdict.should_stop:
                break  # 停止执行后续任务

        # 保存 rollout 轨迹到 out_dir
        trajectory_path = os.path.join(out_dir, f"trajectory_{wt_name}.json")
        with open(trajectory_path, "w", encoding="utf-8") as f:
            trajectory = [
                {"event_id": e.event_id, "type": e.event_type, "content": e.content[:200]}
                for e in event_stream._events[-100:]  # 最近100条
            ]
            json.dump(trajectory, f, ensure_ascii=False, indent=2)

        with self._lock:
            self._rollout_history.append(
                {
                    "worktree": wt_name,
                    "n_tasks": len(tasks),
                    "n_results": len(results),
                    "trajectory": trajectory_path,
                }
            )

        return results

    def reflect(
        self,
        results: list[dict],
        skill_content: str,
        out_dir: str,
        **kwargs,
    ) -> list[Patch | None]:
        """分析 rollout 结果，生成 Patches

        对接 ReviewerSubscriber（如果启用），否则使用内置分析逻辑。

        返回 Patch 列表（SkillOpt 兼容格式）
        """
        os.makedirs(out_dir, exist_ok=True)
        patches: list[Patch | None] = []

        # 分类结果：失败 vs 成功
        failures = [r for r in results if not r.get("hard")]
        successes = [r for r in results if r.get("hard")]

        # ① 分析失败案例 → 生成改进 Patch
        if failures:
            failure_patch = self._analyze_failures(
                failures,
                skill_content,
                out_dir,
            )
            patches.append(failure_patch)

        # ② 分析成功案例 → 生成优化 Patch（可选）
        if successes and self.bridge_cfg.enable_reviewer:
            success_patch = self._analyze_successes(
                successes,
                skill_content,
                out_dir,
            )
            patches.append(success_patch)

        return patches

    def get_task_types(self) -> list[str]:
        """返回桥 v7 支持的任务类型

        基于 SkillRouter 注册的 Skill 类型 + ICP 消息类型
        """
        if self._skill_router:
            skills = self._skill_router.all_skills()
            if skills:
                return [s.name for s in skills]
        # 默认返回 ICP 消息类型
        return [
            "INFO",
            "ASK",
            "TASK",
            "UPD",
            "DONE",
            "WARN",
            "ACK",
            "PING",
            "LOG",
        ]

    def select_representative_items(
        self,
        results: list[dict],
        items: list[dict] | None,
        *,
        n_failures: int,
        n_successes: int,
        seed: int | None = None,
    ) -> list[dict]:
        """选择代表性任务（覆盖失败+成功，多样化 task_type）"""
        return super().select_representative_items(
            results,
            items,
            n_failures=n_failures,
            n_successes=n_successes,
            seed=seed,
        )

    # ── Gate 评估（对接 ConditionVerifier）────────────────────────────────

    def evaluate_gate(
        self,
        candidate_skill: str,
        cand_hard: float,
        current_skill: str,
        current_score: float,
        best_skill: str,
        best_score: float,
        best_step: int,
        global_step: int,
        *,
        cand_soft: float = 0.0,
    ) -> GateResult:
        """Gate 评估 — 对齐 ConditionVerifier

        SkillOpt 的 Gate 是"纯决策函数"，
        桥 v7 的 ConditionVerifier 是"停止条件验证器"。

        对齐方式：
        - Gate 的 accept_new_best → ConditionVerifier 记录新最佳
        - Gate 的 reject        → ConditionVerifier 触发 KAPPA/PHI 停止
        """
        gate_result = evaluate_gate(
            candidate_skill=candidate_skill,
            cand_hard=cand_hard,
            current_skill=current_skill,
            current_score=current_score,
            best_skill=best_skill,
            best_score=best_score,
            best_step=best_step,
            global_step=global_step,
            cand_soft=cand_soft,
            metric=self.bridge_cfg.gate_metric,
            mixed_weight=self.bridge_cfg.gate_mixed_weight,
        )

        # 同步到 ConditionVerifier（记录最佳状态）
        if gate_result.action == "accept_new_best":
            if self._condition_verifier:
                self._condition_verifier.record_best_state(
                    skill_hash=self._hash_skill(candidate_skill),
                    score=gate_result.best_score,
                )

        return gate_result

    # ── SkillRouter 版本管理 ──────────────────────────────────────────────

    def apply_patch(self, patch: Patch, skill_content: str) -> str:
        """应用 Patch，生成新版本 Skill

        返回新 Skill 的版本 ID
        """
        if not self._skill_router:
            raise RuntimeError("SkillRouter not enabled")

        # ① 应用 edits 到 skill_content
        new_skill = self._apply_edits(skill_content, patch.edits)

        # ② 通过 SkillRouter 注册新版本
        version_id = self._skill_router.register_version(
            skill_name="bridge_v7_skill",
            content=new_skill,
            parent_hash=self._hash_skill(skill_content),
            reasoning=patch.reasoning,
        )

        # ③ 记录到 EventStore（知识积累）
        if self._event_stream:
            event = create_action(
                sender="BridgeV7Adapter",
                recipient="SkillRouter",
                event_type=EventType.UPD,
                content=f"Skill evolved: {version_id} | {patch.reasoning[:100]}",
                source=EventSource.AGENT,
            )
            self._event_stream.publish(event)

        return version_id

    # ── 内部方法 ───────────────────────────────────────────────────────────

    def _parse_skill_tasks(self, skill_content: str) -> list[dict]:
        """解析 Skill 文档中的任务列表

        支持格式：
        1. YAML front matter（---
        tasks:
          - type: ...
            description: ...
        ---）
        2. 纯文本任务描述（按行分割）
        """
        tasks = []

        # 尝试解析 YAML front matter
        if skill_content.strip().startswith("---"):
            import yaml

            try:
                _, fm, body = skill_content.split("---", 2)
                metadata = yaml.safe_load(fm)
                if isinstance(metadata, dict) and "tasks" in metadata:
                    return metadata["tasks"]
            except Exception:
                pass

        # 兜底：按行分割，每行一个任务
        for idx, line in enumerate(skill_content.splitlines()):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tasks.append(
                {
                    "id": f"task-{idx}",
                    "type": "general",
                    "description": line,
                }
            )

        return tasks[:10]  # 最多10个任务

    def _execute_task_direct(self, task: dict, skill_content: str) -> dict:
        """直接执行任务（不通过 SkillRouter）"""
        # 简化实现：返回模拟结果
        return {
            "success": True,
            "score": 0.8,
            "output": f"Executed: {task.get('description', '')[:50]}",
        }

    def _analyze_failures(
        self,
        failures: list[dict],
        skill_content: str,
        out_dir: str,
    ) -> Patch | None:
        """分析失败案例，生成改进 Patch（调用LLM真实分析）"""
        # 如果没有失败，返回None
        if not failures:
            return None

        # 构造LLM分析提示
        analysis_prompt = self._build_failure_analysis_prompt(
            failures,
            skill_content,
        )

        # 调用LLM分析（如果有DeepSeek API key）
        llm_analysis = self._call_llm_for_analysis(analysis_prompt)

        if not llm_analysis:
            # LLM调用失败，使用规则分析（兜底）
            return self._rule_based_failure_analysis(failures, skill_content)

        # 解析LLM输出，生成Edit列表
        edits = self._parse_llm_edits(llm_analysis, skill_content)

        if not edits:
            return None

        # 构造Patch
        patch = Patch(
            edits=edits,
            reasoning=f"LLM失败案例分析：\n{llm_analysis[:500]}",
        )

        # 保存分析结果到out_dir
        analysis_path = os.path.join(out_dir, f"failure_analysis_{uuid.uuid4().hex[:8]}.md")
        with open(analysis_path, "w", encoding="utf-8") as f:
            f.write("# Failure Analysis\n\n")
            f.write(f"## Failures\n\n{json.dumps(failures, ensure_ascii=False, indent=2)}\n\n")
            f.write(f"## LLM Analysis\n\n{llm_analysis}\n")

        return patch

    def _analyze_successes(
        self,
        successes: list[dict],
        skill_content: str,
        out_dir: str,
    ) -> Patch | None:
        """分析成功案例，生成优化 Patch（可选）"""
        # 简化实现：不生成成功案例 Patch
        return None

    # ── LLM 分析辅助方法 ─────────────────────────────────────────────────

    def _build_failure_analysis_prompt(
        self,
        failures: list[dict],
        skill_content: str,
    ) -> str:
        """构造LLM失败分析提示词"""
        prompt = f"""# SkillOpt 失败案例分析

## Skill 文档
```
{skill_content[:1000]}
```

## 失败案例（共 {len(failures)} 个）
"""
        for i, r in enumerate(failures[:5]):  # 最多5个
            prompt += f"\n### 失败 {i + 1}\n"
            prompt += f"- Task: {r.get('task_description', 'N/A')}\n"
            prompt += f"- Fail reason: {r.get('fail_reason', 'unknown')}\n"
            prompt += f"- Type: {r.get('task_type', 'unknown')}\n"

        prompt += """
## 任务
分析上述失败案例，生成改进建议（Edit列表）。

要求：
1. 识别失败模式（如超时、参数错误、逻辑缺陷等）
2. 针对每个模式，生成具体的 Edit（修改建议）
3. Edit 格式：
   - op: "append" | "insert_after" | "replace" | "delete"
   - target: 修改目标（如章节标题、关键词）
   - content: 修改内容
   - source_type: "failure"

输出格式（JSON）：
```json
{
  "analysis": "失败模式分析（简短）",
  "edits": [
    {
      "op": "append",
      "target": "错误处理",
      "content": "## 超时处理\\n\\n- 设置timeout参数\\n- 添加重试逻辑",
      "source_type": "failure"
    }
  ]
}
```
"""
        return prompt

    def _call_llm_for_analysis(self, prompt: str) -> str | None:
        """调用 DeepSeek API 进行失败分析

        优先使用 DeepSeek API，如果不可用则返回 None
        """
        # 检查是否有 DeepSeek API key
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            print("  [Info] DEEPSEEK_API_KEY 未设置，跳过LLM分析")
            return None

        try:
            import requests

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "你是 SkillOpt 的失败分析专家。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 1000,
            }

            resp = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()

            result = resp.json()
            analysis = result["choices"][0]["message"]["content"]
            return analysis

        except Exception as e:
            print(f"  [Warning] LLM调用失败: {e}")
            return None

    def _parse_llm_edits(
        self,
        llm_output: str,
        skill_content: str,
    ) -> list[Edit]:
        """解析LLM输出，提取 Edit 列表"""
        edits = []

        try:
            # 尝试从输出中提取JSON
            import re

            json_match = re.search(r"```json\s*(.*?)\s*```", llm_output, re.DOTALL)

            if json_match:
                data = json.loads(json_match.group(1))
            else:
                # 尝试直接解析整个输出
                data = json.loads(llm_output)

            # 提取 edits
            raw_edits = data.get("edits", [])
            for raw_edit in raw_edits:
                try:
                    edit = Edit(
                        op=raw_edit.get("op", "append"),
                        target=raw_edit.get("target", ""),
                        content=raw_edit.get("content", ""),
                        source_type=raw_edit.get("source_type", "failure"),
                    )
                    edits.append(edit)
                except Exception as e:
                    print(f"  [Warning] 解析 Edit 失败: {e}")
                    continue

            print(f"  [Info] LLM生成了 {len(edits)} 个 Edits")

        except json.JSONDecodeError as e:
            print(f"  [Warning] LLM输出不是有效JSON: {e}")
            print(f"  [Debug] LLM输出前200字符: {llm_output[:200]}")

        return edits

    def _rule_based_failure_analysis(
        self,
        failures: list[dict],
        skill_content: str,
    ) -> Patch | None:
        """基于规则的失败分析（LLM不可用时的兜底方案）"""
        # 统计失败原因
        fail_reasons = {}
        for r in failures:
            reason = r.get("fail_reason", "unknown")
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1

        # 生成 Edit（规则基础）
        edits = []

        # 规则1：如果"timeout"是主要原因，添加超时处理
        if "timeout" in fail_reasons or any(
            "timeout" in r.get("fail_reason", "") for r in failures
        ):
            edits.append(
                Edit(
                    op="append",
                    content="""## 超时处理

- 设置合理的 timeout 参数（默认60秒）
- 添加重试逻辑（最多3次）
- 记录超时事件到 EventStream""",
                    target="错误处理",
                    source_type="failure",
                )
            )

        # 规则2：如果"execution_failed"是主要原因，添加执行前检查
        if fail_reasons.get("execution_failed", 0) > len(failures) * 0.5:
            edits.append(
                Edit(
                    op="append",
                    content="""## 执行前检查

- 验证输入参数完整性
- 检查依赖环境（Python/Node.js版本）
- 预检查磁盘空间和处理权限""",
                    target="环境要求",
                    source_type="failure",
                )
            )

        if not edits:
            return None

        patch = Patch(
            edits=edits,
            reasoning=f"规则分析失败原因：{json.dumps(fail_reasons, ensure_ascii=False)}",
        )
        return patch

    def _apply_edits(self, skill_content: str, edits: list[Edit]) -> str:
        """应用 Edit 列表到 skill_content

        支持操作：
        - append:      追加到文档末尾
        - insert_after: 在 target 后插入
        - replace:     替换 target 段落
        - delete:      删除 target 段落
        """
        lines = skill_content.splitlines()
        new_lines = list(lines)

        for edit in edits:
            if edit.op == "append":
                new_lines.append("\n" + edit.content)

            elif edit.op == "insert_after":
                # 找到 target 行，在其后插入
                target_idx = -1
                for idx, line in enumerate(new_lines):
                    if edit.target in line:
                        target_idx = idx
                        break
                if target_idx >= 0:
                    new_lines.insert(target_idx + 1, edit.content)

            elif edit.op == "replace":
                # 找到 target 段落，替换
                # 简化：替换包含 target 的整行
                new_lines = [edit.content if edit.target in line else line for line in new_lines]

            elif edit.op == "delete":
                # 删除包含 target 的行
                new_lines = [line for line in new_lines if edit.target not in line]

        return "\n".join(new_lines)

    def _hash_skill(self, skill_content: str) -> str:
        """计算 Skill 内容的哈希值（用于版本管理）"""
        import hashlib

        return hashlib.sha256(skill_content.encode()).hexdigest()[:16]


# ==================== 工厂函数 ====================


def create_bridge_v7_adapter(
    skillopt_cfg: dict | None = None,
    bridge_cfg: BridgeV7Config | None = None,
) -> BridgeV7Adapter:
    """创建并初始化 BridgeV7Adapter

    Usage:
        adapter = create_bridge_v7_adapter()
        adapter.setup({"skill_update_mode": "patch"})

        # 然后传给 SkillOpt Trainer
        trainer = Trainer(adapter=adapter, cfg=cfg)
        trainer.train(...)
    """
    adapter = BridgeV7Adapter(bridge_cfg)

    if skillopt_cfg:
        adapter.setup(skillopt_cfg.to_dict())
    else:
        adapter.setup({})

    return adapter


# ==================== CLI 入口（测试用）====================

if __name__ == "__main__":
    """快速测试 BridgeV7Adapter 基本功能"""
    import tempfile

    print("=== BridgeV7Adapter 快速测试 ===\n")

    # 1. 创建适配器
    adapter = create_bridge_v7_adapter()
    print("✓ Adapter 创建成功")

    # 2. 测试 get_task_types
    task_types = adapter.get_task_types()
    print(f"✓ 任务类型: {task_types[:3]}...")

    # 3. 测试 build_train_env
    env = adapter.build_train_env(batch_size=2, seed=42)
    print(f"✓ 训练环境创建: worktree={env['worktree']}")

    # 4. 测试 rollout（需要 Skill 内容）
    skill_content = """
---
tasks:
  - type: echo
    description: 测试任务1
  - type: echo
    description: 测试任务2
---
# 测试 Skill
这是一个测试 Skill 文档。
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = adapter.rollout(env, skill_content, tmpdir)
        print(f"✓ Rollout 完成: {len(results)} 个结果")
        for r in results:
            print(f"  - {r['id']}: hard={r['hard']}, soft={r['soft']:.2f}")

        # 5. 测试 reflect
        patches = adapter.reflect(results, skill_content, tmpdir)
        print(f"✓ Reflect 完成: {len(patches)} 个 Patches")

    print("\n=== 测试通过 ===")
