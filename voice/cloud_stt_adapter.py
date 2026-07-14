"""
云端 STT 适配接口 — LiveKit Agents 1.6.5 兼容 (可选维度升级)

设计哲学: 同 cloud_tts_adapter —— 默认 FunASR 本地免费托底, 云端 STT 仅作可选维度升级。
对接 OpenAI 兼容 Whisper 端点 /v1/audio/transcriptions (multipart, file=audio.wav)。

实现策略 (缓冲模式):
  LiveKit SpeechStream 收集整段音频 (input_ch 帧), 在用户说完 (流关闭) 后,
  一次性 POST 给云端 Whisper 端点, 返回文本作为 FINAL_TRANSCRIPT。
  这是云端 API 的天然调用方式 (非实时流式), 作为本地 FunASR 不可用/跨语种时的兜底。

支持 provider (OpenAI 兼容 transcription 端点):
  - openai   base=https://api.openai.com/v1    model=whisper-1
  - azure    base=https://<res>.openai.azure.com/v1
  - 任意 OpenAI 兼容 Whisper 服务
详见 docs/语音云对接说明.md
IP 合规: 纯 httpx 调 REST, 不复制云 SDK。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from typing import Any

import numpy as np

logger = logging.getLogger("homestream.voice.cloud_stt")

try:
    from livekit import rtc
    from livekit.agents import stt
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions

    _LK_AVAILABLE = True
except ImportError:
    _LK_AVAILABLE = False
    stt = None  # type: ignore
    rtc = None  # type: ignore
    DEFAULT_API_CONNECT_OPTIONS = None  # type: ignore
    NOT_GIVEN = None  # type: ignore
    APIConnectOptions = None  # type: ignore

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    httpx = None  # type: ignore


# ========== 音频工具 ==========


def _frame_to_cloud_pcm(frame, target_rate: int = 16000) -> bytes:
    """将 LiveKit AudioFrame / ndarray / bytes 转为 16kHz 16-bit mono PCM bytes"""
    if hasattr(frame, "data"):
        data = frame.data
        if isinstance(data, (bytes, bytearray)):
            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        elif isinstance(data, np.ndarray):
            arr = data.astype(np.float32)
            if np.max(np.abs(arr)) > 1.0:
                arr = arr / 32768.0
        else:
            arr = np.array(data, dtype=np.float32)
    elif isinstance(frame, np.ndarray):
        arr = frame.astype(np.float32)
    elif isinstance(frame, (bytes, bytearray)):
        arr = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        arr = np.array(frame, dtype=np.float32)

    if arr.ndim > 1:
        arr = arr.mean(axis=1)

    src_rate = getattr(frame, "sample_rate", 48000) if hasattr(frame, "sample_rate") else 48000
    if isinstance(src_rate, str):
        src_rate = 48000
    if src_rate and src_rate != target_rate:
        ratio = target_rate / src_rate
        n_out = int(len(arr) * ratio)
        if n_out > 0:
            arr = np.interp(np.linspace(0, len(arr) - 1, n_out), np.arange(len(arr)), arr)

    return np.clip(arr * 32768.0, -32768, 32767).astype(np.int16).tobytes()


def _buffer_to_cloud_pcm(buffer, target_rate: int = 16000) -> bytes:
    """将一次性识别的 AudioBuffer 转为 PCM bytes"""
    if buffer is None:
        return b""
    if not isinstance(buffer, (list, tuple)):
        buffer = [buffer]
    chunks = []
    for f in buffer:
        if isinstance(f, rtc.AudioFrame):
            chunks.append(_frame_to_cloud_pcm(f, target_rate))
        elif isinstance(f, np.ndarray):
            chunks.append(_frame_to_cloud_pcm(f, target_rate))
        elif isinstance(f, (bytes, bytearray)):
            chunks.append(bytes(f))
    return b"".join(chunks)


def _frames_to_wav_bytes(frames: list, sample_rate: int = 16000) -> bytes:
    """把收集到的 16k int16 PCM 帧拼成 WAV bytes (供云端 Whisper 上传)"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        for f in frames:
            if isinstance(f, (bytes, bytearray)) and f:
                w.writeframes(bytes(f))
    return buf.getvalue()


# ========== LiveKit 1.6.5 STT 实现 ==========


