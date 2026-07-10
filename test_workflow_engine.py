"""
workflow_engine.py 测试 — 可视化工作流引擎验证

覆盖范围：
- WorkflowDSL 定义与验证
- DAG拓扑排序（验证+排序+环检测）
- 6种节点执行器（LLM/Code/HTTP/Condition/Loop/Aggregator）
- WorkflowExecutor 拓扑执行（检查点+降级+条件分支）
- 工作流工厂与便捷API
"""

import time

import pytest

from workflow_engine import (
    EXECUTOR_REGISTRY,
    AggregatorNodeExecutor,
    BaseNodeExecutor,
    CodeNodeExecutor,
    ConditionNodeExecutor,
    DAGTopology,
    HTTPNodeExecutor,
    LLMNodeExecutor,
    LoopNodeExecutor,
    NodeDefinition,
    NodeExecutionResult,
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowExecutor,
    create_simple_workflow,
    register_executor,
    run_workflow,
    validate_workflow,
)

# ============================================================
# WorkflowDSL 测试
# ============================================================


class TestWorkflowDSL:
    """工作流DSL定义测试。"""

    def test_node_definition_basic(self):
        """基本节点定义。"""
        node = NodeDefinition(
            id="node_1",
            type=NodeType.LLM,
            name="LLM步骤",
            config={"prompt_template": "你好{{name}}"},
            inputs=[],
        )
        assert node.id == "node_1"
        assert node.type == NodeType.LLM
        assert node.timeout_seconds == 30.0
        assert node.retries == 0

    def test_node_definition_with_fallback(self):
        """带降级的节点定义。"""
        node = NodeDefinition(
            id="node_1",
            type=NodeType.LLM,
            config={"prompt_template": "测试"},
            inputs=[],
            fallback="node_fallback",
        )
        assert node.fallback == "node_fallback"

    def test_workflow_definition(self):
        """完整工作流定义。"""
        wf = WorkflowDefinition(
            name="测试工作流",
            nodes=[
                NodeDefinition(id="start", type=NodeType.LLM, inputs=[]),
                NodeDefinition(id="end", type=NodeType.AGGREGATOR, inputs=["start"]),
            ],
            start_node="start",
            end_nodes=["end"],
        )
        assert wf.name == "测试工作流"
        assert len(wf.nodes) == 2
        assert wf.global_timeout == 300.0

    def test_node_type_enum(self):
        """节点类型枚举完整性。"""
        assert len(NodeType) == 6
        assert NodeType.LLM.value == "llm"
        assert NodeType.CODE.value == "code"
        assert NodeType.HTTP.value == "http"
        assert NodeType.CONDITION.value == "condition"
        assert NodeType.LOOP.value == "loop"
        assert NodeType.AGGREGATOR.value == "aggregator"

    def test_node_status_enum(self):
        """节点状态枚举。"""
        assert NodeStatus.PENDING.value == "pending"
        assert NodeStatus.SUCCESS.value == "success"
        assert NodeStatus.FAILED.value == "failed"
        assert NodeStatus.SKIPPED.value == "skipped"


# ============================================================
# DAG拓扑排序测试
# ============================================================


class TestDAGTopology:
    """DAG拓扑排序与验证测试。"""

    def test_validate_simple_chain(self):
        """简单链式DAG验证。"""
        nodes = [
            NodeDefinition(id="n1", type=NodeType.LLM, inputs=[]),
            NodeDefinition(id="n2", type=NodeType.CODE, inputs=["n1"]),
            NodeDefinition(id="n3", type=NodeType.AGGREGATOR, inputs=["n2"]),
        ]
        valid, msg = DAGTopology.validate(nodes)
        assert valid is True
        assert "通过" in msg

    def test_validate_no_entry_node(self):
        """无入口节点（所有节点都有前置依赖=死循环）。"""
        nodes = [
            NodeDefinition(id="n1", type=NodeType.LLM, inputs=["n2"]),
            NodeDefinition(id="n2", type=NodeType.CODE, inputs=["n1"]),
        ]
        valid, msg = DAGTopology.validate(nodes)
        assert valid is False
        assert "入口" in msg or "死循环" in msg

    def test_validate_empty_nodes(self):
        """空节点列表。"""
        valid, msg = DAGTopology.validate([])
        assert valid is False
        assert "无节点" in msg

    def test_validate_cycle(self):
        """循环依赖检测。"""
        nodes = [
            NodeDefinition(id="n1", type=NodeType.LLM, inputs=["n3"]),
            NodeDefinition(id="n2", type=NodeType.CODE, inputs=["n1"]),
            NodeDefinition(id="n3", type=NodeType.HTTP, inputs=["n2"]),
        ]
        valid, msg = DAGTopology.validate(nodes)
        assert valid is False
        assert "循环" in msg

    def test_topology_sort(self):
        """拓扑排序输出正确顺序。"""
        nodes = [
            NodeDefinition(id="n1", type=NodeType.LLM, inputs=[]),
            NodeDefinition(id="n2", type=NodeType.CODE, inputs=["n1"]),
            NodeDefinition(id="n3", type=NodeType.HTTP, inputs=["n1"]),
            NodeDefinition(id="n4", type=NodeType.AGGREGATOR, inputs=["n2", "n3"]),
        ]
        order = DAGTopology.sort(nodes)
        assert order[0] == "n1"
        assert "n4" in order
        # n1必须在n2和n3之前
        assert order.index("n1") < order.index("n2")
        assert order.index("n1") < order.index("n3")

    def test_topology_sort_single_node(self):
        """单节点拓扑排序。"""
        nodes = [
            NodeDefinition(id="only", type=NodeType.LLM, inputs=[]),
        ]
        order = DAGTopology.sort(nodes)
        assert order == ["only"]


