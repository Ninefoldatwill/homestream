"""
conftest.py — pytest 全局配置与共享 fixture

作用：
1. 为 test_day3_integration.py 提供 `r` fixture（TestResult 实例）
2. 确保测试隔离：每个测试函数获得独立的 TestResult
"""

import sys
import os

# 确保项目目录在 sys.path 中
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import pytest


@pytest.fixture
def r():
    """为 day3 集成测试提供 TestResult 实例"""
    from test_day3_integration import TestResult
    return TestResult()
