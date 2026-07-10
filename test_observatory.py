"""可观测性仪表盘测试 — observatory.py + /api/v7/observatory 端点

验证项：
  1. collect_observatory_data 降级安全（None输入不崩溃）
  2. 返回结构完整性（summary + panels + providers）
  3. Prometheus 指标读取函数
  4. API端点集成测试
"""

from unittest.mock import MagicMock

from observatory import (
    _calc_percentiles,
    _group_http_by_endpoint,
    _parse_label_str,
    _read_counter_by_label,
    _read_counter_total,
    _read_gauge,
    collect_observatory_data,
)


class TestCollectObservatoryDataDegradation:
    """降级安全测试：数据源缺失时不应崩溃"""

    def test_all_none(self):
        """所有数据源都为None时应正常返回"""
        result = collect_observatory_data(
            event_store=None,
            model_router=None,
            session_id="test",
        )
        assert "timestamp" in result
        assert "summary" in result
        assert "panels" in result
        assert "providers" in result
        assert result["providers"] == []
        assert result["summary"]["http_total_requests"] >= 0

    def test_no_crash_with_empty_store(self):
        """空EventStore不崩溃"""
        mock_store = MagicMock()
        mock_store.stats.return_value = {
            "total_events": 0,
            "by_type": {},
            "top_senders": {},
            "sessions": {},
        }
        result = collect_observatory_data(
            event_store=mock_store,
            model_router=None,
        )
        assert result["panels"]["event_distribution"]["total"] == 0

    def test_store_stats_exception_handled(self):
        """EventStore.stats()异常不崩溃"""
        mock_store = MagicMock()
        mock_store.stats.side_effect = RuntimeError("DB error")
        result = collect_observatory_data(
            event_store=mock_store,
            model_router=None,
        )
        assert result["panels"]["event_distribution"]["total"] == 0


class TestReturnStructure:
    """返回结构完整性测试"""

    def test_summary_fields(self):
        result = collect_observatory_data()
        s = result["summary"]
        expected_keys = {
            "http_success_rate",
            "http_total_requests",
            "total_events",
            "active_sessions",
            "active_connections",
            "total_tokens_in",
            "total_tokens_out",
            "total_cost",
            "skill_success_rate",
            "strategy",
        }
        assert expected_keys.issubset(s.keys())

    def test_panels_fields(self):
        result = collect_observatory_data()
        p = result["panels"]
        expected_panels = {
            "success_rate",
            "latency",
            "token_cost",
            "per_request_cost",
            "event_distribution",
            "tool_execution",
            "cost_breakdown",
            "active_throughput",
        }
        assert expected_panels.issubset(p.keys())

    def test_success_rate_panel(self):
        result = collect_observatory_data()
        sr = result["panels"]["success_rate"]
        assert "success" in sr
        assert "error" in sr
        assert "total" in sr
        assert "rate" in sr
        assert "by_endpoint" in sr
        assert 0 <= sr["rate"] <= 1

    def test_latency_panel(self):
        result = collect_observatory_data()
        lat = result["panels"]["latency"]
        assert "avg_ms" in lat
        assert "total_count" in lat
        assert "buckets" in lat
        assert "percentiles_ms" in lat

    def test_cost_breakdown_panel(self):
        result = collect_observatory_data()
        cb = result["panels"]["cost_breakdown"]
        assert "by_tier" in cb
        assert "total" in cb
        assert "L1" in cb["by_tier"]
        assert "L2" in cb["by_tier"]
        assert "L3" in cb["by_tier"]


class TestPrometheusHelpers:
    """Prometheus指标读取辅助函数测试"""

    def test_read_counter_total_returns_float(self):
        val = _read_counter_total("bridge_http_requests_total")
        assert isinstance(val, float)
        assert val >= 0

    def test_read_counter_by_label_returns_dict(self):
        result = _read_counter_by_label("bridge_icp_messages_sent_total", "message_type")
        assert isinstance(result, dict)

    def test_read_gauge_returns_float(self):
        val = _read_gauge("bridge_active_connections")
        assert isinstance(val, float)
        assert val >= 0

    def test_calc_percentiles_empty(self):
        result = _calc_percentiles({}, 0)
        assert result == {"p50": 0, "p75": 0, "p90": 0, "p95": 0, "p99": 0}

    def test_calc_percentiles_with_data(self):
        buckets = {"0.01": 10, "0.05": 30, "0.1": 50, "0.5": 80, "1.0": 90, "5.0": 100}
        result = _calc_percentiles(buckets, 100)
        assert result["p50"] > 0
        assert result["p95"] > 0
        assert result["p99"] > 0

    def test_parse_label_str(self):
        result = _parse_label_str("method=GET|endpoint=/api/test|status=200")
        assert result["method"] == "GET"
        assert result["endpoint"] == "/api/test"
        assert result["status"] == "200"

    def test_group_http_by_endpoint(self):
        raw = {
            "method=GET|endpoint=/api/test|status=200": 10,
            "method=GET|endpoint=/api/test|status=500": 2,
            "method=POST|endpoint=/api/submit|status=200": 5,
        }
        result = _group_http_by_endpoint(raw)
        assert len(result) == 2
        # /api/test should have total=12, success=10, error=2
        test_entry = next(r for r in result if r["endpoint"] == "/api/test")
        assert test_entry["total"] == 12
        assert test_entry["success"] == 10
        assert test_entry["error"] == 2


class TestWithMockModelRouter:
    """使用Mock ModelRouter测试"""

    def test_provider_stats_aggregation(self):
        """测试Provider统计聚合"""
        mock_router = MagicMock()
        mock_router._initialized = True
        mock_provider = MagicMock()
        mock_provider.config.cost_per_1k_input = 0.0
        mock_provider.config.cost_per_1k_output = 0.01
        mock_provider.config.max_tokens = 512
        mock_router.registry.get.return_value = mock_provider
        mock_router.get_status.return_value = {
            "strategy": "cost_first",
            "hardware": {"cpu": "test"},
            "providers": [
                {
                    "name": "test_provider",
                    "display_name": "Test Provider",
                    "tier": "L1",
                    "model": "test-model",
                    "status": "healthy",
                    "enabled": True,
                    "stats": {"requests": 10, "errors": 1, "avg_latency_ms": 500},
                }
            ],
        }

        result = collect_observatory_data(model_router=mock_router)
        assert len(result["providers"]) == 1
        p = result["providers"][0]
        assert p["name"] == "test_provider"
        assert p["requests"] == 10
        assert p["errors"] == 1
        assert p["error_rate"] == 0.1
        assert p["est_tokens_in"] == 2000  # 10 * 200
        assert result["summary"]["strategy"] == "cost_first"
        assert result["hardware"] == {"cpu": "test"}
