#!/usr/bin/env python3
"""
HomeStream VoiceBridge 端到端自动化验证
==========================================
不依赖人工麦克风, 用 EdgeTTS 生成一段中文语音作为"用户说的话",
通过 LiveKit 麦克风轨道发进 voice-test 房间, 监听 Agent 回的音频轨道,
确凿证明 STT -> LLM -> TTS 全链路能"出声"。

用法:
    python test_voice_e2e_auto.py
"""
import asyncio
import json
import os
import subprocess
import sys
import wave
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
VOICE_TEXT = "你好，请简单介绍一下你自己。"
PCM_PATH = PROJECT_ROOT / "voice" / "e2e_test_input.pcm"
MP3_PATH = PROJECT_ROOT / "voice" / "e2e_test_input.mp3"

# 结果累计
RESULT = {
    "agent_joined": False,
    "user_transcribed": [],
    "agent_states": [],
    "agent_audio_bytes": 0,
    "llm_text_seen": [],
}

# 同步事件
agent_joined_event = asyncio.Event()


def log(*a):
    print("[e2e]", *a, flush=True)


async def generate_test_audio():
    """用 EdgeTTS 生成中文语音, ffmpeg 转 raw PCM(16-bit mono 48k)"""
    import edge_tts

    if PCM_PATH.exists() and PCM_PATH.stat().st_size > 1000:
        log(f"测试音频已存在, 跳过生成: {PCM_PATH}")
        return

    comm = edge_tts.Communicate(VOICE_TEXT, voice="zh-CN-XiaoxiaoNeural")
    with open(MP3_PATH, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])

    log(f"mp3 已生成: {MP3_PATH}")

    # ffmpeg 转 raw PCM
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
        room=ROOM_NAME,
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    return (
        AccessToken(API_KEY, API_SECRET)
        .with_identity(USER_ID)
        .with_name("E2E Auto Client")
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
        req.metadata = json.dumps({"source": "e2e-auto"})
        await lk.agent_dispatch.create_dispatch(req)
        log("已触发 /dispatch (agent=homestream-voice)")
    finally:
        await lk.aclose()


async def send_audio(source: rtc.AudioSource, pcm_path: Path, keepalive: float = 6.0):
    data = pcm_path.read_bytes()
    frame_bytes = FRAME_SAMPLES * 2  # 16-bit
    idx = 0
    log(f"开始发送用户语音 ({len(data)/2/SAMPLE_RATE:.1f}s)...")
    while idx + frame_bytes <= len(data):
        frame = rtc.AudioFrame(
            data[idx:idx + frame_bytes],
            SAMPLE_RATE, NUM_CHANNELS, FRAME_SAMPLES,
        )
        await source.capture_frame(frame)
        idx += frame_bytes
        await asyncio.sleep(0.02)  # 实时节奏, 让 VAD 自然检测
    # 保持音频流活跃 (持续发静音帧), 模拟浏览器麦克风持续采集,
    # 避免 LiveKit 因停止 capture 而认为流结束、不再监听下一轮
    if keepalive > 0:
        silence = b"\x00" * frame_bytes
        n_frames = int(keepalive / 0.02)
        for _ in range(n_frames):
            frame = rtc.AudioFrame(silence, SAMPLE_RATE, NUM_CHANNELS, FRAME_SAMPLES)
            await source.capture_frame(frame)
            await asyncio.sleep(0.02)
    log("用户语音发送完毕 (含静音保活)")


async def collect_audio(track: rtc.Track):
    stream = rtc.AudioStream(track)
    async for event in stream:
        frame = getattr(event, "frame", event)
        RESULT["agent_audio_bytes"] += len(frame.data)


async def main():
    await generate_test_audio()

    token = generate_token()
    log(f"连接 {LIVEKIT_URL} / 房间 {ROOM_NAME} ...")
    room = rtc.Room()

    @room.on("participant_connected")
    def on_pc(p):
        log(f"远端加入: {p.identity}")
        if p.identity != USER_ID:  # 非本端即 Agent
            RESULT["agent_joined"] = True
            agent_joined_event.set()

    @room.on("track_subscribed")
    def on_track(track, pub, p):
        log(f"收到 {p.identity} 的 {track.kind} 轨道")
        if track.kind == rtc.TrackKind.KIND_AUDIO and p.identity != USER_ID:
            asyncio.create_task(collect_audio(track))

    @room.on("data_received")
    def on_data(d):
        try:
            msg = json.loads(d.data.decode("utf-8"))
            t = msg.get("type", "")
            if t in ("agent_state_changed", "user_state_changed"):
                RESULT["agent_states"].append(msg)
                log(f"状态: {msg.get('state')} / {msg.get('kind')}")
            elif t == "user_input_transcribed":
                RESULT["user_transcribed"].append(msg.get("text", ""))
                log(f"用户转写: {msg.get('text', '')}")
            elif t == "agent_message" or t == "agent_response":
                RESULT["llm_text_seen"].append(msg.get("message", msg.get("text", "")))
        except Exception:
            pass

    await room.connect(LIVEKIT_URL, token)
    log("已连接房间")

    await trigger_dispatch()

    # 等 Agent 进房 (最多 25s)
    try:
        await asyncio.wait_for(agent_joined_event.wait(), timeout=25)
        log("Agent 已进房, 准备发语音")
    except asyncio.TimeoutError:
        log("⚠️ 25s 内 Agent 未进房, 仍尝试发语音")

    # 发布麦克风轨道
    source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    opts = rtc.TrackPublishOptions()
    opts.source = rtc.TrackSource.SOURCE_MICROPHONE
    await room.local_participant.publish_track(track, opts)
    log("麦克风轨道已发布")

    # 等发布稳定
    await asyncio.sleep(2)

    async def wait_agent_done(timeout: int = 25):
        """等 Agent 出声完毕: 音频字节连续 3s 无增长即视为说完"""
        last = RESULT["agent_audio_bytes"]
        stable = 0
        waited = 0
        while waited < timeout:
            await asyncio.sleep(1)
            waited += 1
            cur = RESULT["agent_audio_bytes"]
            if cur == last:
                stable += 1
                if stable >= 3:
                    return
            else:
                stable = 0
            last = cur

    # 多轮对话测试: 连续发 3 句 (每段后静音保活, 模拟浏览器持续麦克风), 验证多轮连续性
    for i in range(3):
        await send_audio(source, PCM_PATH, keepalive=6.0)
        log(f"第 {i + 1} 轮用户语音已发送, 等待 Agent 回复...")
        try:
            await asyncio.wait_for(wait_agent_done(), timeout=25)
            log(f"第 {i + 1} 轮 Agent 已回复 (累计音频 {RESULT['agent_audio_bytes']} bytes)")
        except asyncio.TimeoutError:
            log(f"⚠️ 第 {i + 1} 轮 Agent 未在 25s 内回复完")
        await asyncio.sleep(1)  # 仅让出控制, 不中断音频流

    await asyncio.sleep(2)
    await room.disconnect()

    # 判定
    print("\n" + "=" * 50)
    print("端到端验证结果")
    print("=" * 50)
    print(f"Agent 进房:        {RESULT['agent_joined']}")
    print(f"用户语音转写:      {RESULT['user_transcribed']}")
    print(f"Agent 状态轨迹:    {[s.get('state') for s in RESULT['agent_states']]}")
    print(f"Agent 音频字节数:  {RESULT['agent_audio_bytes']} ({RESULT['agent_audio_bytes']/2/SAMPLE_RATE:.1f}s)")
    ok = RESULT["agent_joined"] and RESULT["agent_audio_bytes"] > 8000
    print(f"\n结论: {'✅ 全链路出声成功' if ok else '❌ 仍未出声, 需排查'}")
    return ok


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
