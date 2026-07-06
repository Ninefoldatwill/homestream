"""
OpenBridge CLI 测试套件

测试所有CLI命令的正确性：
  - version: 版本显示
  - init: 初始化流程
  - start/stop: 服务管理
  - status: 状态查询
  - mode: 模式切换
  - doctor: 诊断功能
  - test: 测试运行
"""

import subprocess
import sys
import os
import json
from pathlib import Path

import pytest

# 项目根目录（test文件就在根目录下）
PROJECT_ROOT = Path(__file__).resolve().parent

# Python可执行文件
PYTHON = sys.executable


def run_cli(*args, timeout=30):
    """运行CLI命令并返回结果（合并stdout+stderr，因为Rich输出到stderr）"""
    cli_path = PROJECT_ROOT / "openbridge" / "cli.py"
    cmd = [PYTHON, str(cli_path)] + list(args)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # Rich默认输出到stderr，合并到stdout方便测试
    env["NO_COLOR"] = "1"  # 测试时禁用颜色以简化断言
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    # 合并stdout+stderr
    result.stdout = result.stdout + result.stderr
    return result


class TestVersion:
    """版本命令测试"""

    def test_version_flag(self):
        """--version 显示版本号"""
        result = run_cli("--version")
        assert result.returncode == 0
        assert "OpenBridge" in result.stdout
        assert "v5.0" in result.stdout

    def test_version_short_flag(self):
        """-v 短选项"""
        result = run_cli("-v")
        assert result.returncode == 0
        assert "OpenBridge" in result.stdout

    def test_version_contains_python_info(self):
        """版本输出包含Python信息"""
        result = run_cli("--version")
        assert "Python" in result.stdout
        assert platform.system() in result.stdout or "Windows" in result.stdout


class TestHelp:
    """帮助命令测试"""

    def test_help_flag(self):
        """--help 显示帮助"""
        result = run_cli("--help")
        assert result.returncode == 0
        assert "OpenBridge" in result.stdout
        assert "init" in result.stdout
        assert "start" in result.stdout
        assert "status" in result.stdout
        assert "doctor" in result.stdout

    def test_no_command_shows_help(self):
        """无子命令时Typer显示Usage提示"""
        result = run_cli()
        # Typer默认exit code 2 + "Missing command"提示（与click不同）
        assert "Usage" in result.stdout or "openbridge" in result.stdout.lower()

    def test_subcommand_help(self):
        """子命令帮助"""
        for cmd in ["init", "start", "stop", "status", "mode", "doctor", "test"]:
            result = run_cli(cmd, "--help")
            assert result.returncode == 0, f"{cmd} --help failed: {result.stderr}"


class TestStatus:
    """状态命令测试"""

    def test_status_basic(self):
        """status 基本输出"""
        result = run_cli("status")
        assert result.returncode == 0
        assert "OpenBridge" in result.stdout
        assert "版本" in result.stdout

    def test_status_json(self):
        """status --json JSON输出"""
        result = run_cli("status", "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "version" in data
        assert "running" in data
        assert "port" in data

    def test_status_custom_port(self):
        """status -p 自定义端口"""
        result = run_cli("status", "-p", "9999")
        assert result.returncode == 0
        assert "9999" in result.stdout


class TestMode:
    """模式命令测试"""

    def test_mode_display(self):
        """mode 查看当前模式"""
        result = run_cli("mode")
        assert result.returncode == 0
        assert "模式" in result.stdout or "mode" in result.stdout.lower()

    def test_mode_switch_solo(self):
        """mode solo 切换到solo"""
        # 先保存当前模式
        env_path = PROJECT_ROOT / ".env"
        env_existed = env_path.exists()
        original = env_path.read_text(encoding="utf-8") if env_existed else ""

        try:
            result = run_cli("mode", "solo")
            assert result.returncode == 0
            assert "solo" in result.stdout.lower()

            # 验证.env已更新
            content = env_path.read_text(encoding="utf-8")
            assert "OPENBRIDGE_MODE=solo" in content
        finally:
            if env_existed:
                env_path.write_text(original, encoding="utf-8")
            else:
                # 测试自动创建了 .env，需要清理以免影响后续测试
                try:
                    env_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def test_mode_switch_team(self):
        """mode team 切换到team"""
        env_path = PROJECT_ROOT / ".env"
        env_existed = env_path.exists()
        original = env_path.read_text(encoding="utf-8") if env_existed else ""

        try:
            result = run_cli("mode", "team")
            assert result.returncode == 0
            assert "team" in result.stdout.lower()

            content = env_path.read_text(encoding="utf-8")
            assert "OPENBRIDGE_MODE=team" in content
        finally:
            if env_existed:
                env_path.write_text(original, encoding="utf-8")
            else:
                # 测试自动创建了 .env，需要清理以免影响后续测试
                try:
                    env_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def test_mode_invalid(self):
        """mode invalid 无效模式"""
        result = run_cli("mode", "invalid")
        assert result.returncode != 0


class TestDoctor:
    """诊断命令测试"""

    def test_doctor_basic(self):
        """doctor 基本诊断"""
        result = run_cli("doctor", timeout=60)
        assert result.returncode == 0
        assert "Python" in result.stdout
        assert "配置" in result.stdout or "env" in result.stdout.lower()

    def test_doctor_checks(self):
        """doctor 包含6项检查"""
        result = run_cli("doctor", timeout=60)
        # 应该包含6个检查项
        assert "[1/6]" in result.stdout
        assert "[2/6]" in result.stdout
        assert "[3/6]" in result.stdout
        assert "[4/6]" in result.stdout
        assert "[5/6]" in result.stdout
        assert "[6/6]" in result.stdout

    def test_doctor_python_check(self):
        """doctor 检查Python版本"""
        result = run_cli("doctor", timeout=60)
        assert "3.13" in result.stdout or "3.12" in result.stdout or "3.11" in result.stdout

    def test_doctor_dependency_check(self):
        """doctor 检查依赖"""
        result = run_cli("doctor", timeout=60)
        assert "fastapi" in result.stdout.lower()
        assert "uvicorn" in result.stdout.lower()
        assert "pydantic" in result.stdout.lower()


class TestInit:
    """初始化命令测试"""

    def test_init_force(self):
        """init --force --mode team 非交互初始化"""
        result = run_cli("init", "--force", "--mode", "team", timeout=30)
        assert result.returncode == 0
        assert "初始化" in result.stdout
        assert "完成" in result.stdout


class TestStop:
    """停止命令测试"""

    def test_stop_when_not_running(self):
        """stop 当服务未运行时"""
        # 先确保PID文件不存在
        pid_file = PROJECT_ROOT / ".openbridge.pid"
        if pid_file.exists():
            pid_file.unlink()

        result = run_cli("stop")
        assert result.returncode == 0
        assert "未在运行" in result.stdout or "not running" in result.stdout.lower()


# 导入platform用于测试
import platform


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
