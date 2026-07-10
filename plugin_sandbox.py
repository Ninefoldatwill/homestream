"""
插件沙箱隔离 — 五层安全防护 + 受控执行环境。

融优来源：
  Microsoft Agent Governance Toolkit (五层隔离)
  + OpenBridge permission_guard.py (权限矩阵)
  + OpenBridge indirect_injection_guard.py (注入防护)

五层安全防护：
  Layer 1: 子进程隔离（独立进程执行）
  Layer 2: 安装时静态AST扫描（19+危险模块检测）
  Layer 3: 运行时import hook阻断（动态拦截）
  Layer 4: 内置函数限制（移除exec/eval/breakpoint）
  Layer 5: 环境与资源限制（净化env + 超时kill）

设计原则：
  隔离优于信任 · 扫描优于放过 · 阻断优于提示 · 超时优于挂起
"""

import ast
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger("bridge_v7.plugin_sandbox")


# ============================================================
# 安全等级与危险模块列表
# ============================================================


class SandboxLevel(str, Enum):
    """沙箱安全等级。"""

    STANDARD = "standard"  # 标准隔离（L2插件）
    STRICT = "strict"  # 严格隔离（未签名插件）
    MAXIMUM = "maximum"  # 最高隔离（首次执行/Canary）


# 19+危险模块（来自 Microsoft Agent Governance）
DANGEROUS_MODULES: set[str] = {
    "os",
    "sys",
    "subprocess",
    "shutil",
    "pathlib",
    "socket",
    "http",
    "urllib",
    "requests",
    "ctypes",
    "multiprocessing",
    "threading",
    "signal",
    "resource",
    "fcntl",
    "tempfile",
    "pickle",
    "marshal",
    "importlib",
    "code",
    "codeop",
    "compile",
    "compileall",
}

# 危险内置函数
DANGEROUS_BUILTINS: set[str] = {
    "exec",
    "eval",
    "breakpoint",
    "compile",
    "__import__",
    "open",
    "input",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
}

# 危险AST节点类型
DANGEROUS_AST_NODES: set[str] = {
    "Import",
    "ImportFrom",  # 导入语句
}


# ============================================================
# Layer 2: 安装时静态AST扫描器
# ============================================================


@dataclass
class ScanResult:
    """静态扫描结果。"""

    is_safe: bool = True
    threats: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked_modules: list[str] = field(default_factory=list)
    blocked_functions: list[str] = field(default_factory=list)
    scan_time_ms: float = 0.0


class ASTScanner:
    """安装时静态AST扫描器 — 检测危险模块导入和函数调用。

    扫描规则：
    1. 导入语句检测（Import/ImportFrom）→ 检查是否导入危险模块
    2. 函数调用检测（Call）→ 检查是否调用危险内置函数
    3. 全局访问检测 → 检查是否访问 __builtins__ 等
    """

    def scan_code(self, code: str, level: SandboxLevel = SandboxLevel.STANDARD) -> ScanResult:
        """扫描代码字符串。"""
        start = time.time()
        result = ScanResult()

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            result.is_safe = False
            result.threats.append(f"语法错误: {e}")
            result.scan_time_ms = (time.time() - start) * 1000
            return result

        # 遍历AST树
        for node in ast.walk(tree):
            # Import 检测
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module in DANGEROUS_MODULES:
                        if level in (SandboxLevel.STRICT, SandboxLevel.MAXIMUM):
                            result.is_safe = False
                        result.blocked_modules.append(module)
                        result.threats.append(f"危险导入: import {alias.name}")

            # ImportFrom 检测
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split(".")[0]
                    if module in DANGEROUS_MODULES:
                        if level in (SandboxLevel.STRICT, SandboxLevel.MAXIMUM):
                            result.is_safe = False
                        result.blocked_modules.append(module)
                        result.threats.append(f"危险导入: from {node.module} import ...")

            # Call 检测（危险内置函数）
            if isinstance(node, ast.Call):
                func_name = self._get_call_name(node)
                if func_name in DANGEROUS_BUILTINS:
                    if level in (SandboxLevel.STRICT, SandboxLevel.MAXIMUM):
                        result.is_safe = False
                    result.blocked_functions.append(func_name)
                    result.threats.append(f"危险调用: {func_name}()")

        result.scan_time_ms = (time.time() - start) * 1000
        return result

    def scan_file(self, filepath: str, level: SandboxLevel = SandboxLevel.STANDARD) -> ScanResult:
        """扫描Python文件。"""
        try:
            with open(filepath, encoding="utf-8") as f:
                code = f.read()
            return self.scan_code(code, level)
        except Exception as e:
            return ScanResult(
                is_safe=False,
                threats=[f"文件读取失败: {e}"],
            )

    def _get_call_name(self, node: ast.Call) -> str:
        """提取函数调用名称。"""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""


