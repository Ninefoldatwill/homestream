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
        AutoSubscribe,
        JobContext,
        JobProcess,
        RoomInputOptions,
        WorkerOptions,
        cli,
    )
    from livekit.plugins import silero
    from livekit.plugins.turn_detector.multilingual import MultilingualModel

    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False
    logger.warning(
        "livekit-agents 未安装。安装: pip install "
        "'livekit-agents[silero,turn-detector]~=1.4'"
    )


# ========== Agent 定义 ==========

class VoiceBridgeAgent(Agent if _LIVEKIT_AVAILABLE else object):
    """
    HomeStream 语音 Agent

    核心: 覆写 llm_node, 将对话路由到三层 ModelRouter
    (L1本地Ollama → L2免费GLM → L3付费DeepSeek, 双保障降级)
    """

    def __init__(self, config: VoiceBridgeConfig | None = None):
        self._config = config or VoiceBridgeConfig.from_env()
        self._llm = HomeStreamLLM(strategy=self._config.router_strategy)

        if _LIVEKIT_AVAILABLE:
            super().__init__(
                instructions=(
                    "你是 HomeStream 语音助手。"
                    "回答简洁自然, 适合语音输出, 每次回复不超过 2-3 句话。"
                    "用中文回答, 语气温暖亲切。"
                ),
                allow_interruptions=self._config.allow_interruptions,
            )

    async def llm_node(self, chat_ctx, tools, model_settings):
        """
        覆写 LLM 节点 - 将对话路由到 HomeStream 三层模型路由器。

        这是 VoiceBridge 与三层路由的核心接入点:
          LiveKit ChatContext → HomeStream ChatMessage → ModelRouter.chat()
        """
        async for chunk in self._llm.llm_node(chat_ctx, tools, model_settings):
            yield chunk

    def on_enter(self):
        """Agent 加入房间时触发"""
        logger.info("VoiceBridgeAgent 已加入房间")
        if _LIVEKIT_AVAILABLE and hasattr(self, "session"):
            import asyncio

            asyncio.create_task(
                self.session.generate_reply(
                    instructions="用中文简短地打个招呼, 告诉用户你已准备好。"
                )
            )


# ========== VAD 预热 ==========

def _prewarm(proc: "JobProcess"):
    """Worker 启动时预加载 Silero VAD 模型, 减少首次连接延迟"""
    if _LIVEKIT_AVAILABLE:
        proc.userdata["vad"] = silero.VAD.load()
        logger.info("Silero VAD 预加载完成")


# ========== STT / TTS 构建 (可插拔) ==========

def _build_stt(config: VoiceBridgeConfig) -> Any | None:
    """
    构建 STT (语音转文字) 插件

    架构: FunASR 2-pass (官方推荐生产方案)
      Pass 1: paraformer-zh-streaming (实时反馈, ~80ms 延迟)
      Pass 2: SenseVoiceSmall (句末修正 + 情感 + 事件)

    Docker: registry.cn-hangzhou.aliyuncs.com/funasr_repo/funasr:funasr-runtime-sdk-online-cpu
    WebSocket: ws://localhost:10096
    """
    if not _LIVEKIT_AVAILABLE:
        return None

    try:
        from voice.stt_adapter import create_funasr_stt

        stt = create_funasr_stt(uri=config.funasr_ws_uri)
        if stt is not None:
            logger.info(
                "STT: FunASR 2-pass uri=%s (Pass1 Paraformer + Pass2 SenseVoice)",
                config.funasr_ws_uri,
            )
            return stt
        logger.warning("FunASR STT 创建失败, 尝试降级")
    except Exception as e:
        logger.warning("FunASR STT 初始化失败: %s, 尝试降级", e)

    # 降级: 尝试 OpenAI STT (如果配置了 API key)
    if config.tts_api_key and False:  # 禁用降级, 本地优先
        try:
            from livekit.plugins import openai

            logger.info("STT: OpenAI Whisper (api)")
            return openai.STT(
                model="whisper-1",
                api_key=config.tts_api_key,
            )
        except Exception as e:
            logger.warning("OpenAI STT 初始化失败: %s", e)

    logger.warning("STT 未配置, Agent 将无法处理语音输入 (文本模式)")
    return None


def _build_tts(config: VoiceBridgeConfig) -> Any | None:
    """
    构建 TTS (文字转语音) 插件

    模式:
      - "local": CosyVoice2 本地模型 (Apache 2.0, 中文第一, 18+方言, 流式合成)
      - "api": 外部 API
      - "auto": 优先 local, 降级 api
    """
    if not _LIVEKIT_AVAILABLE:
        return None

    mode = config.tts_mode

    if mode in ("local", "auto"):
        try:
            from voice.tts_adapter import create_cosyvoice_tts

            tts = create_cosyvoice_tts(
                model_path=config.tts_model_path,
                voice=config.tts_voice,
                speed=config.tts_speed,
                sample_rate=config.tts_sample_rate,
            )
            if tts is not None:
                logger.info(
                    "TTS: CosyVoice2 (local) voice=%s speed=%.1f",
                    config.tts_voice,
                    config.tts_speed,
                )
                return tts
            logger.warning("CosyVoice TTS 创建失败, 尝试降级")
        except Exception as e:
            logger.warning("CosyVoice TTS 初始化失败: %s, 尝试降级", e)

    # 降级: 尝试 OpenAI TTS
    if config.tts_api_key:
        try:
            from livekit.plugins import openai

            logger.info("TTS: OpenAI tts-1 (api) voice=%s", "alloy")
            return openai.TTS(
                model="tts-1",
                voice="alloy",
                api_key=config.tts_api_key,
                base_url=config.tts_api_base or None,
            )
        except Exception as e:
            logger.warning("OpenAI TTS 初始化失败: %s", e)

    logger.warning("TTS 未配置, Agent 将无法输出语音 (文本模式)")
    return None


# ========== Agent 入口 ==========

async def entrypoint(ctx: "JobContext"):
    """LiveKit Agent 入口 - 每次用户连接时调用"""
    config = VoiceBridgeConfig.from_env()

    logger.info(
        "VoiceBridge entrypoint 启动, LiveKit=%s, 策略=%s",
        config.livekit_url,
        config.router_strategy,
    )

    # 连接 LiveKit 房间 (仅订阅音频)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    await ctx.wait_for_participant()

    # 构建管线组件
    stt = _build_stt(config)
    tts = _build_tts(config)
    vad = ctx.proc.userdata.get("vad") if _LIVEKIT_AVAILABLE else None

    if not _LIVEKIT_AVAILABLE:
        logger.error("LiveKit SDK 不可用, 无法启动 Agent")
        return

    # 创建 AgentSession
    # 注: llm 不直接传 (由 VoiceBridgeAgent.llm_node 覆写)
    #     但 AgentSession 可能需要 llm 参数, 传 None 或占位
    session_kwargs = {
        "vad": vad,
        "turn_detection": MultilingualModel(),
    }
    if stt is not None:
        session_kwargs["stt"] = stt
    if tts is not None:
        session_kwargs["tts"] = tts

    session = AgentSession(**session_kwargs)

    agent = VoiceBridgeAgent(config=config)

    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=RoomInputOptions(
            noise_cancellation=True,  # 降噪
        ),
    )

    # 初始问候
    await session.generate_reply(
        instructions="用中文简短打招呼, 告诉用户 HomeStream 语音助手已就绪。"
    )


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

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=_prewarm,
            agent_name=config.agent_name,
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
