"""
FunASR 2-pass STT Streaming Adapter

将 FunASR 2-pass WebSocket 服务 (Docker 自托管) 适配为 LiveKit Agents
的 streaming STT 接口。

架构 (官方推荐 2-pass 生产方案):
  用户音频段 ──→ FunASR WebSocket (ws://localhost:10096)
          ├── Pass 1: paraformer-zh-streaming (600ms chunk, ~80ms 延迟)
          │   → 实时 Interim 结果: 边说边出字
          └── Pass 2: SenseVoiceSmall (VAD 端点后整段重写)
              → 最终 Final 结果: 高准确率 + 情感 + 事件标签

SenseVoice 输出标签:
  <|zh|><|HAPPY|><|Speech|>实际文本 → text/language/emotion/event

Zero 注册, Zero 云端依赖.
Docker: registry.cn-hangzhou.aliyuncs.com/funasr_repo/funasr:funasr-runtime-sdk-online-cpu
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import numpy as np

logger = logging.getLogger("homestream.voice.stt")

# --- WebSocket 客户端 (可选导入) ---
try:
    import websockets as _ws

    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    _ws = None  # type: ignore

# --- LiveKit Agents SDK (可选导入) ---
try:
    from livekit.agents import stt as lk_stt
    from livekit.agents.stt import (
        SpeechAlternative,
        SpeechData,
        SpeechEvent,
        SpeechEventType,
    )

    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False
    lk_stt = None  # type: ignore

    class SpeechEventType:  # type: ignore
        START_OF_SPEECH = 0
        INTERIM_TRANSCRIPT = 1
        FINAL_TRANSCRIPT = 2
        END_OF_SPEECH = 3

    @dataclass
    class SpeechAlternative:  # type: ignore
        text: str = ""
        confidence: float = 0.0
        language: str = ""

    @dataclass
    class SpeechData:  # type: ignore
        text: str = ""
        confidence: float = 0.0
        language: str = ""
        emotion: str = ""
        event: str = ""

    @dataclass
    class SpeechEvent:  # type: ignore
        type: int = 0
        alternatives: list = field(default_factory=list)
        request_id: str = ""
        timestamp: float = 0.0


# ========== FunASR 2-pass WebSocket 客户端 ==========

class FunASR2PassClient:
    """
    FunASR 2-pass WebSocket 客户端

    连接自托管的 FunASR Docker 服务, 使用 2-pass 协议:
      - Pass 1: streaming Paraformer → 实时文本 (INTERIM)
      - Pass 2: SenseVoice → 最终文本 + 情感 + 事件 (FINAL)

    用法:
        client = FunASR2PassClient(uri="ws://localhost:10096")
        async for result in client.transcribe_stream(audio_chunks):
            if result.pass_type == "online":
                print(f"[实时] {result.text}")
            else:
                print(f"[最终] {result.text} (情感={result.emotion})")
    """

    def __init__(
        self,
        uri: str = "ws://localhost:10096",
        chunk_size: list[int] | None = None,
        sample_rate: int = 16000,
        reconnect_attempts: int = 3,
        reconnect_delay: float = 1.0,
    ):
        self._uri = uri
        self._chunk_size = chunk_size or [5, 10, 5]  # 600ms center chunk
        self._sample_rate = sample_rate
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay = reconnect_delay

    @property
    def uri(self) -> str:
        return self._uri

    async def transcribe_stream(
        self,
        audio_iter: AsyncIterator[bytes],
    ) -> AsyncIterator[FunASRResult]:
        """
        流式识别 (async generator)

        Args:
            audio_iter: 16kHz 16-bit mono PCM 音频块迭代器

        Yields:
            FunASRResult (pass_type="online" 为实时, "offline" 为最终)
        """
        if not _WS_AVAILABLE:
            raise RuntimeError(
                "websockets 未安装。安装: pip install websockets"
            )

        last_error = None
        for attempt in range(self._reconnect_attempts):
            try:
                async with _ws.connect(self._uri) as ws:
                    # 发送初始化消息 (JSON)
                    init_msg = {
                        "mode": "2pass",
                        "chunk_size": self._chunk_size,
                        "wav_name": "microphone",
                        "is_speaking": True,
                        "itn": True,
                    }
                    await ws.send(json.dumps(init_msg))

                    # 并行: 发送音频 + 接收结果
                    async def _send_audio():
                        async for chunk in audio_iter:
                            await ws.send(chunk)
                        # 发送结束标记
                        await ws.send(json.dumps({"is_speaking": False}))

                    async def _recv_results():
                        async for msg in ws:
                            result = self._parse_message(msg)
                            if result:
                                yield result

                    send_task = asyncio.create_task(_send_audio())
                    try:
                        async for result in _recv_results():
                            yield result
                    finally:
                        await send_task

                # 正常退出
                return

            except (ConnectionRefusedError, OSError) as e:
                last_error = e
                logger.warning(
                    "FunASR 连接失败 (尝试 %d/%d): %s",
                    attempt + 1,
                    self._reconnect_attempts,
                    e,
                )
                if attempt < self._reconnect_attempts - 1:
                    await asyncio.sleep(self._reconnect_delay)

        raise ConnectionError(
            f"FunASR 连接失败 ({self._reconnect_attempts} 次尝试): {last_error}"
        )

    def _parse_message(self, msg: Any) -> FunASRResult | None:
        """解析 FunASR WebSocket 返回消息"""
        if isinstance(msg, bytes):
            # 服务端通常不返回二进制, 忽略
            return None

        try:
            data = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            return None

        mode = data.get("mode", "")
        text = data.get("text", "")

        if not text:
            return None

        if mode == "2pass-online":
            # Pass 1: 实时流式结果 (不加标签)
            return FunASRResult(
                text=text.strip(),
                pass_type="online",  # noqa: S106 — FunASR protocol mode identifier
                is_final=data.get("is_final", False),
                timestamp=data.get("timestamp"),
            )

        elif mode == "2pass-offline":
            # Pass 2: 最终结果 (含 SenseVoice <|...|> 标签)
            language, emotion, event, clean_text = self._parse_tags(text)

            return FunASRResult(
                text=clean_text,
                pass_type="offline",  # noqa: S106 — FunASR protocol mode identifier
                is_final=True,
                language=language,
                emotion=emotion,
                event=event,
                timestamp=data.get("timestamp"),
            )

        return None

    @staticmethod
    def _parse_tags(text: str) -> tuple[str, str, str, str]:
        """
        解析 SenseVoice 标签 <|zh|><|HAPPY|><|Speech|>实际文本

        Returns:
            (language, emotion, event, clean_text)
        """
        language = "zh"
        emotion = ""
        event = ""
        clean_text = text

        tags = re.findall(r"<\|([^|]+)\|>", text)
        clean_text = re.sub(r"<\|[^|]+\|>", "", text).strip()

        for tag in tags:
            tag_upper = tag.upper()
            if tag_upper in ("ZH", "EN", "JA", "KO", "YUE", "AUTO"):
                language = tag_upper.lower()
            elif tag_upper in (
                "HAPPY", "SAD", "ANGRY", "NEUTRAL",
                "FEARFUL", "DISGUSTED", "SURPRISED",
            ):
                emotion = tag_upper.lower()
            elif tag_upper in ("SPEECH", "MUSIC", "SILENCE", "APPLAUSE"):
                event = tag_upper.lower()

        return language, emotion, event, clean_text


@dataclass
class FunASRResult:
    """FunASR 2-pass 识别结果"""
    text: str = ""
    pass_type: str = "online"  # noqa: S105 — FunASR protocol mode identifier
    is_final: bool = False
    language: str = ""
    emotion: str = ""
    event: str = ""
    confidence: float = 0.95
    timestamp: Any = None


# ========== 音频工具 (帧转 PCM bytes) ==========

def _frame_to_pcm_bytes(frame: Any, target_rate: int = 16000) -> bytes:
    """
    将 LiveKit AudioFrame 转为 16kHz 16-bit mono PCM bytes

    这是 FunASR WebSocket 要求的音频格式。
    """
    if hasattr(frame, "data"):
        data = frame.data
        if isinstance(data, bytes):
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

    # 单声道
    if arr.ndim > 1:
        arr = arr.mean(axis=1)

    # 重采样到 16kHz
    src_rate = getattr(frame, "sample_rate", 48000) if hasattr(frame, "sample_rate") else 48000
    if src_rate != target_rate:
        ratio = target_rate / src_rate
        n_out = int(len(arr) * ratio)
        indices = np.linspace(0, len(arr) - 1, n_out)
        arr = np.interp(indices, np.arange(len(arr)), arr)

    # float32 → int16 PCM
    arr_int16 = np.clip(arr * 32768.0, -32768, 32767).astype(np.int16)
    return arr_int16.tobytes()


# ========== LiveKit STT Adapter ==========

if _LIVEKIT_AVAILABLE:

    class FunASRSpeechStream(lk_stt.SpeechStream):  # type: ignore[misc]
        """
        FunASR 2-pass SpeechStream

        接收 LiveKit 音频帧 → 转为 PCM → 发送到 FunASR WebSocket
        → 接收 2-pass 结果 → 发出 INTERIM/FINAL 事件
        """

        def __init__(
            self,
            client: FunASR2PassClient,
            sample_rate: int = 48000,
        ):
            super().__init__()
            self._client = client
            self._source_rate = sample_rate

        async def _main_task(self) -> None:
            """主循环: 音频帧 → PCM → FunASR WS → 事件"""
            # 构建音频块 async iter
            async def _audio_chunks():
                while True:
                    frame = await self._input_ch.recv()
                    pcm_bytes = _frame_to_pcm_bytes(frame, 16000)
                    yield pcm_bytes

            try:
                async for result in self._client.transcribe_stream(
                    _audio_chunks()
                ):
                    if not result.text:
                        continue

                    alt = SpeechAlternative(
                        text=result.text,
                        confidence=result.confidence,
                        language=result.language or "zh",
                    )

                    if result.pass_type == "online":  # noqa: S105
                        # Pass 1: 实时结果 (INTERIM)
                        event = SpeechEvent(
                            type=SpeechEventType.INTERIM_TRANSCRIPT,
                            alternatives=[alt],
                            request_id=f"funasr_p1_{int(time.monotonic() * 1000)}",
                            timestamp=time.monotonic(),
                        )
                        self._event_ch.send_nowait(event)

                    elif result.pass_type == "offline":  # noqa: S105
                        # Pass 2: 最终结果 (FINAL) + 情感/事件
                        event = SpeechEvent(
                            type=SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[alt],
                            request_id=f"funasr_p2_{int(time.monotonic() * 1000)}",
                            timestamp=time.monotonic(),
                        )
                        # 附加情感/事件到 event (供下游)
                        event.emotion = result.emotion
                        event.audio_event = result.event
                        self._event_ch.send_nowait(event)

                        logger.info(
                            "STT final: text=%s emotion=%s lang=%s",
                            result.text[:50],
                            result.emotion,
                            result.language,
                        )

            except ConnectionError as e:
                logger.error("FunASR 连接断开: %s", e)

    class FunASR2PassSTT(lk_stt.STT):  # type: ignore[misc]
        """
        FunASR 2-pass STT 插件 (LiveKit Agents 兼容)

        Pass 1: streaming Paraformer → 实时文本 (低延迟)
        Pass 2: SenseVoice → 最终文本 + 情感 + 事件 (高准确率)

        用法:
            stt = FunASR2PassSTT(uri="ws://localhost:10096")
            session = AgentSession(stt=stt, ...)
        """

        def __init__(
            self,
            uri: str = "ws://localhost:10096",
            *,
            capabilities: lk_stt.STTCapabilities | None = None,
        ):
            super().__init__(
                capabilities=capabilities
                or lk_stt.STTCapabilities(
                    streaming=True,
                    interleaved=True,
                )
            )
            self._client = FunASR2PassClient(uri=uri)

        async def stream(self) -> lk_stt.SpeechStream:
            """创建流式识别会话"""
            return FunASRSpeechStream(client=self._client)

        async def recognize(
            self,
            frame: Any,
            *,
            language: str | None = None,
        ) -> lk_stt.SpeechEvent:
            """非流式单帧识别 (较少使用)"""
            # 转为 PCM → 用一次 offline 请求
            pcm_bytes = _frame_to_pcm_bytes(frame, 16000)

            async def _single_chunk():
                yield pcm_bytes

            async for result in self._client.transcribe_stream(
                _single_chunk()
            ):
                if result.pass_type == "offline" and result.text:  # noqa: S105
                    alt = SpeechAlternative(
                        text=result.text,
                        confidence=result.confidence,
                        language=result.language or language or "zh",
                    )
                    event = SpeechEvent(
                        type=SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=[alt],
                        request_id=f"funasr_rec_{int(time.monotonic() * 1000)}",
                        timestamp=time.monotonic(),
                    )
                    event.emotion = result.emotion
                    event.audio_event = result.event
                    return event

            # 空结果
            return SpeechEvent(
                type=SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[],
                request_id=f"funasr_rec_{int(time.monotonic() * 1000)}",
                timestamp=time.monotonic(),
            )

else:
    class FunASR2PassSTT:  # type: ignore[no-redef]
        """Stub (LiveKit SDK 未安装)"""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "LiveKit Agents SDK 未安装。安装: pip install 'livekit-agents~=1.4'"
            )


# ========== 工厂函数 ==========

def create_funasr_stt(
    uri: str = "ws://localhost:10096",
) -> Any:
    """
    工厂函数: 创建 FunASR 2-pass STT 实例

    当 LiveKit SDK 不可用时返回 None
    """
    if not _LIVEKIT_AVAILABLE:
        logger.warning("LiveKit SDK 不可用, FunASR2PassSTT 不可创建")
        return None

    if not _WS_AVAILABLE:
        logger.warning("websockets 不可用, FunASR2PassSTT 不可创建")
        return None

    try:
        return FunASR2PassSTT(uri=uri)
    except Exception as e:
        logger.error("FunASR2PassSTT 创建失败: %s", e)
        return None


# ========== 模块状态 ==========

def is_available() -> dict[str, bool]:
    """检查依赖可用性"""
    return {
        "livekit": _LIVEKIT_AVAILABLE,
        "websockets": _WS_AVAILABLE,
    }
