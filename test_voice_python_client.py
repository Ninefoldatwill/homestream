"""
Python 测试客户端 - 加入 voice-test 房间
模拟"用户说话" -> 让 Agent 触发
"""
import asyncio
import sys
from pathlib import Path
from datetime import timedelta

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from livekit import rtc
from livekit.api import AccessToken, VideoGrants

# === 配置 ===
LIVEKIT_URL = "ws://localhost:7880"
API_KEY = "devkey"
API_SECRET = "devsecret"
ROOM_NAME = "voice-test"
USER_ID = "python-test-client"


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
        .with_name("Python Test Client")
        .with_grants(grants)
        .with_ttl(timedelta(hours=1))
        .to_jwt()
    )


async def main():
    token = generate_token()
    print(f"🔑 Token 已生成 (用户: {USER_ID})")
    print(f"📡 连接到 {LIVEKIT_URL} 房间 {ROOM_NAME}...")

    room = rtc.Room()
    await room.connect(LIVEKIT_URL, token)

    print(f"✅ 已连接到房间")
    print(f"   Local participant: {room.local_participant.identity}")
    print(f"   Remote participants: {len(room.remote_participants)}")
    for sid, p in room.remote_participants.items():
        print(f"     - {p.identity} (sid={sid})")

    # 监听远端 participant 加入
    @room.on("participant_connected")
    def on_participant_connected(participant):
        print(f"🆕 远端加入: {participant.identity}")

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        print(f"👋 远端离开: {participant.identity}")

    @room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant):
        print(f"🎵 收到远端 {participant.identity} 的 {track.kind} 轨道")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            # 自动播放远端音频
            audio_stream = rtc.AudioStream(track)
            asyncio.create_task(play_audio(audio_stream))

    @room.on("data_received")
    def on_data_received(data):
        try:
            text = data.data.decode("utf-8")
            print(f"💬 收到数据: {text}")
        except Exception:
            pass

    print("")
    print("⏳ 等待 Agent 加入房间 (浏览器那边说话会触发 Agent 响应)...")
    print("📢 你现在对着浏览器说话，Agent 会响应 (Python 客户端会显示)")

    # 保持运行 60 秒
    try:
        await asyncio.sleep(60)
    except KeyboardInterrupt:
        pass
    finally:
        await room.disconnect()
        print("👋 已断开连接")


async def play_audio(audio_stream):
    """播放远端音频流"""
    try:
        async for event in audio_stream:
            # 这里只是简单消费 audio frame
            # 实际播放需要 sounddevice 等
            pass
    except Exception as e:
        print(f"audio play err: {e}")


if __name__ == "__main__":
    asyncio.run(main())
