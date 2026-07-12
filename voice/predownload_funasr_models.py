"""
FunASR 2-pass 模型预下载脚本

容器内从 ModelScope 下载模型常因网络问题失败, 改为 Windows 本机预下载。
下载到 ./funasr-models/ 目录, 与 docker-compose.yml 的 volumes 挂载对应。

使用: python predownload_funasr_models.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 模型下载到 voice/funasr-models/ 目录
MODELS_DIR = Path(__file__).parent / "funasr-models"
MODELS_DIR.mkdir(exist_ok=True)

# FunASR 2-pass 需要的模型列表
MODELS = [
    # VAD
    "damo/speech_fsmn_vad_zh-cn-16k-common-onnx",
    # Pass 1: 流式 Paraformer
    "damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx",
    # Pass 2: 离线 Paraformer (替代 SenseVoice, 通用性更好)
    "damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx",
    # 标点
    "damo/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727-onnx",
    # 可选: SenseVoice (中文+情感+事件)
    "iic/SenseVoiceSmall",
]


def download_all():
    """下载所有模型到 MODELS_DIR"""
    from modelscope import snapshot_download

    print(f"Models directory: {MODELS_DIR}")
    print(f"Total models to download: {len(MODELS)}")
    print("=" * 60)

    for i, model_id in enumerate(MODELS, 1):
        print(f"\n[{i}/{len(MODELS)}] Downloading: {model_id}")
        print("-" * 60)
        try:
            local_path = snapshot_download(
                model_id=model_id,
                cache_dir=str(MODELS_DIR),
            )
            print(f"OK Saved to: {local_path}")
        except Exception as e:
            print(f"FAILED: {e}")
            print("(可选模型可忽略, 核心模型必须成功)")
            if "fsmn-vad" in model_id or "paraformer" in model_id or "punc" in model_id:
                print("CRITICAL: 核心模型下载失败, 请重试")
                sys.exit(1)

    print("\n" + "=" * 60)
    print("All critical models downloaded!")
    print(f"Models directory: {MODELS_DIR}")


if __name__ == "__main__":
    download_all()
