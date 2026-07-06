"""
OpenBridge CLI — 五维重构版（Typer + Rich）

第五维冲浪成果融优：
  ✦ Typer (v0.26.8, FastAPI作者): 类型提示驱动，自动帮助+补全，内置Rich
  ✦ Rich: 终端美化（表格/面板/进度条/Markdown/语法高亮）
  ✦ 2026 CLI gold standard: Typer+Rich 组合

独树一帜差异化：
  ✦ 弹性模式三档渐进（solo→team→ecosystem）— Rich表格展示功能矩阵
  ✦ 双保障自动切换 — Rich面板展示诊断结果
  ✦ ICP协议礼貌协作 — 嵌入产品理念
  ✦ init进度条 — Rich进度展示
  ✦ doctor全息诊断 — Rich面板+表格

命令清单:
  openbridge --version           显示版本（Rich格式）
  openbridge init                初始化项目（Rich进度条+硬件检测）
  openbridge start               启动服务（Rich面板展示）
  openbridge stop                停止服务
  openbridge status              查看状态（Rich表格+面板）
  openbridge mode [solo|team|eco] 切换弹性模式（Rich功能矩阵表）
  openbridge doctor              诊断问题（Rich面板+六项检查）
  openbridge test                运行测试
"""

import os
import sys
import subprocess
import platform
import shutil
import json
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.align import Align
from rich.text import Text
from rich import box

# ============================================================
# 常量
# ============================================================

VERSION = "5.0.0"

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PORT = 3458

PID_FILE = PROJECT_ROOT / ".openbridge.pid"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

# ============================================================
# Typer App + Rich Console
# ============================================================

app = typer.Typer(
    name="openbridge",
    help="OpenBridge — 有温度的自进化AI生态操作系统",
    add_completion=True,
    rich_markup_mode="rich",
)

console = Console()


# ============================================================
# 版本展示
# ============================================================

def version_callback(value: bool):
    if value:
        # Rich 格式化版本展示（独树一帜！）
        version_text = Text()
        version_text.append(f"OpenBridge ", style="bold cyan")
        version_text.append(f"v{VERSION}", style="bold green")
        version_text.append("\n")
        version_text.append("弹性模式 + ICP协议 + 双保障 + 自进化生态", style="italic")
        version_text.append("\n")
        version_text.append(f"Python {platform.python_version()} on {platform.system()}", style="dim")
        console.print(Panel(version_text, title="[bold]🌉 OpenBridge[/bold]", border_style="cyan"))
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(False, "--version", "-v", help="显示版本号", callback=version_callback, is_eager=True),
):
    """OpenBridge — 有温度的自进化AI生态操作系统

    [bold cyan]弹性模式[/] + [bold green]ICP协议[/] + [bold magenta]双保障[/] + [bold yellow]自进化生态[/]

    快速开始:
      [cyan]openbridge init[/]      初始化项目
      [cyan]openbridge start[/]     启动服务
      [cyan]openbridge status[/]    查看状态
      [cyan]openbridge doctor[/]    诊断问题
    """


# ============================================================
# init 命令 — Rich进度条 + 硬件检测
# ============================================================

