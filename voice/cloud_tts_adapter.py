"""
云端 TTS 适配接口 — LiveKit Agents 1.6.5 兼容 (可选维度升级)

设计哲学 (铸钥匠 🔑 生态架构):
  - 默认自托管免费托底: CosyVoice2 (本地 GPU, 用户设计首选) → EdgeTTS (兜底)。
  - 云端 TTS 仅作为**可选维度升级接口**: 用户自持 API Key、主动开启 (tts_mode=cloud),
    不默认启用、不捆绑任何云 SDK (纯 httpx 调用 OpenAI 兼容 /v1/audio/speech 端点)。
  - 这是"维度拉开距离"而非"法律筑墙": 我们不强求用户只用本地, 也不替用户决定上云,
    把选择权交还给用户自己 (免费托底永远在, 云是用户的自由选项)。

支持 provider (均 OpenAI 兼容音频端点, 仅示例非绑定):
  - openai   base=https://api.openai.com/v1         model=tts-1 / gpt-4o-mini-tts
  - minimax  base=https://api.minimax.chat/v1       model=speech-01-turbo
  - azure    base=https://<res>.openai.azure.com/v1 model=<部署名>
  - 任意 OpenAI 兼容 TTS 服务 (自填 base/model/voice)
完整配置与成本/隐私权衡见 docs/语音云对接说明.md

IP 合规: 本文件不复制任何云厂商 SDK 源码, 仅用标准 httpx 调 REST, 用户自担 Key 与费用。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("homestream.voice.cloud_tts")

# --- LiveKit Agents SDK (可选导入) ---
try:
    from livekit import rtc  # noqa: F401
    from livekit.agents import tts as lk_tts
    from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
    from livekit.agents.tts import SynthesizedAudio, TTSCapabilities

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

# --- HTTP 客户端 (调用云端 TTS, 需 httpx) ---
try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    httpx = None  # type: ignore


# ========== Provider 预设 (仅 OpenAI 兼容音频端点的默认 model/voice) ==========
CLOUD_TTS_PRESETS: dict[str, dict] = {
    "openai":  {"model": "tts-1", "voice": "alloy", "endpoint": "/audio/speech"},
    "minimax": {"model": "speech-01-turbo", "voice": "Chinese (Mandarin) Girl", "endpoint": "/audio/speech"},
    "azure":   {"model": "tts-1", "voice": "zh-CN-XiaoxiaoNeural", "endpoint": "/audio/speech"},
    # 通用 OpenAI 兼容: model/voice 由用户通过环境变量自填
    "custom":  {"model": "", "voice": "", "endpoint": "/audio/speech"},
}


def _resolve_settings(config) -> tuple[str, str, str, str]:
    """返回 (base_url, api_key, model, voice)"""
    base = (getattr(config, "tts_api_base", "") or os.getenv("VOICE_TTS_API_BASE", "")).rstrip("/")
    key = getattr(config, "tts_api_key", "") or os.getenv("VOICE_TTS_API_KEY", "")
    provider = (getattr(config, "tts_cloud_provider", "openai") or "openai").lower()
    preset = CLOUD_TTS_PRESETS.get(provider, CLOUD_TTS_PRESETS["custom"])
    model = os.getenv("VOICE_TTS_CLOUD_MODEL", "") or preset["model"]
    voice = getattr(config, "tts_cloud_voice", "") or os.getenv("VOICE_TTS_CLOUD_VOICE", "") or preset["voice"]
    return base, key, model, voice


if _LIVEKIT_AVAILABLE:

    class CloudTTSChunkedStream(lk_tts.ChunkedStream):
        """云端 TTS 合成 Stream (整段请求, 直接推送 mp3, LiveKit 1.6.5)

        与 EdgeTTS 对称: 云端返回 mp3, 直接 output_emitter.push(mp3_bytes),
        mime_type="audio/mp3"。AgentSession 的 StreamAdapter 自动桥接为流式播放。
        """

        def __init__(self, *, tts, input_text, conn_options, base, key, model, voice, timeout):
            super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
            self._base = base
            self._key = key
            self._model = model
            self._voice = voice
            self._timeout = timeout

        async def _run(self, output_emitter) -> None:
            request_id = f"cloud_{int(time.monotonic() * 1000)}"
            output_emitter.initialize(
                request_id=request_id, sample_rate=24000, num_channels=1, mime_type="audio/mp3"
            )
            try:
                audio = await asyncio.to_thread(self._synthesize)
                if not audio:
                    output_emitter.flush()
                    return
                output_emitter.push(audio)
                output_emitter.flush()
            except Exception as e:  # noqa: BLE001
                logger.error("云端 TTS 合成失败: %s", e)
                output_emitter.flush()

        def _synthesize(self) -> bytes:
            if not _HTTPX_AVAILABLE:
                raise RuntimeError("httpx 未安装, 无法调用云端 TTS")
            resp = httpx.post(
                f"{self._base}/audio/speech",
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                json={
                    "model": self._model,
                    "input": self.input_text,
                    "voice": self._voice,
                    "response_format": "mp3",
                    "speed": 1.0,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.content

    class CloudTTS(lk_tts.TTS):
        """云端 TTS 插件 (LiveKit Agents 1.6.5 兼容, 可选维度升级)

        streaming=False: 云端整段合成, 由 StreamAdapter 桥接为流式播放。
        """

        def __init__(self, base: str, key: str, model: str, voice: str, timeout: float = 60.0, *, capabilities=None):
            super().__init__(
                capabilities=capabilities or TTSCapabilities(streaming=False),
                sample_rate=24000,
                num_channels=1,
            )
            self._base = base
            self._key = key
            self._model = model
            self._voice = voice
            self._timeout = timeout

        def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
            return CloudTTSChunkedStream(
                tts=self, input_text=text, conn_options=conn_options,
                base=self._base, key=self._key, model=self._model, voice=self._voice,
                timeout=self._timeout,
            )

        async def list_voices(self):
            return [{"name": self._voice, "description": f"云端 {self._model} ({self._voice})"}]


def create_cloud_tts(config=None, timeout: float = 60.0) -> Any:
    """工厂: 创建云端 TTS 实例 (需 tts_mode=cloud 且 tts_api_base 非空)"""
    if not _LIVEKIT_AVAILABLE:
        logger.warning("LiveKit SDK 不可用, 无法创建云端 TTS")
        return None
    if not _HTTPX_AVAILABLE:
        logger.warning("httpx 未安装, 无法调用云端 TTS")
        return None
    base, key, model, voice = _resolve_settings(config)
    if not base or not key or not model or not voice:
        logger.warning("云端 TTS 配置不完整 (需 base+key+model+voice), 跳过")
        return None
    try:
        tts = CloudTTS(base=base, key=key, model=model, voice=voice, timeout=timeout)
        logger.info("云端 TTS 创建成功: provider=%s model=%s", getattr(config, "tts_cloud_provider", "openai"), model)
        return tts
    except Exception as e:  # noqa: BLE001
        logger.error("云端 TTS 创建失败: %s", e)
        return None
