"""
CosyVoice2 独立 TTS 微服务 — 运行于 Conda 3.10 环境。

为什么是独立服务?
  CosyVoice2 依赖 pynini / Matcha-TTS / kaldifst 等大量原生库, 且官方要求
  Python 3.10 + Conda, 无法塞进 HomeStream Worker 当前的 3.13 venv。
  因此把它拆成独立微服务: Conda 3.10 跑本服务加载 CosyVoice2,
  Worker (3.13 venv) 通过 HTTP 调用 (见 voice/tts_adapter.py 的 CosyVoiceClient)。

依赖 (Conda 3.10 环境内):
  torch (CUDA 12.x) / cosyvoice / numpy / (stdlib http.server 无需额外 web 框架)

接口:
  GET  /health     -> {"status":"ok","loaded":bool,"gpu":str}
  GET  /voices     -> [{"name":..., "description":...}, ...]
  POST /synthesize -> JSON {"text":..., "voice":..., "speed":1.0}
                      -> audio/wav (24000Hz, 16bit, mono)

启动 (在 cosyvoice Conda 环境中):
  python voice/cosyvoice_service.py \
      --host 127.0.0.1 --port 50000 \
      --model_dir E:/.../pretrained_models/CosyVoice2-0.5B
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import os
import sys

import numpy as np

# --- 路径自动发现: 把 CosyVoice 仓库根目录加入 sys.path, 使 `import cosyvoice` 可用 ---
# CosyVoice 官方仓库根目录没有 setup.py, 无法 pip 安装, 只能用 PYTHONPATH 引入。
# 这里按本文件位置自动推断 (voice/cosyvoice_service.py -> 项目根 -> CosyVoice/)。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COSYVOICE_REPO = os.getenv("COSYVOICE_REPO", os.path.join(_PROJECT_ROOT, "CosyVoice"))
if os.path.isdir(COSYVOICE_REPO) and COSYVOICE_REPO not in sys.path:
    sys.path.insert(0, COSYVOICE_REPO)

# CosyVoice 依赖 Matcha-TTS (`import matcha`), 但 Matcha-TTS 含一个需 C 编译器编译的
# Cython 扩展 (monotonic_align.core), 本机无 MSVC 时 `pip install -e` 会失败。
# 已用纯 Python 桩 core.py 替代该扩展 (仅训练用, 推理不调用), 这里把 Matcha-TTS 目录
# 直接加入 sys.path 即可 import matcha, 无需编译/安装。
MATCHA_REPO = os.path.join(COSYVOICE_REPO, "third_party", "Matcha-TTS")
if os.path.isdir(MATCHA_REPO) and MATCHA_REPO not in sys.path:
    sys.path.insert(0, MATCHA_REPO)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [cosyvoice-svc] %(levelname)s: %(message)s")
logger = logging.getLogger("cosyvoice.svc")

# CosyVoice2 内置音色 (与 tts_adapter.py 保持一致)
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

SAMPLE_RATE = 24000


class CosyVoiceModel:
    """CosyVoice2 模型封装 (懒加载)"""

    def __init__(self, model_dir: str):
        self._model_dir = model_dir
        self._model = None
        self._loaded = False
        self._gpu = "n/a"
        self._sample_rate = SAMPLE_RATE
        # zero_shot 兜底提示音 (CosyVoice 仓库自带, 无需 spk2info.pt)
        # 提示文本取自官方 example.py 对 zero_shot_prompt.wav 的转录
        self._prompt_text = "希望你以后能够做的比我还好呦。"
        self._prompt_wav = os.path.join(COSYVOICE_REPO, "asset", "zero_shot_prompt.wav")

    def load(self):
        if self._loaded:
            return
        logger.info("加载 CosyVoice2 模型: %s", self._model_dir)
        try:
            import torch
            from cosyvoice.cli.cosyvoice import CosyVoice2

            if torch.cuda.is_available():
                self._gpu = torch.cuda.get_device_name(0)
                logger.info("CUDA 可用: %s", self._gpu)
            else:
                self._gpu = "CPU"
                logger.warning("CUDA 不可用, 将使用 CPU (极慢, 仅作兜底)")
            # CosyVoice2 真实签名: (model_dir, load_jit, load_trt, load_vllm, fp16, trt_concurrent)
            # 注意: 没有 use_flow_cache 参数 (那是旧版 CosyVoice 的), 传了会 TypeError。
            # fp16=False 兼容性最好; RTX 4050 上 0.5B 模型 fp16=False 也足够快。
            self._model = CosyVoice2(self._model_dir, fp16=False)
            self._sample_rate = getattr(self._model, "sample_rate", SAMPLE_RATE)
            self._loaded = True
            logger.info("CosyVoice2 加载完成 (sample_rate=%d)", self._sample_rate)
        except Exception as e:  # noqa: BLE001
            logger.error("CosyVoice2 加载失败: %s", e)
            raise

    def synthesize(self, text: str, voice: str = "longxiaochun", speed: float = 1.0) -> np.ndarray:
        """整句合成, 返回 float32 [-1,1] 单声道音频。

        模式策略:
          - 若模型含 spk2info (SFT 固定音色可用) 且指定 voice 在其中 -> inference_sft
          - 否则 (CosyVoice2-0.5B 官方发布不含 spk2info.pt) -> inference_zero_shot
            用 CosyVoice 自带的 zero_shot_prompt.wav 做音色克隆, 无需 spk2info.pt
        """
        self.load()
        if not text.strip():
            return np.zeros(0, dtype=np.float32)
        # 探测可用 SFT 音色
        try:
            spks = self._model.list_available_spks()
        except Exception:
            spks = []
        use_sft = bool(spks) and voice in spks
        if use_sft:
            gen = self._model.inference_sft(tts_text=text, spk_id=voice, stream=False, speed=speed)
            mode = "sft:" + voice
        else:
            gen = self._model.inference_zero_shot(
                tts_text=text, prompt_text=self._prompt_text, prompt_wav=self._prompt_wav,
                stream=False, speed=speed,
            )
            mode = "zero_shot"
        logger.info("合成模式: %s (voice=%s)", mode, voice)
        # text_normalize(split=True) 会把长文本拆成多句, 每句 yield 一个 chunk;
        # 必须拼接所有 chunk 的 tts_speech, 否则多句回复只剩最后一句音频。
        chunks = []
        for chunk in gen:
            t = chunk.get("tts_speech", chunk)
            if hasattr(t, "squeeze"):
                t = t.squeeze().cpu().numpy()
            t = np.asarray(t, dtype=np.float32)
            chunks.append(t)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks, axis=-1)

    @property
    def loaded(self):
        return self._loaded

    @property
    def gpu(self):
        return self._gpu

    @property
    def sample_rate(self):
        return self._sample_rate


def pcm_float32_to_wav_bytes(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """float32 [-1,1] -> 16bit PCM WAV bytes"""
    pcm = np.asarray(pcm, dtype=np.float32)
    pcm_int16 = np.clip(pcm, -1.0, 1.0) * 32767.0
    pcm_int16 = pcm_int16.astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_int16.tobytes())
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    model: CosyVoiceModel = None  # 类级共享实例 (由 main 注入)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _wav(self, wav_bytes: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav_bytes)))
        self.end_headers()
        self.wfile.write(wav_bytes)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json({"status": "ok", "loaded": Handler.model.loaded, "gpu": Handler.model.gpu})
        elif parsed.path == "/voices":
            self._json({"voices": COSYVOICE_VOICES})
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/synthesize":
            self._json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"bad request: {e}"}, status=400)
            return

        text = (payload.get("text") or "").strip()
        voice = payload.get("voice") or "longxiaochun"
        try:
            speed = float(payload.get("speed") or 1.0)
        except (TypeError, ValueError):
            speed = 1.0

        if not text:
            self._json({"error": "empty text"}, status=400)
            return

        try:
            t0 = time.monotonic()
            pcm = Handler.model.synthesize(text, voice, speed)
            wav = pcm_float32_to_wav_bytes(pcm, Handler.model.sample_rate)
            dt = time.monotonic() - t0
            logger.info("合成完成: voice=%s len=%d chars, 音频 %.2fs, 耗时 %.2fs", voice, len(text), len(pcm) / Handler.model.sample_rate, dt)
            self._wav(wav)
        except Exception as e:  # noqa: BLE001
            logger.error("合成失败: %s", e)
            self._json({"error": str(e)}, status=500)

    def log_message(self, fmt, *args):  # 静默默认访问日志
        return


def main():
    parser = argparse.ArgumentParser(description="CosyVoice2 TTS 微服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50000)
    parser.add_argument("--model_dir", default="pretrained_models/CosyVoice2-0.5B")
    args = parser.parse_args()

    Handler.model = CosyVoiceModel(args.model_dir)
    # 预热: 首次请求再加载, 这里仅打印提示
    logger.info("CosyVoice2 微服务就绪: http://%s:%d  (模型目录 %s, 首次合成时加载)", args.host, args.port, args.model_dir)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到中断, 关闭服务")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