# ============================================================
# 节点执行器测试
# ============================================================


class TestLLMNodeExecutor:
    """LLM节点执行器测试。"""

    def test_execute_with_template(self):
        """模板变量替换。"""
        executor = LLMNodeExecutor()
        result = executor.execute(
            config={"prompt_template": "你好{{name}}"},
            inputs={"name": "九重"},
            context={},
        )
        # 模拟模式下输出包含提示词或模拟标记
        assert result["output"]  # 有输出
        assert "九重" in result["output"] or "LLM模拟" in result["output"]

    def test_execute_no_model_router(self):
        """无model_router时的fallback。"""
        executor = LLMNodeExecutor()
        result = executor.execute(
            config={"prompt_template": "测试提示词"},
            inputs={},
            context={},
        )
        assert result["output"]  # 应有输出（fallback模式）

    def test_execute_with_fallback(self):
        """双保障降级执行。"""
        executor = LLMNodeExecutor()
        result = executor.execute_with_fallback(
            config={"prompt_template": "测试"},
            inputs={},
            context={},
        )
        assert result["output"]


class TestCodeNodeExecutor:
    """Code节点执行器测试。"""

    def test_execute_simple_code(self):
        """简单代码执行。"""
        executor = CodeNodeExecutor()
        result = executor.execute(
            config={"code": "result = sum([1, 2, 3])"},
            inputs={},
            context={},
        )
        assert result["output"] == "6"
        assert result["error"] is False

    def test_execute_with_inputs(self):
        """代码使用输入变量。"""
        executor = CodeNodeExecutor()
        result = executor.execute(
            config={"code": "result = len(initial_data)"},
            inputs={"initial_data": "hello world"},
            context={},
        )
        assert "11" in result["output"] or result["error"] is False

    def test_execute_dangerous_code_blocked(self):
        """危险代码被拦截。"""
        executor = CodeNodeExecutor()
        result = executor.execute(
            config={"code": "import os\nresult = os.listdir('.')"},
            inputs={},
            context={},
        )
        assert result["blocked"] is True
        assert result["error"] is True

    def test_execute_eval_blocked(self):
        """eval被拦截。"""
        executor = CodeNodeExecutor()
        result = executor.execute(
            config={"code": "result = eval('1+1')"},
            inputs={},
            context={},
        )
        assert result["blocked"] is True

    def test_execute_non_python_blocked(self):
        """不支持的语言。"""
        executor = CodeNodeExecutor()
        result = executor.execute(
            config={"code": "console.log('hello')", "language": "javascript"},
            inputs={},
            context={},
        )
        assert result["error"] is True

    def test_execute_empty_code(self):
        """空代码返回空输出。"""
        executor = CodeNodeExecutor()
        result = executor.execute(
            config={"code": ""},
            inputs={},
            context={},
        )
        assert result["output"] == ""
        assert result["error"] is False


class TestHTTPNodeExecutor:
    """HTTP节点执行器测试。"""

    def test_execute_missing_url(self):
        """缺少URL。"""
        executor = HTTPNodeExecutor()
        result = executor.execute(config={}, inputs={}, context={})
        assert result["error"] is True

    def test_execute_get_request(self):
        """GET请求（模拟/真实）。"""
        executor = HTTPNodeExecutor()
        result = executor.execute(
            config={"url": "https://httpbin.org/get", "method": "GET"},
            inputs={},
            context={},
        )
        # 有requests库则真实请求，否则模拟
        assert result["output"]


