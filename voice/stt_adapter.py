"""
FunASR 2-pass STT Adapter — LiveKit Agents 1.6.5 兼容实现。

对接 LiveKit 1.6.5 的真实 STT API:
  - 继承 stt.STT, 实现 stream() 返回 stt.SpeechStream 子类
  - SpeechStream._run() 读取 self._input_ch 的音频帧, 转发给 FunASR WebSocket
  - FunASR 回传结果 -> 发送 stt.SpeechEvent(FINAL_TRANSCRIPT / INTERIM_TRANSCRIPT)

⚠️ 关键踩坑 (v5.2.5):
  LiveKit 1.6.5 已移除 `SpeechAlternative`, 改用 `SpeechData`。
  STTCapabilities 现在**必须**同时传 streaming + interim_results (无默认值)。
  SpeechStream 的抽象方法是 `_run()` (不再是旧版子进程导入卡死问题)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import numpy as np

logger = logging.getLogger("homestream.voice.stt")

try:
    import websockets as _ws

    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    _ws = None

try:
    from livekit import rtc
    from livekit.agents import stt
    from livekit.agents.types import (
        DEFAULT_API_CONNECT_OPTIONS,
        NOT_GIVEN,
        APIConnectOptions,
    )

    _LK_AVAILABLE = True
except ImportError:
    _LK_AVAILABLE = False
    stt = None  # type: ignore
    rtc = None  # type: ignore
    DEFAULT_API_CONNECT_OPTIONS = None  # type: ignore
    NOT_GIVEN = None  # type: ignore
    APIConnectOptions = None  # type: ignore


# ========== FunASR 结果数据 ==========


@dataclass
class FunASRResult:
    """FunASR 2-pass 识别结果"""

    text: str = ""
    pass_type: str = "online"
    is_final: bool = False
    language: str = ""
    emotion: str = ""
    event: str = ""
    confidence: float = 0.95


def _parse_funasr_message(msg) -> FunASRResult | None:
    """解析 FunASR WebSocket 回传消息 (纯函数, 无 LiveKit 依赖)"""
    if isinstance(msg, bytes):
        return None
    try:
        data = json.loads(msg)
    except (json.JSONDecodeError, TypeError):
        return None
    mode, text = data.get("mode", ""), data.get("text", "")
    if not text:
        return None
    if mode == "2pass-online":
        return FunASRResult(text=text.strip(), pass_type="online", is_final=data.get("is_final", False))
    elif mode == "2pass-offline":
        lang, emo, evt, clean = _parse_tags(text)
        return FunASRResult(text=clean, pass_type="offline", is_final=True, language=lang, emotion=emo, event=evt)
    return None


def _parse_tags(text):
    lang, emo, evt = "zh-CN", "", ""
    clean = re.sub(r"<\|[^|]+\|>", "", text).strip()
    for tag in re.findall(r"<\|([^|]+)\|>", text):
        t = tag.upper()
        if t in ("ZH", "EN", "JA", "KO", "YUE", "AUTO", "ZH-CN", "CMN"):
            lang = t.lower()
        elif t in ("HAPPY", "SAD", "ANGRY", "NEUTRAL", "FEARFUL", "DISGUSTED", "SURPRISED"):
            emo = t.lower()
        elif t in ("SPEECH", "MUSIC", "SILENCE", "APPLAUSE"):
            evt = t.lower()
    return lang, emo, evt, clean


# ========== 音频工具 ==========


def _frame_to_pcm_bytes(frame, target_rate=16000) -> bytes:
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
        if np.max(np.abs(arr)) > 1.0:
            arr = arr / 32768.0
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


def _buffer_to_pcm(buffer, target_rate=16000) -> bytes:
    """将一次性识别的 AudioBuffer (list[AudioFrame] 或 ndarray) 转为 PCM bytes"""
    if buffer is None:
        return b""
    if not isinstance(buffer, (list, tuple)):
        buffer = [buffer]
    chunks = []
    for f in buffer:
        if isinstance(f, rtc.AudioFrame):
            chunks.append(_frame_to_pcm_bytes(f, target_rate))
        elif isinstance(f, np.ndarray):
            chunks.append(_frame_to_pcm_bytes(f, target_rate))
        elif isinstance(f, (bytes, bytearray)):
            chunks.append(bytes(f))
    return b"".join(chunks)


# ========== FunASR 2-pass WebSocket 客户端 (一次性识别复用) ==========


class FunASR2PassClient:
    """FunASR 2-pass WebSocket 客户端 (用于一次性识别)"""

    def __init__(self, uri="ws://localhost:10096", chunk_size=None, sample_rate=16000,
                 reconnect_attempts=2, reconnect_delay=1.0):
        self._uri = uri
        self._chunk_size = chunk_size or [5, 10, 5]
        self._sample_rate = sample_rate
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay = reconnect_delay

    @property
    def uri(self):
        return self._uri

    async def transcribe_stream(self, audio_iter: AsyncIterator[bytes]) -> AsyncIterator[FunASRResult]:
        """流式识别 (audio_iter 产出 16k PCM bytes)"""
        if not _WS_AVAILABLE:
            raise RuntimeError("websockets 未安装")
        last_error = None
        for attempt in range(self._reconnect_attempts):
            try:
                async with _ws.connect(self._uri) as ws:
                    init = {"mode": "2pass", "chunk_size": self._chunk_size,
                            "wav_name": "microphone", "is_speaking": True, "itn": True}
                    await ws.send(json.dumps(init))

                    async def send():
                        async for chunk in audio_iter:
                            await ws.send(chunk)
                        await ws.send(json.dumps({"is_speaking": False}))

                    async def recv():
                        async for msg in ws:
                            result = _parse_funasr_message(msg)
                            if result:
                                yield result

                    send_task = asyncio.create_task(send())
                    try:
                        async for r in recv():
                            yield r
                    finally:
                        await send_task
                return
            except (ConnectionRefusedError, OSError) as e:
                last_error = e
                if attempt < self._reconnect_attempts - 1:
                    await asyncio.sleep(self._reconnect_delay)
        raise ConnectionError(f"FunASR 连接失败: {last_error}")


# ========== LiveKit 1.6.5 STT 实现 ==========


if _LK_AVAILABLE:

    class FunASRStream(stt.SpeechStream):
        """FunASR 流式识别 Stream (LiveKit 1.6.5)"""

        def __init__(self, *, stt_inst, conn_options, sample_rate=48000, uri="ws://localhost:10096"):
            super().__init__(stt=stt_inst, conn_options=conn_options, sample_rate=sample_rate)
            self._uri = uri

        async def _run(self) -> None:
            if not _WS_AVAILABLE:
                logger.error("websockets 未安装, FunASR STT 不可用")
                self._event_ch.send_nowait(stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    alternatives=[stt.SpeechData(text="", language="zh-CN")],
                ))
                return
            try:
                async with _ws.connect(self._uri) as ws:
                    await ws.send(json.dumps({
                        "mode": "2pass", "chunk_size": [5, 10, 5],
                        "wav_name": "mic", "is_speaking": True, "itn": True,
                    }))

                    async def sender():
                        async for data in self._input_ch:
                            if isinstance(data, rtc.AudioFrame):
                                pcm = _frame_to_pcm_bytes(data, 16000)
                                if pcm:
                                    try:
                                        await ws.send(pcm)
                                    except Exception:
                                        return
                        # 输入结束 -> 通知 FunASR 产出最终结果
                        try:
                            await ws.send(json.dumps({"is_speaking": False}))
                        except Exception:
                            pass

                    final_event = asyncio.Event()

                    async def receiver():
                        async for msg in ws:
                            res = _parse_funasr_message(msg)
                            if not res or not res.text:
                                continue
                            if res.pass_type == "offline":
                                self._event_ch.send_nowait(stt.SpeechEvent(
                                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                                    alternatives=[stt.SpeechData(
                                        text=res.text,
                                        language=res.language or "zh-CN",
                                        confidence=res.confidence,
                                    )],
                                ))
                                final_event.set()
                                return
                            else:
                                self._event_ch.send_nowait(stt.SpeechEvent(
                                    type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                                    alternatives=[stt.SpeechData(
                                        text=res.text, language="zh-CN", confidence=res.confidence,
                                    )],
                                ))

                    send_task = asyncio.create_task(sender())
                    recv_task = asyncio.create_task(receiver())
                    await send_task
                    try:
                        await asyncio.wait_for(final_event.wait(), timeout=12.0)
                    except asyncio.TimeoutError:
                        logger.warning("FunASR 未在 12s 内返回最终结果")
                    finally:
                        recv_task.cancel()
            except Exception as e:
                logger.error("FunASR STT 流错误: %s", e)
                try:
                    self._event_ch.send_nowait(stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=[stt.SpeechData(text="", language="zh-CN")],
                    ))
                except Exception:
                    pass

    class FunASRSTT(stt.STT):
        """FunASR 2-pass STT (LiveKit 1.6.5)"""

        def __init__(self, uri="ws://localhost:10096", *, capabilities=None):
            super().__init__(
                capabilities=capabilities
                or stt.STTCapabilities(streaming=True, interim_results=True, offline_recognize=True)
            )
            self._uri = uri

        def stream(self, *, language=NOT_GIVEN, conn_options=DEFAULT_API_CONNECT_OPTIONS) -> "stt.SpeechStream":
            return FunASRStream(stt_inst=self, conn_options=conn_options, sample_rate=48000, uri=self._uri)

        async def _recognize_impl(
            self, buffer, *, language=NOT_GIVEN, conn_options=DEFAULT_API_CONNECT_OPTIONS
        ) -> "stt.SpeechEvent":
            pcm = _buffer_to_pcm(buffer, 16000)
            text = ""
            client = FunASR2PassClient(uri=self._uri)
            async def single():
                if pcm:
                    yield pcm
            try:
                async for r in client.transcribe_stream(single()):
                    if r.pass_type == "offline" and r.text:
                        text = r.text
            except Exception as e:
                logger.warning("FunASR 一次性识别失败: %s", e)
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[stt.SpeechData(text=text, language="zh-CN", confidence=0.95)],
            )


# ========== 工厂函数 ==========


def create_funasr_stt(uri="ws://localhost:10096"):
    """工厂函数: 创建 FunASR 2-pass STT 实例 (LiveKit 1.6.5 兼容)"""
    if not _LK_AVAILABLE:
        logger.warning("livekit.agents 不可用, 无法创建 FunASR STT")
        return None
    if not _WS_AVAILABLE:
        logger.warning("websockets 未安装, 无法创建 FunASR STT")
        return None
    try:
        stt_obj = FunASRSTT(uri=uri)
        logger.info("FunASR 2-pass STT 创建成功: uri=%s", uri)
        return stt_obj
    except Exception as e:
        logger.error("FunASR STT 创建失败: %s", e)
        return None