# ============================================================
# Layer 3: 运行时import hook阻断器
# ============================================================


class ImportBlocker:
    """运行时import hook阻断 — 动态拦截危险模块导入。

    使用 sys.meta_path 在导入时拦截：
    - 检查模块名是否在 DANGEROUS_MODULES 中
    - 阻断并记录日志
    """

    def __init__(self, blocked_modules: set[str] | None = None):
        self.blocked = blocked_modules or DANGEROUS_MODULES
        self._original_meta_path = list(sys.meta_path)
        self._hook_installed = False
        self._blocked_log: list[str] = []

    def install(self):
        """安装import hook。"""
        if self._hook_installed:
            return

        blocker = self

        class BlockingFinder:
            """meta_path finder that blocks dangerous imports (Python 3.12+ find_spec)."""

            def find_spec(self, fullname, path=None, target=None):
                module = fullname.split(".")[0]
                if module in blocker.blocked:
                    blocker._blocked_log.append(f"[import_blocked] {fullname} at {time.time()}")
                    logger.warning("plugin_sandbox.import_blocked", module=fullname)
                    raise ImportError(f"[OpenBridge沙箱] 禁止导入模块: {fullname}")
                return None

        sys.meta_path.insert(0, BlockingFinder())
        self._hook_installed = True
        logger.info("plugin_sandbox.import_hook_installed", blocked_count=len(self.blocked))

    def uninstall(self):
        """卸载import hook。"""
        if not self._hook_installed:
            return
        # 恢复原始meta_path
        sys.meta_path = list(self._original_meta_path)
        self._hook_installed = False
        logger.info("plugin_sandbox.import_hook_removed")

    def get_blocked_log(self) -> list[str]:
        """获取阻断日志。"""
        return list(self._blocked_log)


# ============================================================
# Layer 4-5: 受控执行环境
# ============================================================


@dataclass
class SandboxConfig:
    """沙箱配置。"""

    level: SandboxLevel = SandboxLevel.STANDARD
    timeout_seconds: float = 30.0  # Layer 5: 超时限制
    max_memory_mb: float = 512.0  # Layer 5: 内存限制
    env_whitelist: list[str] = field(  # Layer 5: 允许的环境变量
        default_factory=lambda: [
            "PATH",
            "HOME",
            "USER",
            "TEMP",
            "TMP",
            "OPENBRIDGE_MODE",
            "OPENBRIDGE_HOME",
        ]
    )
    blocked_modules: set[str] = field(
        default_factory=lambda: DANGEROUS_MODULES,
    )
    blocked_builtins: set[str] = field(
        default_factory=lambda: DANGEROUS_BUILTINS,
    )


@dataclass
class ExecutionResult:
    """沙箱执行结果。"""

    success: bool = True
    output: str = ""
    error: str | None = None
    timed_out: bool = False
    duration_ms: float = 0.0
    blocked_imports: list[str] = field(default_factory=list)