@app.command()
def init(
    force: bool = typer.Option(False, "--force", "-f", help="覆盖已有.env文件"),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="非交互模式：solo/team/ecosystem"),
):
    """初始化项目（硬件检测 + 模式推荐 + .env创建 + 环境验证）

    非交互模式（用于脚本/CI）:
      [cyan]openbridge init --mode team --force[/]
    """
    console.print(Panel(
        "[bold cyan]OpenBridge 初始化向导[/bold cyan]\n融众之优，铸己之新",
        border_style="cyan",
    ))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:

        # Step 1: 硬件检测
        task1 = progress.add_task("[cyan][1/4] 检测硬件环境...", total=100)
        hw = _detect_hardware()
        progress.update(task1, advance=100)

    # 硬件信息表格（独树一帜！）
    hw_table = Table(title="硬件检测结果", box=box.ROUNDED, show_header=True)
    hw_table.add_column("项目", style="cyan")
    hw_table.add_column("结果", style="green")
    hw_table.add_row("CPU核心", str(hw["cpu_cores"]))
    hw_table.add_row("内存", f"{hw['memory_gb']:.1f} GB")
    hw_table.add_row("GPU", hw["gpu"] or "未检测到")
    hw_table.add_row("操作系统", hw["os"])
    console.print(hw_table)

    # Step 2: 模式推荐
    recommended = _recommend_mode(hw)
    mode_info = {
        "solo": ("🟢 Solo", "单Agent轻量（零配置，个人/低配）", "green"),
        "team": ("🔵 Team", "团队协作（推荐，3+Agent）", "cyan"),
        "ecosystem": ("🟣 Ecosystem", "生态扩展（完整功能+外部服务）", "magenta"),
    }
    label, desc, color = mode_info[recommended]
    console.print(Panel(f"[bold]推荐: {label}[/bold]\n{desc}", title="模式推荐", border_style=color))

    # 交互选择模式或使用指定模式
    if mode:
        selected_mode = mode
        console.print(f"  已指定: [bold]{selected_mode}[/bold]")
    else:
        selected_mode = typer.prompt(
            "请选择部署模式",
            default=recommended,
        )

    # Step 3: 创建.env
    console.print("[yellow][3/4] 创建配置文件...[/yellow]")
    if ENV_FILE.exists() and not force:
        console.print("[yellow]  .env 已存在（使用 --force 覆盖）[/yellow]")
    else:
        if ENV_EXAMPLE.exists():
            shutil.copy(ENV_EXAMPLE, ENV_FILE)
            _update_env_mode(ENV_FILE, selected_mode)
            console.print("[green]  ✓ .env 创建成功[/green]")
        else:
            console.print("[red]  .env.example 不存在，跳过[/red]")

    # Step 4: 验证环境
    console.print("[yellow][4/4] 验证Python环境...[/yellow]")
    _verify_environment_rich()

    # 完成面板（独树一帜！）
    console.print(Panel(
        "[bold green]✓ 初始化完成![/bold green]\n\n"
        "[cyan]下一步:[/cyan]\n"
        "  1. 编辑 .env 填入 Agent Token 和 API Key\n"
        "  2. [cyan]openbridge start[/cyan] 启动服务\n"
        "  3. 访问 [link=http://localhost:3458/docs]http://localhost:3458/docs[/link]",
        title="[bold]🎉 欢迎加入九重生态[/bold]",
        border_style="green",
    ))


# ============================================================
# start 命令
# ============================================================

