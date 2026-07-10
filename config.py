"""
HomeStream 配置模块 — 从环境变量加载配置，绝不硬编码密钥。

来源：12-Factor App + FastAPI安全指南 + 融优实践配置模板
"""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置，从.env文件加载。"""

    # === 服务配置 ===
    app_name: str = "HomeStream"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 3458
    database_url: str = "sqlite:///./events_v7.db"

    # === 弹性模式配置 ===
    openbridge_mode: str = "team"  # solo | team | ecosystem
    openbridge_features: str = ""  # 逗号分隔的功能列表（覆盖模式默认）

    # === Agent Tokens（从.env加载，绝不硬编码） ===
    # 通用命名：用户自定义Agent名称和Token
    agent_1_token: str = ""
    agent_2_token: str = ""
    agent_3_token: str = ""
    agent_4_token: str = ""
    agent_5_token: str = ""

    # === 外部服务 ===
    deepseek_api_key: str = ""
    glm_api_key: str = ""

    # === External gateway (optional, for third-party agent bridging) ===
    openclaw_gateway_url: str = ""
    openclaw_gateway_token: str = ""

    # === Model Router ===
    llama_api_base: str = "http://localhost:1342/v1"
    llama_api_key: str = "jan-local"
    llama_model_name: str = "qwen_model.gguf"
    glm_plus_api_key: str = ""
    deepseek_reasoner_api_key: str = ""

    # === Model Router 双保障配置 ===
    model_router_dual_redundancy: bool = True  # 是否启用双保障
    model_router_primary_tiers: str = "L1,L2"  # 主线路层级（逗号分隔）
    model_router_backup_tier: str = "L3"  # 复线层级
    model_router_timeout_primary: int = 10  # 主线路超时（秒）
    model_router_timeout_backup: int = 15  # 复线超时（秒）

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()

# Token → Agent名称映射（从环境变量动态构建）
# 用户在.env中定义 AGENT_1_NAME / AGENT_2_NAME ... 对应Agent名称
AGENT_TOKENS = {}
for i in range(1, 6):
    _token = getattr(settings, f"agent_{i}_token", "")
    _name = os.environ.get(f"AGENT_{i}_NAME", f"Agent-{i}")
    if _token:
        AGENT_TOKENS[_token] = _name

# Agent名称 → Token反向映射（便于频道路由）
AGENT_NAMES = {v: k for k, v in AGENT_TOKENS.items()}

# 持久化数据库路径（相对于项目根目录）
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
V7_DB_PATH = os.path.join(_PROJECT_DIR, "events_v7.db")

# v6兼容路径（开源版默认空，用户可自定义）
V6_DB_PATH = os.environ.get("V6_DB_PATH", "")

# === 把.env中的关键变量写回os.environ（供ModelRouter等模块用os.getenv读取） ===
_env_sync_keys = [
    "LLAMA_API_BASE",
    "LLAMA_API_KEY",
    "LLAMA_MODEL_NAME",
    "GLM_API_KEY",
    "GLM_PLUS_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_REASONER_API_KEY",
]
for _key in _env_sync_keys:
    _val = os.environ.get(_key, "")
    if not _val:
        # 从settings对象读取（pydantic-settings已加载.env）
        _setting_attr = _key.lower()
        _val = str(getattr(settings, _setting_attr, ""))
        if _val:
            os.environ[_key] = _val
