"""
LiveKit Access Token 生成 + 测试网页启动器

用法: python test_voice_browser_demo.py
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

import json
import time
import webbrowser
from datetime import timedelta
from livekit.api import AccessToken, VideoGrants

# === 配置 ===
LIVEKIT_URL = "ws://localhost:7880"
API_KEY = "devkey"
API_SECRET = "devsecret"
ROOM_NAME = "voice-test"
USER_ID = "browser-user-001"


def generate_token() -> str:
    """生成 LiveKit 访问 token"""
    grants = VideoGrants(
        room=ROOM_NAME,
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = AccessToken(API_KEY, API_SECRET) \
        .with_identity(USER_ID) \
        .with_name("Browser User") \
        .with_grants(grants) \
        .with_ttl(timedelta(hours=1)) \
        .to_jwt()
    return token


def serve_browser_demo():
    """写一个简易 HTML demo + 用浏览器打开"""
    token = generate_token()
    demo_dir = PROJECT_ROOT / "voice" / "browser_demo"
    demo_dir.mkdir(parents=True, exist_ok=True)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>HomeStream VoiceBridge 测试</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 800px; margin: 40px auto; padding: 20px;
         background: #0a0a0a; color: #e6e6e6; }}
  h1 {{ color: #88c0d0; border-bottom: 1px solid #333; padding-bottom: 12px; }}
  .status {{ padding: 12px; border-radius: 6px; margin: 12px 0;
             background: #1a1a1a; border: 1px solid #333; }}
  .connected {{ background: #1a3a1a; border-color: #4caf50; color: #a5d6a7; }}
  button {{ padding: 10px 20px; font-size: 14px; border-radius: 4px;
            border: 1px solid #555; background: #2a2a2a; color: #e6e6e6;
            cursor: pointer; margin: 4px; }}
  button:hover {{ background: #3a3a3a; }}
  button:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  #log {{ background: #000; color: #88c0d0; padding: 12px;
         border-radius: 4px; height: 300px; overflow-y: auto;
         font-family: 'Consolas', monospace; font-size: 12px;
         border: 1px solid #333; }}
  .log-entry {{ margin: 2px 0; }}
  .log-time {{ color: #666; }}
  .log-info {{ color: #88c0d0; }}
  .log-error {{ color: #f44336; }}
  .log-text {{ color: #a5d6a7; }}
</style>
</head>
<body>
  <h1>🔑 HomeStream VoiceBridge 测试</h1>

  <div class="status" id="status">⚪ 未连接</div>

  <div>
    <button id="connectBtn" onclick="connect()">🔌 Connect</button>
    <button id="micBtn" onclick="toggleMic()" disabled>🎤 启用麦克风</button>
    <button id="disconnectBtn" onclick="disconnect()" disabled>❌ Disconnect</button>
  </div>

  <h2>📋 事件日志</h2>
  <div id="log"></div>

<script src="https://cdn.jsdelivr.net/npm/livekit-client@1.6.5/dist/livekit-client.umd.min.js"></script>
<script>
  const LK = window.LivekitClient;
  const URL = '{LIVEKIT_URL}';
  const TOKEN = '{token}';
  const ROOM = '{ROOM_NAME}';

  let room = null;
  let micEnabled = false;

  function log(msg, type = 'info') {{
    const el = document.getElementById('log');
    const time = new Date().toLocaleTimeString();
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `<span class="log-time">[${{time}}]</span> ` +
                      `<span class="log-${{type}}">${{msg}}</span>`;
    el.appendChild(entry);
    el.scrollTop = el.scrollHeight;
  }}

  function setStatus(text, connected = false) {{
    const el = document.getElementById('status');
    el.textContent = text;
    el.className = 'status' + (connected ? ' connected' : '');
  }}

  async function connect() {{
    try {{
      if (!LK) {{
        log('❌ LivekitClient SDK 加载失败 (CDN 不可用?)', 'error');
        return;
      }}
      log('正在连接 ' + URL + ' ...', 'info');
      room = new LK.Room({{
        adaptiveStream: true,
        dynacast: true,
      }});

      room.on(LK.RoomEvent.Connected, () => {{
        log('✅ 已连接到房间: ' + ROOM, 'info');
        setStatus('🟢 已连接 (' + ROOM + ')', true);
        document.getElementById('connectBtn').disabled = true;
        document.getElementById('micBtn').disabled = false;
        document.getElementById('disconnectBtn').disabled = false;
      }});

      room.on(LK.RoomEvent.Disconnected, (reason) => {{
        log('❌ 断开连接: ' + reason, 'error');
        setStatus('⚪ 已断开');
        document.getElementById('connectBtn').disabled = false;
        document.getElementById('micBtn').disabled = true;
        document.getElementById('disconnectBtn').disabled = true;
      }});

      room.on(LK.RoomEvent.TrackSubscribed, (track, publication, participant) => {{
        log('🔊 收到远端音轨: ' + participant.identity, 'info');
        if (track.kind === LK.Track.Kind.Audio) {{
          const audioEl = track.attach();
          audioEl.autoplay = true;
          document.body.appendChild(audioEl);
        }}
      }});

      room.on(LK.RoomEvent.DataReceived, (payload, participant) => {{
        const text = new TextDecoder().decode(payload);
        log('💬 收到 ' + participant.identity + ': ' + text, 'text');
      }});

      await room.connect(URL, TOKEN);
      log('连接成功，等待 Agent 加入...', 'info');

    }} catch (err) {{
      log('连接失败: ' + err.message, 'error');
      console.error(err);
    }}
  }}

  async function toggleMic() {{
    if (!room) return;
    try {{
      if (!micEnabled) {{
        log('启用麦克风...', 'info');
        await room.localParticipant.setMicrophoneEnabled(true);
        micEnabled = true;
        document.getElementById('micBtn').textContent = '🎤 关闭麦克风';
        log('✅ 麦克风已启用，开始说话吧！', 'info');
      }} else {{
        log('关闭麦克风...', 'info');
        await room.localParticipant.setMicrophoneEnabled(false);
        micEnabled = false;
        document.getElementById('micBtn').textContent = '🎤 启用麦克风';
      }}
    }} catch (err) {{
      log('麦克风操作失败: ' + err.message, 'error');
    }}
  }}

  async function disconnect() {{
    if (room) {{
      await room.disconnect();
      room = null;
    }}
  }}

  if (LK) {{
    log('页面已加载，LivekitClient SDK 准备就绪。点 Connect 开始。', 'info');
  }} else {{
    log('⚠️ LivekitClient SDK 加载失败，请检查网络 (CDN: jsdelivr)', 'error');
  }}
</script>
</body>
</html>"""

    html_file = demo_dir / "index.html"
    html_file.write_text(html, encoding="utf-8")

    print(f"✅ Token 已生成 (TTL: 1h)")
    print(f"✅ Demo 页面已写入: {html_file}")
    print(f"")
    print(f"📋 复制下面这行打开浏览器测试:")
    print(f"   {html_file.as_uri()}")
    print(f"")
    print(f"或者手动复制 token 到 LiveKit Playground (需自行启动 playground 容器)")
    print(f"")

    # 自动打开浏览器
    try:
        webbrowser.open(html_file.as_uri())
        print(f"🌐 浏览器已自动打开")
    except Exception as e:
        print(f"⚠️  自动打开失败: {e}")
        print(f"   请手动复制上面的 file URI 到浏览器打开")

    return token, html_file


if __name__ == "__main__":
    serve_browser_demo()