@app.command()
def start(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="绑定地址"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="端口号"),
    reload: bool = typer.Option(False, "--reload", "-r", help="开发模式热重载"),
    background: bool = typer.Option(False, "--background", "-b", help="后台运行"),
):
    """启动 OpenBridge 服务"""
    console.print("[cyan]启动 OpenBridge 服务...[/cyan]")

    # 检查是否已在运行
    if PID_FILE.exists():
        old_pid = PID_FILE.read_text().strip()
        if _is_process_running(old_pid):
            console.print(Panel(
                f"[yellow]服务已在运行[/yellow] (PID: {old_pid})\n\n"
                "[cyan]openbridge stop[/cyan]  停止服务\n"
                "[cyan]openbridge status[/cyan]  查看状态",
                border_style="yellow",
            ))
            return
        else:
            PID_FILE.unlink()

    # 检查.env
    if not ENV_FILE.exists():
        console.print("[red].env 不存在，请先运行 [cyan]openbridge init[/cyan][/red]")
        raise typer.Exit(code=1)

    # 启动服务
    server_script = PROJECT_ROOT / "bridge_v7_server.py"
    if not server_script.exists():
        console.print(f"[red]bridge_v7_server.py 不存在于 {PROJECT_ROOT}[/red]")
        raise typer.Exit(code=1)

    if background:
        console.print("  后台模式启动中...")
        proc = subprocess.Popen(
            [sys.executable, str(server_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(PROJECT_ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
        )
        PID_FILE.write_text(str(proc.pid))
        time.sleep(2)

        if _check_health(port):
            console.print(Panel(
                f"[bold green]✓ 服务已启动[/bold green] (PID: {proc.pid})\n\n"
                f"[link=http://localhost:{port}/docs]API文档[/link]\n"
                f"[link=http://localhost:{port}/meeting]会议室[/link]\n"
                f"[link=http://localhost:{port}/metrics]指标[/link]",
                border_style="green",
            ))
        else:
            console.print(Panel(
                "[yellow]服务可能启动失败[/yellow]\n[cyan]openbridge doctor[/cyan] 诊断问题",
                border_style="yellow",
            ))
    else:
        console.print(Panel(
            f"[cyan]前台运行[/cyan]\n\n"
            f"地址: http://localhost:{port}\n"
            f"API: http://localhost:{port}/docs\n"
            f"[dim]按 Ctrl+C 停止[/dim]",
            border_style="cyan",
        ))

        env = os.environ.copy()
        env["HOST"] = host
        env["PORT"] = str(port)

        cmd = [sys.executable, str(server_script)]
        try:
            subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env)
        except KeyboardInterrupt:
            console.print("\n[yellow]服务已停止[/yellow]")


# ============================================================
# stop 命令
# ============================================================

@app.command()
def stop(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="端口号"),
):
    """停止 OpenBridge 服务"""
    console.print("[cyan]停止 OpenBridge 服务...[/cyan]")

    stopped = False

    if PID_FILE.exists():
        pid = PID_FILE.read_text().strip()
        if _is_process_running(pid):
            try:
                if platform.system() == "Windows":
                    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                else:
                    subprocess.run(["kill", pid], capture_output=True)
                console.print(f"[green]✓ 已停止进程 (PID: {pid})[/green]")
                stopped = True
            except Exception as e:
                console.print(f"[red]停止失败: {e}[/red]")
        PID_FILE.unlink()

    if not stopped:
        pid = _find_pid_by_port(port)
        if pid:
            try:
                if platform.system() == "Windows":
                    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                else:
                    subprocess.run(["kill", pid], capture_output=True)
                console.print(f"[green]✓ 已停止端口 {port} 的进程 (PID: {pid})[/green]")
                stopped = True
            except Exception as e:
                console.print(f"[red]停止失败: {e}[/red]")

    if not stopped:
        console.print("[yellow]服务未在运行[/yellow]")


# ============================================================
# status 命令 — Rich表格+面板
# ============================================================

@app.command()
def status(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="端口号"),
    as_json: bool = typer.Option(False, "--json", help="JSON格式输出"),
):
    """查看服务状态（Rich表格+面板）"""
    info = {
        "version": VERSION,
        "running": False,
        "mode": "unknown",
        "providers": [],
        "port": port,
    }

    health = _get_health(port)
    if health:
        info["running"] = True
        info["mode"] = health.get("mode", "unknown")
        info["uptime"] = health.get("uptime", "unknown")

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("OPENBRIDGE_MODE="):
                info["mode"] = line.split("=", 1)[1].strip()
                break

    if as_json:
        console.print(json.dumps(info, indent=2, ensure_ascii=False))
        return

    # Rich状态面板（独树一帜！）
    status_table = Table(title="🌉 OpenBridge 状态", box=box.ROUNDED, show_header=False)
    status_table.add_column("项目", style="cyan", width=12)
    status_table.add_column("结果", style="bold", width=40)

    status_table.add_row("版本", f"v{info['version']}")

    if info["running"]:
        status_table.add_row("服务", "[green]✓ 运行中[/green]")
        status_table.add_row("端口", str(port))
        status_table.add_row("模式", f"[bold]{info['mode']}[/bold]")
        status_table.add_row("API文档", f"http://localhost:{port}/docs")
        status_table.add_row("会议室", f"http://localhost:{port}/meeting")
        status_table.add_row("指标", f"http://localhost:{port}/metrics")
    else:
        status_table.add_row("服务", "[red]✗ 未运行[/red]")
        status_table.add_row("端口", str(port))
        status_table.add_row("模式", f"{info['mode']}（配置中）")
        status_table.add_row("启动", "[cyan]openbridge start[/cyan]")

    console.print(status_table)

    # 双保障状态（如果有配置）
    if ENV_FILE.exists():
        env_content = ENV_FILE.read_text()
        dual_enabled = "MODEL_ROUTER_DUAL_REDUNDANCY=true" in env_content
        dual_color = "green" if dual_enabled else "yellow"
        dual_status = "✓ 已启用" if dual_enabled else "⚠ 未启用"
        dual_panel = Panel(
            f"[bold {dual_color}]{dual_status}[/bold {dual_color}]\n\n"
            f"主线路: L1(Qwen) + L2(GLM) — 10秒超时\n"
            f"复线:   L3(DeepSeek) — 15秒超时\n"
            + ("主线路失败→自动切换复线" if dual_enabled else "[dim]设置 MODEL_ROUTER_DUAL_REDUNDANCY=true[/dim]"),
            title="[bold]双保障[/bold]",
            border_style=dual_color,
        )
        console.print(dual_panel)