if _LK_AVAILABLE:

    class CloudSTTStream(stt.SpeechStream):
        """云端 STT 缓冲流 (LiveKit 1.6.5): 收齐音频后一次性识别"""

        def __init__(self, *, stt_inst, conn_options, sample_rate=48000, base, key, model, timeout):
            super().__init__(stt=stt_inst, conn_options=conn_options, sample_rate=sample_rate)
            self._base = base
            self._key = key
            self._model = model
            self._timeout = timeout
            self._frames: list = []

        async def _run(self) -> None:
            if not _HTTPX_AVAILABLE:
                logger.error("httpx 未安装, 云端 STT 不可用")
                self._event_ch.send_nowait(stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    alternatives=[stt.SpeechData(text="", language="zh-CN")],
                ))
                return
            try:
                async for data in self._input_ch:
                    if isinstance(data, rtc.AudioFrame):
                        pcm = _frame_to_cloud_pcm(data)
                        if pcm:
                            self._frames.append(pcm)
                wav = _frames_to_wav_bytes(self._frames)
                if not wav:
                    return
                text = await asyncio.to_thread(self._transcribe, wav)
                if text:
                    self._event_ch.send_nowait(stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=[stt.SpeechData(text=text.strip(), language="zh-CN", confidence=0.9)],
                    ))
            except Exception as e:  # noqa: BLE001
                logger.error("云端 STT 流错误: %s", e)

        def _transcribe(self, wav_bytes: bytes) -> str:
            resp = httpx.post(
                f"{self._base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._key}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"model": self._model, "language": "zh"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            try:
                return resp.json().get("text", "")
            except Exception:
                return ""

    class CloudSTT(stt.STT):
        """云端 STT (LiveKit 1.6.5), OpenAI 兼容 Whisper 端点"""

        def __init__(self, base: str, key: str, model: str, timeout: float = 30.0, *, capabilities=None):
            super().__init__(
                capabilities=capabilities
                or stt.STTCapabilities(streaming=True, interim_results=False, offline_recognize=True)
            )
            self._base = base
            self._key = key
            self._model = model
            self._timeout = timeout

        def stream(self, *, language=NOT_GIVEN, conn_options=DEFAULT_API_CONNECT_OPTIONS):
            return CloudSTTStream(
                stt_inst=self, conn_options=conn_options, sample_rate=48000,
                base=self._base, key=self._key, model=self._model, timeout=self._timeout,
            )

        async def _recognize_impl(self, buffer, *, language=NOT_GIVEN, conn_options=DEFAULT_API_CONNECT_OPTIONS):
            pcm = _buffer_to_cloud_pcm(buffer)
            wav = _frames_to_wav_bytes([pcm] if pcm else [])
            text = ""
            if wav:
                try:
                    text = await asyncio.to_thread(self._transcribe_sync, wav)
                except Exception as e:
                    logger.warning("云端 STT 一次性识别失败: %s", e)
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[stt.SpeechData(text=text, language="zh-CN", confidence=0.9)],
            )

        def _transcribe_sync(self, wav_bytes: bytes) -> str:
            resp = httpx.post(
                f"{self._base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._key}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"model": self._model, "language": "zh"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            try:
                return resp.json().get("text", "")
            except Exception:
                return ""


# ========== 工厂函数 ==========


def create_cloud_stt(config=None, timeout: float = 30.0) -> Any:
    """工厂: 创建云端 STT 实例 (需 stt_mode=cloud 且 stt_api_base 非空)"""
    if not _LK_AVAILABLE:
        logger.warning("LiveKit SDK 不可用, 无法创建云端 STT")
        return None
    if not _HTTPX_AVAILABLE:
        logger.warning("httpx 未安装, 无法调用云端 STT")
        return None
    base = (getattr(config, "stt_api_base", "") or "").rstrip("/")
    key = getattr(config, "stt_api_key", "") or ""
    provider = (getattr(config, "stt_cloud_provider", "openai") or "openai").lower()
    model = os.getenv("VOICE_STT_CLOUD_MODEL", "") or {
        "openai": "whisper-1", "azure": "whisper", "custom": "",
    }.get(provider, "whisper-1")
    if not base or not key or not model:
        logger.warning("云端 STT 配置不完整 (需 base+key+model), 跳过")
        return None
    try:
        stt_obj = CloudSTT(base=base, key=key, model=model, timeout=timeout)
        logger.info("云端 STT 创建成功: provider=%s model=%s", provider, model)
        return stt_obj
    except Exception as e:  # noqa: BLE001
        logger.error("云端 STT 创建失败: %s", e)
        return None
