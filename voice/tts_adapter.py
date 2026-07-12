"""
CosyVoice2 TTS Streaming Adapter

将阿里 CosyVoice2 的语音合成能力适配为 LiveKit Agents 的 TTS 接口。

CosyVoice2 优势:
  - Apache 2.0, 可商用
  - 中文自然度第一, 支持 18+ 方言
  - 流式合成 (chunk-by-chunk 输出)
  - 带内置 MCP Server
  - 声音克隆 (3秒参考音频)

Streaming 策略:
  CosyVoice2 原生支持流式合成, 直接将 chunk 转为 LiveKit AudioFrame。

用法:
  # 在 agent.py 中
  from voice.tts_adapter import CosyVoiceTTS
  tts = CosyVoiceTTS(model_path="pretrained_models/CosyVoice2-0.5B")
  session = AgentSession(tts=tts, ...)

  # 独立测试 (不需要 LiveKit SDK)
  from voice.tts_adapter import CosyVoiceEngine
  engine = CosyVoiceEngine()
  async for audio_chunk in engine.synthesize_stream("你好世界"):
    # audio_chunk: float32 numpy array, 24kHz
    ...
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import numpy as np

logger = logging.getLogger("homestream.voice.tts")

# --- LiveKit Agents SDK (可选导入) ---
try:
    from livekit.agents import tts as lk_tts
    from livekit.agents.tts import (
        SynthesizedAudio,
        TTSCapabilities,
    )

    # AudioFrame 可能在不同位置
    try:
        from livekit.rtc import AudioFrame
    except ImportError:
        try:
            from livekit.agents.utils import AudioFrame
        except ImportError:
            AudioFrame = None  # type: ignore

    _LIVEKIT_AVAILABLE = True
except ImportError:
    _LIVEKIT_AVAILABLE = False
    lk_tts = None  # type: ignore

    @dataclass
    class SynthesizedAudio:  # type: ignore
        data: Any = None
        sample_rate: int = 24000
        num_channels: int = 1
        request_id: str = ""
        timestamp: float = 0.0
        frame_id: int = 0

    @dataclass
    class TTSCapabilities:  # type: ignore
        streaming: bool = False

    AudioFrame = None  # type: ignore

# --- CosyVoice2 (可选导入) ---
try:
    from cosyvoice.cli.cosyvoice import CosyVoice2 as _CosyVoice2Model

    _COSYVOICE_AVAILABLE = True
except ImportError:
    _COSYVOICE_AVAILABLE = False
    _CosyVoice2Model = None  # type: ignore


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


# ========== CosyVoice2 推理引擎 ==========


@dataclass
class CosyVoiceChunk:
    """CosyVoice2 合成块"""

    audio: np.ndarray  # float32, -1.0~1.0
    sample_rate: int = 24000
    is_final: bool = False


class CosyVoiceEngine:
    """
    CosyVoice2 推理引擎 (独立于 LiveKit, 可单独测试)

    封装 CosyVoice2 模型, 提供:
      - synthesize_stream(text, voice) → AsyncIterator[CosyVoiceChunk]
      - synthesize(text, voice) → np.ndarray (完整音频)
      - 模型延迟加载
    """

    def __init__(
        self,
        model_path: str = "pretrained_models/CosyVoice2-0.5B",
        sample_rate: int = 24000,
    ):
        self._model_path = model_path
        self._sample_rate = sample_rate
        self._model: Any = None
        self._loaded = False

    def _ensure_model(self):
        """延迟加载模型"""
        if self._loaded:
            return
        if not _COSYVOICE_AVAILABLE:
            raise RuntimeError(
                "CosyVoice2 未安装。"
                "安装: git clone https://github.com/FunAudioLLM/CosyVoice.git && "
                "cd CosyVoice && pip install -r requirements.txt"
            )

        logger.info("加载 CosyVoice2 模型: %s use_flow_cache=True ...", self._model_path)
        self._model = _CosyVoice2Model(
            self._model_path,
            use_flow_cache=True,  # 启用流式 KV cache, 降低流式合成延迟
        )
        self._loaded = True
        logger.info("CosyVoice2 模型加载完成 (use_flow_cache=True)")

    async def synthesize_stream(
        self,
        text: str,
        voice: str = "longxiaochun",
        speed: float = 1.0,
    ) -> AsyncIterator[CosyVoiceChunk]:
        """
        流式合成语音

        Args:
            text: 要合成的文本
            voice: 声音名称 (如 longxiaochun)
            speed: 语速 (0.5~2.0, 1.0=正常)

        Yields:
            CosyVoiceChunk (audio float32 numpy array)
        """
        if not text.strip():
            return

        # 模型推理放到线程池, 但流式 yield 需要桥接
        queue: asyncio.Queue[CosyVoiceChunk | None] = asyncio.Queue()

        async def _producer():
            try:
                await asyncio.to_thread(self._infer_stream, text, voice, speed, queue)
            except Exception as e:
                logger.error("CosyVoice2 流式合成失败: %s", e)
            finally:
                await queue.put(None)  # sentinel

        producer_task = asyncio.create_task(_producer())

        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk

        await producer_task

    def _infer_stream(
        self,
        text: str,
        voice: str,
        speed: float,
        queue: asyncio.Queue,
    ):
        """同步流式推理 (在线程池中执行, 通过 queue 传递结果)"""
        self._ensure_model()

        try:
            # CosyVoice2 inference_sft 返回生成器
            chunks = self._model.inference_sft(
                text,
                voice,
                stream=True,
                speed=speed,
            )

            for chunk in chunks:
                # chunk 是 dict: {"tts_speech": np.ndarray}
                audio = chunk.get("tts_speech", chunk) if isinstance(chunk, dict) else chunk
                if isinstance(audio, np.ndarray):
                    # 确保是 1D float32
                    if audio.ndim > 1:
                        audio = audio.squeeze()
                    audio = audio.astype(np.float32)
                    # 通过 queue 传递 (线程安全)
                    asyncio.run_coroutine_threadsafe(
                        queue.put(
                            CosyVoiceChunk(
                                audio=audio,
                                sample_rate=self._sample_rate,
                                is_final=False,
                            )
                        ),
                        asyncio.get_event_loop(),
                    )

            # 最终标记
            asyncio.run_coroutine_threadsafe(
                queue.put(
                    CosyVoiceChunk(
                        audio=np.array([], dtype=np.float32),
                        sample_rate=self._sample_rate,
                        is_final=True,
                    )
                ),
                asyncio.get_event_loop(),
            )

        except Exception as e:
            logger.error("CosyVoice2 推理异常: %s", e)
            raise

    async def synthesize(
        self,
        text: str,
        voice: str = "longxiaochun",
        speed: float = 1.0,
    ) -> np.ndarray:
        """非流式合成 (返回完整音频)"""
        chunks = []
        async for chunk in self.synthesize_stream(text, voice, speed):
            if not chunk.is_final and len(chunk.audio) > 0:
                chunks.append(chunk.audio)
        return np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)


# ========== LiveKit TTS Adapter ==========

if _LIVEKIT_AVAILABLE and AudioFrame is not None:

    def _audio_to_frame(
        audio: np.ndarray,
        sample_rate: int = 24000,
    ) -> AudioFrame:
        """将 float32 numpy 转为 LiveKit AudioFrame"""
        # float32 → int16
        audio_int16 = np.clip(audio * 32768.0, -32768, 32767).astype(np.int16)
        return AudioFrame(
            data=audio_int16.tobytes(),
            sample_rate=sample_rate,
            num_channels=1,
            samples_per_channel=len(audio_int16),
        )

    class CosyVoiceSynthesizeStream(lk_tts.SynthesizeStream):  # type: ignore[misc]
        """CosyVoice2 流式合成 Stream"""

        def __init__(
            self,
            engine: CosyVoiceEngine,
            voice: str = "longxiaochun",
            speed: float = 1.0,
        ):
            super().__init__()
            self._engine = engine
            self._voice = voice
            self._speed = speed
            self._text_buffer = ""

        def push_text(self, text: str) -> None:
            """接收文本 (可分块推送)"""
            self._text_buffer += text

        async def _main_task(self) -> None:
            """主循环: 等待文本完成 → 流式合成 → 发出音频帧"""
            # 等待 flush 信号
            await self._input_ch.recv()

            text = self._text_buffer.strip()
            if not text:
                self._event_ch.send_nowait(
                    SynthesizedAudio(
                        data=_audio_to_frame(np.array([], dtype=np.float32)),
                        sample_rate=self._engine._sample_rate,
                    )
                )
                return

            request_id = f"cv_{int(time.monotonic() * 1000)}"

            try:
                frame_idx = 0
                async for chunk in self._engine.synthesize_stream(text, self._voice, self._speed):
                    if chunk.is_final or len(chunk.audio) == 0:
                        continue

                    frame = _audio_to_frame(chunk.audio, chunk.sample_rate)
                    audio_data = SynthesizedAudio(
                        data=frame,
                        sample_rate=chunk.sample_rate,
                        request_id=request_id,
                        timestamp=time.monotonic(),
                        frame_id=frame_idx,
                    )
                    self._event_ch.send_nowait(audio_data)
                    frame_idx += 1

                logger.info(
                    "TTS 合成完成: text=%s frames=%d",
                    text[:30],
                    frame_idx,
                )
            except Exception as e:
                logger.error("TTS 合成失败: %s", e)
                # 发出空帧以避免管线卡住
                self._event_ch.send_nowait(
                    SynthesizedAudio(
                        data=_audio_to_frame(np.array([], dtype=np.float32)),
                        sample_rate=self._engine._sample_rate,
                        request_id=request_id,
                    )
                )

    class CosyVoiceTTS(lk_tts.TTS):  # type: ignore[misc]
        """
        CosyVoice2 TTS 插件 (LiveKit Agents 兼容)

        用法:
            tts = CosyVoiceTTS(
                model_path="pretrained_models/CosyVoice2-0.5B",
                voice="longxiaochun",
            )
            session = AgentSession(tts=tts, ...)
        """

        def __init__(
            self,
            model_path: str = "pretrained_models/CosyVoice2-0.5B",
            voice: str = "longxiaochun",
            speed: float = 1.0,
            sample_rate: int = 24000,
            *,
            capabilities: TTSCapabilities | None = None,
        ):
            super().__init__(
                capabilities=capabilities or TTSCapabilities(streaming=True),
            )
            self._engine = CosyVoiceEngine(
                model_path=model_path,
                sample_rate=sample_rate,
            )
            self._voice = voice
            self._speed = speed

        def synthesize(self, text: str) -> lk_tts.SynthesizeStream:
            """创建流式合成会话"""
            stream = CosyVoiceSynthesizeStream(
                engine=self._engine,
                voice=self._voice,
                speed=self._speed,
            )
            stream.push_text(text)
            return stream

        async def list_voices(self) -> list[dict[str, str]]:
            """列出可用声音"""
            return COSYVOICE_VOICES

else:
    # LiveKit SDK 不可用时的 stub
    class CosyVoiceTTS:  # type: ignore[no-redef]
        """Stub (LiveKit SDK 未安装). 安装: pip install livekit-agents"""

        def __init__(self, *args, **kwargs):
            raise RuntimeError("LiveKit Agents SDK 未安装。安装: pip install 'livekit-agents~=1.4'")


# ========== 工厂函数 ==========


def create_cosyvoice_tts(
    model_path: str = "pretrained_models/CosyVoice2-0.5B",
    voice: str = "longxiaochun",
    speed: float = 1.0,
    sample_rate: int = 24000,
) -> Any:
    """
    工厂函数: 创建 CosyVoice TTS 实例

    当 LiveKit SDK 不可用时返回 None (供 agent.py 降级处理)
    """
    if not _LIVEKIT_AVAILABLE:
        logger.warning("LiveKit SDK 不可用, CosyVoiceTTS 不可创建")
        return None

    try:
        return CosyVoiceTTS(
            model_path=model_path,
            voice=voice,
            speed=speed,
            sample_rate=sample_rate,
        )
    except Exception as e:
        logger.error("CosyVoiceTTS 创建失败: %s", e)
        return None


# ========== 模块状态 ==========


def is_available() -> dict[str, bool]:
    """检查依赖可用性"""
    return {
        "livekit": _LIVEKIT_AVAILABLE,
        "cosyvoice": _COSYVOICE_AVAILABLE,
    }
