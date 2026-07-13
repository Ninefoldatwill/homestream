"""
VoiceBridge Agent - LiveKit Agent 入口

将 STT → 三层路由LLM → TTS → VAD 管线组装为实时语音 Agent。
自托管 LiveKit Server, 零注册, 连 localhost。

启动方式:
  # 1. 先起自托管 LiveKit Server
  cd voice && docker compose up -d

  # 2. 启动 Agent Worker
  python -m voice.agent dev    # 开发模式 (热重载)
  python -m voice.agent start  # 生产模式

架构:
  用户浏览器 ──WebRTC──→ LiveKit SFU (localhost:7880)
                              │
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
               Silero VAD  STT      TTS
                    │         │         ▲
                    │         ▼         │
                    │   HomeStreamLLM (三层路由)
                    │   L1本地→L2免费API→L3付费API
                    │         │
                    └─────────┴─────────┘
                     barge-in (DataChannel)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv

from voice.config import VoiceBridgeConfig
from voice.llm_adapter import HomeStreamLLM

logger = logging.getLogger("homestream.voice.agent")
load_dotenv()

# --- LiveKit Agents SDK (可选导入) ---
try:
    from livekit import agents
    from livekit.agents import (
        Agent,
        AgentSession,
        JobContext,
        JobProcess,
        WorkerOptions,
        cli,
    )
    from livekit.plugins import silero
    from livekit.plugins.openai import LLM as OpenAILLM

    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False
    logger.warning(
        "livekit-agents 未安装。安装: pip install 'livekit-agents[silero,turn-detector]~=1.4'"
    )


# ========== Agent 定义 ==========


class VoiceBridgeAgent(Agent if _LIVEKIT_AVAILABLE else object):
    """
    HomeStream 语音 Agent

    LiveKit 1.6.5: LLM 通过 Agent.__init__ 直接传入
    由 AgentSession 统一管理对话流程 (不再手动覆写 llm_node)
    """

    def __init__(self, llm, config: VoiceBridgeConfig | None = None):
        self._config = config or VoiceBridgeConfig.from_env()

        if _LIVEKIT_AVAILABLE:
            super().__init__(
                instructions=(
                    "你是 HomeStream 语音助手。"
                    "回答简洁自然, 适合语音输出, 每次回复不超过 2-3 句话。"
                    "用中文回答, 语气温暖亲切。"
                ),
                allow_interruptions=self._config.allow_interruptions,
                llm=llm,
            )

    async def on_enter(self) -> None:
        """Agent 加入房间时触发 (LiveKit 1.6.5: on_enter 为 async)"""
        logger.info("VoiceBridgeAgent 已加入房间")


# ========== VAD 预热 ==========


def _prewarm(proc: JobProcess):
    """Worker 启动时预加载 Silero VAD 模型, 减少首次连接延迟"""
    if _LIVEKIT_AVAILABLE:
        proc.userdata["vad"] = silero.VAD.load()
        logger.info("Silero VAD 预加载完成")


# ========== STT / TTS 构建 (可插拔) ==========


def _build_stt(config: VoiceBridgeConfig) -> Any | None:
    """
    构建 STT (语音转文字) — LiveKit 1.6.5 兼容
    FunASR 2-pass 为主, 失败则返回 None
    """
    try:
        from voice.stt_adapter import create_funasr_stt

        stt = create_funasr_stt(uri=config.funasr_ws_uri)
        if stt is not None:
            logger.info("STT: FunASR 2-pass uri=%s", config.funasr_ws_uri)
            return stt
        logger.warning("FunASR STT 返回 None (livekit/websockets 可能缺失)")
    except Exception as e:
        logger.warning("FunASR STT 不可用: %s", e)

    return None


def _build_tts(config: VoiceBridgeConfig) -> Any | None:
    """
    构建 TTS (文字转语音) 插件 — LiveKit 1.6.5 兼容

    引擎分层 (免费托底):
      1. CosyVoice2 (本地 GPU, 用户设计首选)
      2. EdgeTTS (微软中文神经语音, 兜底)
    """
    if not _LIVEKIT_AVAILABLE:
        return None
    try:
        from voice.tts_adapter import create_tts

        return create_tts(config)
    except Exception as e:
        logger.warning("TTS 创建失败: %s", e)
        return None


# ========== Agent 入口 ==========


async def entrypoint(ctx: JobContext):
    """LiveKit Agent 入口 - LiveKit 1.6.5 模式"""
    config = VoiceBridgeConfig.from_env()

    logger.info(
        "VoiceBridge entrypoint 启动, LiveKit=%s, 策略=%s",
        config.livekit_url,
        config.router_strategy,
    )

    if not _LIVEKIT_AVAILABLE:
        logger.error("LiveKit SDK 不可用, 无法启动 Agent")
        return

    # 1. 构建组件
    stt = _build_stt(config)
    tts = _build_tts(config)

    # 2. VAD (prewarm 已预加载, 失败则现场加载)
    vad = ctx.proc.userdata.get("vad")
    if vad is None:
        try:
            vad = silero.VAD.load()
            logger.info("Silero VAD 现场加载完成")
        except Exception as e:
            logger.warning("Silero VAD 加载失败: %s", e)

    # 3. 创建 LLM
    llm = OpenAILLM(base_url="http://localhost:11434/v1", model="qwen2.5:3b", api_key="not-needed")
    logger.info("LLM: Ollama qwen2.5:3b (OpenAI compat)")

    # 4. 创建 Agent
    agent = VoiceBridgeAgent(llm=llm, config=config)

    # 5. 创建 Session (vad 必填, stt/tts 可选)
    sess_kw: dict = {}
    if vad is not None:
        sess_kw["vad"] = vad
    else:
        logger.error("VAD 缺失, AgentSession 无法启动!")
    if stt is not None:
        sess_kw["stt"] = stt
    else:
        logger.warning("STT 未接入 -> Agent 将听不到语音!")
    if tts is not None:
        sess_kw["tts"] = tts
    else:
        logger.warning("TTS 未接入 -> Agent 将无法语音回复!")
    session = AgentSession(**sess_kw)

    # 6. 启动 (session.start 内部会连接房间, 无需再 ctx.connect())
    await session.start(room=ctx.room, agent=agent)
    logger.info("VoiceBridge Agent 已就绪 (room=%s)", ctx.room.name)


# ========== CLI 入口 ==========


def main():
    """启动 VoiceBridge Agent Worker"""
    if not _LIVEKIT_AVAILABLE:
        logger.error(
            "livekit-agents 未安装。\n"
            "安装: pip install 'livekit-agents[silero,turn-detector]~=1.4'\n"
            "然后: python -m voice.agent dev"
        )
        return

    config = VoiceBridgeConfig.from_env()

    # 设置 LiveKit 连接环境变量 (LiveKit SDK 从 env 读取)
    os.environ["LIVEKIT_URL"] = config.livekit_url
    os.environ["LIVEKIT_API_KEY"] = config.livekit_api_key
    os.environ["LIVEKIT_API_SECRET"] = config.livekit_api_secret

    logger.info("启动 VoiceBridge Worker: %s", config.to_dict())

    # ⚠️ v5.2.5: 不再创建 dispatch rule
    # LiveKit 1.3+ 行为: 设置 agent_name 后必须显式 dispatch
    # 当前 dev 模式不传 agent_name → 启用默认自动分配
    # 生产模式再创建显式 dispatch rule
    # (原代码: 创建 agent_name=homestream-voice, room=voice-test 的 rule
    #  但因 worker 启动时已错过分发窗口, 后续用户进入房间不会被分配)

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=_prewarm,
            # dev 模式不传 agent_name, 启用 LiveKit 默认自动分配
            # (LiveKit 1.3+: 设置 agent_name 后必须显式 dispatch)
        ),
    )


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # 支持: python -m voice.agent dev / start
    if len(sys.argv) > 1 and sys.argv[1] in ("dev", "start"):
        # LiveKit CLI 会接管参数
        pass

    main()