# ============================================================
# mode 命令 — Rich功能矩阵表格（独树一帜！）
# ============================================================

@app.command()
def mode(
    mode_name: Optional[str] = typer.Argument(None, help="模式名: solo/team/ecosystem"),
):
    """查看或切换弹性模式

    三档渐进式模式:
      [green]solo[/]       单Agent轻量（零配置启动）
      [cyan]team[/]        团队协作（推荐，3+Agent）
      [magenta]ecosystem[/]  生态扩展（完整功能）
    """

    # 功能矩阵数据
    all_features = [
        ("EventStream", True, True, True),
        ("群聊", True, True, True),
        ("Prometheus", True, True, True),
        ("structlog", True, True, True),
        ("Kanban", False, True, True),
        ("Worktree", False, True, True),
        ("Ratchet Loop", False, True, True),
        ("Handoff", False, True, True),
        ("ICP v2", False, True, True),
        ("Trace", False, True, True),
        ("A2A协议", False, False, True),
        ("MCP Server", False, False, True),
        ("外部Agent", False, False, True),
        ("Webhook", False, False, True),
        ("Rate Limit", False, False, True),
    ]

    mode_colors = {"solo": "green", "team": "cyan", "ecosystem": "magenta"}
    mode_labels = {"solo": "🟢 Solo", "team": "🔵 Team", "ecosystem": "🟣 Ecosystem"}
    mode_desc = {
        "solo": "单Agent轻量模式 — 零配置启动，适合个人/低配设备",
        "team": "团队协作模式 — 推荐，3+Agent协作，Ratchet Loop保障质量",
        "ecosystem": "生态扩展模式 — 完整功能，MCP+A2A+外部Agent",
    }

    if not mode_name:
        # 查看当前模式 + 功能矩阵表
        current = "unknown"
        if ENV_FILE.exists():
            for line in ENV_FILE.read_text().splitlines():
                if line.startswith("OPENBRIDGE_MODE="):
                    current = line.split("=", 1)[1].strip()
                    break

        # 功能矩阵表格（独树一帜！其他Agent CLI都没这个）
        feature_table = Table(
            title="弹性模式功能矩阵",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold",
        )
        feature_table.add_column("功能", style="white", width=16)
        feature_table.add_column("🟢 Solo", style="green", width=8)
        feature_table.add_column("🔵 Team", style="cyan", width=8)
        feature_table.add_column("🟣 Ecosystem", style="magenta", width=12)

        current_color = mode_colors.get(current, "yellow")
        for feat, s, t, e in all_features:
            s_mark = "✓" if s else "—"
            t_mark = "✓" if t else "—"
            e_mark = "✓" if e else "—"
            feature_table.add_row(feat, s_mark, t_mark, e_mark)

        console.print(feature_table)
        console.print(Panel(
            f"[bold]当前模式: {mode_labels.get(current, current)}[/bold]\n"
            f"{mode_desc.get(current, '未配置')}\n\n"
            "[cyan]切换模式:[/cyan]\n"
            "  [green]openbridge mode solo[/]       轻量模式\n"
            "  [cyan]openbridge mode team[/]        团队模式（推荐）\n"
            "  [magenta]openbridge mode ecosystem[/]  生态模式",
            border_style=current_color,
        ))
    else:
        # 切换模式
        valid_modes = ["solo", "team", "ecosystem"]
        if mode_name not in valid_modes:
            console.print(f"[red]无效模式: {mode_name}（可选: {', '.join(valid_modes)}）[/red]")
            raise typer.Exit(code=1)

        if not ENV_FILE.exists():
            # 开源版友好：自动从 .env.example 创建（如果模板存在）
            if ENV_EXAMPLE.exists():
                shutil.copy(ENV_EXAMPLE, ENV_FILE)
                console.print("[yellow]  .env 已从 .env.example 模板创建[/yellow]")
            else:
                # 无模板则创建最小配置（仅设模式）
                ENV_FILE.write_text(f"OPENBRIDGE_MODE={mode_name}\n", encoding="utf-8")
                console.print("[yellow]  .env 已自动创建[/yellow]")

        _update_env_mode(ENV_FILE, mode_name)

        # 显示切换后的功能矩阵
        color = mode_colors[mode_name]
        label = mode_labels[mode_name]
        enabled = [feat for feat, s, t, e in all_features if {"solo": s, "team": t, "ecosystem": e}[mode_name]]

        console.print(Panel(
            f"[bold]模式已切换: {label}[/bold]\n\n"
            f"[bold]启用 {len(enabled)} 个功能:[/bold]\n"
            f"  {', '.join(enabled)}\n\n"
            f"[dim]重启服务使配置生效: openbridge stop && openbridge start[/dim]",
            border_style=color,
        ))


