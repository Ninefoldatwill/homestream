"""
VoiceBridge 端到端集成测试 (无浏览器)

直接测试 Agent 的核心逻辑链路:
  1. 连接 LiveKit Room
  2. 模拟"用户说话" (直接传文字给 LLM)
  3. 验证 LLM 路由 (三层) 返回响应
  4. 验证 FunASR 2-pass WebSocket 连接
  5. 验证 CosyVoice2 TTS 模型可用性

不测试 WebRTC 音频/视频流 (需要真浏览器)
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

import websockets
import json
import numpy as np


async def test_funasr_connection():
    """测试 FunASR 2-pass WebSocket 连接"""
    print("=" * 60)
    print("  Test 1: FunASR 2-pass WebSocket connection")
    print("=" * 60)
    try:
        async with websockets.connect("ws://localhost:10096") as ws:
            # FunASR 2-pass 初始化消息
            init = {
                "mode": "2pass",
                "chunk_size": [5, 10, 5],
                "wav_name": "integration_test",
                "is_speaking": True,
                "itn": True,
            }
            await ws.send(json.dumps(init))

            # 发送 1 秒静音 (16kHz 16-bit)
            silence = np.zeros(16000, dtype=np.int16).tobytes()
            await ws.send(silence)
            await ws.send(json.dumps({"is_speaking": False}))

            # 接收 1 个响应 (应该识别为静音)
            for _ in range(3):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    data = json.loads(msg)
                    print(f"  ✅ FunASR responded: mode={data.get('mode')}, text='{data.get('text', '')[:50]}'")
                    return True
                except asyncio.TimeoutError:
                    print("  ⚠️  FunASR timeout (might be still initializing)")
                    return False
    except Exception as e:
        print(f"  ❌ FunASR connection failed: {e}")
        return False


def test_livekit_health():
    """测试 LiveKit 服务可访问"""
    print("=" * 60)
    print("  Test 2: LiveKit HTTP API health")
    print("=" * 60)
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:7880/") as r:
            body = r.read().decode()
            print(f"  ✅ LiveKit HTTP /: {body[:50]}")
            return True
    except Exception as e:
        print(f"  ❌ LiveKit HTTP failed: {e}")
        return False


async def test_llm_routing():
    """测试 HomeStreamLLM 三层路由"""
    print("=" * 60)
    print("  Test 3: HomeStreamLLM 3-tier routing")
    print("=" * 60)
    try:
        from voice.llm_adapter import HomeStreamLLM
        llm = HomeStreamLLM(strategy="SPEED_FIRST")

        # 构造一个 mock chat context (简化版)
        from types import SimpleNamespace
        msg = SimpleNamespace(role="user", content="你好, 请用一句话介绍 HomeStream")
        chat_ctx = SimpleNamespace(messages=[msg])

        print("  → Routing request to L1 (Ollama local)...")
        chunks = []
        async for chunk in llm.llm_node(chat_ctx, [], None):
            chunks.append(str(chunk))
        full = "".join(chunks)
        print(f"  ✅ LLM responded ({len(chunks)} chunks): {full[:100]}")
        return bool(chunks)
    except Exception as e:
        print(f"  ❌ LLM routing failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_cosyvoice_model():
    """测试 CosyVoice2 模型文件存在"""
    print("=" * 60)
    print("  Test 4: CosyVoice2 TTS model check")
    print("=" * 60)
    model_path = PROJECT_ROOT / "pretrained_models" / "models" / "iic--CosyVoice2-0.5B" / "snapshots" / "master"
    if model_path.exists():
        print(f"  ✅ CosyVoice2 model found: {model_path}")
        # 列出关键文件
        files = list(model_path.iterdir())[:5]
        for f in files:
            print(f"     - {f.name}")
        return True
    else:
        print(f"  ⚠️  CosyVoice2 model NOT found at {model_path}")
        print(f"     TTS will fall back to API mode (no API key configured)")
        return False


async def main():
    print("\n" + "=" * 60)
    print("  HomeStream VoiceBridge · Integration Test")
    print("=" * 60 + "\n")

    results = {}
    results["livekit"] = test_livekit_health()
    results["funasr"] = await test_funasr_connection()
    results["llm"] = await test_llm_routing()
    results["cosyvoice"] = test_cosyvoice_model()

    print("\n" + "=" * 60)
    print("  Summary")
    print("=" * 60)
    for name, ok in results.items():
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {name}")

    total = sum(results.values())
    print(f"\n  Total: {total}/{len(results)} passed")
    return 0 if total == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
