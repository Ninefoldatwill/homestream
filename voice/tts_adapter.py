"""
CosyVoice2 + EdgeTTS 适配器 — LiveKit Agents 1.6.5 兼容实现。

v5.2.5 修复要点:
  - LiveKit 1.6.5 的 SynthesizeStream/ChunkedStream 抽象方法改为 `_run(self, output_emitter)`
    (旧版覆写 _main_task 已失效, 实例化即报 "abstract method '_run'")
  - TTS.synthesize() 必须返回 ChunkedStream (不再是 SynthesizeStream)
  - 音频通过 output_emitter.initialize(...) + output_emitter.push(bytes) 推送
  - pcm 用 mime_type="audio/pcm", mp3 用 mime_type="audio/mp3"

引擎策略 (免费托底 / 维度分层):
  1. CosyVoice2 (本地 GPU, 中文第一, 用户设计首选) — 需 cosyvoice 包 + 模型权重
  2. EdgeTTS (微软中文神经语音, 免费, 无需 GPU) — 开发/兜底默认, 保证今天能出声
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator

import numpy as np

logger = logging.getLogger("homestream.voice.tts")

# --- LiveKit Agents SDK (可选导入) ---
try:
    from livekit import rtc
    from livekit.agents import tts as lk_tts
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
    from livekit.agents.tts import (
        SynthesizedAudio,
        TTSCapabilities,
    )

    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False
    lk_tts = None  # type: ignore
    rtc = None  # type: ignore

    @dataclass
    class SynthesizedAudio:  # type: ignore
        frame: Any = None
        request_id: str = ""
        is_final: bool = False
        segment_id: str = ""
        delta_text: str = ""

    @dataclass
    class TTSCapabilities:  # type: ignore
        streaming: bool = False

    DEFAULT_API_CONNECT_OPTIONS = None  # type: ignore

# --- CosyVoice2 (可选导入) ---
try:
    from cosyvoice.cli.cosyvoice import CosyVoice2 as _CosyVoice2Model

    _COSYVOICE_AVAILABLE = True
except ImportError:
    _COSYVOICE_AVAILABLE = False
    _CosyVoice2Model = None  # type: ignore

# --- EdgeTTS (可选导入, 兜底) ---
try:
    import edge_tts

    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False
    edge_tts = None  # type: ignore

# 延迟导入 dataclass (避免顶层依赖)
from dataclasses import dataclass


# ========== CosyVoice2 内置声音 ==========

COSYVOICE_VOICES = [
    {"name": "longxiaochun", "description": "龙小春 (女声, 温暖自然)"},
    {"name": "longwan", "description": "龙婉 (女声, 柔和)"},
    {"name": "longcheng", "description": "龙诚 (男声, 沉稳)"},
    {"name": "longhua", "description": "龙华 (男声, 清朗)"},
    {"name": "longshu", "description": "龙舒 (女声, 知性)"},
    {"name": "longyue", "description": "龙悦 (女声, 活泼)"},
    {"name": "longjielidou", "description": "龙杰力斗 (男声, 磁性)"},
    {"name": "longmiao", "description": "龙淼 (女声, 甜美)"},
    {"name": "longfei", "description": "龙飞 (男声, 阳光)"},
    {"name": "longbiao", "description": "龙彪 (男声, 浑厚)"},
]

EDGE_TTS_VOICES = [
    {"name": "zh-CN-XiaoxiaoNeural", "description": "晓晓 (女声, 温柔)"},
    {"name": "zh-CN-YunxiNeural", "description": "云希 (男声, 阳光)"},
    {"name": "zh-CN-YunyangNeural", "description": "云扬 (男声, 新闻播报)"},
    {"name": "zh-CN-XiaoyiNeural", "description": "晓伊 (女声, 活泼)"},
]


# ========== CosyVoice2 推理引擎 ==========


@dataclass
class CosyVoiceChunk:
    """CosyVoice2 合成块"""

    audio: np.ndarray  # float32, -1.0~1.0
    sample_rate: int = 24000
    is_final: bool = False


class CosyVoiceEngine:
    """CosyVoice2 推理引擎 (独立于 LiveKit, 可单独测试)"""

    def __init__(self, model_path: str = "pretrained_models/CosyVoice2-0.5B", sample_rate: int = 24000):
        self._model_path = model_path
        self._sample_rate = sample_rate
        self._model: Any = None
        self._loaded = False

    def _ensure_model(self):
        if self._loaded:
            return
        if not _COSYVOICE_AVAILABLE:
            raise RuntimeError("CosyVoice2 未安装。请先安装 cosyvoice 包与模型权重。")
        logger.info("加载 CosyVoice2 模型: %s use_flow_cache=True ...", self._model_path)
        self._model = _CosyVoice2Model(self._model_path, use_flow_cache=True)
        self._loaded = True
        logger.info("CosyVoice2 模型加载完成 (use_flow_cache=True)")

    async def synthesize_stream(self, text: str, voice: str = "longxiaochun", speed: float = 1.0) -> AsyncIterator[CosyVoiceChunk]:
        if not text.strip():
            return
        queue: asyncio.Queue[CosyVoiceChunk | None] = asyncio.Queue()

        async def _producer():
            try:
                await asyncio.to_thread(self._infer_stream, text, voice, speed, queue)
            except Exception as e:
                logger.error("CosyVoice2 流式合成失败: %s", e)
            finally:
                await queue.put(None)

        producer_task = asyncio.create_task(_producer())
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
        await producer_task

    def _infer_stream(self, text, voice, speed, queue):
        self._ensure_model()
        try:
            chunks = self._model.inference_sft(text, voice, stream=True, speed=speed)
            for chunk in chunks:
                audio = chunk.get("tts_speech", chunk) if isinstance(chunk, dict) else chunk
                if isinstance(audio, np.ndarray):
                    if audio.ndim > 1:
                        audio = audio.squeeze()
                    audio = audio.astype(np.float32)
                    asyncio.run_coroutine_threadsafe(
                        queue.put(CosyVoiceChunk(audio=audio, sample_rate=self._sample_rate, is_final=False)),
                        asyncio.get_event_loop(),
                    )
            asyncio.run_coroutine_threadsafe(
                queue.put(CosyVoiceChunk(audio=np.array([], dtype=np.float32), sample_rate=self._sample_rate, is_final=True)),
                asyncio.get_event_loop(),
            )
        except Exception as e:
            logger.error("CosyVoice2 推理异常: %s", e)
            raise

    async def synthesize(self, text, voice="longxiaochun", speed=1.0) -> np.ndarray:
        chunks = []
        async for chunk in self.synthesize_stream(text, voice, speed):
            if not chunk.is_final and len(chunk.audio) > 0:
                chunks.append(chunk.audio)
        return np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)


# ========== LiveKit 1.6.5 TTS 实现 ==========

if _LIVEKIT_AVAILABLE:

    class CosyVoiceChunkedStream(lk_tts.ChunkedStream):
        """CosyVoice2 流式合成 Stream (LiveKit 1.6.5)"""

        def __init__(self, *, tts, input_text, conn_options):
            super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
            self._tts = tts
            self._engine = tts._engine
            self._voice = tts._voice
            self._speed = tts._speed

        async def _run(self, output_emitter) -> None:
            request_id = f"cv_{int(time.monotonic() * 1000)}"
            output_emitter.initialize(
                request_id=request_id, sample_rate=24000, num_channels=1, mime_type="audio/pcm"
            )
            try:
                async for chunk in self._engine.synthesize_stream(self.input_text, self._voice, self._speed):
                    if chunk.is_final or len(chunk.audio) == 0:
                        continue
                    audio_int16 = np.clip(chunk.audio * 32768.0, -32768, 32767).astype(np.int16)
                    output_emitter.push(audio_int16.tobytes())
                output_emitter.flush()
            except Exception as e:
                logger.error("CosyVoice2 合成失败: %s", e)
                output_emitter.flush()

    class CosyVoiceTTS(lk_tts.TTS):
        """CosyVoice2 TTS 插件 (LiveKit Agents 1.6.5 兼容, 用户设计首选)

        streaming=False: CosyVoice2 为整句合成, 由 LiveKit StreamAdapter
        按句桥接为流式接口 (AgentSession 自动包装, 详见 agent.py tts_node)。
        """

        def __init__(self, model_path="pretrained_models/CosyVoice2-0.5B", voice="longxiaochun",
                     speed=1.0, sample_rate=24000, *, capabilities=None):
            super().__init__(
                capabilities=capabilities or TTSCapabilities(streaming=False),
                sample_rate=sample_rate,
                num_channels=1,
            )
            self._engine = CosyVoiceEngine(model_path=model_path, sample_rate=sample_rate)
            self._voice = voice
            self._speed = speed

        def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
            return CosyVoiceChunkedStream(tts=self, input_text=text, conn_options=conn_options)

        async def list_voices(self):
            return COSYVOICE_VOICES

    # ---- EdgeTTS 兜底引擎 (免费, 无需 GPU, 今天即可出声) ----

    if _EDGE_TTS_AVAILABLE:

        class EdgeTTSChunkedStream(lk_tts.ChunkedStream):
            """EdgeTTS 流式合成 Stream (LiveKit 1.6.5)"""

            def __init__(self, *, tts, input_text, conn_options, voice="zh-CN-XiaoxiaoNeural"):
                super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
                self._voice = voice

            async def _run(self, output_emitter) -> None:
                request_id = f"edge_{int(time.monotonic() * 1000)}"
                output_emitter.initialize(
                    request_id=request_id, sample_rate=24000, num_channels=1, mime_type="audio/mp3"
                )
                try:
                    communicate = edge_tts.Communicate(self.input_text, self._voice)
                    async for chunk in communicate.stream():
                        if isinstance(chunk, dict) and chunk.get("type") == "audio":
                            data = chunk.get("data")
                            if isinstance(data, (bytes, bytearray)) and data:
                                output_emitter.push(bytes(data))
                    output_emitter.flush()
                except Exception as e:
                    logger.error("EdgeTTS 合成失败: %s", e)
                    output_emitter.flush()

    class EdgeTTS(lk_tts.TTS):
        """EdgeTTS 兜底 TTS (微软中文神经语音, 免费, 无需 GPU)

        streaming=False: EdgeTTS 为整句合成, 由 LiveKit StreamAdapter
        按句桥接为流式接口 (AgentSession 自动包装)。
        """

        def __init__(self, voice="zh-CN-XiaoxiaoNeural", *, capabilities=None):
            super().__init__(
                capabilities=capabilities or TTSCapabilities(streaming=False),
                sample_rate=24000,
                num_channels=1,
            )
            self._voice = voice

        def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
            return EdgeTTSChunkedStream(tts=self, input_text=text, conn_options=conn_options, voice=self._voice)

        async def list_voices(self):
            return EDGE_TTS_VOICES


# ========== 工厂函数 (引擎选择: CosyVoice2 优先, EdgeTTS 兜底) ==========


def create_tts(config=None) -> Any:
    """
    创建 TTS 实例, 按维度分层选择引擎:
      1. CosyVoice2 (本地 GPU, 用户设计首选) — 需 cosyvoice 包 + 模型权重
      2. EdgeTTS (免费中文神经语音) — 兜底, 保证可出声
      返回 None 表示全部不可用。
    """
    if not _LIVEKIT_AVAILABLE:
        logger.warning("LiveKit SDK 不可用, 无法创建 TTS")
        return None

    # 1. CosyVoice2 (首选)
    model_path = getattr(config, "tts_model_path", "pretrained_models/CosyVoice2-0.5B") if config else "pretrained_models/CosyVoice2-0.5B"
    voice = getattr(config, "tts_voice", "longxiaochun") if config else "longxiaochun"
    speed = getattr(config, "tts_speed", 1.0) if config else 1.0
    sample_rate = getattr(config, "tts_sample_rate", 24000) if config else 24000

    if _COSYVOICE_AVAILABLE and os.path.exists(model_path):
        try:
            tts = CosyVoiceTTS(model_path=model_path, voice=voice, speed=speed, sample_rate=sample_rate)
            logger.info("TTS: CosyVoice2 (本地 GPU) voice=%s", voice)
            return tts
        except Exception as e:
            logger.warning("CosyVoice2 创建失败, 降级 EdgeTTS: %s", e)
    elif _COSYVOICE_AVAILABLE:
        logger.info("CosyVoice2 模型权重缺失 (%s), 降级 EdgeTTS", model_path)

    # 2. EdgeTTS 兜底
    if _EDGE_TTS_AVAILABLE:
        edge_voice = os.getenv("VOICE_EDGE_VOICE", "zh-CN-XiaoxiaoNeural")
        logger.info("TTS: EdgeTTS (兜底) voice=%s", edge_voice)
        return EdgeTTS(voice=edge_voice)

    logger.warning("TTS 不可用: CosyVoice2 未装/模型缺失 且 EdgeTTS 未装")
    return None


def is_available() -> dict[str, bool]:
    """检查依赖可用性"""
    return {
        "livekit": _LIVEKIT_AVAILABLE,
        "cosyvoice": _COSYVOICE_AVAILABLE,
        "edge_tts": _EDGE_TTS_AVAILABLE,
    }
