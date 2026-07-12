"""
VoiceBridge 配置模块

从环境变量加载语音栈配置, 全部可选, 缺省即自托管 localhost 模式。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class VoiceBridgeConfig:
    """VoiceBridge 语音栈配置 (零注册默认值)"""

    # --- LiveKit 自托管连接 (默认 localhost, 零注册) ---
    livekit_url: str = field(
        default_factory=lambda: os.getenv("VOICE_LIVEKIT_URL", "ws://localhost:7880")
    )
    livekit_api_key: str = field(
        default_factory=lambda: os.getenv("VOICE_LIVEKIT_API_KEY", "devkey")
    )
    livekit_api_secret: str = field(
        default_factory=lambda: os.getenv("VOICE_LIVEKIT_API_SECRET", "devsecret")
    )

    # --- LLM 路由策略 (对接三层路由) ---
    router_strategy: str = field(
        default_factory=lambda: os.getenv("VOICE_ROUTER_STRATEGY", "SPEED_FIRST")
    )

    # --- STT 配置 (FunASR 2-pass Docker, 自托管) ---
    # 连接 FunASR 2-pass WebSocket 服务
    # Docker: registry.cn-hangzhou.aliyuncs.com/funasr_repo/funasr:funasr-runtime-sdk-online-cpu
    # Hub port 10095 → host port 10096
    funasr_ws_uri: str = field(
        default_factory=lambda: os.getenv("VOICE_FUNASR_URI", "ws://localhost:10096")
    )
    funasr_chunk_size: str = field(
        default_factory=lambda: os.getenv("VOICE_FUNASR_CHUNK_SIZE", "[5,10,5]")
    )

    # --- TTS 配置 (CosyVoice2 本地 GPU) ---
    tts_mode: str = field(default_factory=lambda: os.getenv("VOICE_TTS_MODE", "local"))
    tts_model_path: str = field(
        default_factory=lambda: os.getenv(
            "VOICE_TTS_MODEL_PATH", "pretrained_models/CosyVoice2-0.5B"
        )
    )
    tts_voice: str = field(default_factory=lambda: os.getenv("VOICE_TTS_VOICE", "longxiaochun"))
    tts_sample_rate: int = field(
        default_factory=lambda: int(os.getenv("VOICE_TTS_SAMPLE_RATE", "24000"))
    )
    tts_speed: float = field(default_factory=lambda: float(os.getenv("VOICE_TTS_SPEED", "1.0")))
    # 降级: 外部 API (仅当本地模型不可用时)
    tts_api_base: str = field(default_factory=lambda: os.getenv("VOICE_TTS_API_BASE", ""))
    tts_api_key: str = field(default_factory=lambda: os.getenv("VOICE_TTS_API_KEY", ""))

    # --- VAD (Silero, 本地) ---
    vad_threshold: float = field(
        default_factory=lambda: float(os.getenv("VOICE_VAD_THRESHOLD", "0.5"))
    )
    vad_min_speech_duration: float = field(
        default_factory=lambda: float(os.getenv("VOICE_VAD_MIN_SPEECH", "0.2"))
    )
    vad_min_silence_duration: float = field(
        default_factory=lambda: float(os.getenv("VOICE_VAD_MIN_SILENCE", "0.12"))
    )

    # --- Agent 行为 ---
    agent_name: str = field(
        default_factory=lambda: os.getenv("VOICE_AGENT_NAME", "homestream-voice")
    )
    allow_interruptions: bool = field(
        default_factory=lambda: os.getenv("VOICE_ALLOW_INTERRUPTIONS", "true").lower() == "true"
    )

    @classmethod
    def from_env(cls) -> VoiceBridgeConfig:
        """从环境变量加载配置"""
        return cls()

    def to_dict(self) -> dict:
        return {
            "livekit_url": self.livekit_url,
            "router_strategy": self.router_strategy,
            "funasr_ws_uri": self.funasr_ws_uri,
            "tts_model_path": self.tts_model_path,
            "tts_voice": self.tts_voice,
            "vad_threshold": self.vad_threshold,
            "agent_name": self.agent_name,
            "allow_interruptions": self.allow_interruptions,
        }
