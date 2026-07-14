#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STT 中文对比小测 —— 融优处理验证脚本
=====================================
目的: 用同一段已知文本的中文音频, 对比 FunASR(本地主 STT) 与 faster-whisper(维度补强候选) 的识别准确率(CER)。

方法论(九重「维度补短板·融优」):
  1. 调研 -> 已有文献结论(CER 量级)
  2. 对比 -> 本脚本在本地真实跑一遍, 取实测 CER
  3. 确认 -> 主 STT 仍 FunASR; faster-whisper 定位「英文/跨语种维度补强 + 无 Docker 本地兜底候选」
  4. 记录 -> 输出结果, 回填 docs/语音STT对比-融优评估.md

样本: CosyVoice 仓库自带 asset/zero_shot_prompt.wav
      参考文本(官方 example.py): "希望你以后能够做的比我还好呦。"
      -> 该音频是真实中文语音, 文本已知, 适合做受控 CER 比对。

用法(在 conda 环境 E:\\conda_envs\\cosyvoice 中运行, 需先装 faster-whisper):
  E:\\conda_envs\\cosyvoice\\python.exe voice/bench_stt_cn.py

注意:
  - FunASR 走本地 funasr python 包(若当前环境未装则跳过该项, 仅测 faster-whisper)
  - faster-whisper 需单独安装: pip install faster-whisper
  - 本脚本仅做离线评估, 不复制任何外部源码(融优=借鉴范式, 不复制实现)
"""
import os
import sys
import json

# ---- 路径 ----
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COSYVOICE_REPO = os.getenv("COSYVOICE_REPO", os.path.join(_PROJECT_ROOT, "CosyVoice"))
SAMPLE_WAV = os.path.join(COSYVOICE_REPO, "asset", "zero_shot_prompt.wav")
REFERENCE_TEXT = "希望你以后能够做的比我还好呦。"


def compute_cer(ref: str, hyp: str) -> float:
    """字符错误率 (Chinese: 按字), 经典编辑距离实现。"""
    ref_chars = list(ref)
    hyp_chars = list(hyp)
    # 动态规划编辑距离
    import numpy as np
    d = np.zeros((len(ref_chars) + 1, len(hyp_chars) + 1), dtype=int)
    for i in range(len(ref_chars) + 1):
        d[i][0] = i
    for j in range(len(hyp_chars) + 1):
        d[0][j] = j
    for i in range(1, len(ref_chars) + 1):
        for j in range(1, len(hyp_chars) + 1):
            cost = 0 if ref_chars[i - 1] == hyp_chars[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
    dist = d[len(ref_chars)][len(hyp_chars)]
    return dist / max(1, len(ref_chars))


def norm(text: str) -> str:
    import re
    # 去标点/空格/英文大小写统一, 保留中文与字母数字
    text = text.replace("。", "").replace("，", "").replace(",", "")
    text = text.replace("！", "").replace("!", "").replace("？", "").replace("?", "")
    text = re.sub(r"\s+", "", text)
    return text.strip()


def run_funasr(wav: str) -> str | None:
    try:
        from funasr import AutoModel
    except Exception as e:
        print(f"[FunASR] 当前环境未安装 funasr 包, 跳过 ({e})")
        return None
    try:
        model = AutoModel(
            model="paraformer-zh",
            model_revision="v2.0.4",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            disable_update=True,
        )
        res = model.generate(input=wav, batch_size=1)
        if res and "text" in res[0]:
            return res[0]["text"]
    except Exception as e:
        print(f"[FunASR] 推理失败: {e}")
        return None
    return None


def run_faster_whisper(wav: str, model_size: str = "large-v2") -> str | None:
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        print(f"[faster-whisper] 未安装(需 pip install faster-whisper), 跳过 ({e})")
        return None
    try:
        # 本地 CPU 推理(无 GPU 也可跑, 仅慢一点); 有 CUDA 会自动用
        model = WhisperModel(model_size, device="auto", compute_type="default")
        segments, _ = model.transcribe(wav, language="zh", beam_size=5)
        return "".join(seg.text for seg in segments)
    except Exception as e:
        print(f"[faster-whisper] 推理失败: {e}")
        return None


def main():
    print("=" * 60)
    print("STT 中文对比小测 (融优处理验证)")
    print("=" * 60)
    if not os.path.isfile(SAMPLE_WAV):
        print(f"[错误] 样本音频不存在: {SAMPLE_WAV}")
        sys.exit(1)
    print(f"样本: {SAMPLE_WAV}")
    print(f"参考文本: {REFERENCE_TEXT}")
    ref_norm = norm(REFERENCE_TEXT)

    results = {}

    # 1) FunASR
    fa = run_funasr(SAMPLE_WAV)
    if fa is not None:
        cer_fa = compute_cer(ref_norm, norm(fa))
        results["funasr_paraformer_zh"] = {"raw": fa, "cer": round(cer_fa, 4)}
        print(f"\n[FunASR] 识别: {fa}")
        print(f"[FunASR] CER = {cer_fa:.4f}")

    # 2) faster-whisper
    fw = run_faster_whisper(SAMPLE_WAV, "large-v2")
    if fw is not None:
        cer_fw = compute_cer(ref_norm, norm(fw))
        results["faster_whisper_large_v2"] = {"raw": fw, "cer": round(cer_fw, 4)}
        print(f"\n[faster-whisper] 识别: {fw}")
        print(f"[faster-whisper] CER = {cer_fw:.4f}")

    # 结论
    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)
    if "funasr_paraformer_zh" in results and "faster_whisper_large_v2" in results:
        if results["funasr_paraformer_zh"]["cer"] <= results["faster_whisper_large_v2"]["cer"]:
            print("实测: FunASR CER 更低 -> 主 STT 维持 FunASR(本地, 中文专精)")
            print("        faster-whisper 定位: 英文/跨语种维度补强 + 无 Docker 本地兜底候选")
        else:
            print("实测: faster-whisper CER 更低 -> 需重新评估 STT 选型(异常, 复查样本)")
    elif "funasr_paraformer_zh" in results:
        print("仅 FunASR 可用(本环境): 主 STT 维持 FunASR")
    elif "faster_whisper_large_v2" in results:
        print("仅 faster-whisper 可用: 可作无 Docker 本地兜底候选")
    else:
        print("两项均不可用, 请检查依赖安装")

    out = os.path.join(_PROJECT_ROOT, "voice", "bench_stt_cn_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"reference": REFERENCE_TEXT, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入: {out}")


if __name__ == "__main__":
    main()