class TestConditionNodeExecutor:
    """Condition节点执行器测试。"""

    def test_simple_comparison_true(self):
        """简单比较——结果为真。"""
        executor = ConditionNodeExecutor()
        result = executor.execute(
            config={
                "condition": "{{score}} > 0.5",
                "branches": {"true": "node_yes", "false": "node_no"},
            },
            inputs={"score": "0.8"},
            context={},
        )
        assert result["branch"] == "true"
        assert result["next_node"] == "node_yes"

    def test_simple_comparison_false(self):
        """简单比较——结果为假。"""
        executor = ConditionNodeExecutor()
        result = executor.execute(
            config={
                "condition": "{{score}} > 0.5",
                "branches": {"true": "node_yes", "false": "node_no"},
            },
            inputs={"score": "0.3"},
            context={},
        )
        assert result["branch"] == "false"
        assert result["next_node"] == "node_no"

    def test_comparison_operators(self):
        """多种比较运算符。"""
        executor = ConditionNodeExecutor()
        # >= 测试
        result = executor.execute(
            config={"condition": "{{val}} >= 10"},
            inputs={"val": "10"},
            context={},
        )
        assert result["branch"] == "true"

        # < 测试
        result = executor.execute(
            config={"condition": "{{val}} < 5"},
            inputs={"val": "3"},
            context={},
        )
        assert result["branch"] == "true"


class TestLoopNodeExecutor:
    """Loop节点执行器测试。"""

    def test_execute_loop(self):
        """循环迭代执行。"""
        executor = LoopNodeExecutor()
        result = executor.execute(
            config={"max_iterations": 3},
            inputs={},
            context={},
        )
        assert result["iterations"] == 3
        assert len(result["output"]) == 3

    def test_execute_default_iterations(self):
        """默认迭代次数。"""
        executor = LoopNodeExecutor()
        result = executor.execute(
            config={},
            inputs={},
            context={},
        )
        assert result["iterations"] == 10  # 默认值


class TestAggregatorNodeExecutor:
    """Aggregator节点执行器测试。"""

    def test_concat_aggregation(self):
        """拼接聚合。"""
        executor = AggregatorNodeExecutor()
        result = executor.execute(
            config={"aggregation_type": "concat"},
            inputs={"node_1": "结果A", "node_2": "结果B"},
            context={},
        )
        assert "结果A" in result["output"]
        assert "结果B" in result["output"]

    def test_latest_aggregation(self):
        """最新值聚合。"""
        executor = AggregatorNodeExecutor()
        result = executor.execute(
            config={"aggregation_type": "latest"},
            inputs={"node_1": "旧结果", "node_2": "新结果"},
            context={},
        )
        assert result["output"] == "新结果"

    def test_sum_aggregation(self):
        """求和聚合。"""
        executor = AggregatorNodeExecutor()
        result = executor.execute(
            config={"aggregation_type": "sum"},
            inputs={"node_1": 10, "node_2": 20},
            context={},
        )
        assert result["output"] == 30

    def test_average_aggregation(self):
        """平均聚合。"""
        executor = AggregatorNodeExecutor()
        result = executor.execute(
            config={"aggregation_type": "average"},
            inputs={"node_1": 10, "node_2": 20},
            context={},
        )
        assert result["output"] == 15.0


# ============================================================
# 执行器注册表测试
# ============================================================


class TestExecutorRegistry:
    """执行器注册表测试。"""

    def test_all_types_registered(self):
        """6种类型全部注册。"""
        assert len(EXECUTOR_REGISTRY) == 6
        for nt in NodeType:
            assert nt in EXECUTOR_REGISTRY

    def test_register_custom_executor(self):
        """注册自定义执行器。"""

        class CustomExecutor(BaseNodeExecutor):
            node_type = NodeType.LLM

            def execute(self, config, inputs, context):
                return {"output": "custom_result"}

        original = EXECUTOR_REGISTRY[NodeType.LLM]
        register_executor(NodeType.LLM, CustomExecutor())
        assert isinstance(EXECUTOR_REGISTRY[NodeType.LLM], CustomExecutor)

        # 恢复原始执行器
        register_executor(NodeType.LLM, original)


# ============================================================
# WorkflowExecutor 测试
# ============================================================


