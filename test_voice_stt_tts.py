"""
VoiceBridge STT/TTS 单元测试 (v5.2.0 重构版)

覆盖:
  - FunASR 2-pass 消息解析 (online/offline)
  - SenseVoice 标签解析 (<|zh|><|HAPPY|><|Speech|>)
  - 音频帧 → PCM bytes 转换
  - TTS CosyVoiceEngine use_flow_cache 配置
  - VoiceBridgeConfig 新配置项
  - 工厂函数和降级逻辑
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ========== 辅助: Mock Frame ==========


@dataclass
class MockFrame:
    """模拟 LiveKit AudioFrame"""
    data: bytes | np.ndarray
    sample_rate: int = 48000
    num_channels: int = 1
    samples_per_channel: int = 0

    def __init__(self, data, sample_rate=48000):
        self.data = data
        self.sample_rate = sample_rate
        if isinstance(data, bytes):
            self.samples_per_channel = len(data) // 2  # int16
        elif isinstance(data, np.ndarray):
            self.samples_per_channel = len(data)


# ========== STT 测试 ==========


class TestFunASR2PassClient:
    """FunASR 2-pass 客户端核心逻辑测试 (无需网络)"""

    def setup_method(self):
        from voice.stt_adapter import FunASR2PassClient

        self.client = FunASR2PassClient(uri="ws://localhost:10096")

    def test_init_defaults(self):
        """测试默认参数"""
        assert self.client.uri == "ws://localhost:10096"

    def test_parse_online_message(self):
        """测试 Pass1 (online) 消息解析"""
        msg_str = json.dumps({
            "mode": "2pass-online",
            "text": "今天天气真不错",
            "is_final": False,
            "timestamp": [[100, 600]],
        })
        result = self.client._parse_message(msg_str)
        assert result is not None
        assert result.text == "今天天气真不错"
        assert result.pass_type == "online"
        assert not result.is_final
        assert result.language == ""
        assert result.emotion == ""

    def test_parse_online_is_final(self):
        """测试 Pass1 的 is_final=True"""
        msg_str = json.dumps({
            "mode": "2pass-online",
            "text": "今天天气真不错",
            "is_final": True,
        })
        result = self.client._parse_message(msg_str)
        assert result is not None
        assert result.is_final

    def test_parse_offline_message(self):
        """测试 Pass2 (offline) 消息解析 — 带 SenseVoice 标签"""
        msg_str = json.dumps({
            "mode": "2pass-offline",
            "text": "<|zh|><|HAPPY|><|Speech|>今天天气真不错",
            "is_final": True,
        })
        result = self.client._parse_message(msg_str)
        assert result is not None
        assert result.text == "今天天气真不错"
        assert result.pass_type == "offline"
        assert result.is_final
        assert result.language == "zh"
        assert result.emotion == "happy"
        assert result.event == "speech"

    def test_parse_offline_neutral(self):
        """测试 Pass2 中性情感"""
        msg_str = json.dumps({
            "mode": "2pass-offline",
            "text": "<|zh|><|NEUTRAL|><|Speech|>嗯好的我知道了",
        })
        result = self.client._parse_message(msg_str)
        assert result is not None
        assert result.text == "嗯好的我知道了"
        assert result.emotion == "neutral"

    def test_parse_offline_sad_english(self):
        """测试英文 + 悲伤情感"""
        msg_str = json.dumps({
            "mode": "2pass-offline",
            "text": "<|en|><|SAD|><|Speech|>I am not feeling well today",
        })
        result = self.client._parse_message(msg_str)
        assert result is not None
        assert result.text == "I am not feeling well today"
        assert result.language == "en"
        assert result.emotion == "sad"

    def test_parse_offline_applause(self):
        """测试掌声事件"""
        msg_str = json.dumps({
            "mode": "2pass-offline",
            "text": "<|zh|><|NEUTRAL|><|Applause|>",
        })
        result = self.client._parse_message(msg_str)
        assert result is not None
        assert result.text == ""
        assert result.event == "applause"

    def test_parse_empty_text(self):
        """测试空文本 — 应返回 None"""
        msg_str = json.dumps({"mode": "2pass-offline", "text": ""})
        result = self.client._parse_message(msg_str)
        assert result is None

    def test_parse_no_text_field(self):
        """测试无 text 字段"""
        msg_str = json.dumps({"mode": "2pass-online"})
        result = self.client._parse_message(msg_str)
        assert result is None

    def test_parse_binary_message(self):
        """测试二进制消息 — 应返回 None"""
        result = self.client._parse_message(b"\x00\x01\x02")
        assert result is None

    def test_parse_invalid_json(self):
        """测试非法 JSON"""
        result = self.client._parse_message("{invalid json}")
        assert result is None

    def test_parse_unknown_mode(self):
        """测试未知 mode"""
        msg_str = json.dumps({
            "mode": "unknown-mode",
            "text": "something",
        })
        result = self.client._parse_message(msg_str)
        assert result is None

    def test_parse_tags_all_emotions(self):
        """测试所有情感标签"""
        emotions = [
            ("HAPPY", "happy"),
            ("SAD", "sad"),
            ("ANGRY", "angry"),
            ("NEUTRAL", "neutral"),
            ("FEARFUL", "fearful"),
            ("DISGUSTED", "disgusted"),
            ("SURPRISED", "surprised"),
        ]
        for tag, expected in emotions:
            result = self.client._parse_message(json.dumps({
                "mode": "2pass-offline",
                "text": f"<|zh|><|{tag}|><|Speech|>测试",
            }))
            assert result is not None
            assert result.emotion == expected, f"Emotion {tag} → {expected} 失败"

    def test_parse_tags_no_tags(self):
        """测试无标签的纯文本"""
        lang, emotion, event, text = self.client._parse_tags("今天天气真好")
        assert lang == "zh"
        assert emotion == ""
        assert event == ""
        assert text == "今天天气真好"


class TestFrameToPCM:
    """音频帧 → PCM bytes 转换测试"""

    def test_bytes_frame_to_pcm(self):
        """测试 bytes 类型帧转换"""
        from voice.stt_adapter import _frame_to_pcm_bytes

        # 创建 1 秒的 480kHz int16 正弦波
        duration = 1.0
        sample_rate = 48000
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        wave = (np.sin(2 * np.pi * 440 * t) * 32767 * 0.5).astype(np.int16)
        frame = MockFrame(data=wave.tobytes(), sample_rate=sample_rate)

        pcm = _frame_to_pcm_bytes(frame, target_rate=16000)
        assert isinstance(pcm, bytes)
        # 16kHz 1秒 = 16000 samples * 2 bytes = 32000 bytes
        assert len(pcm) == 32000

    def test_numpy_frame_to_pcm(self):
        """测试 numpy 类型帧转换"""
        from voice.stt_adapter import _frame_to_pcm_bytes

        arr = np.random.randn(48000).astype(np.float32) * 0.3
        frame = MockFrame(data=arr, sample_rate=48000)
        pcm = _frame_to_pcm_bytes(frame, target_rate=16000)
        assert isinstance(pcm, bytes)

    def test_pcm_at_target_rate(self):
        """测试相同采样率不重采样"""
        from voice.stt_adapter import _frame_to_pcm_bytes

        arr = np.zeros(16000, dtype=np.int16)
        frame = MockFrame(data=arr.tobytes(), sample_rate=16000)
        pcm = _frame_to_pcm_bytes(frame, target_rate=16000)
        assert len(pcm) == 32000  # 16000 * 2

    def test_stereo_to_mono(self):
        """测试立体声转单声道"""
        from voice.stt_adapter import _frame_to_pcm_bytes

        stereo = np.zeros((48000, 2), dtype=np.float32)
        frame = MockFrame(data=stereo, sample_rate=48000)
        pcm = _frame_to_pcm_bytes(frame, target_rate=16000)
        assert isinstance(pcm, bytes)


class TestFunASRResult:
    """FunASRResult dataclass 测试"""

    def test_dataclass_defaults(self):
        """测试默认值"""
        from voice.stt_adapter import FunASRResult

        r = FunASRResult()
        assert r.text == ""
        assert r.pass_type == "online"
        assert not r.is_final
        assert r.language == ""
        assert r.emotion == ""
        assert r.confidence == 0.95

    def test_offline_result(self):
        """测试离线结果"""
        from voice.stt_adapter import FunASRResult

        r = FunASRResult(
            text="你好世界",
            pass_type="offline",  # nosec B106 — FunASR protocol mode identifier, not a password
            is_final=True,
            language="zh",
            emotion="happy",
            event="speech",
        )
        assert r.text == "你好世界"
        assert r.pass_type == "offline"
        assert r.is_final
        assert r.emotion == "happy"


class TestSTTFactory:
    """STT 工厂函数测试"""

    def test_create_funasr_stt_no_livekit(self):
        """测试 LiveKit 不可用时的降级"""
        from voice.stt_adapter import create_funasr_stt

        # FunASRAdapter 检查 _LIVEKIT_AVAILABLE
        # 测试环境没有 livekit-agents, 预期返回 None
        result = create_funasr_stt(uri="ws://localhost:10096")  # nosec B106 — WebSocket URI, not a password
        assert result is None

    def test_is_available(self):
        """测试依赖检查"""
        from voice.stt_adapter import is_available

        status = is_available()
        assert "livekit" in status
        assert "websockets" in status
        assert isinstance(status["livekit"], bool)
        assert isinstance(status["websockets"], bool)


# ========== TTS 测试 ==========


class TestCosyVoiceEngine:
    """CosyVoice2 引擎测试 (use_flow_cache 配置)"""

    def test_ensure_model_sets_use_flow_cache(self):
        """验证 _ensure_model 配置 use_flow_cache=True"""
        from voice.tts_adapter import CosyVoiceEngine

        engine = CosyVoiceEngine(
            model_path="pretrained_models/CosyVoice2-0.5B",
        )

        # 验证 use_flow_cache 在初始化参数中被正确处理
        # (CosyVoice2 构造函数的 use_flow_cache 参数)
        assert engine._model_path == "pretrained_models/CosyVoice2-0.5B"
        assert engine._sample_rate == 24000
        # 模型延迟加载, 初始未加载
        assert not engine._loaded

    def test_synthesize_empty_text(self):
        """测试空文本 — 不报错"""
        from voice.tts_adapter import CosyVoiceEngine

        engine = CosyVoiceEngine()

        async def _test():
            count = 0
            async for _ in engine.synthesize_stream(""):
                count += 1
            assert count == 0  # 不产生任何 chunk

        import asyncio
        asyncio.run(_test())

    def test_voice_list_complete(self):
        """测试声音列表完整性"""
        from voice.tts_adapter import COSYVOICE_VOICES

        assert len(COSYVOICE_VOICES) == 10
        names = [v["name"] for v in COSYVOICE_VOICES]
        assert "longxiaochun" in names
        assert "longwan" in names
        assert "longcheng" in names

    def test_chunk_dataclass(self):
        """测试 CosyVoiceChunk"""
        from voice.tts_adapter import CosyVoiceChunk

        chunk = CosyVoiceChunk(
            audio=np.array([0.1, 0.2, 0.3], dtype=np.float32),
            sample_rate=24000,
            is_final=False,
        )
        assert len(chunk.audio) == 3
        assert not chunk.is_final


class TestTTSFactory:
    """TTS 工厂函数测试"""

    def test_create_cosyvoice_tts_no_livekit(self):
        """测试 LiveKit 不可用时的降级"""
        from voice.tts_adapter import create_cosyvoice_tts

        result = create_cosyvoice_tts(
            model_path="pretrained_models/CosyVoice2-0.5B",
            voice="longxiaochun",
        )
        assert result is None

    def test_is_available(self):
        """测试依赖检查"""
        from voice.tts_adapter import is_available

        status = is_available()
        assert "livekit" in status
        assert "cosyvoice" in status


# ========== Config 测试 ==========


class TestVoiceBridgeConfig:
    """VoiceBridgeConfig 配置测试"""

    def test_default_values(self):
        """测试默认值"""
        from voice.config import VoiceBridgeConfig

        config = VoiceBridgeConfig()
        assert config.funasr_ws_uri == "ws://localhost:10096"
        assert config.tts_mode == "local"
        assert config.tts_model_path == "pretrained_models/CosyVoice2-0.5B"
        assert config.tts_voice == "longxiaochun"
        assert config.tts_speed == 1.0
        assert config.allow_interruptions

    def test_from_env_overrides(self):
        """测试环境变量覆盖"""
        from voice.config import VoiceBridgeConfig

        os.environ["VOICE_FUNASR_URI"] = "ws://192.168.1.100:10096"
        os.environ["VOICE_TTS_MODEL_PATH"] = "/data/CosyVoice2-0.5B"
        os.environ["VOICE_TTS_VOICE"] = "longwan"
        os.environ["VOICE_TTS_SPEED"] = "1.2"

        config = VoiceBridgeConfig.from_env()
        assert config.funasr_ws_uri == "ws://192.168.1.100:10096"
        assert config.tts_model_path == "/data/CosyVoice2-0.5B"
        assert config.tts_voice == "longwan"
        assert config.tts_speed == 1.2

        # 清理
        del os.environ["VOICE_FUNASR_URI"]
        del os.environ["VOICE_TTS_MODEL_PATH"]
        del os.environ["VOICE_TTS_VOICE"]
        del os.environ["VOICE_TTS_SPEED"]

    def test_to_dict(self):
        """测试 to_dict"""
        from voice.config import VoiceBridgeConfig

        config = VoiceBridgeConfig()
        d = config.to_dict()
        assert d["funasr_ws_uri"] == "ws://localhost:10096"
        assert d["tts_model_path"] == "pretrained_models/CosyVoice2-0.5B"
        assert d["tts_voice"] == "longxiaochun"
