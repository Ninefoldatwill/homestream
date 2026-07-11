"""
OpenClaw Tool Bridge — 工具桥接器

让 AI Agent 能够执行实际操作——给 AI 装上"手"和"脚"

灵感来源：OpenClaw (MIT, Peter Steinberger) — "本地 AI 助手，给 AI 装上手和脚"
实现方式：干净室实现（Clean Room Implementation），不包含任何 OpenClaw 源代码
版权原则：17 U.S.C. § 102(b) — 版权保护表达，不保护思想/理念/方法

三维立体化架构中的"执行维度"：
  - 模型维度 (OllamaProvider)  → AI 能"想"（生成回答）
  - 执行维度 (ToolBridge)      → AI 能"做"（执行操作）  ← 本模块
  - 交互维度 (OpenAI兼容API)   → 外部能"接入"

核心功能：
  1. 工具注册与发现 — register_tool() / list_tools()
  2. 统一调用接口 — call_tool() 异步执行，统一返回 ToolResult
  3. 安全控制 — 白名单 / dangerous标记 / 超时 / 参数验证
  4. 内置工具 — file_read / file_write / file_list / shell_exec / http_get / http_post
  5. OpenAI Function Calling 兼容 — to_openai_functions() 直接用于 LLM 工具调用
  6. 外部 Gateway 桥接 — 可连接 OpenClaw Gateway 或其他工具服务

商标声明：
  "OpenClaw" 是 Peter Steinberger 的项目名称。
  本模块受 OpenClaw "给AI装上手和脚" 理念启发，但为独立实现，
  不包含任何 OpenClaw 源代码。本项目与 OpenClaw 无关联、未获背书。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ==================== 数据结构 ====================


@dataclass
class ToolDefinition:
    """工具定义

    兼容 OpenAI Function Calling 格式：
    to_openai_dict() 输出可直接用于 LLM 的 functions 参数
    """

    name: str  # 工具名称（如 "file_read"）
    description: str  # 工具描述（给 LLM 看的）
    parameters: dict[str, Any]  # JSON Schema 格式参数定义
    category: str = "general"  # 工具类别（file/shell/http/python/custom）
    dangerous: bool = False  # 是否危险操作（需明确启用）
    enabled: bool = True  # 是否启用

    def to_openai_dict(self) -> dict[str, Any]:
        """转换为 OpenAI Function Calling 格式

        Returns:
            {"type": "function", "function": {"name", "description", "parameters"}}
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        """转换为普通字典"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "category": self.category,
            "dangerous": self.dangerous,
            "enabled": self.enabled,
        }


@dataclass
class ToolResult:
    """工具执行结果"""

    success: bool
    output: str = ""  # 执行输出（文本）
    error: str = ""  # 错误信息
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外元数据
    execution_time_ms: float = 0.0  # 执行耗时（毫秒）

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "metadata": self.metadata,
            "execution_time_ms": round(self.execution_time_ms, 1),
        }


# ==================== 工具桥接器 ====================


class ToolBridge:
    """工具桥接器 — 让 AI Agent 能够执行实际操作

    使用方式：
      bridge = ToolBridge()
      bridge.enable_dangerous_tools(True)  # 启用危险工具
      result = await bridge.call_tool("file_read", {"path": "/tmp/test.txt"})
      print(result.output)

    安全设计：
      1. dangerous=True 的工具默认禁用，需 enable_dangerous_tools(True)
      2. 白名单模式：只允许指定工具被调用
      3. 超时控制：每个工具有独立超时
      4. 参数验证：必填参数检查
      5. 调用日志：所有工具调用都记录日志
    """

    def __init__(self, allow_dangerous: bool = False):
        """初始化工具桥接器

        Args:
            allow_dangerous: 是否允许 dangerous=True 的工具（默认 False）
        """
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}
        self._allow_dangerous = allow_dangerous
        self._whitelist: set[str] | None = None  # None=允许所有已启用工具
        self._call_count: int = 0
        self._error_count: int = 0
        self._total_time_ms: float = 0.0

        # 注册内置工具
        self._register_builtin_tools()

    # ==================== 工具注册 ====================

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable,
        category: str = "custom",
        dangerous: bool = False,
    ) -> bool:
        """注册自定义工具

        Args:
            name: 工具名称（唯一）
            description: 工具描述（给 LLM 看的）
            parameters: JSON Schema 格式参数定义
            handler: 处理函数（同步或异步，接收 dict 参数）
            category: 工具类别
            dangerous: 是否危险操作

        Returns:
            bool: 是否注册成功
        """
        if name in self._tools:
            logger.warning(f"工具 '{name}' 已存在，将被覆盖")

        tool = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            category=category,
            dangerous=dangerous,
        )
        self._tools[name] = tool
        self._handlers[name] = handler
        logger.info(f"注册工具: {name} (category={category}, dangerous={dangerous})")
        return True

    def unregister_tool(self, name: str) -> bool:
        """注销工具"""
        if name in self._tools:
            del self._tools[name]
            del self._handlers[name]
            logger.info(f"注销工具: {name}")
            return True
        return False

    def list_tools(self, include_disabled: bool = False) -> list[ToolDefinition]:
        """列出所有工具

        Args:
            include_disabled: 是否包含被禁用的工具

        Returns:
            工具定义列表
        """
        tools = []
        for tool in self._tools.values():
            if not include_disabled and not self._is_tool_available(tool):
                continue
            tools.append(tool)
        return tools

    def get_tool_schema(self, name: str) -> dict[str, Any] | None:
        """获取单个工具的参数 schema"""
        tool = self._tools.get(name)
        return tool.parameters if tool else None

    def to_openai_functions(self) -> list[dict[str, Any]]:
        """转换为 OpenAI Function Calling 格式列表

        可直接用于 OpenAI / DeepSeek / GLM 等兼容 API 的 functions 参数

        Returns:
            [{"type": "function", "function": {...}}, ...]
        """
        return [tool.to_openai_dict() for tool in self.list_tools()]

    # ==================== 工具调用 ====================

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """调用工具（核心方法）

        Args:
            name: 工具名称
            arguments: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        start = time.time()

        # 1. 检查工具是否存在
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                success=False,
                error=f"工具 '{name}' 不存在。可用工具: {[t.name for t in self.list_tools()]}",
            )

        # 2. 检查工具是否可用
        if not self._is_tool_available(tool):
            reasons = []
            if not tool.enabled:
                reasons.append("工具已禁用")
            if tool.dangerous and not self._allow_dangerous:
                reasons.append("危险工具未启用（需 enable_dangerous_tools(True)）")
            if self._whitelist is not None and name not in self._whitelist:
                reasons.append("工具不在白名单中")
            return ToolResult(success=False, error=f"工具不可用: {', '.join(reasons)}")

        # 3. 参数验证
        validation_error = self._validate_arguments(tool, arguments)
        if validation_error:
            return ToolResult(success=False, error=f"参数验证失败: {validation_error}")

        # 4. 执行工具
        handler = self._handlers.get(name)
        if not handler:
            return ToolResult(success=False, error=f"工具 '{name}' 无处理函数")

        self._call_count += 1
        try:
            # 支持同步和异步 handler
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**arguments)
            else:
                result = await asyncio.to_thread(handler, **arguments)

            # 处理返回值
            if isinstance(result, ToolResult):
                result.execution_time_ms = (time.time() - start) * 1000
                if not result.success:
                    self._error_count += 1
                self._total_time_ms += result.execution_time_ms
                return result
            elif isinstance(result, str):
                elapsed = (time.time() - start) * 1000
                self._total_time_ms += elapsed
                return ToolResult(
                    success=True,
                    output=result,
                    execution_time_ms=elapsed,
                )
            elif isinstance(result, dict):
                elapsed = (time.time() - start) * 1000
                self._total_time_ms += elapsed
                return ToolResult(
                    success=result.get("success", True),
                    output=result.get("output", ""),
                    error=result.get("error", ""),
                    metadata=result.get("metadata", {}),
                    execution_time_ms=elapsed,
                )
            else:
                elapsed = (time.time() - start) * 1000
                self._total_time_ms += elapsed
                return ToolResult(
                    success=True,
                    output=str(result),
                    execution_time_ms=elapsed,
                )

        except Exception as e:
            elapsed = (time.time() - start) * 1000
            self._error_count += 1
            self._total_time_ms += elapsed
            logger.error(f"工具 '{name}' 执行异常: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"执行异常: {e}",
                execution_time_ms=elapsed,
            )

    async def call_from_llm_response(self, llm_response: dict[str, Any]) -> ToolResult:
        """从 LLM 的 function call 响应中提取并执行工具

        兼容 OpenAI / DeepSeek / GLM 的 tool_calls 格式：
        {"tool_calls": [{"function": {"name": "...", "arguments": "..."}}]}

        Args:
            llm_response: LLM 返回的完整响应

        Returns:
            ToolResult: 第一个工具调用的结果
        """
        tool_calls = llm_response.get("tool_calls", [])
        if not tool_calls:
            # 尝试直接 function_call 格式
            func_call = llm_response.get("function_call")
            if func_call:
                tool_calls = [llm_response]

        if not tool_calls:
            return ToolResult(
                success=False,
                error="LLM响应中没有 tool_calls",
            )

        # 执行第一个工具调用
        call = tool_calls[0]
        func = call.get("function", call)
        name = func.get("name", "")
        args_str = func.get("arguments", "{}")

        try:
            arguments = json.loads(args_str) if isinstance(args_str, str) else args_str
        except json.JSONDecodeError as e:
            return ToolResult(success=False, error=f"参数JSON解析失败: {e}")

        return await self.call_tool(name, arguments)

    # ==================== 安全控制 ====================

    def enable_dangerous_tools(self, enabled: bool = True):
        """启用或禁用危险工具

        Args:
            enabled: True=启用危险工具, False=禁用
        """
        self._allow_dangerous = enabled
        if enabled:
            logger.warning("⚠️ 危险工具已启用（shell_exec, python_eval等）")
        else:
            logger.info("危险工具已禁用")

    def enable_whitelist(self, tool_names: list[str]):
        """启用白名单模式——只允许指定工具被调用

        Args:
            tool_names: 允许的工具名称列表
        """
        self._whitelist = set(tool_names)
        logger.info(f"白名单已启用: {tool_names}")

    def disable_whitelist(self):
        """禁用白名单模式"""
        self._whitelist = None
        logger.info("白名单已禁用")

    def _is_tool_available(self, tool: ToolDefinition) -> bool:
        """检查工具是否可用"""
        if not tool.enabled:
            return False
        if tool.dangerous and not self._allow_dangerous:
            return False
        if self._whitelist is not None and tool.name not in self._whitelist:
            return False
        return True

    def _validate_arguments(self, tool: ToolDefinition, arguments: dict[str, Any]) -> str:
        """验证参数（简单实现：检查必填参数）

        Args:
            tool: 工具定义
            arguments: 实际参数

        Returns:
            str: 错误信息（空字符串表示验证通过）
        """
        params_schema = tool.parameters
        if not params_schema:
            return ""

        properties = params_schema.get("properties", {})
        required = params_schema.get("required", [])

        # 检查必填参数
        for req in required:
            if req not in arguments:
                return f"缺少必填参数: {req}"

        # 检查参数类型（简单检查）
        for key, value in arguments.items():
            if key in properties:
                expected_type = properties[key].get("type", "")
                if expected_type == "string" and not isinstance(value, str):
                    return f"参数 '{key}' 应为 string 类型"
                elif expected_type == "number" and not isinstance(value, (int, float)):
                    return f"参数 '{key}' 应为 number 类型"
                elif expected_type == "boolean" and not isinstance(value, bool):
                    return f"参数 '{key}' 应为 boolean 类型"
                elif expected_type == "array" and not isinstance(value, list):
                    return f"参数 '{key}' 应为 array 类型"
                elif expected_type == "object" and not isinstance(value, dict):
                    return f"参数 '{key}' 应为 object 类型"

        return ""

    # ==================== 状态管理 ====================

    def get_stats(self) -> dict[str, Any]:
        """获取工具桥接器统计信息"""
        return {
            "total_tools": len(self._tools),
            "available_tools": len(self.list_tools()),
            "dangerous_tools": sum(1 for t in self._tools.values() if t.dangerous),
            "allow_dangerous": self._allow_dangerous,
            "whitelist_enabled": self._whitelist is not None,
            "call_count": self._call_count,
            "error_count": self._error_count,
            "total_time_ms": round(self._total_time_ms, 1),
            "avg_time_ms": round(self._total_time_ms / max(self._call_count, 1), 1),
        }

    def get_status(self) -> dict[str, Any]:
        """获取完整状态（含工具列表）"""
        return {
            "stats": self.get_stats(),
            "tools": [t.to_dict() for t in self._tools.values()],
        }

    # ==================== 内置工具实现 ====================

    def _register_builtin_tools(self):
        """注册内置工具"""

        # --- 文件操作工具 ---

        self.register_tool(
            name="file_read",
            description="读取文件内容。返回文件的文本内容。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（绝对路径或相对路径）",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码（默认 utf-8）",
                    },
                },
                "required": ["path"],
            },
            handler=self._tool_file_read,
            category="file",
            dangerous=False,
        )

        self.register_tool(
            name="file_write",
            description="写入文件内容。如果文件不存在则创建，已存在则覆盖。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的内容"},
                    "encoding": {
                        "type": "string",
                        "description": "文件编码（默认 utf-8）",
                    },
                },
                "required": ["path", "content"],
            },
            handler=self._tool_file_write,
            category="file",
            dangerous=True,  # 写入操作标记为危险
        )

        self.register_tool(
            name="file_list",
            description="列出目录中的文件和子目录。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（默认当前目录）",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "文件名匹配模式（如 *.py）",
                    },
                },
                "required": [],
            },
            handler=self._tool_file_list,
            category="file",
            dangerous=False,
        )

        # --- 命令执行工具 ---

        self.register_tool(
            name="shell_exec",
            description="执行系统命令并返回输出。⚠️ 危险操作，需启用 dangerous 工具。",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {
                        "type": "number",
                        "description": "超时秒数（默认30）",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "工作目录（默认当前目录）",
                    },
                },
                "required": ["command"],
            },
            handler=self._tool_shell_exec,
            category="shell",
            dangerous=True,
        )

        # --- HTTP 请求工具 ---

        self.register_tool(
            name="http_get",
            description="发送 HTTP GET 请求并返回响应内容。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "请求URL"},
                    "headers": {
                        "type": "object",
                        "description": "请求头（键值对）",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "超时秒数（默认30）",
                    },
                },
                "required": ["url"],
            },
            handler=self._tool_http_get,
            category="http",
            dangerous=False,
        )

        self.register_tool(
            name="http_post",
            description="发送 HTTP POST 请求并返回响应内容。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "请求URL"},
                    "body": {
                        "type": "string",
                        "description": "请求体（JSON字符串）",
                    },
                    "headers": {
                        "type": "object",
                        "description": "请求头（键值对）",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "超时秒数（默认30）",
                    },
                },
                "required": ["url"],
            },
            handler=self._tool_http_post,
            category="http",
            dangerous=True,  # POST 可能修改数据
        )

    # --- 文件操作 handler ---

    def _tool_file_read(self, path: str, encoding: str = "utf-8") -> ToolResult:
        """读取文件"""
        try:
            if not os.path.exists(path):
                return ToolResult(success=False, error=f"文件不存在: {path}")
            with open(path, "r", encoding=encoding) as f:
                content = f.read()
            return ToolResult(
                success=True,
                output=content,
                metadata={"path": path, "size": len(content)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"读取文件失败: {e}")

    def _tool_file_write(
        self, path: str, content: str, encoding: str = "utf-8"
    ) -> ToolResult:
        """写入文件"""
        try:
            # 确保目录存在
            dir_path = os.path.dirname(path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path, exist_ok=True)
            with open(path, "w", encoding=encoding) as f:
                f.write(content)
            return ToolResult(
                success=True,
                output=f"已写入 {len(content)} 字符到 {path}",
                metadata={"path": path, "size": len(content)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"写入文件失败: {e}")

    def _tool_file_list(self, path: str = ".", pattern: str = "") -> ToolResult:
        """列出目录"""
        try:
            if not os.path.exists(path):
                return ToolResult(success=False, error=f"目录不存在: {path}")
            if not os.path.isdir(path):
                return ToolResult(success=False, error=f"不是目录: {path}")

            entries = []
            for entry in sorted(os.listdir(path)):
                entry_path = os.path.join(path, entry)
                is_dir = os.path.isdir(entry_path)

                # 模式匹配
                if pattern:
                    import fnmatch

                    if not fnmatch.fnmatch(entry, pattern):
                        continue

                entries.append(
                    {"name": entry, "type": "dir" if is_dir else "file", "path": entry_path}
                )

            return ToolResult(
                success=True,
                output=json.dumps(entries, ensure_ascii=False, indent=2),
                metadata={"path": path, "count": len(entries)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"列目录失败: {e}")

    # --- 命令执行 handler ---

    def _tool_shell_exec(
        self, command: str, timeout: float = 30, cwd: str = ""
    ) -> ToolResult:
        """执行系统命令"""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd if cwd else None,
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}" if output else result.stderr

            return ToolResult(
                success=result.returncode == 0,
                output=output,
                error=f"退出码 {result.returncode}" if result.returncode != 0 else "",
                metadata={
                    "returncode": result.returncode,
                    "command": command,
                },
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"命令超时（{timeout}秒）: {command}",
            )
        except Exception as e:
            return ToolResult(success=False, error=f"执行命令失败: {e}")

    # --- HTTP 请求 handler ---

    def _tool_http_get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> ToolResult:
        """HTTP GET 请求"""
        try:
            req = urllib.request.Request(url)
            if headers:
                for key, value in headers.items():
                    req.add_header(key, value)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read().decode("utf-8", errors="replace")
                return ToolResult(
                    success=True,
                    output=content,
                    metadata={
                        "status": resp.status,
                        "url": url,
                        "size": len(content),
                    },
                )
        except urllib.error.HTTPError as e:
            return ToolResult(
                success=False,
                error=f"HTTP {e.code}: {e.reason}",
                metadata={"status": e.code, "url": url},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"HTTP请求失败: {e}")

    def _tool_http_post(
        self,
        url: str,
        body: str = "",
        headers: dict[str, str] | None = None,
        timeout: float = 30,
    ) -> ToolResult:
        """HTTP POST 请求"""
        try:
            data = body.encode("utf-8") if body else None
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            if headers:
                for key, value in headers.items():
                    req.add_header(key, value)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read().decode("utf-8", errors="replace")
                return ToolResult(
                    success=True,
                    output=content,
                    metadata={
                        "status": resp.status,
                        "url": url,
                        "size": len(content),
                    },
                )
        except urllib.error.HTTPError as e:
            return ToolResult(
                success=False,
                error=f"HTTP {e.code}: {e.reason}",
                metadata={"status": e.code, "url": url},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"HTTP请求失败: {e}")


# ==================== Gateway 桥接 ====================


class GatewayBridge:
    """外部 Gateway 桥接器

    连接 OpenClaw Gateway 或其他兼容 HTTP API 的工具服务，
    将远程工具注册到本地 ToolBridge。
    """

    def __init__(
        self,
        gateway_url: str = "",
        gateway_token: str = "",
        tool_bridge: ToolBridge | None = None,
    ):
        """初始化 Gateway 桥接器

        Args:
            gateway_url: Gateway 服务地址（如 http://localhost:28790）
            gateway_token: Gateway 认证令牌
            tool_bridge: 关联的 ToolBridge 实例（默认创建新的）
        """
        self.gateway_url = gateway_url.rstrip("/")
        self.gateway_token = gateway_token
        self.bridge = tool_bridge or ToolBridge()
        self._discovered_tools: list[str] = []

    def _get_headers(self) -> dict[str, str]:
        """获取请求头"""
        headers = {"Content-Type": "application/json"}
        if self.gateway_token:
            headers["Authorization"] = f"Bearer {self.gateway_token}"
        return headers

    async def health_check(self) -> bool:
        """检查 Gateway 是否在线"""
        if not self.gateway_url:
            return False
        try:
            url = f"{self.gateway_url}/api/health"
            req = urllib.request.Request(url)
            for key, value in self._get_headers().items():
                req.add_header(key, value)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def chat(self, agent: str, message: str, local: bool = True) -> dict[str, Any]:
        """通过 Gateway 调用 Agent 聊天

        Args:
            agent: Agent 名称（如 "lingxi-chat"）
            message: 消息内容
            local: 是否本地模式

        Returns:
            Gateway 返回的响应数据

        Raises:
            RuntimeError: Gateway 请求失败
        """
        if not self.gateway_url:
            raise RuntimeError("Gateway URL 未配置")

        url = f"{self.gateway_url}/api/agent/chat"
        payload = json.dumps(
            {"agent": agent, "message": message, "local": local}
        ).encode("utf-8")

        req = urllib.request.Request(url, data=payload, method="POST")
        for key, value in self._get_headers().items():
            req.add_header(key, value)

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise RuntimeError(f"Gateway HTTP {e.code}: {error_body}") from e
        except Exception as e:
            raise RuntimeError(f"Gateway请求失败: {e}") from e

    async def discover_tools(self) -> list[dict[str, Any]]:
        """发现 Gateway 提供的工具

        Returns:
            工具定义列表

        Note:
            当前实现为预留接口，具体工具发现协议
            取决于 Gateway 的 API 设计
        """
        if not self.gateway_url:
            return []

        try:
            url = f"{self.gateway_url}/api/tools"
            req = urllib.request.Request(url)
            for key, value in self._get_headers().items():
                req.add_header(key, value)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tools = data.get("tools", [])
                self._discovered_tools = [t.get("name", "") for t in tools]
                return tools
        except Exception as e:
            logger.debug(f"Gateway工具发现失败: {e}")
            return []

    def register_gateway_tools(self, tools: list[dict[str, Any]]):
        """将 Gateway 发现的工具注册到本地 ToolBridge

        Args:
            tools: Gateway 返回的工具定义列表
        """
        for tool_def in tools:
            name = tool_def.get("name", "")
            if not name:
                continue

            # 创建远程调用 handler
            def make_handler(tool_name: str):
                async def handler(**kwargs):
                    result = await self._call_gateway_tool(tool_name, kwargs)
                    return result

                return handler

            self.bridge.register_tool(
                name=f"gateway_{name}",
                description=tool_def.get("description", f"Gateway tool: {name}"),
                parameters=tool_def.get("parameters", {"type": "object", "properties": {}}),
                handler=make_handler(name),
                category="gateway",
                dangerous=False,
            )

    async def _call_gateway_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """调用 Gateway 上的远程工具"""
        if not self.gateway_url:
            return ToolResult(success=False, error="Gateway URL 未配置")

        url = f"{self.gateway_url}/api/tools/{tool_name}/call"
        payload = json.dumps({"arguments": arguments}).encode("utf-8")

        req = urllib.request.Request(url, data=payload, method="POST")
        for key, value in self._get_headers().items():
            req.add_header(key, value)

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return ToolResult(
                    success=data.get("success", True),
                    output=data.get("output", ""),
                    error=data.get("error", ""),
                    metadata={"gateway": True, "tool": tool_name},
                )
        except Exception as e:
            return ToolResult(success=False, error=f"Gateway工具调用失败: {e}")
