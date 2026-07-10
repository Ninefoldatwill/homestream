"""
OCR Provider - 图片文字提取（Optical Character Recognition）

开源线·L1基础设施——本地优先，云端备选：
  本地方案: PaddleOCR（离线·隐私·零费用）
  云端备选: 免费OCR API

V8多模态生态·视觉入口
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class OCREngine(Enum):
    """OCR引擎"""

    PADDLE_OCR = "paddle_ocr"  # 本地·离线·零费用（推荐）
    TESSERACT = "tesseract"  # 本地备选
    CLOUD_FALLBACK = "cloud"  # 云端备选


class OCRMode(Enum):
    """识别模式"""

    TEXT_ONLY = "text_only"  # 纯文本
    STRUCTURED = "structured"  # 带位置信息
    TABLE = "table"  # 表格提取


@dataclass
class OCRConfig:
    """OCR配置"""

    engine: OCREngine = OCREngine.PADDLE_OCR
    mode: OCRMode = OCRMode.TEXT_ONLY
    language: str = "ch"  # ch / en / ch_en
    offline_only: bool = True  # 开源线默认脱网
    dpi: int = 300


@dataclass
class OCRBlock:
    """OCR文字块"""

    text: str
    confidence: float = 0.0
    bbox: tuple[int, int, int, int] | None = None  # (x, y, w, h)


@dataclass
class OCRResult:
    """OCR结果"""

    text: str  # 全部文字（换行分隔）
    blocks: list[OCRBlock] = field(default_factory=list)
    engine_used: str = "paddle_ocr"
    processing_ms: float = 0.0


class OCRProvider:
    """图片文字提取 Provider"""

    def __init__(self, config: OCRConfig | None = None):
        self.config = config or OCRConfig()
        self._paddle_available = self._check_paddle()
        self._tesseract_available = self._check_tesseract()

    # ── 公开API ──────────────────────────────────

    def extract_text(self, image_path: str) -> OCRResult:
        """提取图片中的文字

        Args:
            image_path: 图片文件路径

        Returns:
            OCRResult: 识别结果
        """
        import time

        start = time.time()

        if self.config.engine == OCREngine.PADDLE_OCR and self._paddle_available:
            result = self._extract_paddle(image_path)
        elif self.config.engine == OCREngine.TESSERACT and self._tesseract_available:
            result = self._extract_tesseract(image_path)
        elif not self.config.offline_only:
            result = self._extract_cloud(image_path)
        else:
            logger.warning("所有OCR引擎不可用")
            return OCRResult(text="[OCR不可用]", engine_used="none")

        result.processing_ms = (time.time() - start) * 1000
        return result

    def extract_text_bytes(self, image_bytes: bytes, ext: str = "png") -> OCRResult:
        """提取图片字节数据中的文字"""
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name
        try:
            return self.extract_text(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def extract_text_base64(self, b64_data: str) -> OCRResult:
        """从Base64图片提取文字"""
        raw = base64.b64decode(b64_data)
        return self.extract_text_bytes(raw)

    def is_available(self) -> bool:
        return self._paddle_available or self._tesseract_available or not self.config.offline_only

    # ── 内部实现 ──────────────────────────────────

    def _check_paddle(self) -> bool:
        try:
            from paddleocr import PaddleOCR

            return True
        except ImportError:
            logger.debug("PaddleOCR 未安装（pip install paddleocr）")
            return False

    def _check_tesseract(self) -> bool:
        try:
            result = subprocess.run(
                ["tesseract", "--version"], capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("tesseract 未安装")
            return False

    def _extract_paddle(self, image_path: str) -> OCRResult:
        """使用 PaddleOCR 提取"""
        try:
            from paddleocr import PaddleOCR

            ocr = PaddleOCR(lang=self.config.language, use_angle_cls=True)
            results = ocr.ocr(image_path)

            blocks = []
            full_text = []
            if results and results[0]:
                for line in results[0]:
                    bbox_points = line[0]
                    text, confidence = line[1]
                    # 计算边界框
                    x = min(p[0] for p in bbox_points)
                    y = min(p[1] for p in bbox_points)
                    w = max(p[0] for p in bbox_points) - x
                    h = max(p[1] for p in bbox_points) - y
                    blocks.append(
                        OCRBlock(
                            text=text,
                            confidence=confidence,
                            bbox=(int(x), int(y), int(w), int(h)),
                        )
                    )
                    full_text.append(text)

            return OCRResult(
                text="\n".join(full_text),
                blocks=blocks,
                engine_used="paddle_ocr",
            )
        except Exception as e:
            logger.error(f"PaddleOCR失败: {e}")
            return OCRResult(text=f"[PaddleOCR错误: {e}]", engine_used="paddle_ocr")

    def _extract_tesseract(self, image_path: str) -> OCRResult:
        """使用 Tesseract 提取"""
        lang = (
            "chi_sim+eng"
            if self.config.language == "ch_en"
            else "chi_sim"
            if self.config.language == "ch"
            else "eng"
        )
        cmd = ["tesseract", image_path, "stdout", "-l", lang]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            text = result.stdout.strip()
            return OCRResult(text=text, engine_used="tesseract")
        except subprocess.TimeoutExpired:
            return OCRResult(text="[tesseract超时]", engine_used="tesseract")
        except Exception as e:
            return OCRResult(text=f"[tesseract错误: {e}]", engine_used="tesseract")

    def _extract_cloud(self, image_path: str) -> OCRResult:
        logger.info("使用云端OCR备选")
        return OCRResult(text="[云端OCR·需配置API密钥]", engine_used="cloud_fallback")


# ── 便捷工厂 ──────────────────────────────────


def create_ocr_provider(offline_only: bool = True) -> OCRProvider:
    """创建OCR Provider（开源线·默认离线）"""
    return OCRProvider(
        OCRConfig(
            engine=OCREngine.PADDLE_OCR,
            offline_only=offline_only,
        )
    )