# ============================================================
# doctor 命令 — Rich面板+表格全息诊断
# ============================================================

@app.command()
def doctor(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="端口号"),
    fix: bool = typer.Option(False, "--fix", help="尝试自动修复问题"),
):
    """诊断问题（六项全息检查 + Rich面板展示）

    检查项目:
      1. Python环境和依赖
      2. .env配置完整性
      3. 服务运行状态
      4. 模型路由（L1/L2/L3）
      5. 双保障配置
      6. 数据库状态
    """
    console.print(Panel(
        "[bold cyan]OpenBridge 全息诊断[/bold cyan]\n"
        "六项检查 + 自动修复（--fix）",
        border_style="cyan",
    ))

    issues = []

    # 1. Python环境
    py_table = Table(title="[1/6] Python环境", box=box.SIMPLE, show_header=True)
    py_table.add_column("检查项", style="cyan")
    py_table.add_column("结果", style="bold")
    py_table.add_column("状态", width=6)

    py_ok = sys.version_info >= (3, 10)
    py_table.add_row("Python版本", platform.python_version(), "[green]✓[/green]" if py_ok else "[red]✗[/red]")

    # 关键依赖
    deps = ["fastapi", "uvicorn", "pydantic", "structlog", "prometheus_client", "typer", "rich"]
    for dep in deps:
        try:
            mod = __import__(dep)
            ver = getattr(mod, "__version__", "?")
            py_table.add_row(dep, ver, "[green]✓[/green]")
        except ImportError:
            py_table.add_row(dep, "未安装", "[red]✗[/red]")
            issues.append(f"缺少依赖: {dep}")
            if fix:
                subprocess.run([sys.executable, "-m", "pip", "install", dep], capture_output=True)

    console.print(py_table)

    # 2. .env配置
    env_checks = [("AGENT_TOKEN", "Agent Token"), ("OPENBRIDGE_MODE", "部署模式"), ("DEEPSEEK_API_KEY", "DeepSeek复线")]
    env_table = Table(title="[2/6] 配置文件", box=box.SIMPLE, show_header=True)
    env_table.add_column("配置项", style="cyan")
    env_table.add_column("描述")
    env_table.add_column("状态", width=8)

    if ENV_FILE.exists():
        env_table.add_row(".env", "文件", "[green]✓ 存在[/green]")
        env_content = ENV_FILE.read_text()
        for key, desc in env_checks:
            if key in env_content and "your_" not in env_content.split(key)[1].split("\n")[0]:
                env_table.add_row(key, desc, "[green]✓ 已配置[/green]")
            elif key in env_content:
                env_table.add_row(key, desc, "[yellow]⚠ 未配置[/yellow]")
                issues.append(f"{desc} 未配置")
            else:
                env_table.add_row(key, desc, "[yellow]⚠ 缺失[/yellow]")
    else:
        env_table.add_row(".env", "文件", "[red]✗ 不存在[/red]")
        issues.append(".env不存在，请运行 openbridge init")
        if fix and ENV_EXAMPLE.exists():
            shutil.copy(ENV_EXAMPLE, ENV_FILE)
            console.print("[green]  .env 已从模板创建[/green]")

    console.print(env_table)

    # 3. 服务状态
    health = _get_health(port)
    svc_status = "[green]✓ 运行中[/green]" if health else "[yellow]⚠ 未运行[/yellow]"
    if not health:
        issues.append("服务未运行")
    console.print(Panel(
        f"[3/6] 服务状态\n\n{svc_status}\n"
        + (f"版本: {health.get('version', '?')}\n模式: {health.get('mode', '?')}" if health else "[dim]启动服务: openbridge start[/dim]"),
        border_style="green" if health else "yellow",
    ))

    # 4. 模型路由
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from model_router import ModelRouter
        router = ModelRouter()
        router.auto_init_from_env()
        providers = router.registry.get_all()

        route_table = Table(title="[4/6] 模型路由", box=box.SIMPLE, show_header=True)
        route_table.add_column("层级", style="bold")
        route_table.add_column("Provider")
        route_table.add_column("状态", width=8)

        tier_styles = {"L1": "green", "L2": "cyan", "L3": "magenta"}
        if providers:
            for name, p in providers.items():
                tier = p.config.tier.value
                status = "[green]就绪 ✓[/green]" if p.config.enabled else "[yellow]已禁用[/yellow]"
                route_table.add_row(f"[{tier_styles.get(tier, 'white')}]{tier}[/]", name, status)
        else:
            route_table.add_row("—", "无可用Provider", "[red]✗[/red]")
            issues.append("无可用模型Provider")

        console.print(route_table)
    except Exception as e:
        console.print(Panel(f"[4/6] 模型路由\n\n[red]检查失败: {e}[/red]", border_style="red"))
        issues.append(f"模型路由检查失败: {e}")

    # 5. 双保障
    dual_enabled = False
    if ENV_FILE.exists():
        env_content = ENV_FILE.read_text()
        dual_enabled = "MODEL_ROUTER_DUAL_REDUNDANCY=true" in env_content

    dual_color = "green" if dual_enabled else "yellow"
    dual_status = "✓ 已启用" if dual_enabled else "⚠ 未启用"
    console.print(Panel(
        f"[5/6] 双保障配置\n\n"
        f"[bold]{dual_status}[/bold]\n\n"
        f"主线路: L1(Qwen本地) + L2(GLM免费) — 10秒超时\n"
        f"复线:   L3(DeepSeek) — 15秒超时\n"
        + ("主线路失败→10秒内自动切换复线" if dual_enabled else "[dim]启用: MODEL_ROUTER_DUAL_REDUNDANCY=true[/dim]"),
        border_style=dual_color,
    ))
    if not dual_enabled:
        issues.append("双保障未启用")

    # 6. 数据库
    db_path = PROJECT_ROOT / "events_v7.db"
    db_status = "[green]✓[/green]" if db_path.exists() else "[yellow]首次启动自动创建[/yellow]"
    db_info = f"{db_path.stat().st_size / (1024 * 1024):.1f} MB" if db_path.exists() else "不存在"
    console.print(Panel(
        f"[6/6] 数据库\n\n{db_status}\n大小: {db_info}",
        border_style="green" if db_path.exists() else "yellow",
    ))

    # 总结面板
    if issues:
        console.print(Panel(
            f"[bold yellow]发现 {len(issues)} 个问题:[/bold yellow]\n\n"
            + "\n".join(f"  {i+1}. {issue}" for i, issue in enumerate(issues))
            + "\n\n[dim]使用 --fix 尝试自动修复[/dim]",
            title="[bold]诊断总结[/bold]",
            border_style="yellow",
        ))
    else:
        console.print(Panel(
            "[bold green]✓ 所有检查通过![/bold green]\n\n系统健康，服务就绪",
            title="[bold]诊断总结[/bold]",
            border_style="green",
        ))


