"""
OpenClaw Tool Bridge 测试

覆盖工具注册/调用/安全控制/内置工具/Gateway桥接等核心功能。
文件操作使用 pytest tmp_path fixture，HTTP/Shell 工具使用 mock。
"""

import asyncio
import json
import os
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from openclaw_bridge import (
    GatewayBridge,
    ToolBridge,
    ToolDefinition,
    ToolResult,
)

# ==================== Mock 辅助函数 ====================


def _make_mock_response(data: dict | str, status: int = 200) -> MagicMock:
    """创建 mock HTTP 响应"""
    resp = MagicMock()
    resp.status = status
    if isinstance(data, dict):
        resp.read.return_value = json.dumps(data).encode("utf-8")
    else:
        resp.read.return_value = data.encode("utf-8")
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_mock_http_error(status: int, body: str = "") -> urllib.error.HTTPError:
    """创建 mock HTTPError"""
    return urllib.error.HTTPError(
        url="http://test/api",
        code=status,
        msg=f"HTTP {status}",
        hdrs=None,
        fp=BytesIO(body.encode("utf-8")),
    )


# ==================== ToolDefinition 测试 ====================


class TestToolDefinition:
    """ToolDefinition 数据类测试"""

    def test_creation(self):
        """测试创建工具定义"""
        tool = ToolDefinition(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}, "required": []},
        )
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert tool.category == "general"
        assert tool.dangerous is False
        assert tool.enabled is True

    def test_to_openai_dict(self):
        """测试 OpenAI Function Calling 格式转换"""
        tool = ToolDefinition(
            name="get_weather",
            description="Get weather",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        d = tool.to_openai_dict()
        assert d["type"] == "function"
        assert d["function"]["name"] == "get_weather"
        assert d["function"]["description"] == "Get weather"
        assert "city" in d["function"]["parameters"]["properties"]

    def test_to_dict(self):
        """测试普通字典转换"""
        tool = ToolDefinition(
            name="test",
            description="test",
            parameters={"type": "object"},
            category="custom",
            dangerous=True,
        )
        d = tool.to_dict()
        assert d["name"] == "test"
        assert d["category"] == "custom"
        assert d["dangerous"] is True


# ==================== ToolResult 测试 ====================


class TestToolResult:
    """ToolResult 数据类测试"""

    def test_creation_success(self):
        """测试成功结果"""
        result = ToolResult(success=True, output="done")
        assert result.success is True
        assert result.output == "done"
        assert result.error == ""
        assert result.execution_time_ms == 0.0

    def test_creation_error(self):
        """测试错误结果"""
        result = ToolResult(success=False, error="something went wrong")
        assert result.success is False
        assert result.error == "something went wrong"

    def test_to_dict(self):
        """测试序列化"""
        result = ToolResult(
            success=True,
            output="hello",
            metadata={"key": "value"},
            execution_time_ms=42.5,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["output"] == "hello"
        assert d["metadata"]["key"] == "value"
        assert d["execution_time_ms"] == 42.5


# ==================== ToolBridge 注册测试 ====================


class TestToolBridgeRegistration:
    """ToolBridge 工具注册测试"""

    def test_builtin_tools_registered(self):
        """测试内置工具已注册"""
        bridge = ToolBridge()
        tool_names = [t.name for t in bridge.list_tools(include_disabled=True)]
        assert "file_read" in tool_names
        assert "file_write" in tool_names
        assert "file_list" in tool_names
        assert "shell_exec" in tool_names
        assert "http_get" in tool_names
        assert "http_post" in tool_names

    def test_register_custom_tool(self):
        """测试注册自定义工具"""
        bridge = ToolBridge()

        def my_handler(query: str) -> str:
            return f"result: {query}"

        bridge.register_tool(
            name="my_search",
            description="Search something",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=my_handler,
        )
        assert "my_search" in [t.name for t in bridge.list_tools()]

    def test_register_overwrites_existing(self):
        """测试注册同名工具会覆盖"""
        bridge = ToolBridge()

        bridge.register_tool(
            name="custom",
            description="v1",
            parameters={"type": "object", "properties": {}},
            handler=lambda: "v1",
        )
        bridge.register_tool(
            name="custom",
            description="v2",
            parameters={"type": "object", "properties": {}},
            handler=lambda: "v2",
        )
        tools = [t for t in bridge.list_tools(include_disabled=True) if t.name == "custom"]
        assert len(tools) == 1
        assert tools[0].description == "v2"

    def test_unregister_tool(self):
        """测试注销工具"""
        bridge = ToolBridge()
        bridge.register_tool(
            name="temp",
            description="temp",
            parameters={"type": "object", "properties": {}},
            handler=lambda: "temp",
        )
        assert bridge.unregister_tool("temp") is True
        assert "temp" not in [t.name for t in bridge.list_tools(include_disabled=True)]
        assert bridge.unregister_tool("nonexistent") is False

    def test_get_tool_schema(self):
        """测试获取工具参数schema"""
        bridge = ToolBridge()
        schema = bridge.get_tool_schema("file_read")
        assert schema is not None
        assert "path" in schema.get("properties", {})

    def test_to_openai_functions(self):
        """测试转换为OpenAI格式列表"""
        bridge = ToolBridge()
        functions = bridge.to_openai_functions()
        assert len(functions) > 0
        assert functions[0]["type"] == "function"
        assert "function" in functions[0]


# ==================== ToolBridge 安全控制测试 ====================


class TestToolBridgeSecurity:
    """ToolBridge 安全控制测试"""

    def test_dangerous_tools_hidden_by_default(self):
        """测试危险工具默认不可见"""
        bridge = ToolBridge()
        available = [t.name for t in bridge.list_tools()]
        # file_write, shell_exec, http_post 是 dangerous
        assert "file_write" not in available
        assert "shell_exec" not in available
        assert "http_post" not in available
        # file_read, file_list, http_get 不是 dangerous
        assert "file_read" in available
        assert "file_list" in available
        assert "http_get" in available

    def test_enable_dangerous_tools(self):
        """测试启用危险工具"""
        bridge = ToolBridge()
        bridge.enable_dangerous_tools(True)
        available = [t.name for t in bridge.list_tools()]
        assert "shell_exec" in available
        assert "file_write" in available

    def test_whitelist_mode(self):
        """测试白名单模式"""
        bridge = ToolBridge()
        bridge.enable_whitelist(["file_read"])
        available = [t.name for t in bridge.list_tools()]
        assert available == ["file_read"]

    def test_disable_whitelist(self):
        """测试禁用白名单"""
        bridge = ToolBridge()
        bridge.enable_whitelist(["file_read"])
        bridge.disable_whitelist()
        available = [t.name for t in bridge.list_tools()]
        assert "file_read" in available
        assert "file_list" in available

    @pytest.mark.asyncio
    async def test_call_dangerous_without_enable(self):
        """测试未启用危险工具时调用被拒绝"""
        bridge = ToolBridge()
        result = await bridge.call_tool("shell_exec", {"command": "echo hi"})
        assert result.success is False
        assert "危险工具未启用" in result.error

    @pytest.mark.asyncio
    async def test_call_not_in_whitelist(self):
        """测试白名单外工具被拒绝"""
        bridge = ToolBridge()
        bridge.enable_whitelist(["file_read"])
        result = await bridge.call_tool("file_list", {"path": "."})
        assert result.success is False
        assert "白名单" in result.error


# ==================== ToolBridge 参数验证测试 ====================


class TestToolBridgeValidation:
    """ToolBridge 参数验证测试"""

    @pytest.mark.asyncio
    async def test_missing_required_param(self):
        """测试缺少必填参数"""
        bridge = ToolBridge()
        result = await bridge.call_tool("file_read", {})  # 缺少 path
        assert result.success is False
        assert "缺少必填参数" in result.error

    @pytest.mark.asyncio
    async def test_wrong_param_type(self):
        """测试参数类型错误"""
        bridge = ToolBridge()
        result = await bridge.call_tool("file_read", {"path": 123})  # 应为 string
        assert result.success is False
        assert "应为 string 类型" in result.error

    @pytest.mark.asyncio
    async def test_call_nonexistent_tool(self):
        """测试调用不存在的工具"""
        bridge = ToolBridge()
        result = await bridge.call_tool("nonexistent", {})
        assert result.success is False
        assert "不存在" in result.error


# ==================== ToolBridge 调用测试 ====================


class TestToolBridgeCall:
    """ToolBridge 工具调用测试"""

    @pytest.mark.asyncio
    async def test_call_sync_handler_returns_str(self):
        """测试同步handler返回字符串"""
        bridge = ToolBridge()
        bridge.register_tool(
            name="echo",
            description="Echo input",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=lambda text: f"echo: {text}",
        )
        result = await bridge.call_tool("echo", {"text": "hello"})
        assert result.success is True
        assert result.output == "echo: hello"

    @pytest.mark.asyncio
    async def test_call_async_handler(self):
        """测试异步handler"""
        bridge = ToolBridge()

        async def async_handler(text: str) -> str:
            await asyncio.sleep(0.01)
            return f"async: {text}"

        bridge.register_tool(
            name="async_echo",
            description="Async echo",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=async_handler,
        )
        result = await bridge.call_tool("async_echo", {"text": "world"})
        assert result.success is True
        assert result.output == "async: world"

    @pytest.mark.asyncio
    async def test_call_handler_returns_toolresult(self):
        """测试handler返回ToolResult"""
        bridge = ToolBridge()

        def custom_handler(value: str) -> ToolResult:
            return ToolResult(
                success=True,
                output=f"processed: {value}",
                metadata={"original": value},
            )

        bridge.register_tool(
            name="process",
            description="Process",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            handler=custom_handler,
        )
        result = await bridge.call_tool("process", {"value": "data"})
        assert result.success is True
        assert result.output == "processed: data"
        assert result.metadata["original"] == "data"

    @pytest.mark.asyncio
    async def test_call_handler_returns_dict(self):
        """测试handler返回dict"""
        bridge = ToolBridge()

        bridge.register_tool(
            name="dict_handler",
            description="Returns dict",
            parameters={"type": "object", "properties": {}},
            handler=lambda: {"success": True, "output": "from dict"},
        )
        result = await bridge.call_tool("dict_handler", {})
        assert result.success is True
        assert result.output == "from dict"

    @pytest.mark.asyncio
    async def test_call_handler_raises_exception(self):
        """测试handler抛出异常"""
        bridge = ToolBridge()

        def bad_handler(text: str) -> str:
            raise ValueError("intentional error")

        bridge.register_tool(
            name="bad",
            description="Bad handler",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=bad_handler,
        )
        result = await bridge.call_tool("bad", {"text": "test"})
        assert result.success is False
        assert "intentional error" in result.error

    @pytest.mark.asyncio
    async def test_call_from_llm_response(self):
        """测试从LLM响应中提取工具调用"""
        bridge = ToolBridge()
        bridge.register_tool(
            name="echo",
            description="Echo",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=lambda text: f"echo: {text}",
        )

        llm_response = {
            "tool_calls": [
                {
                    "function": {
                        "name": "echo",
                        "arguments": json.dumps({"text": "from LLM"}),
                    }
                }
            ]
        }
        result = await bridge.call_from_llm_response(llm_response)
        assert result.success is True
        assert result.output == "echo: from LLM"

    @pytest.mark.asyncio
    async def test_call_from_llm_response_no_tool_calls(self):
        """测试LLM响应无工具调用"""
        bridge = ToolBridge()
        result = await bridge.call_from_llm_response({"content": "just text"})
        assert result.success is False
        assert "没有 tool_calls" in result.error

    @pytest.mark.asyncio
    async def test_stats_tracking(self):
        """测试统计追踪"""
        bridge = ToolBridge()
        bridge.register_tool(
            name="ok",
            description="OK",
            parameters={"type": "object", "properties": {}},
            handler=lambda: "ok",
        )
        bridge.register_tool(
            name="fail",
            description="Fail",
            parameters={"type": "object", "properties": {}},
            handler=lambda: (_ for _ in ()).throw(ValueError("fail")),
        )

        await bridge.call_tool("ok", {})
        await bridge.call_tool("fail", {})

        stats = bridge.get_stats()
        assert stats["call_count"] == 2
        assert stats["error_count"] == 1


# ==================== 内置文件工具测试 ====================


class TestBuiltinFileTools:
    """内置文件工具测试"""

    @pytest.mark.asyncio
    async def test_file_read_success(self, tmp_path):
        """测试读取文件成功"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")

        bridge = ToolBridge()
        result = await bridge.call_tool("file_read", {"path": str(test_file)})
        assert result.success is True
        assert result.output == "hello world"
        assert result.metadata["size"] == 11

    @pytest.mark.asyncio
    async def test_file_read_not_exist(self, tmp_path):
        """测试读取不存在的文件"""
        bridge = ToolBridge()
        result = await bridge.call_tool("file_read", {"path": str(tmp_path / "noexist.txt")})
        assert result.success is False
        assert "不存在" in result.error

    @pytest.mark.asyncio
    async def test_file_write_success(self, tmp_path):
        """测试写入文件成功"""
        bridge = ToolBridge()
        bridge.enable_dangerous_tools(True)
        test_file = tmp_path / "output.txt"

        result = await bridge.call_tool(
            "file_write", {"path": str(test_file), "content": "written content"}
        )
        assert result.success is True
        assert test_file.read_text() == "written content"

    @pytest.mark.asyncio
    async def test_file_write_creates_dir(self, tmp_path):
        """测试写入文件时自动创建目录"""
        bridge = ToolBridge()
        bridge.enable_dangerous_tools(True)
        test_file = tmp_path / "subdir" / "nested" / "file.txt"

        result = await bridge.call_tool("file_write", {"path": str(test_file), "content": "nested"})
        assert result.success is True
        assert test_file.read_text() == "nested"

    @pytest.mark.asyncio
    async def test_file_list_success(self, tmp_path):
        """测试列目录成功"""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.py").write_text("b")
        (tmp_path / "subdir").mkdir()

        bridge = ToolBridge()
        result = await bridge.call_tool("file_list", {"path": str(tmp_path)})
        assert result.success is True
        entries = json.loads(result.output)
        names = [e["name"] for e in entries]
        assert "a.txt" in names
        assert "b.py" in names
        assert "subdir" in names

    @pytest.mark.asyncio
    async def test_file_list_with_pattern(self, tmp_path):
        """测试带模式匹配的列目录"""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.py").write_text("b")
        (tmp_path / "c.txt").write_text("c")

        bridge = ToolBridge()
        result = await bridge.call_tool("file_list", {"path": str(tmp_path), "pattern": "*.txt"})
        assert result.success is True
        entries = json.loads(result.output)
        names = [e["name"] for e in entries]
        assert "a.txt" in names
        assert "c.txt" in names
        assert "b.py" not in names

    @pytest.mark.asyncio
    async def test_file_list_not_dir(self, tmp_path):
        """测试对文件执行列目录"""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        bridge = ToolBridge()
        result = await bridge.call_tool("file_list", {"path": str(test_file)})
        assert result.success is False
        assert "不是目录" in result.error


# ==================== 内置Shell工具测试 ====================


class TestBuiltinShellTool:
    """内置 Shell 工具测试"""

    @pytest.mark.asyncio
    async def test_shell_exec_echo(self):
        """测试执行echo命令"""
        bridge = ToolBridge()
        bridge.enable_dangerous_tools(True)
        result = await bridge.call_tool("shell_exec", {"command": "echo test123"})
        assert result.success is True
        assert "test123" in result.output

    @pytest.mark.asyncio
    async def test_shell_exec_error_code(self):
        """测试命令返回非零退出码"""
        bridge = ToolBridge()
        bridge.enable_dangerous_tools(True)
        # 使用一个一定失败的命令
        result = await bridge.call_tool("shell_exec", {"command": "exit 1", "timeout": 5})
        assert result.success is False
        assert "退出码 1" in result.error


# ==================== 内置HTTP工具测试 ====================


class TestBuiltinHttpTools:
    """内置 HTTP 工具测试"""

    @pytest.mark.asyncio
    async def test_http_get_success(self):
        """测试HTTP GET成功"""
        bridge = ToolBridge()
        mock_data = {"status": "ok", "data": "hello"}

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            result = await bridge.call_tool("http_get", {"url": "http://test/api"})
        assert result.success is True
        assert "ok" in result.output

    @pytest.mark.asyncio
    async def test_http_get_error(self):
        """测试HTTP GET错误"""
        bridge = ToolBridge()
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_mock_http_error(404, "Not Found"),
        ):
            result = await bridge.call_tool("http_get", {"url": "http://test/missing"})
        assert result.success is False
        assert "404" in result.error

    @pytest.mark.asyncio
    async def test_http_post_success(self):
        """测试HTTP POST成功"""
        bridge = ToolBridge()
        bridge.enable_dangerous_tools(True)
        mock_data = {"created": True}

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            result = await bridge.call_tool(
                "http_post",
                {"url": "http://test/api", "body": json.dumps({"key": "value"})},
            )
        assert result.success is True
        assert "created" in result.output


# ==================== GatewayBridge 测试 ====================


class TestGatewayBridge:
    """GatewayBridge 测试"""

    def test_no_url(self):
        """测试无URL时初始化"""
        gw = GatewayBridge()
        assert gw.gateway_url == ""
        assert gw.bridge is not None

    @pytest.mark.asyncio
    async def test_health_check_no_url(self):
        """测试无URL时健康检查"""
        gw = GatewayBridge()
        assert await gw.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_online(self):
        """测试Gateway在线"""
        gw = GatewayBridge(gateway_url="http://localhost:28790")
        with patch("urllib.request.urlopen", return_value=_make_mock_response({"ok": True})):
            assert await gw.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_offline(self):
        """测试Gateway离线"""
        gw = GatewayBridge(gateway_url="http://localhost:28790")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            assert await gw.health_check() is False

    @pytest.mark.asyncio
    async def test_chat_success(self):
        """测试Gateway聊天成功"""
        gw = GatewayBridge(
            gateway_url="http://localhost:28790",
            gateway_token="test_token",  # nosec B106 — 测试用 token, 非真实密码
        )
        mock_data = {"ok": True, "reply": "Hello from gateway!"}

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            result = await gw.chat("lingxi-chat", "你好")

        assert result["ok"] is True
        assert result["reply"] == "Hello from gateway!"

    @pytest.mark.asyncio
    async def test_chat_no_url(self):
        """测试无URL时聊天"""
        gw = GatewayBridge()
        with pytest.raises(RuntimeError, match="未配置"):
            await gw.chat("test", "hello")

    @pytest.mark.asyncio
    async def test_chat_http_error(self):
        """测试Gateway聊天HTTP错误"""
        gw = GatewayBridge(gateway_url="http://localhost:28790")
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_mock_http_error(500, '{"error":"internal"}'),
        ):
            with pytest.raises(RuntimeError, match="500"):
                await gw.chat("test", "hello")

    @pytest.mark.asyncio
    async def test_discover_tools_success(self):
        """测试工具发现成功"""
        gw = GatewayBridge(gateway_url="http://localhost:28790")
        mock_data = {
            "tools": [
                {"name": "search", "description": "Search the web"},
                {"name": "calc", "description": "Calculator"},
            ]
        }

        with patch("urllib.request.urlopen", return_value=_make_mock_response(mock_data)):
            tools = await gw.discover_tools()

        assert len(tools) == 2
        assert tools[0]["name"] == "search"

    @pytest.mark.asyncio
    async def test_discover_tools_no_url(self):
        """测试无URL时工具发现"""
        gw = GatewayBridge()
        tools = await gw.discover_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_register_gateway_tools(self):
        """测试注册Gateway工具到本地"""
        gw = GatewayBridge(gateway_url="http://localhost:28790", tool_bridge=ToolBridge())
        tools = [
            {"name": "search", "description": "Search", "parameters": {"type": "object"}},
        ]
        gw.register_gateway_tools(tools)

        local_tools = [t.name for t in gw.bridge.list_tools(include_disabled=True)]
        assert "gateway_search" in local_tools


# ==================== 集成测试 ====================


class TestToolBridgeIntegration:
    """ToolBridge 集成测试"""

    @pytest.mark.asyncio
    async def test_full_flow_register_and_call(self, tmp_path):
        """测试完整流程：注册→调用→验证"""
        bridge = ToolBridge()

        # 注册自定义工具
        def file_counter(directory: str) -> ToolResult:
            count = len(os.listdir(directory))
            return ToolResult(
                success=True,
                output=f"{count} files in {directory}",
                metadata={"count": count},
            )

        bridge.register_tool(
            name="count_files",
            description="Count files in a directory",
            parameters={
                "type": "object",
                "properties": {"directory": {"type": "string"}},
                "required": ["directory"],
            },
            handler=file_counter,
            category="custom",
        )

        # 创建测试文件
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")

        # 调用
        result = await bridge.call_tool("count_files", {"directory": str(tmp_path)})
        assert result.success is True
        assert "2 files" in result.output
        assert result.metadata["count"] == 2

    def test_openai_functions_format_complete(self):
        """测试OpenAI格式输出完整性"""
        bridge = ToolBridge()
        bridge.register_tool(
            name="custom_tool",
            description="A custom tool for testing",
            parameters={
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "Input text"},
                },
                "required": ["input"],
            },
            handler=lambda input: input,
        )

        functions = bridge.to_openai_functions()
        # 应包含内置工具 + 自定义工具
        names = [f["function"]["name"] for f in functions]
        assert "file_read" in names
        assert "file_list" in names
        assert "http_get" in names
        assert "custom_tool" in names

    def test_get_status(self):
        """测试获取完整状态"""
        bridge = ToolBridge(allow_dangerous=True)
        status = bridge.get_status()

        assert "stats" in status
        assert "tools" in status
        assert status["stats"]["total_tools"] >= 6
        assert status["stats"]["allow_dangerous"] is True
        assert len(status["tools"]) >= 6
