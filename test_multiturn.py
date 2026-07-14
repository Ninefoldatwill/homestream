#!/usr/bin/env python3
"""
HomeStream VoiceBridge 多轮连续性验证 (改进版 v2)
================================================
判定方式改为"状态机驱动"(监听 DataChannel 的 voice_state):
  每成功处理一轮用户语音, Agent 都会经历 正在聆听->正在思考->正在说话->等待说话,
  其中"正在思考"状态会携带当轮用户转写(detail)。
  因此: 发出的轮数 == 观测到的"正在思考"次数 且 转写非空  -> 多轮连续正常。

这避免了旧版"按音频字节增长"的测量假象(rtc.AudioStream 重复计数)。

用法:
    python test_multiturn.py            # 默认 3 轮
    python test_multiturn.py --turns 4
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from livekit import rtc, api  # noqa: E402
from livekit.api import AccessToken, VideoGrants  # noqa: E402

# === 配置 ===
LIVEKIT_URL = "ws://localhost:7880"
API_KEY = "devkey"
API_SECRET = "devsecret"
ROOM_NAME = "voice-test"
AGENT_NAME = "homestream-voice"
USER_ID = "e2e-auto-client"
SAMPLE_RATE = 48000
NUM_CHANNELS = 1
FRAME_SAMPLES = 960  # 20ms @ 48k
VOICE_TEXT = "今天天气怎么样？"
PCM_PATH = PROJECT_ROOT / "voice" / "mt_input.pcm"
MP3_PATH = PROJECT_ROOT / "voice" / "mt_input.mp3"

# 全局观测
thinking_events = []      # 每次"正在思考"的转写(detail)
state_history = []        # 状态轨迹


def log(*a):
    print("[mt]", *a, flush=True)


async def generate_test_audio():
    import edge_tts

    if PCM_PATH.exists():
        PCM_PATH.unlink()
    if MP3_PATH.exists():
        MP3_PATH.unlink()

    comm = edge_tts.Communicate(VOICE_TEXT, voice="zh-CN-XiaoxiaoNeural")
    with open(MP3_PATH, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
    log(f"mp3 已生成: {MP3_PATH}")

    cmd = [
        "ffmpeg", "-y", "-i", str(MP3_PATH),
        "-ar", str(SAMPLE_RATE), "-ac", str(NUM_CHANNELS),
        "-f", "s16le", str(PCM_PATH),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    size = PCM_PATH.stat().st_size
    log(f"PCM 已生成: {PCM_PATH} ({size} bytes, 约 {size/2/SAMPLE_RATE:.1f}s)")


def generate_token() -> str:
    grants = VideoGrants(
        room=ROOM_NAME, room_join=True, can_publish=True,
        can_subscribe=True, can_publish_data=True,
    )
    return (
        AccessToken(API_KEY, API_SECRET)
        .with_identity(USER_ID)
        .with_name("MT Auto Client")
        .with_grants(grants)
        .with_ttl(timedelta(hours=1))
        .to_jwt()
    )


async def trigger_dispatch():
    lk = api.LiveKitAPI(url=LIVEKIT_URL, api_key=API_KEY, api_secret=API_SECRET)
    try:
        req = api.CreateAgentDispatchRequest()
        req.agent_name = AGENT_NAME
        req.room = ROOM_NAME
        req.metadata = json.dumps({"source": "mt-auto"})
        await lk.agent_dispatch.create_dispatch(req)
        log("已触发 dispatch (agent=homestream-voice)")
    finally:
        await lk.aclose()


async def send_audio(source: rtc.AudioSource, pcm_path: Path, keepalive: float = 4.0):
    data = pcm_path.read_bytes()
    frame_bytes = FRAME_SAMPLES * 2
    idx = 0
    while idx + frame_bytes <= len(data):
        frame = rtc.AudioFrame(data[idx:idx + frame_bytes], SAMPLE_RATE, NUM_CHANNELS, FRAME_SAMPLES)
        await source.capture_frame(frame)
        idx += frame_bytes
        await asyncio.sleep(0.02)
    if keepalive > 0:
        silence = b"\x00" * frame_bytes
        n_frames = int(keepalive / 0.02)
        for _ in range(n_frames):
            frame = rtc.AudioFrame(silence, SAMPLE_RATE, NUM_CHANNELS, FRAME_SAMPLES)
            await source.capture_frame(frame)
            await asyncio.sleep(0.02)


async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=3)
    args = ap.parse_args()

    await generate_test_audio()
    token = generate_token()
    log(f"连接 {LIVEKIT_URL} / 房间 {ROOM_NAME} ...")
    room = rtc.Room()

    agent_joined = asyncio.Event()
    current_state = {"state": "init"}

    @room.on("participant_connected")
    def on_pc(p):
        if p.identity != USER_ID:
            agent_joined.set()

    @room.on("track_subscribed")
    def on_track(track, pub, p):
        if track.kind == rtc.TrackKind.KIND_AUDIO and p.identity != USER_ID:
            log(f"收到 {p.identity} 音频轨道 (仅用于保活, 不作为判定)")

    @room.on("data_received")
    def on_data(d):
        try:
            msg = json.loads(d.data.decode("utf-8"))
            t = msg.get("type", "")
            if t == "voice_state":
                st = msg.get("state", "")
                current_state["state"] = st
                state_history.append(st)
                detail = msg.get("detail", "")
                if st == "正在思考" and detail:
                    thinking_events.append(detail)
                    log(f"  > 第 {len(thinking_events)} 轮被处理, 转写: {detail}")
            elif t in ("agent_state_changed", "user_state_changed"):
                current_state["state"] = msg.get("state", "")
        except Exception:
            pass

    await room.connect(LIVEKIT_URL, token)
    log("已连接房间")
    await trigger_dispatch()
    try:
        await asyncio.wait_for(agent_joined.wait(), timeout=25)
        log("Agent 已进房")
    except asyncio.TimeoutError:
        log("⚠️ 25s 内 Agent 未进房")

    source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    opts = rtc.TrackPublishOptions()
    opts.source = rtc.TrackSource.SOURCE_MICROPHONE
    await room.local_participant.publish_track(track, opts)
    log("麦克风轨道已发布")
    await asyncio.sleep(2)

    async def wait_round_done(timeout=40):
        """等本轮被处理: 观测到一次新的'正在思考' 或 回到'等待说话'。"""
        before = len(thinking_events)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            await asyncio.sleep(0.5)
            if len(thinking_events) > before:
                return True
            # 兜底: 看到'等待说话'说明至少走过一轮
            if current_state["state"] in ("等待说话", "正在聆听") and len(thinking_events) > before:
                return True
        return len(thinking_events) > before

    for i in range(args.turns):
        n_before = len(thinking_events)
        await send_audio(source, PCM_PATH, keepalive=4.0)
        log(f"第 {i + 1} 轮语音已发送, 等待 Agent 处理...")
        ok = await wait_round_done()
        if ok:
            log(f"第 {i + 1} 轮: ✅ 已处理 (转写={thinking_events[-1] if thinking_events else ''})")
        else:
            log(f"第 {i + 1} 轮: ❌ 40s 内未观测到处理")
        # 等 Agent 回到监听再发下一轮, 避免回合重叠
        await asyncio.sleep(3)

    await asyncio.sleep(2)
    await room.disconnect()

    print("\n" + "=" * 56)
    print("多轮连续性验证结果 (状态机驱动)")
    print("=" * 56)
    print(f"  发出轮数:        {args.turns}")
    print(f"  已处理轮数:      {len(thinking_events)}")
    print(f"  用户转写({len(thinking_events)}条): {thinking_events}")
    print(f"  状态轨迹末段:    {state_history[-8:]}")
    ok_all = len(thinking_events) >= args.turns
    print(f"\n结论: {'✅ 多轮连续正常' if ok_all else '❌ 存在多轮断流, 需排查'}")
    return ok_all


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