# ============================================================
# test 命令
# ============================================================

@app.command()
def test(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """运行测试套件"""
    console.print("[cyan]运行 OpenBridge 测试套件...[/cyan]")

    cmd = [sys.executable, "-m", "pytest", "-v" if verbose else "-q", "--tb=short"]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

    raise typer.Exit(code=result.returncode)


# ============================================================
# 辅助函数
# ============================================================

def _detect_hardware() -> dict:
    """检测硬件信息（psutil可选，缺失时降级）"""
    import multiprocessing
    try:
        import psutil
        memory_gb = psutil.virtual_memory().total / (1024**3)
    except ImportError:
        # psutil未安装时降级：用os估算
        import os
        memory_gb = 0.0  # 无法检测，降级为0
        try:
            # Windows: 用wmic尝试获取内存
            if platform.system() == "Windows":
                r = subprocess.run(
                    ["wmic", "OS", "get", "TotalVisibleMemorySize"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout.strip():
                    lines = [l.strip() for l in r.stdout.splitlines() if l.strip() and l.strip().isdigit()]
                    if lines:
                        memory_gb = int(lines[0]) / (1024**2)  # KB→GB
        except Exception:
            pass

    hw = {
        "cpu_cores": multiprocessing.cpu_count(),
        "memory_gb": round(memory_gb, 1),
        "gpu": None,
        "os": f"{platform.system()} {platform.release()}",
    }

    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            hw["gpu"] = result.stdout.strip().split("\n")[0]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return hw


def _recommend_mode(hw: dict) -> str:
    """根据硬件推荐部署模式"""
    if hw["memory_gb"] < 8:
        return "solo"
    elif hw["memory_gb"] < 16 or hw["cpu_cores"] < 4:
        return "solo"
    elif hw["gpu"]:
        return "ecosystem"
    else:
        return "team"


def _update_env_mode(env_path: Path, mode_value: str):
    """更新.env中的模式配置"""
    if not env_path.exists():
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith("OPENBRIDGE_MODE="):
            lines[i] = f"OPENBRIDGE_MODE={mode_value}"
            updated = True
            break
    if not updated:
        lines.append(f"OPENBRIDGE_MODE={mode_value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _verify_environment_rich():
    """验证Python环境（Rich输出）"""
    # Python版本
    if sys.version_info < (3, 10):
        console.print(f"[red]  Python版本: {platform.python_version()} (需要3.10+)[/red]")
    else:
        console.print(f"[green]  ✓ Python: {platform.python_version()}[/green]")

    # 关键依赖
    critical = ["fastapi", "uvicorn", "pydantic"]
    for dep in critical:
        try:
            mod = __import__(dep)
            ver = getattr(mod, "__version__", "?")
            console.print(f"[green]  ✓ {dep}: {ver}[/green]")
        except ImportError:
            console.print(f"[red]  ✗ {dep}: 未安装[/red]")

    # 可选依赖
    optional = ["structlog", "prometheus_client", "psutil", "typer", "rich"]
    for dep in optional:
        try:
            __import__(dep)
            console.print(f"[green]  ✓ {dep}[/green]")
        except ImportError:
            console.print(f"[yellow]  ⚠ {dep}: 未安装（可选）[/yellow]")


def _is_process_running(pid: str) -> bool:
    """检查进程是否在运行"""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True,
            )
            return pid in result.stdout
        else:
            os.kill(int(pid), 0)
            return True
    except (ValueError, OSError):
        return False


def _find_pid_by_port(port: int) -> str:
    """通过端口查找PID"""
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True, text=True,
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    return parts[-1]
        else:
            result = subprocess.run(
                ["lsof", "-i", f":{port}", "-t"],
                capture_output=True, text=True,
            )
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _check_health(port: int) -> bool:
    """检查服务健康状态"""
    try:
        import urllib.request
        url = f"http://localhost:{port}/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _get_health(port: int) -> dict:
    """获取服务健康信息"""
    try:
        import urllib.request
        import json as _json
        url = f"http://localhost:{port}/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return _json.loads(resp.read())
    except Exception:
        return None


# Typer CLI入口（直接运行python cli.py时使用）
# 重要：必须在所有辅助函数定义之后，否则__main__运行时函数尚未被定义
if __name__ == "__main__":
    app()
