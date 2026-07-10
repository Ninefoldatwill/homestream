#!/usr/bin/env bash
# ============================================================
# HomeStream — 一条命令安装
# curl -fsSL https://raw.githubusercontent.com/Ninefoldatwill/homestream/main/install.sh | bash
#
# 参考：Hermes Agent 安装体验
# 目标：零配置、一键启动、30秒上手
# ============================================================

set -e

# ── 颜色 ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
BOLD='\033[1m'

BR="HomeStream"
VERSION="5.0.0"
REPO_GITHUB="https://github.com/Ninefoldatwill/homestream"
REPO_GITEE="https://gitee.com/jiuchong/homestream"
REPO="$REPO_GITHUB"
VENV_DIR="${HOME}/.homestream/venv"
INSTALL_DIR="${HOME}/.homestream"

# ── Banner ────────────────────────────────────────────────
echo ""
echo -e "${CYAN}  ⚓  ${BOLD}HomeStream v${VERSION}${NC} — 有温度的自进化AI生态"
echo -e "${CYAN}  一条命令 · 零配置 · 30秒上手${NC}"
echo ""

# ── 检查Python ───────────────────────────────────────────
echo -e "${BLUE}[1/5]${NC} 检查 Python 环境..."
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        if [[ "$ver" =~ ^3\.1[0-9]$ ]]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}错误:${NC} 需要 Python 3.10-3.13，请先安装"
    echo "  Ubuntu/Debian: sudo apt install python3.12 python3.12-venv"
    echo "  macOS:         brew install python@3.12"
    echo "  Windows:       从 https://python.org 下载安装（勾选 Add to PATH）"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} 找到 $PYTHON ($($PYTHON --version))"

# ── 创建目录 ──────────────────────────────────────────────
echo -e "${BLUE}[2/5]${NC} 准备安装目录..."
mkdir -p "$INSTALL_DIR"
echo -e "  ${GREEN}✓${NC} ${INSTALL_DIR}"

# ── 虚拟环境 ──────────────────────────────────────────────
echo -e "${BLUE}[3/5]${NC} 创建虚拟环境..."
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
    echo -e "  ${GREEN}✓${NC} 虚拟环境已创建"
else
    echo -e "  ${YELLOW}○${NC} 复用已有虚拟环境"
fi

PIP="$VENV_DIR/bin/pip"
PY="$VENV_DIR/bin/python"
"$PIP" install --upgrade pip -q

# ── 安装 HomeStream ───────────────────────────────────────
echo -e "${BLUE}[4/5]${NC} 安装 HomeStream v${VERSION}..."

# 尝试从 PyPI 安装，失败则从源码安装（双源回退：GitHub → Gitee）
if "$PIP" install "openbridge>=$VERSION" -q 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} 从 PyPI 安装成功"
else
    echo -e "  ${YELLOW}○${NC} PyPI 不可用，从源码安装..."
    TMP_DIR=$(mktemp -d)
    if command -v git &>/dev/null; then
        # 双源回退：先 GitHub，失败则 Gitee（国内用户友好）
        git clone --depth 1 "$REPO_GITHUB" "$TMP_DIR" -q 2>/dev/null \
            || git clone --depth 1 "$REPO_GITEE" "$TMP_DIR" -q 2>/dev/null \
            || true
    fi
    if [ -f "$TMP_DIR/pyproject.toml" ]; then
        "$PIP" install "$TMP_DIR" -q
        echo -e "  ${GREEN}✓${NC} 从源码安装成功"
    else
        # 最后的兜底：直接装核心依赖
        echo -e "  ${YELLOW}○${NC} 安装核心依赖..."
        "$PIP" install fastapi uvicorn pydantic pydantic-settings structlog rich typer -q
    fi
    rm -rf "$TMP_DIR" 2>/dev/null || true
fi

# ── 配置 ──────────────────────────────────────────────────
echo -e "${BLUE}[5/5]${NC} 完成配置..."

# 创建 .env 模板（如果不存在）
ENV_FILE="$INSTALL_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'EOF'
# HomeStream 配置文件
# 详细文档: https://github.com/Ninefoldatwill/homestream#配置
# 国内镜像: https://gitee.com/jiuchong/homestream

# 模式（solo/team/ecosystem）
OPENBRIDGE_MODE=solo

# 本地模型（可选，留空则仅云端模式可用）
OPENBRIDGE_LLAMA_SERVER=http://127.0.0.1:8080

# GLM API（可选）
OPENBRIDGE_GLM_KEY=

# DeepSeek API（可选）
OPENBRIDGE_DS_KEY=

# 书阁知识库（可选）
OPENBRIDGE_BOOKHOUSE_URL=http://127.0.0.1:3460
EOF
    echo -e "  ${GREEN}✓${NC} 配置文件已创建: ${ENV_FILE}"
else
    echo -e "  ${YELLOW}○${NC} 配置文件已存在"
fi

# ── 快捷命令 ──────────────────────────────────────────────
LINK_PATH="$INSTALL_DIR/bin/openbridge"
mkdir -p "$INSTALL_DIR/bin"
cat > "$LINK_PATH" << 'SHEOF'
#!/usr/bin/env bash
exec "$HOME/.homestream/venv/bin/python" -m openbridge "$@"
SHEOF
chmod +x "$LINK_PATH"

SHELL_RC=""
if [ -n "$ZSH_VERSION" ] || [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -n "$BASH_VERSION" ] || [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi
if [ -n "$SHELL_RC" ]; then
    if ! grep -q "openbridge" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "# HomeStream" >> "$SHELL_RC"
        echo "export PATH=\"$INSTALL_DIR/bin:\$PATH\"" >> "$SHELL_RC"
    fi
fi

# ── 结果 ──────────────────────────────────────────────────
echo ""
echo -e "  ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  ${BOLD}${GREEN}  HomeStream v${VERSION} 安装完成！${NC}"
echo -e "  ${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}启动命令:${NC}"
echo -e "    ${CYAN}openbridge serve${NC}          # 启动服务 → http://localhost:3458"
echo -e "    ${CYAN}openbridge serve --mode team${NC} # 团队模式启动"
echo ""
echo -e "  ${BOLD}快速开始:${NC}"
echo -e "    1. 编辑配置: ${YELLOW}${ENV_FILE}${NC}"
echo -e "    2. 启动服务: ${CYAN}openbridge serve${NC}"
echo -e "    3. 打开仪表盘: ${CYAN}http://localhost:3458${NC}"
echo ""
echo -e "  ${BOLD}加入社区:${NC}"
echo -e "    GitHub: ${CYAN}${REPO_GITHUB}${NC}"
echo -e "    Gitee:  ${CYAN}${REPO_GITEE}${NC}（国内推荐）"
echo ""
echo -e "  ${YELLOW}提示:${NC} 重新打开终端或执行 ${CYAN}source ${SHELL_RC:-~/.bashrc}${NC} 启用 openbridge 命令"
echo ""
