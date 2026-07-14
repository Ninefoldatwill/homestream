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
  - FunASR 走**生产 funasr-runtime websocket**(:10096, 与 VoiceBridge 主 STT 同源),
    不依赖 python funasr 包(该 conda 环境加载会 segfault)。:10096 须已启动。
  - faster-whisper 需单独安装: pip install faster-whisper; 权重走 HF_ENDPOINT 镜像。
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


def prep_audio_16k_mono(wav: str) -> str:
    """规整为 16k 单声道 wav, 保证两个引擎喂的是完全一致的信号。"""
    import numpy as np
    import soundfile as sf
    import librosa

    data, sr = sf.read(wav)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float32)
    if sr != 16000:
        data = librosa.resample(data, orig_sr=sr, target_sr=16000)
    tmp = os.path.join(_PROJECT_ROOT, "voice", "_bench_16k.wav")
    sf.write(tmp, data, 16000)
    return tmp


def run_funasr(wav: str) -> str | None:
    """走生产 funasr-runtime (:10096 websocket), 与 VoiceBridge 主 STT 同源。
    不用 python funasr 包(该 conda 环境加载会 segfault), 直接打已在跑的 runtime。"""
    try:
        import asyncio
        import json
        import re as _re
        import websockets as _ws
        import numpy as np
        import soundfile as sf
    except Exception as e:
        print(f"[FunASR] websockets/soundfile 不可用, 跳过 ({e})")
        return None
    try:
        data, sr = sf.read(wav)
        if data.ndim > 1:
            data = data.mean(axis=1)
        pcm = (np.clip(data.astype(np.float32), -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    except Exception as e:
        print(f"[FunASR] 读音频失败: {e}")
        return None

    uri = os.getenv("FUNASR_WS_URI", "ws://127.0.0.1:10096")

    async def _run():
        text = ""
        async with _ws.connect(uri, open_timeout=15, close_timeout=15) as ws:
            await ws.send(json.dumps({
                "mode": "2pass", "chunk_size": [5, 10, 5],
                "wav_name": "bench", "is_speaking": True, "itn": True,
            }))
            step = 3200  # ~0.2s / 块
            for i in range(0, len(pcm), step):
                await ws.send(pcm[i:i + step])
            await ws.send(json.dumps({"is_speaking": False}))
            async for msg in ws:
                if isinstance(msg, bytes):
                    continue
                try:
                    d = json.loads(msg)
                except Exception:
                    continue
                if d.get("mode") != "2pass-offline":
                    continue
                t = d.get("text", "")
                if not t:
                    continue
                text = _re.sub(r"<\|[^|]+\|>", "", t).strip()
                break  # 拿到最终稿即止
        return text

    try:
        text = asyncio.run(_run())
        return text if text else None
    except Exception as e:
        print(f"[FunASR] websocket 识别失败: {e}")
        return None


def run_faster_whisper(wav: str, model_size: str = "large-v2") -> str | None:
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        print(f"[faster-whisper] 未安装(需 pip install faster-whisper), 跳过 ({e})")
        return None
    try:
        # 强制 CPU + int8: 与生产 CosyVoice2 GPU 服务隔离, 避免抢显存 OOM;
        # 同时让两个引擎在同一硬件下比「纯模型能力」, 对比更纯净。
        # cpu_threads=4 + 外部 OMP_NUM_THREADS=4: 压低 MKL 每线程缓冲峰值,
        # 规避本机空闲内存(约 3.5GB)不足导致的 mkl_malloc 失败。
        model = WhisperModel(model_size, device="cpu", compute_type="int8",
                             cpu_threads=4, num_workers=1)
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

    # 规整为 16k 单声道, 两个引擎喂同一信号
    wav16 = prep_audio_16k_mono(SAMPLE_WAV)
    print(f"规整音频(16k单声道): {wav16}")

    results = {}

    # 1) FunASR
    fa = run_funasr(wav16)
    if fa is not None:
        cer_fa = compute_cer(ref_norm, norm(fa))
        results["funasr_paraformer_zh"] = {"raw": fa, "cer": round(cer_fa, 4)}
        print(f"\n[FunASR] 识别: {fa}")
        print(f"[FunASR] CER = {cer_fa:.4f}")

    # 2) faster-whisper (优先 large-v2 最佳态; 本机空闲内存不足时自动降级 small)
    fw = run_faster_whisper(wav16, "large-v2")
    fw_model = "large-v2"
    if fw is None:
        print("\n[提示] large-v2 因内存不足失败, 自动降级 faster-whisper small 作为代表")
        fw = run_faster_whisper(wav16, "small")
        fw_model = "small"
    if fw is not None:
        cer_fw = compute_cer(ref_norm, norm(fw))
        results[f"faster_whisper_{fw_model}"] = {"raw": fw, "cer": round(cer_fw, 4)}
        print(f"\n[faster-whisper/{fw_model}] 识别: {fw}")
        print(f"[faster-whisper/{fw_model}] CER = {cer_fw:.4f}")

    # 结论
    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)
    fa_key = "funasr_paraformer_zh"
    fw_key = f"faster_whisper_{fw_model}"
    if fa_key in results and fw_key in results:
        if results[fa_key]["cer"] <= results[fw_key]["cer"]:
            print(f"实测: FunASR CER({results[fa_key]['cer']:.4f}) <= faster-whisper/{fw_model} CER({results[fw_key]['cer']:.4f})")
            print("        -> 主 STT 维持 FunASR(本地, 中文专精)")
            print("        -> faster-whisper 定位: 英文/跨语种维度补强 + 无 Docker 本地兜底候选")
        else:
            print(f"实测: faster-whisper/{fw_model} CER 更低 -> 需重新评估(异常, 复查样本)")
    elif fa_key in results:
        print("仅 FunASR 可用(本环境): 主 STT 维持 FunASR")
    elif fw_key in results:
        print("仅 faster-whisper 可用: 可作无 Docker 本地兜底候选")
    else:
        print("两项均不可用, 请检查依赖安装")

    out = os.path.join(_PROJECT_ROOT, "voice", "bench_stt_cn_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"reference": REFERENCE_TEXT, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写入: {out}")


if __name__ == "__main__":
    main()