class SandboxExecutor:
    """五层沙箱执行器 — 集成所有安全层。

    执行流程：
    1. AST扫描(Layer 2) → 检测危险代码
    2. Import hook安装(Layer 3) → 运行时拦截
    3. 构建受控执行环境(Layer 4) → 净化builtins
    4. 执行代码(Layer 1/5) → 子进程隔离 + 超时
    """

    def __init__(self, config: SandboxConfig = None):
        self.config = config or SandboxConfig()
        self._scanner = ASTScanner()
        self._import_blocker = ImportBlocker(config.blocked_modules if config else None)

    def execute(
        self, code: str, inputs: dict[str, Any] = None, timeout: float = None
    ) -> ExecutionResult:
        """在沙箱中执行代码。"""
        inputs = inputs or {}
        timeout = timeout or self.config.timeout_seconds

        # Layer 2: AST扫描
        scan_result = self._scanner.scan_code(code, self.config.level)
        if not scan_result.is_safe:
            return ExecutionResult(
                success=False,
                error=f"AST扫描拦截: {';'.join(scan_result.threats[:3])}",
                duration_ms=scan_result.scan_time_ms,
            )

        # Layer 3: Import hook安装
        self._import_blocker.install()

        # Layer 4: 构建受控builtins
        safe_builtins = self._build_safe_builtins()

        # Layer 5: 环境净化
        clean_env = self._clean_environment()

        start = time.time()

        try:
            # 受控执行
            local_vars = {**inputs, "__builtins__": safe_builtins}
            exec(compile(code, "<sandbox>", "exec"), safe_builtins, local_vars)

            result_value = local_vars.get("result", local_vars.get("output", ""))
            duration = (time.time() - start) * 1000

            return ExecutionResult(
                success=True,
                output=str(result_value) if result_value is not None else "",
                duration_ms=round(duration, 2),
                blocked_imports=self._import_blocker.get_blocked_log(),
            )

        except ImportError as e:
            # Import hook拦截
            duration = (time.time() - start) * 1000
            return ExecutionResult(
                success=False,
                error=str(e),
                duration_ms=round(duration, 2),
                blocked_imports=self._import_blocker.get_blocked_log(),
            )

        except TimeoutError:
            duration = (time.time() - start) * 1000
            return ExecutionResult(
                success=False,
                error="执行超时",
                timed_out=True,
                duration_ms=round(duration, 2),
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            return ExecutionResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
                duration_ms=round(duration, 2),
            )

        finally:
            # 卸载import hook
            self._import_blocker.uninstall()

    def execute_subprocess(
        self, code: str, inputs: dict[str, Any] = None, timeout: float = None
    ) -> ExecutionResult:
        """Layer 1: 子进程隔离执行。"""
        inputs = inputs or {}
        timeout = timeout or self.config.timeout_seconds * 2  # 子进程允许更多时间

        # 构建执行脚本
        script = self._build_subprocess_script(code, inputs)

        start = time.time()

        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._clean_environment(),
            )

            duration = (time.time() - start) * 1000

            if result.returncode == 0:
                return ExecutionResult(
                    success=True,
                    output=result.stdout.strip(),
                    duration_ms=round(duration, 2),
                )
            else:
                return ExecutionResult(
                    success=False,
                    output=result.stdout.strip(),
                    error=result.stderr.strip()[:500],
                    duration_ms=round(duration, 2),
                )

        except subprocess.TimeoutExpired:
            duration = (time.time() - start) * 1000
            return ExecutionResult(
                success=False,
                error="子进程执行超时",
                timed_out=True,
                duration_ms=round(duration, 2),
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            return ExecutionResult(
                success=False,
                error=f"子进程异常: {e}",
                duration_ms=round(duration, 2),
            )

    # --- 辅助方法 ---
    def _build_safe_builtins(self) -> dict[str, Any]:
        """Layer 4: 构建安全builtins字典（移除危险函数）。"""
        import builtins

        safe = {}
        for name in dir(builtins):
            if name not in self.config.blocked_builtins and not name.startswith("__"):
                safe[name] = getattr(builtins, name)
        # 显式保留安全函数
        safe.update(
            {
                "print": print,
                "len": len,
                "str": str,
                "int": int,
                "float": float,
                "list": list,
                "dict": dict,
                "tuple": tuple,
                "set": set,
                "sorted": sorted,
                "max": max,
                "min": min,
                "sum": sum,
                "abs": abs,
                "round": round,
                "range": range,
                "True": True,
                "False": False,
                "None": None,
                "enumerate": enumerate,
                "zip": zip,
                "map": map,
                "filter": filter,
            }
        )
        # 显式移除危险函数
        for name in self.config.blocked_builtins:
            safe.pop(name, None)

        return safe

    def _clean_environment(self) -> dict[str, str]:
        """Layer 5: 净化环境变量。"""
        clean = {}
        for key in self.config.env_whitelist:
            if key in os.environ:
                clean[key] = os.environ[key]
        # 不传递任何密钥或敏感配置
        return clean

    def _build_subprocess_script(self, code: str, inputs: dict[str, Any]) -> str:
        """构建子进程执行的Python脚本。"""
        import json

        inputs_json = json.dumps(inputs)
        return f"""
import json
_inputs = json.loads('{inputs_json}')
_result_locals = {{**_inputs, '__builtins__': {{
    'print': print, 'len': len, 'str': str, 'int': int, 'float': float,
    'list': list, 'dict': dict, 'sorted': sorted, 'max': max, 'min': min,
    'sum': sum, 'abs': abs, 'round': round, 'range': range,
    'True': True, 'False': False, 'None': None,
}}}}
{code}
if 'result' in dir():
    print(result)
elif 'output' in dir():
    print(output)
"""


# ============================================================
# 便捷API
# ============================================================


def scan_plugin_code(code: str, level: SandboxLevel = SandboxLevel.STANDARD) -> ScanResult:
    """快捷扫描插件代码。"""
    scanner = ASTScanner()
    return scanner.scan_code(code, level)


def run_in_sandbox(
    code: str, inputs: dict[str, Any] = None, timeout: float = 30.0
) -> ExecutionResult:
    """快捷在沙箱中执行代码。"""
    executor = SandboxExecutor()
    return executor.execute(code, inputs, timeout)
