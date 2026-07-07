"""
conftest.py — pytest 全局配置与共享 fixture

作用：
1. 为 test_day3_integration.py 提供 `r` fixture（TestResult 实例）
2. 确保测试隔离：每个测试函数获得独立的 TestResult
3. 提供 `work_dir` fixture — 项目内临时目录（绕过 sandbox rmtree 拦截）
"""

import sys
import os
import uuid
from pathlib import Path

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


@pytest.fixture
def work_dir():
    """项目内临时目录 — 不使用 shutil.rmtree 清理（绕过 sandbox 安全拦截）。

    .pytest_tmp/ 已在 .gitignore 中，不会污染仓库。
    每个测试获得唯一子目录，互不干扰。
    """
    d = Path(PROJECT_DIR) / ".pytest_tmp" / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    yield d
