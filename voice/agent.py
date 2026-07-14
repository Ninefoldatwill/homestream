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

import asyncio
import json
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
    from livekit.agents import llm as lk_llm
    from livekit.agents.types import APIConnectOptions, NOT_GIVEN
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
        config = VoiceBridgeConfig.from_env()
        vad = silero.VAD.load(
            activation_threshold=config.vad_threshold,
            min_speech_duration=config.vad_min_speech_duration,
            min_silence_duration=config.vad_min_silence_duration,
            prefix_padding_duration=config.vad_prefix_padding_duration,
        )
        proc.userdata["vad"] = vad
        logger.info(
            "Silero VAD 预加载完成 (threshold=%s, min_speech=%s, min_silence=%s, prefix=%s)",
            config.vad_threshold,
            config.vad_min_speech_duration,
            config.vad_min_silence_duration,
            config.vad_prefix_padding_duration,
        )


# ========== STT / TTS 构建 (可插拔) ==========


def _build_stt(config: VoiceBridgeConfig) -> Any | None:
    """
    构建 STT (语音转文字) — LiveKit 1.6.5 兼容

    默认 FunASR 2-pass (本地免费托底); 若显式配置 stt_mode=cloud 且提供
    api_base/key, 则走云端 STT (可选维度升级, 用户自持 key, 默认关闭)。
    """
    # 云端 STT (可选, 默认关闭)
    if getattr(config, "stt_mode", "local") == "cloud" and getattr(config, "stt_api_base", ""):
        try:
            from voice.cloud_stt_adapter import create_cloud_stt

            stt = create_cloud_stt(config)
            if stt is not None:
                logger.info("STT: 云端 (%s) uri=%s", getattr(config, "stt_cloud_provider", "openai"), config.stt_api_base)
                return stt
            logger.warning("云端 STT 创建失败, 回退 FunASR")
        except Exception as e:
            logger.warning("云端 STT 不可用, 回退 FunASR: %s", e)
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

    引擎分层 (免费托底 / 可选维度升级):
      1. CosyVoice2 (本地 GPU, 用户设计首选) — 经独立微服务调用
      2. EdgeTTS (微软中文神经语音, 兜底)
      3. [可选] 云端 TTS: 仅当 tts_mode=cloud 且配置 api_base/key 时启用
         (用户自持 Key, 不默认启用, 见 docs/语音云对接说明.md)
    """
    if not _LIVEKIT_AVAILABLE:
        return None
    # 云端 TTS (可选, 默认关闭)
    if getattr(config, "tts_mode", "local") == "cloud" and getattr(config, "tts_api_base", ""):
        try:
            from voice.cloud_tts_adapter import create_cloud_tts

            tts = create_cloud_tts(config)
            if tts is not None:
                logger.info("TTS: 云端 (%s)", getattr(config, "tts_cloud_provider", "openai"))
                return tts
            logger.warning("云端 TTS 创建失败, 回退本地")
        except Exception as e:
            logger.warning("云端 TTS 不可用, 回退本地: %s", e)
    try:
        from voice.tts_adapter import create_tts

        return create_tts(config)
    except Exception as e:
        logger.warning("TTS 创建失败: %s", e)
        return None


# ========== LLM 超时包装 (Ollama 冷启动保护) ==========


if _LIVEKIT_AVAILABLE:

    class _TimeoutLLM(lk_llm.LLM):
        """
        包装任意 LLM，强制默认使用长 timeout 的 APIConnectOptions。

        LiveKit 1.6.5 的 AgentSession 调用 llm.chat() 时不传 conn_options，会走 LLM
        默认的 10s 超时。Ollama qwen2.5:3b 冷启动加载到显存约 24s，10s 会连续超时失败，
        导致 Agent 不回话。通过子类化并覆写 chat()，在无 conn_options 时注入 60s 超时。
        """

        def __init__(self, base_llm: lk_llm.LLM, timeout: float = 180.0, max_retry: int = 2):
            super().__init__()
            self._base = base_llm
            self._conn_options = APIConnectOptions(
                timeout=timeout,
                max_retry=max_retry,
                retry_interval=2.0,
            )

        def chat(
            self,
            *,
            chat_ctx: lk_llm.ChatContext,
            tools: list | None = None,
            conn_options: Any | None = None,
            parallel_tool_calls: Any = NOT_GIVEN,
            tool_choice: Any = NOT_GIVEN,
            extra_kwargs: Any = NOT_GIVEN,
        ):
            if conn_options is None:
                conn_options = self._conn_options
            return self._base.chat(
                chat_ctx=chat_ctx,
                tools=tools,
                conn_options=conn_options,
                parallel_tool_calls=parallel_tool_calls,
                tool_choice=tool_choice,
                extra_kwargs=extra_kwargs,
            )

        def __getattr__(self, name):
            return getattr(self._base, name)

    def _create_llm(timeout: float = 180.0, max_retry: int = 2) -> lk_llm.LLM:
        """创建带冷启动保护的 Ollama LLM (OpenAI 兼容端点)"""
        base_llm = OpenAILLM(
            base_url="http://localhost:11434/v1",
            model="qwen2.5:3b",
            api_key="not-needed",
        )
        return _TimeoutLLM(base_llm, timeout=timeout, max_retry=max_retry)

else:

    def _create_llm(timeout: float = 60.0, max_retry: int = 2) -> Any:
        """LiveKit 不可用时占位"""
        raise RuntimeError("livekit-agents 未安装，无法创建 LLM")


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
            vad = silero.VAD.load(
                activation_threshold=config.vad_threshold,
                min_speech_duration=config.vad_min_speech_duration,
                min_silence_duration=config.vad_min_silence_duration,
                prefix_padding_duration=config.vad_prefix_padding_duration,
            )
            logger.info("Silero VAD 现场加载完成")
        except Exception as e:
            logger.warning("Silero VAD 加载失败: %s", e)

    # 3. 创建 LLM（Ollama 本地 qwen2.5:3b 冷启动约 24s，必须强制 180s 超时 + 启动预热）
    llm = _create_llm(timeout=180.0, max_retry=2)
    logger.info("LLM: Ollama qwen2.5:3b (OpenAI compat, timeout=180s)")

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

    # 自托管 LiveKit 下 cloud turn detector 会报 401，明确使用本地 VAD 模式
    # 注意: endpointing 必须用 "fixed" 模式。原 "dynamic" 模式依赖会话暂停统计动态估算
    # end-of-turn 延迟, 在测试/合成音频下会估算失当, 导致第一轮后 turn 永不结束 ->
    # 后续用户输入被吞进同一轮而不触发新 STT (多轮断流)。fixed 模式用固定 min/max 延迟。
    sess_kw["turn_handling"] = {
        "turn_detection": "vad",
        "endpointing": {"mode": "fixed", "min_delay": 0.5, "max_delay": 3.0},
        "interruption": {
            "enabled": config.allow_interruptions,
            "mode": "vad",
            "min_duration": config.min_interruption_duration,
        },
    }
    logger.info(
        "TurnHandling: VAD 模式 + VAD 打断 (min_interrupt=%s, false_interrupt=%s)",
        config.min_interruption_duration,
        config.false_interruption_timeout,
    )

    session = AgentSession(**sess_kw)

    # 6. 状态同步：通过 DataChannel 把 agent/user 状态推给浏览器，做"正在聆听/思考/说话"视觉反馈
    async def _publish_state(label: str, detail: str = ""):
        try:
            payload = json.dumps({
                "type": "voice_state",
                "state": label,
                "detail": detail,
            }, ensure_ascii=False).encode("utf-8")
            await ctx.room.local_participant.publish_data(payload, reliable=True)
        except Exception as e:
            logger.debug("状态同步失败: %s", e)

    _STATE_MAP = {
        "initializing": "初始化中...",
        "idle": "等待说话",
        "listening": "正在聆听",
        "thinking": "正在思考",
        "speaking": "正在说话",
    }

    @session.on("agent_state_changed")
    def _on_agent_state(ev):
        label = _STATE_MAP.get(ev.new_state, ev.new_state)
        logger.info("Agent 状态: %s -> %s", ev.old_state, ev.new_state)
        asyncio.create_task(_publish_state(label))

    @session.on("user_state_changed")
    def _on_user_state(ev):
        if ev.new_state == "speaking":
            asyncio.create_task(_publish_state("用户说话中"))
        elif ev.new_state == "listening":
            asyncio.create_task(_publish_state("等待说话"))

    @session.on("user_input_transcribed")
    def _on_transcribed(ev):
        if ev.is_final:
            logger.info("STT 识别结果: %s", ev.transcript)
            asyncio.create_task(_publish_state("正在思考", detail=ev.transcript))

    # 7. 启动 (session.start 内部会连接房间, 无需再 ctx.connect())
    await session.start(room=ctx.room, agent=agent)
    logger.info("VoiceBridge Agent 已就绪 (room=%s)", ctx.room.name)


# ========== CLI 入口 ==========


def _warmup_ollama():
    """Worker 启动时预热 Ollama 模型，避免用户首次对话冷启动 20s+"""
    try:
        from openai import OpenAI

        client = OpenAI(base_url="http://localhost:11434/v1", api_key="not-needed")
        r = client.chat.completions.create(
            model="qwen2.5:3b",
            messages=[{"role": "user", "content": "你好"}],
            timeout=120,
        )
        logger.info("Ollama 预热完成: %s", r.choices[0].message.content[:30])
    except Exception as e:
        logger.warning("Ollama 预热失败: %s", e)


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

    # Worker 启动时先预热 Ollama，避免用户首次对话冷启动 20s+ 导致超时
    _warmup_ollama()

    # LiveKit 1.6.5: 自动 dispatch 在 dev/reconnect/二次进房 等场景不可靠，
    # 官方推荐显式 dispatch。设置 agent_name 后，浏览器 token 通过 RoomAgentDispatch
    # 在连接时触发 dispatch，或调用 AgentDispatchService API 显式分发。
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=_prewarm,
            agent_name=config.agent_name,  # "homestream-voice"
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
