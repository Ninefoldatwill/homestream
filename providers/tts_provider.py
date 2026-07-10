"""
TTS Provider - 文字转语音（Text-to-Speech）

开源线·L1基础设施——本地优先，云端备选：
  本地方案: piper-tts（离线·隐私·零费用）
  云端备选: edge-tts（免费·需网络）

V8多模态生态·语音入口
"""

from __future__ import annotations

import io
import logging
import subprocess
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class TTSEngine(Enum):
    """TTS引擎"""

    PIPER = "piper"  # 本地·离线·零费用
    EDGE_TTS = "edge_tts"  # 微软免费TTS·需网络
    CLOUD_FALLBACK = "cloud"  # 其他云端备选


class TTSVoice(Enum):
    """语音风格"""

    ZH_FEMALE = "zh_female"  # 中文女声
    ZH_MALE = "zh_male"  # 中文男声
    EN_FEMALE = "en_female"  # 英文女声
    EN_MALE = "en_male"  # 英文男声


@dataclass
class TTSConfig:
    """TTS配置"""

    engine: TTSEngine = TTSEngine.PIPER
    voice: TTSVoice = TTSVoice.ZH_FEMALE
    piper_binary: str = "piper"  # piper可执行文件路径
    piper_model: str = "zh_CN-huayan-medium.onnx"  # 中文女声模型
    speed: float = 1.0
    offline_only: bool = True  # 开源线默认脱网


@dataclass
class TTSResult:
    """TTS结果"""

    audio_bytes: bytes
    format: str = "wav"  # wav / mp3
    duration_ms: float = 0.0
    engine_used: str = "piper"
    text: str = ""


class TTSProvider:
    """文字转语音 Provider"""

    # 各语音的 piper 模型映射
    VOICE_MODELS = {
        TTSVoice.ZH_FEMALE: "zh_CN-huayan-medium.onnx",
        TTSVoice.ZH_MALE: "zh_CN-xiaobei-medium.onnx",
        TTSVoice.EN_FEMALE: "en_US-lessac-medium.onnx",
        TTSVoice.EN_MALE: "en_US-ryan-medium.onnx",
    }

    def __init__(self, config: TTSConfig | None = None):
        self.config = config or TTSConfig()
        self._piper_available = self._check_piper()
        self._edge_tts_available = self._check_edge_tts()

    # ── 公开API ──────────────────────────────────

    def speak(self, text: str, voice: TTSVoice | None = None) -> TTSResult:
        """将文字转为语音

        Args:
            text: 要播报的文字
            voice: 语音风格（默认使用config中的）

        Returns:
            TTSResult: 音频数据
        """
        v = voice or self.config.voice

        if self.config.engine == TTSEngine.PIPER and self._piper_available:
            return self._speak_piper(text, v)
        elif self.config.engine == TTSEngine.EDGE_TTS and self._edge_tts_available:
            return self._speak_edge_tts(text, v)
        elif not self.config.offline_only:
            return self._speak_cloud(text, v)
        else:
            logger.warning("所有TTS引擎不可用且 offline_only=True")
            return TTSResult(audio_bytes=b"", format="wav", engine_used="none", text=text)

    def speak_to_file(
        self, text: str, output_path: str, voice: TTSVoice | None = None
    ) -> TTSResult:
        """播报并保存到文件"""
        result = self.speak(text, voice)
        if result.audio_bytes:
            with open(output_path, "wb") as f:
                f.write(result.audio_bytes)
        return result

    def is_available(self) -> bool:
        return self._piper_available or self._edge_tts_available or not self.config.offline_only

    # ── 内部实现 ──────────────────────────────────

    def _check_piper(self) -> bool:
        try:
            result = subprocess.run(
                [self.config.piper_binary, "--version"], capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("piper-tts 未安装（开源用户需自行下载）")
            return False

    def _check_edge_tts(self) -> bool:
        try:
            import edge_tts

            return True
        except ImportError:
            logger.debug("edge-tts 未安装")
            return False

    def _speak_piper(self, text: str, voice: TTSVoice) -> TTSResult:
        """使用 piper 本地TTS"""
        model = self.VOICE_MODELS.get(voice, self.config.piper_model)
        cmd = [
            self.config.piper_binary,
            "--model",
            model,
            "--output_raw",  # 输出原始音频
        ]
        try:
            result = subprocess.run(
                cmd,
                input=text,
                capture_output=True,
                timeout=60,
            )
            if result.returncode != 0:
                logger.error(f"piper TTS失败: {result.stderr}")
                return TTSResult(audio_bytes=b"", engine_used="piper", text=text)
            return TTSResult(
                audio_bytes=result.stdout,
                format="wav",
                engine_used="piper",
                text=text,
            )
        except subprocess.TimeoutExpired:
            logger.error("piper TTS超时")
            return TTSResult(audio_bytes=b"", engine_used="piper", text=text)

    def _speak_edge_tts(self, text: str, voice: TTSVoice) -> TTSResult:
        """使用微软Edge TTS（免费·需网络）"""
        try:
            import edge_tts

            # 映射语音
            voice_map = {
                TTSVoice.ZH_FEMALE: "zh-CN-XiaoxiaoNeural",
                TTSVoice.ZH_MALE: "zh-CN-YunxiNeural",
                TTSVoice.EN_FEMALE: "en-US-JennyNeural",
                TTSVoice.EN_MALE: "en-US-GuyNeural",
            }
            voice_name = voice_map.get(voice, "zh-CN-XiaoxiaoNeural")

            # edge_tts 异步，用 subprocess 简化
            import asyncio

            async def _run():
                communicate = edge_tts.Communicate(text, voice_name)
                buf = io.BytesIO()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        buf.write(chunk["data"])
                return buf.getvalue()

            # Windows 兼容
            try:
                loop = asyncio.get_event_loop()
                audio = loop.run_until_complete(_run())
            except RuntimeError:
                audio = asyncio.run(_run())

            return TTSResult(
                audio_bytes=audio,
                format="mp3",
                engine_used="edge_tts",
                text=text,
            )
        except Exception as e:
            logger.error(f"edge_tts失败: {e}")
            return TTSResult(audio_bytes=b"", engine_used="edge_tts", text=text)

    def _speak_cloud(self, text: str, voice: TTSVoice) -> TTSResult:
        """云端备选"""
        logger.info("使用云端TTS备选")
        return TTSResult(audio_bytes=b"", engine_used="cloud_fallback", text=text)


# ── 便捷工厂 ──────────────────────────────────


def create_tts_provider(offline_only: bool = True) -> TTSProvider:
    """创建TTS Provider（开源线·默认离线）"""
    return TTSProvider(
        TTSConfig(
            engine=TTSEngine.PIPER,
            offline_only=offline_only,
        )
    )
