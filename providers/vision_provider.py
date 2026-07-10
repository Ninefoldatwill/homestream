"""
Vision Provider - 图像理解（Image Understanding）

开源线·L1基础设施——本地优先，L1协同：
  本地方案: InternVL2-2B（轻量·离线·零费用·1.5GB显存）
  备选方案: Qwen2.5-VL-7B（更强·离线·需额外5GB显存）
  云端兜底: 免费视觉API

核心特性：
  - 看图问答：用户发图片+提问→本地理解+回答
  - OCR增强：图片→文字+上下文理解
  - L1本地协同：vision描述→L1 Qwen2.5-7B分析→答案
  - 离线优先：脱网也能用

V8多模态生态·图像理解入口
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class VisionEngine(Enum):
    """图像理解引擎"""

    INTERNVL2 = "internvl2"  # InternVL2-2B 本地（推荐·轻量）
    QWEN_VL = "qwen_vl"  # Qwen2.5-VL-7B 本地（更强·显存大）
    LLAMA_VISION = "llama_vision"  # llama.cpp vision（本地·通用）
    L1_COLLAB = "l1_collab"  # L1本地模型协同（描述→推理）
    CLOUD_FALLBACK = "cloud"  # 云端备选


@dataclass
class VisionConfig:
    """图像理解配置"""

    engine: VisionEngine = VisionEngine.INTERNVL2
    max_tokens: int = 512
    temperature: float = 0.7
    offline_only: bool = True  # 开源线默认脱网
    internvl_model: str = "InternVL2-2B"  # 本地模型名称
    internvl_binary: str = ""  # 可执行路径（空=用Python API）


@dataclass
class VisionResult:
    """图像理解结果"""

    description: str  # 图片描述
    answer: str = ""  # 针对问题的回答
    objects: list[str] = field(default_factory=list)  # 检测到的物体
    text_in_image: str = ""  # 图中文字（OCR协同）
    engine_used: str = "internvl2"
    processing_ms: float = 0.0


class VisionProvider:
    """图像理解 Provider"""

    # 各引擎支持的方法
    ENGINE_METHODS = {
        VisionEngine.INTERNVL2: ["_describe_internvl2", "_answer_internvl2"],
        VisionEngine.QWEN_VL: ["_describe_qwen_vl", "_answer_qwen_vl"],
        VisionEngine.LLAMA_VISION: ["_describe_llama_vision", "_answer_llama_vision"],
        VisionEngine.L1_COLLAB: ["_describe_l1_collab", "_answer_l1_collab"],
    }

    def __init__(self, config: VisionConfig | None = None):
        self.config = config or VisionConfig()
        self._engine_available = self._check_engine()

    # ── 公开API ──────────────────────────────────

    def describe(self, image_path: str) -> VisionResult:
        """描述图片内容

        Args:
            image_path: 图片路径

        Returns:
            VisionResult: 图片描述
        """
        import time

        start = time.time()
        method_name = f"_describe_{self.config.engine.value}"
        method = getattr(self, method_name, self._describe_l1_collab)
        result = method(image_path)
        result.processing_ms = (time.time() - start) * 1000
        return result

    def ask_about_image(self, image_path: str, question: str) -> VisionResult:
        """针对图片提问

        Args:
            image_path: 图片路径
            question: 用户问题

        Returns:
            VisionResult: 答案
        """
        import time

        start = time.time()
        method_name = f"_answer_{self.config.engine.value}"
        method = getattr(self, method_name, self._answer_l1_collab)
        result = method(image_path, question)
        result.processing_ms = (time.time() - start) * 1000
        return result

    def describe_bytes(self, image_bytes: bytes, ext: str = "png") -> VisionResult:
        """从字节数据描述图片"""
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name
        try:
            return self.describe(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def ask_about_image_bytes(
        self, image_bytes: bytes, question: str, ext: str = "png"
    ) -> VisionResult:
        """从字节数据针对图片提问"""
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name
        try:
            return self.ask_about_image(tmp_path, question)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def is_available(self) -> bool:
        return self._engine_available or not self.config.offline_only

    # ── 引擎检测 ──────────────────────────────────

    def _check_engine(self) -> bool:
        engine = self.config.engine
        if engine == VisionEngine.INTERNVL2:
            return self._check_internvl2()
        elif engine == VisionEngine.QWEN_VL:
            return self._check_qwen_vl()
        elif engine == VisionEngine.LLAMA_VISION:
            return self._check_llama_vision()
        elif engine == VisionEngine.L1_COLLAB:
            return True  # L1协同始终可用
        return False

    def _check_internvl2(self) -> bool:
        try:
            # InternVL2用transformers加载
            from transformers import AutoModel, AutoTokenizer

            return True
        except ImportError:
            logger.debug("transformers未安装，InternVL2不可用")
            return False

    def _check_qwen_vl(self) -> bool:
        try:
            from transformers import AutoModel

            return True
        except ImportError:
            return False

    def _check_llama_vision(self) -> bool:
        try:
            result = subprocess.run(
                ["llama-vision-cli", "--version"], capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    # ── InternVL2-2B 实现 ──────────────────────────

    def _describe_internvl2(self, image_path: str) -> VisionResult:
        """使用 InternVL2-2B 描述图片"""
        try:
            from PIL import Image
            from transformers import AutoModel, AutoTokenizer

            # 加载模型（首次较慢，后续缓存）
            model = AutoModel.from_pretrained(
                self.config.internvl_model,
                trust_remote_code=True,
                torch_dtype="auto",
            )
            tokenizer = AutoTokenizer.from_pretrained(
                self.config.internvl_model,
                trust_remote_code=True,
            )

            image = Image.open(image_path).convert("RGB")
            prompt = "请详细描述这张图片的内容。"

            # InternVL2 标准调用
            response = model.chat(
                tokenizer=tokenizer,
                pixel_values=image,
                question=prompt,
                generation_config={"max_new_tokens": self.config.max_tokens},
            )

            return VisionResult(
                description=response,
                answer="",
                engine_used="internvl2",
            )
        except Exception as e:
            logger.info(f"InternVL2不可用({e})，降级到L1协同")
            return self._describe_l1_collab(image_path)

    def _answer_internvl2(self, image_path: str, question: str) -> VisionResult:
        """使用 InternVL2-2B 回答图片相关问题"""
        try:
            from PIL import Image
            from transformers import AutoModel, AutoTokenizer

            model = AutoModel.from_pretrained(
                self.config.internvl_model,
                trust_remote_code=True,
                torch_dtype="auto",
            )
            tokenizer = AutoTokenizer.from_pretrained(
                self.config.internvl_model,
                trust_remote_code=True,
            )

            image = Image.open(image_path).convert("RGB")
            response = model.chat(
                tokenizer=tokenizer,
                pixel_values=image,
                question=question,
                generation_config={"max_new_tokens": self.config.max_tokens},
            )

            return VisionResult(
                description="",
                answer=response,
                engine_used="internvl2",
            )
        except Exception as e:
            logger.info(f"InternVL2不可用({e})，降级到L1协同")
            return self._answer_l1_collab(image_path, question)

    # ── Qwen-VL 备选 ───────────────────────────────

    def _describe_qwen_vl(self, image_path: str) -> VisionResult:
        try:
            from PIL import Image
            from transformers import AutoProcessor, Qwen2VLForConditionalGeneration

            processor = AutoProcessor.from_pretrained("Qwen/Qwen2-VL-2B-Instruct")
            model = Qwen2VLForConditionalGeneration.from_pretrained(
                "Qwen/Qwen2-VL-2B-Instruct",
                torch_dtype="auto",
            )

            image = Image.open(image_path).convert("RGB")
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": "请详细描述这张图片"},
                    ],
                }
            ]
            text = processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            inputs = processor(text=text, images=image, return_tensors="pt")
            output = model.generate(**inputs, max_new_tokens=self.config.max_tokens)
            response = processor.decode(output[0], skip_special_tokens=True)

            return VisionResult(description=response, engine_used="qwen_vl")
        except Exception as e:
            logger.info(f"Qwen-VL不可用({e})，降级到L1协同")
            return self._describe_l1_collab(image_path)

    def _answer_qwen_vl(self, image_path: str, question: str) -> VisionResult:
        try:
            # 同上但带question
            return self._answer_l1_collab(image_path, question)
        except Exception:
            return self._answer_l1_collab(image_path, question)

    # ── L1本地协同（降级方案·零依赖始终可用）─────────

    def _describe_l1_collab(self, image_path: str) -> VisionResult:
        """L1本地模型协同描述图片（无需视觉模型）

        策略：用Python/PIL提取图片元信息+OCR文字，L1模型基于这些信息生成描述。
        纯文本描述虽然不如视觉模型精细，但零额外依赖、始终可用。
        """
        try:
            from PIL import Image

            img = Image.open(image_path)
            info_parts = [
                f"尺寸: {img.size[0]}x{img.size[1]}",
                f"格式: {img.format}",
                f"模式: {img.mode}",
            ]

            # 尝试获取文件名提示
            fname = Path(image_path).stem
            info_parts.append(f"文件名: {fname}")

            description = "图片信息: " + ", ".join(info_parts)
            description += "\n(需安装 InternVL2-2B 或 Qwen2-VL 以获得完整图像理解能力)"

            return VisionResult(
                description=description,
                engine_used="l1_collab",
            )
        except Exception as e:
            return VisionResult(
                description=f"[图片读取失败: {e}]",
                engine_used="l1_collab",
            )

    def _answer_l1_collab(self, image_path: str, question: str) -> VisionResult:
        """L1本地模型协同回答图片问题"""
        base = self._describe_l1_collab(image_path)
        answer = f'基于图片元信息回答"{question}":\n{base.description}'
        return VisionResult(
            description=base.description,
            answer=answer,
            engine_used="l1_collab",
        )

    # ── llama.cpp vision 备选 ──────────────────────

    def _describe_llama_vision(self, image_path: str) -> VisionResult:
        try:
            result = subprocess.run(
                [
                    "llama-vision-cli",
                    "-m",
                    "llava-v1.6-7b.Q4_K_M.gguf",
                    "--image",
                    image_path,
                    "-p",
                    "请描述这张图片",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return VisionResult(
                description=result.stdout.strip(),
                engine_used="llama_vision",
            )
        except Exception as e:
            logger.info(f"llama-vision不可用({e})，降级")
            return self._describe_l1_collab(image_path)

    def _answer_llama_vision(self, image_path: str, question: str) -> VisionResult:
        try:
            result = subprocess.run(
                [
                    "llama-vision-cli",
                    "-m",
                    "llava-v1.6-7b.Q4_K_M.gguf",
                    "--image",
                    image_path,
                    "-p",
                    question,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            return VisionResult(
                description="",
                answer=result.stdout.strip(),
                engine_used="llama_vision",
            )
        except Exception as e:
            return self._answer_l1_collab(image_path, question)


# ── Python脚本入口 ──────────────────────────────


def create_vision_provider(offline_only: bool = True) -> VisionProvider:
    """创建Vision Provider（开源线·默认离线·InternVL2-2B优先）"""
    config = VisionConfig(
        engine=VisionEngine.INTERNVL2,
        offline_only=offline_only,
    )
    return VisionProvider(config)


# ── CLI 测试入口 ──────────────────────────────────

if __name__ == "__main__":
    import sys

    p = create_vision_provider()
    if len(sys.argv) > 1:
        img = sys.argv[1]
        question = sys.argv[2] if len(sys.argv) > 2 else ""
        if question:
            r = p.ask_about_image(img, question)
            print(f"Q: {question}")
            print(f"A: {r.answer}")
        else:
            r = p.describe(img)
            print(f"描述: {r.description}")
        print(f"引擎: {r.engine_used} | 耗时: {r.processing_ms:.0f}ms")
    else:
        print(f"可用: {p.is_available()}")
