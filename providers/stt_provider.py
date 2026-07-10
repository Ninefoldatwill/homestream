"""
STT Provider - 语音转文字（Speech-to-Text）

开源线·L1基础设施——本地优先，云端备选：
  本地方案: whisper.cpp（离线·隐私·零费用）
  云端备选: 免费语音API

V8多模态生态·语音入口
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class STTEngine(Enum):
    """语音识别引擎"""

    WHISPER_CPP = "whisper_cpp"  # 本地·离线·零费用
    CLOUD_FALLBACK = "cloud_fallback"  # 云端备选


class STTLanguage(Enum):
    """支持的语言"""

    ZH = "zh"  # 中文（默认）
    EN = "en"  # 英文
    AUTO = "auto"  # 自动检测


@dataclass
class STTConfig:
    """STT配置"""

    engine: STTEngine = STTEngine.WHISPER_CPP
    language: STTLanguage = STTLanguage.AUTO
    whisper_binary: str = "whisper-cli"  # whisper.cpp 可执行文件路径
    whisper_model: str = "ggml-base-q5_1.bin"  # 模型文件路径
    sample_rate: int = 16000
    offline_only: bool = True  # 开源线默认脱网


@dataclass
class STTResult:
    """语音识别结果"""

    text: str
    language: str = "zh"
    segments: list[dict] = field(default_factory=list)
    duration_ms: float = 0.0
    engine_used: str = "whisper_cpp"


class STTProvider:
    """语音转文字 Provider"""

    def __init__(self, config: STTConfig | None = None):
        self.config = config or STTConfig()
        self._whisper_available = self._check_whisper()

    # ── 公开API ──────────────────────────────────

    def transcribe(self, audio_path: str, language: STTLanguage | None = None) -> STTResult:
        """转录本地音频文件

        Args:
            audio_path: 音频文件路径 (wav/mp3/m4a)
            language: 语言（默认自动检测）

        Returns:
            STTResult: 识别结果
        """
        lang = language or self.config.language

        if self.config.engine == STTEngine.WHISPER_CPP and self._whisper_available:
            return self._transcribe_whisper(audio_path, lang)
        elif not self.config.offline_only:
            return self._transcribe_cloud(audio_path, lang)
        else:
            logger.warning("whisper.cpp 不可用且 offline_only=True，返回空结果")
            return STTResult(text="[STT不可用]", engine_used="none")

    def transcribe_bytes(self, audio_bytes: bytes, format: str = "wav") -> STTResult:
        """转录音频字节数据（适合API调用）"""
        with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            return self.transcribe(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def is_available(self) -> bool:
        """检查STT是否可用"""
        return self._whisper_available or not self.config.offline_only

    # ── 内部实现 ──────────────────────────────────

    def _check_whisper(self) -> bool:
        """检测 whisper.cpp 是否可用"""
        try:
            result = subprocess.run(
                [self.config.whisper_binary, "--version"], capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("whisper.cpp 未安装（开源用户需自行下载）")
            return False

    def _transcribe_whisper(self, audio_path: str, language: STTLanguage) -> STTResult:
        """使用 whisper.cpp 转录"""
        lang_arg = language.value if language != STTLanguage.AUTO else "auto"
        cmd = [
            self.config.whisper_binary,
            "-m",
            self.config.whisper_model,
            "-f",
            audio_path,
            "-l",
            lang_arg,
            "-oj",  # JSON输出
            "-nt",  # 不翻译
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                logger.error(f"whisper 转录失败: {result.stderr}")
                return STTResult(text="", engine_used="whisper_cpp")

            # 解析JSON输出
            output = result.stdout.strip()
            if output:
                data = json.loads(output)
                text = data.get("text", "")
                segments = data.get("segments", [])
                return STTResult(
                    text=text,
                    language=language.value,
                    segments=segments,
                    engine_used="whisper_cpp",
                )
            return STTResult(text="", engine_used="whisper_cpp")

        except subprocess.TimeoutExpired:
            logger.error("whisper转录超时")
            return STTResult(text="[超时]", engine_used="whisper_cpp")
        except json.JSONDecodeError:
            return STTResult(text=output or "[解析失败]", engine_used="whisper_cpp")

    def _transcribe_cloud(self, audio_path: str, language: STTLanguage) -> STTResult:
        """云端备选转录（需网络，开源用户可选配置）"""
        logger.info("使用云端STT备选")
        return STTResult(text="[云端STT·需配置API密钥]", engine_used="cloud_fallback")


# ── 便捷工厂 ──────────────────────────────────


def create_stt_provider(offline_only: bool = True) -> STTProvider:
    """创建STT Provider（开源线·默认离线）"""
    return STTProvider(
        STTConfig(
            engine=STTEngine.WHISPER_CPP,
            offline_only=offline_only,
        )
    )