class TestWorkflowExecutor:
    """工作流执行引擎测试。"""

    def test_execute_simple_chain(self):
        """简单链式工作流执行。"""
        wf = WorkflowDefinition(
            name="简单链",
            nodes=[
                NodeDefinition(
                    id="n1", type=NodeType.CODE, inputs=[], config={"code": "result = '第一步'"}
                ),
                NodeDefinition(
                    id="n2",
                    type=NodeType.AGGREGATOR,
                    inputs=["n1"],
                    config={"aggregation_type": "latest"},
                ),
            ],
            start_node="n1",
            end_nodes=["n2"],
        )
        executor = WorkflowExecutor()
        result = executor.execute(wf)
        assert result["status"] in ("completed", "partial")
        assert result["total_count"] == 2

    def test_execute_with_condition_branch(self):
        """条件分支工作流。"""
        wf = WorkflowDefinition(
            name="条件分支",
            nodes=[
                NodeDefinition(
                    id="start", type=NodeType.CODE, inputs=[], config={"code": "result = 0.8"}
                ),
                NodeDefinition(
                    id="check",
                    type=NodeType.CONDITION,
                    inputs=["start"],
                    config={
                        "condition": "{{start}} > 0.5",
                        "branches": {"true": "yes", "false": "no"},
                    },
                ),
                NodeDefinition(
                    id="yes",
                    type=NodeType.CODE,
                    inputs=["check"],
                    config={"code": "result = '通过了'"},
                ),
                NodeDefinition(
                    id="no",
                    type=NodeType.CODE,
                    inputs=["check"],
                    config={"code": "result = '未通过'"},
                ),
                NodeDefinition(
                    id="end",
                    type=NodeType.AGGREGATOR,
                    inputs=["yes", "no"],
                    config={"aggregation_type": "latest"},
                ),
            ],
            start_node="start",
            end_nodes=["end"],
        )
        executor = WorkflowExecutor()
        result = executor.execute(wf)
        assert result["total_count"] == 5
        # "no" 分支应被跳过
        assert result["results"]["no"]["status"] == "skipped"

    def test_execute_invalid_dag(self):
        """无效DAG工作流。"""
        wf = WorkflowDefinition(
            name="无效DAG",
            nodes=[
                NodeDefinition(id="n1", type=NodeType.LLM, inputs=["n2"]),
                NodeDefinition(id="n2", type=NodeType.CODE, inputs=["n1"]),
            ],
            start_node="n1",
            end_nodes=["n2"],
        )
        executor = WorkflowExecutor()
        result = executor.execute(wf)
        assert result["status"] == "failed"
        assert "循环" in result["error"] or "入口" in result["error"]

    def test_execute_with_fallback(self):
        """失败节点降级到fallback。"""
        wf = WorkflowDefinition(
            name="降级测试",
            nodes=[
                NodeDefinition(
                    id="n1",
                    type=NodeType.CODE,
                    inputs=[],
                    config={"code": "import os\nresult = '危险操作'"},
                    fallback="n1_fb",
                ),
                NodeDefinition(
                    id="n1_fb",
                    type=NodeType.CODE,
                    inputs=[],
                    config={"code": "result = '安全降级'"},
                ),
                NodeDefinition(
                    id="n2",
                    type=NodeType.AGGREGATOR,
                    inputs=["n1"],
                    config={"aggregation_type": "latest"},
                ),
            ],
            start_node="n1",
            end_nodes=["n2"],
        )
        executor = WorkflowExecutor()
        result = executor.execute(wf)
        # n1 被安全拦截，降级到 n1_fb
        assert "n1_fb" in result["results"]

    def test_checkpoint_save_and_get(self):
        """检查点保存与读取。"""
        wf = WorkflowDefinition(
            name="检查点测试",
            nodes=[
                NodeDefinition(
                    id="n1",
                    type=NodeType.CODE,
                    inputs=[],
                    config={"code": "result = 'checkpoint_test'"},
                ),
            ],
            start_node="n1",
            end_nodes=["n1"],
        )
        executor = WorkflowExecutor()
        executor.execute(wf)
        checkpoint = executor.get_checkpoint(wf.id, "n1")
        assert checkpoint is not None
        assert checkpoint["status"] == "success"


# ============================================================
# 便捷API测试
# ============================================================


class TestConvenienceAPI:
    """便捷API测试。"""

    def test_create_simple_workflow(self):
        """快捷创建线性工作流。"""
        wf = create_simple_workflow(
            "测试工作流",
            [
                {"type": "code", "config": {"code": "result = 'step1'"}},
                {"type": "code", "config": {"code": "result = 'step2'"}},
                {"type": "aggregator", "config": {"aggregation_type": "latest"}},
            ],
        )
        assert wf.name == "测试工作流"
        assert len(wf.nodes) == 3
        assert wf.start_node == "node_0"

    def test_validate_workflow(self):
        """快捷验证工作流。"""
        wf = create_simple_workflow(
            "验证测试",
            [
                {"type": "llm", "config": {"prompt_template": "测试"}},
            ],
        )
        valid, msg = validate_workflow(wf)
        assert valid is True

    def test_run_workflow(self):
        """快捷执行工作流。"""
        wf = create_simple_workflow(
            "运行测试",
            [
                {"type": "code", "config": {"code": "result = 42"}},
                {"type": "aggregator", "config": {"aggregation_type": "latest"}},
            ],
        )
        result = run_workflow(wf)
        assert result["status"] in ("completed", "partial")
        assert result["total_count"] == 2
