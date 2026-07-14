"""
CosyVoice2 微服务端到端测试 — 独立于 cosyvoice 依赖, 用 stdlib 即可。

用法 (在任意 Python 3 环境, 不需要 Conda/cosyvoice):
  python voice/test_cosyvoice_service.py

它会:
  1. 用 Conda 的 python 启动 voice/cosyvoice_service.py (子进程)
  2. 轮询 /health 直到服务就绪 (模型首次合成时才加载)
  3. POST /synthesize 一句中文, 验证返回 WAV 且音频非空
  4. 保存为 /tmp/cosyvoice_test.wav 供人工试听
  5. 清理子进程
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ---------- 路径 (按本文件位置推断) ----------
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
CONDA_PY = r"E:\conda_envs\cosyvoice\python.exe"
SERVICE_PY = _HERE / "cosyvoice_service.py"
MODEL_DIR = _PROJECT_ROOT / "pretrained_models" / "CosyVoice2-0.5B"
HOST, PORT = "127.0.0.1", 50000
BASE = f"http://{HOST}:{PORT}"

# 首次模型加载在 GPU 上可能较慢; 合成本身也给宽裕超时
HEALTH_TIMEOUT = 30
SYNTH_TIMEOUT = 600


def _http_json(path: str, data: dict | None = None, timeout: float = 10.0):
    url = BASE + path
    if data is not None:
        req = urllib.request.Request(
            url, data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
    else:
        req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        body = resp.read()
        if "application/json" in ctype:
            return "json", json.loads(body.decode("utf-8"))
        return "bytes", body


def main() -> int:
    if not CONDA_PY or not Path(CONDA_PY).exists():
        print(f"[FAIL] 找不到 Conda python: {CONDA_PY}")
        return 2
    if not SERVICE_PY.exists():
        print(f"[FAIL] 找不到服务脚本: {SERVICE_PY}")
        return 2
    if not MODEL_DIR.exists():
        print(f"[FAIL] 找不到模型目录: {MODEL_DIR}")
        return 2

    cmd = [
        CONDA_PY, str(SERVICE_PY),
        "--host", HOST, "--port", str(PORT),
        "--model_dir", str(MODEL_DIR),
    ]
    print(f"[launch] {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    try:
        # 1) 等 /health 就绪
        t0 = time.monotonic()
        healthy = False
        while time.monotonic() - t0 < HEALTH_TIMEOUT:
            try:
                _, h = _http_json("/health", timeout=3.0)
                if h.get("status") == "ok":
                    healthy = True
                    print(f"[health] ready: {h}")
                    break
            except Exception:
                time.sleep(1.0)
        if not healthy:
            print("[FAIL] 服务在超时内未就绪")
            return 3

        # 2) 触发合成 (首次会加载模型, 较慢)
        text = "你好，我是铸钥匠，很高兴为你服务。今天天气真不错呀。"
        print(f"[synth] POST /synthesize  text={text!r}")
        t1 = time.monotonic()
        try:
            kind, payload = _http_json(
                "/synthesize",
                {"text": text, "voice": "longxiaochun", "speed": 1.0},
                timeout=SYNTH_TIMEOUT,
            )
        except urllib.error.HTTPError as e:
            print(f"[FAIL] /synthesize HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:500]}")
            return 4
        dt = time.monotonic() - t1
        if kind != "bytes":
            print(f"[FAIL] 期望 audio/wav, 得到 json: {payload}")
            return 4
        wav = payload
        # WAV 头: RIFF....WAVE, 至少 44 字节头 + 一些数据
        if len(wav) < 100 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
            print(f"[FAIL] 返回的不是合法 WAV (len={len(wav)})")
            return 4
        out = Path(r"E:\cosyvoice_test.wav")
        out.write_bytes(wav)
        # 估算时长 (16bit 单声道 24000Hz)
        n_bytes = len(wav) - 44
        dur = n_bytes / (24000 * 2)
        print(f"[OK] 合成成功: wav={len(wav)}bytes, 约 {dur:.2f}s 音频, 耗时 {dt:.1f}s -> {out}")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
