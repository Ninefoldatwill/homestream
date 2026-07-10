# HomeStream Windows 一键安装
# iwr -useb https://raw.githubusercontent.com/Ninefoldatwill/homestream/main/install.ps1 | iex
param(
    [string]$InstallDir = "$env:USERPROFILE\.homestream",
    [string]$Version = "5.0.0"
)

$ErrorActionPreference = "Stop"
$BR = "HomeStream"
$RepoGithub = "https://github.com/Ninefoldatwill/homestream"
$RepoGitee = "https://gitee.com/jiuchong/homestream"
$REPO = $RepoGithub

Write-Host ""
Write-Host "  ⚓  HomeStream v$Version — 有温度的自进化AI生态" -ForegroundColor Cyan
Write-Host "  一条命令 · 零配置 · 30秒上手" -ForegroundColor Cyan
Write-Host ""

# ── 检查 Python ───────────────────────────────────────────
Write-Host "[1/5] 检查 Python 环境..." -ForegroundColor Blue
$Python = $null
foreach ($cmd in @("python3.13", "python3.12", "python3.11", "python3.10", "python3", "python")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        try {
            $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($ver -match "^3\.1[0-9]$") {
                $Python = $cmd
                break
            }
        } catch {}
    }
}

if (-not $Python) {
    Write-Host "  错误: 需要 Python 3.10-3.13" -ForegroundColor Red
    Write-Host "  请从 https://python.org 下载安装（勾选 Add to PATH）" -ForegroundColor Yellow
    exit 1
}
Write-Host "  √ 找到 $Python" -ForegroundColor Green

# ── 创建目录 ──────────────────────────────────────────────
Write-Host "[2/5] 准备安装目录..." -ForegroundColor Blue
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Write-Host "  √ $InstallDir" -ForegroundColor Green

# ── 虚拟环境 ──────────────────────────────────────────────
Write-Host "[3/5] 创建虚拟环境..." -ForegroundColor Blue
$VenvDir = Join-Path $InstallDir "venv"
if (-not (Test-Path $VenvDir)) {
    & $Python -m venv $VenvDir
    Write-Host "  √ 虚拟环境已创建" -ForegroundColor Green
} else {
    Write-Host "  ◦ 复用已有虚拟环境" -ForegroundColor Yellow
}

$Pip = Join-Path $VenvDir "Scripts" "pip.exe"
$Py = Join-Path $VenvDir "Scripts" "python.exe"
& $Pip install --upgrade pip -q

# ── 安装 ──────────────────────────────────────────────────
Write-Host "[4/5] 安装 HomeStream v$Version..." -ForegroundColor Blue
$installed = $false
try {
    & $Pip install "openbridge>=$Version" -q 2>$null
    Write-Host "  √ 从 PyPI 安装成功" -ForegroundColor Green
    $installed = $true
} catch {}

if (-not $installed) {
    Write-Host "  ◦ PyPI 不可用，尝试从源码安装..." -ForegroundColor Yellow
    $TmpDir = Join-Path $env:TEMP "homestream_$(Get-Random)"
    # 双源回退：先 GitHub，失败则 Gitee（国内用户友好）
    git clone --depth 1 $RepoGithub $TmpDir -q 2>$null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $TmpDir)) {
        git clone --depth 1 $RepoGitee $TmpDir -q 2>$null
    }
    if ((Test-Path $TmpDir) -and (Test-Path (Join-Path $TmpDir "pyproject.toml"))) {
        & $Pip install $TmpDir -q
        Write-Host "  √ 从源码安装成功" -ForegroundColor Green
        $installed = $true
    }
}
if (-not $installed) {
    Write-Host "  ◦ 安装核心依赖..." -ForegroundColor Yellow
    & $Pip install fastapi uvicorn pydantic pydantic-settings structlog rich typer httpx -q
}
Write-Host "  √ 依赖安装完成" -ForegroundColor Green

# ── 配置 ──────────────────────────────────────────────────
Write-Host "[5/5] 完成配置..." -ForegroundColor Blue
$EnvFile = Join-Path $InstallDir ".env"
if (-not (Test-Path $EnvFile)) {
@"
# HomeStream 配置文件
# 详细文档: $REPO#配置
# 国内镜像: $RepoGitee

# 模式
OPENBRIDGE_MODE=solo

# 本地模型（可选）
OPENBRIDGE_LLAMA_SERVER=http://127.0.0.1:8080

# GLM API（可选）
OPENBRIDGE_GLM_KEY=

# DeepSeek API（可选）
OPENBRIDGE_DS_KEY=

# 书阁知识库（可选）
OPENBRIDGE_BOOKHOUSE_URL=http://127.0.0.1:3460
"@ | Out-File -FilePath $EnvFile -Encoding UTF8
    Write-Host "  √ 配置文件已创建: $EnvFile" -ForegroundColor Green
} else {
    Write-Host "  ◦ 配置文件已存在" -ForegroundColor Yellow
}

# ── 快捷命令 ──────────────────────────────────────────────
$BinDir = Join-Path $InstallDir "bin"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$LinkPath = Join-Path $BinDir "openbridge.ps1"
@"
`$Py = "$Py"
& `$Py -m openbridge @args
"@ | Out-File -FilePath $LinkPath -Encoding UTF8

# 添加到 PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$BinDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
    $env:Path = "$env:Path;$BinDir"
}

# ── 结果 ──────────────────────────────────────────────────
Write-Host ""
Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host "  HomeStream v$Version 安装完成！" -ForegroundColor Green
Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Green
Write-Host ""
Write-Host "  启动命令:" -ForegroundColor White
Write-Host "    openbridge serve          # 启动服务 -> http://localhost:3458" -ForegroundColor Cyan
Write-Host "    openbridge serve --mode team # 团队模式" -ForegroundColor Cyan
Write-Host ""
Write-Host "  快速开始:" -ForegroundColor White
Write-Host "    $EnvFile" -ForegroundColor Yellow
Write-Host "    openbridge serve" -ForegroundColor Cyan
Write-Host "    http://localhost:3458" -ForegroundColor Cyan
Write-Host ""
Write-Host "  加入社区:" -ForegroundColor White
Write-Host "    GitHub: $RepoGithub" -ForegroundColor Cyan
Write-Host "    Gitee:  $RepoGitee（国内推荐）" -ForegroundColor Cyan
Write-Host ""
