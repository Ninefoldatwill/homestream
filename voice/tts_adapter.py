"""
CosyVoice2 + EdgeTTS 适配器 — LiveKit Agents 1.6.5 兼容实现。

v5.2.5 修复要点:
  - LiveKit 1.6.5 的 SynthesizeStream/ChunkedStream 抽象方法改为 `_run(self, output_emitter)`
    (旧版覆写 _main_task 已失效, 实例化即报 "abstract method '_run'")
  - TTS.synthesize() 必须返回 ChunkedStream (不再是 SynthesizeStream)
  - 音频通过 output_emitter.initialize(...) + output_emitter.push(bytes) 推送
  - pcm 用 mime_type="audio/pcm", mp3 用 mime_type="audio/mp3"

引擎策略 (免费托底 / 维度分层):
  1. CosyVoice2 (本地 GPU, 中文第一, 用户设计首选) — 通过独立微服务调用
     (CosyVoice2 依赖 pynini/Matcha-TTS/kaldifst, 必须 Python 3.10 Conda,
      无法塞进本 Worker 的 3.13 venv, 故拆为 voice/cosyvoice_service.py 微服务)
  2. EdgeTTS (微软中文神经语音, 免费, 无需 GPU) — 开发/兜底默认, 保证今天能出声

调用关系:
  Worker (3.13 venv) --HTTP--> CosyVoice2 微服务 (Conda 3.10, :50000)
  CosyVoiceClient 负责探活与健康检查; 不可达时自动降级 EdgeTTS。
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

# --- HTTP 客户端 (调用 CosyVoice2 微服务, 需 httpx) ---
try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    httpx = None  # type: ignore

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

COSYVOICE_SERVICE_URL = os.getenv("COSYVOICE_SERVICE_URL", "http://127.0.0.1:50000")
COSYVOICE_SR = 24000


# ========== CosyVoice2 HTTP 客户端 ==========


class CosyVoiceClient:
    """调用 CosyVoice2 微服务的 HTTP 客户端 (运行于 Worker 3.13 venv, 不依赖 cosyvoice)"""

    def __init__(self, base_url: str = COSYVOICE_SERVICE_URL, timeout: float = 90.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def health(self) -> bool:
        if not _HTTPX_AVAILABLE:
            return False
        try:
            r = httpx.get(f"{self._base}/health", timeout=5.0)
            if r.status_code == 200:
                data = r.json()
                return data.get("status") == "ok"
        except Exception:  # noqa: BLE001
            return False
        return False

    def synthesize(self, text: str, voice: str = "longxiaochun", speed: float = 1.0) -> bytes:
        """返回 WAV bytes (24000Hz, 16bit, mono)"""
        if not _HTTPX_AVAILABLE:
            raise RuntimeError("httpx 未安装, 无法调用 CosyVoice2 微服务")
        r = httpx.post(
            f"{self._base}/synthesize",
            json={"text": text, "voice": voice, "speed": speed},
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.content

    def voices(self) -> list[dict]:
        try:
            r = httpx.get(f"{self._base}/voices", timeout=5.0)
            if r.status_code == 200:
                return r.json().get("voices", [])
        except Exception:  # noqa: BLE001
            pass
        return COSYVOICE_VOICES


def _wav_bytes_to_pcm_float32(wav_bytes: bytes) -> np.ndarray:
    """WAV bytes -> float32 [-1,1] 单声道"""
    import io

    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        n_frames = w.getnframes()
        raw = w.readframes(n_frames)
    pcm_int16 = np.frombuffer(raw, dtype=np.int16)
    return pcm_int16.astype(np.float32) / 32768.0


# ========== LiveKit 1.6.5 TTS 实现 ==========

if _LIVEKIT_AVAILABLE:

    class CosyVoiceChunkedStream(lk_tts.ChunkedStream):
        """CosyVoice2 合成 Stream (经微服务 HTTP 调用, LiveKit 1.6.5)"""

        def __init__(self, *, tts, input_text, conn_options):
            super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
            self._client = tts._client
            self._voice = tts._voice
            self._speed = tts._speed

        async def _run(self, output_emitter) -> None:
            request_id = f"cv_{int(time.monotonic() * 1000)}"
            output_emitter.initialize(
                request_id=request_id, sample_rate=COSYVOICE_SR, num_channels=1, mime_type="audio/pcm"
            )
            try:
                wav = await asyncio.to_thread(self._client.synthesize, self.input_text, self._voice, self._speed)
                pcm = _wav_bytes_to_pcm_float32(wav)
                if pcm.size == 0:
                    output_emitter.flush()
                    return
                # 按 ~100ms 分片推送, 由 AgentSession 的 StreamAdapter 桥接为流式
                chunk_samples = COSYVOICE_SR // 10
                for i in range(0, pcm.size, chunk_samples):
                    seg = pcm[i : i + chunk_samples]
                    audio_int16 = np.clip(seg * 32768.0, -32768, 32767).astype(np.int16)
                    output_emitter.push(audio_int16.tobytes())
                output_emitter.flush()
            except Exception as e:  # noqa: BLE001
                logger.error("CosyVoice2 合成失败: %s", e)
                output_emitter.flush()

    class CosyVoiceTTS(lk_tts.TTS):
        """CosyVoice2 TTS 插件 (LiveKit Agents 1.6.5 兼容, 用户设计首选)

        streaming=False: CosyVoice2 为整句合成, 由 LiveKit StreamAdapter
        按句桥接为流式接口 (AgentSession 自动包装, 详见 agent.py tts_node)。
        音频来自独立微服务 (Conda 3.10), 本类仅做 HTTP 客户端适配。
        """

        def __init__(self, client: CosyVoiceClient | None = None, voice: str = "longxiaochun",
                     speed: float = 1.0, *, capabilities=None):
            super().__init__(
                capabilities=capabilities or TTSCapabilities(streaming=False),
                sample_rate=COSYVOICE_SR,
                num_channels=1,
            )
            self._client = client or CosyVoiceClient()
            self._voice = voice
            self._speed = speed

        def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
            return CosyVoiceChunkedStream(tts=self, input_text=text, conn_options=conn_options)

        async def list_voices(self):
            return self._client.voices()

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
                except Exception as e:  # noqa: BLE001
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


# ========== 工厂函数 (引擎选择: CosyVoice2 微服务优先, EdgeTTS 兜底) ==========


def create_tts(config=None) -> Any:
    """
    创建 TTS 实例, 按维度分层选择引擎:
      1. CosyVoice2 (本地 GPU, 用户设计首选) — 经独立微服务调用
      2. EdgeTTS (免费中文神经语音) — 兜底, 保证可出声
    返回 None 表示全部不可用。
    """
    if not _LIVEKIT_AVAILABLE:
        logger.warning("LiveKit SDK 不可用, 无法创建 TTS")
        return None

    voice = getattr(config, "tts_voice", "longxiaochun") if config else "longxiaochun"
    speed = getattr(config, "tts_speed", 1.0) if config else 1.0

    # 1. CosyVoice2 微服务 (首选): 探活, 可达则使用
    if _HTTPX_AVAILABLE:
        client = CosyVoiceClient()
        if client.health():
            try:
                tts = CosyVoiceTTS(client=client, voice=voice, speed=speed)
                logger.info("TTS: CosyVoice2 微服务 (本地 GPU) voice=%s url=%s", voice, client._base)
                return tts
            except Exception as e:  # noqa: BLE001
                logger.warning("CosyVoice2 微服务创建失败, 降级 EdgeTTS: %s", e)
        else:
            logger.info("CosyVoice2 微服务不可达 (%s), 降级 EdgeTTS", client._base)

    # 2. EdgeTTS 兜底
    if _EDGE_TTS_AVAILABLE:
        edge_voice = os.getenv("VOICE_EDGE_VOICE", "zh-CN-XiaoxiaoNeural")
        logger.info("TTS: EdgeTTS (兜底) voice=%s", edge_voice)
        return EdgeTTS(voice=edge_voice)

    logger.warning("TTS 不可用: CosyVoice2 微服务不可达 且 EdgeTTS 未装")
    return None


def is_available() -> dict[str, bool]:
    """检查依赖可用性"""
    return {
        "livekit": _LIVEKIT_AVAILABLE,
        "httpx": _HTTPX_AVAILABLE,
        "edge_tts": _EDGE_TTS_AVAILABLE,
    }
