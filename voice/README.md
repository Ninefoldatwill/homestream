# HomeStream VoiceBridge v5.2.0

> 自托管 LiveKit + 三层模型路由 + Silero VAD + SenseVoice STT + CosyVoice2 TTS
> — 零注册、零账号、本地优先、全栈 Apache 2.0

## 快速开始 (6 步起手)

### 1. 安装依赖

```bash
# 核心 SDK (必须)
pip install "livekit-agents[silero,turn-detector]~=1.4" python-dotenv numpy

# STT: SenseVoice (Apache 2.0, 中文+情感+事件一次推理)
pip install funasr

# TTS: CosyVoice2 (Apache 2.0, 中文第一, 18+方言)
# 需从 GitHub 安装:
git clone https://github.com/FunAudioLLM/CosyVoice.git
cd CosyVoice && pip install -e .
```

### 2. 启动自托管 LiveKit Server (零注册)

```bash
cd voice
docker compose up -d

# 验证服务健康
curl http://localhost:7880/rtc/health
# 返回 "OK" 即成功
```

> **不需要注册任何账号。** LiveKit Server 是 Apache 2.0 开源软件,
> Docker 直接运行, 密钥用 `generate-keys` 本地生成。

### 3. 配置环境变量 (可选)

在项目根目录 `.env` 中添加 (全部可选, 缺省即 localhost 模式):

```env
# LiveKit 自托管连接 (默认 localhost)
VOICE_LIVEKIT_URL=ws://localhost:7880
VOICE_LIVEKIT_API_KEY=devkey
VOICE_LIVEKIT_API_SECRET=devsecret

# 路由策略 (语音场景推荐 SPEED_FIRST)
VOICE_ROUTER_STRATEGY=SPEED_FIRST

# VAD 参数 (Silero, 中文场景调优)
VOICE_VAD_THRESHOLD=0.5
VOICE_VAD_MIN_SPEECH=0.2
VOICE_VAD_MIN_SILENCE=0.12

# STT: SenseVoice (本地)
VOICE_STT_MODE=auto
VOICE_STT_MODEL=iic/SenseVoiceSmall
VOICE_STT_LANGUAGE=zh

# TTS: CosyVoice2 (本地)
VOICE_TTS_MODE=auto
VOICE_TTS_MODEL_PATH=pretrained_models/CosyVoice2-0.5B
VOICE_TTS_VOICE=longxiaochun
VOICE_TTS_SPEED=1.0
```

### 4. 启动 Agent Worker

```bash
# 开发模式 (热重载, 详细日志)
python -m voice.agent dev

# 生产模式
python -m voice.agent start
```

### 5. 测试连接

用 LiveKit 的 Playground 或前端 SDK 连接 `ws://localhost:7880`,
创建房间即可与 VoiceBridge Agent 对话。

### 6. 运行单元测试

```bash
python -m pytest test_voice_llm_adapter.py test_voice_stt_tts.py -v
# 47 tests passed (16 LLM + 31 STT/TTS)
```

---

## 架构

```
用户浏览器 ──WebRTC──→ LiveKit SFU (localhost:7880, 自托管Docker)
                              │
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
               Silero VAD  SenseVoice  CosyVoice2
              (本地,免费)  STT(本地)   TTS(本地)
                    │      │情感+事件    ▲流式合成
                    │      │            │
                    │      ▼            │
                    │   HomeStreamLLM (llm_node覆写)
                    │   ┌─────────────────┐
                    │   │  ModelRouter    │
                    │   │  L1本地Ollama   │ ← 零配置, 离线可用
                    │   │  L2免费GLM      │ ← 免费API
                    │   │  L3付费DeepSeek │ ← 付费API
                    │   │  双保障降级      │ ← 主线失败→复线
                    │   └─────────────────┘
                    │         │
                    └─────────┴─────────┘
                     barge-in (DataChannel, 零延迟打断)
```

## 文件结构

```
voice/
├── __init__.py          # 包导出 (Config, LLM, STT, TTS)
├── config.py            # VoiceBridgeConfig (环境变量配置, 22项)
├── llm_adapter.py       # HomeStreamLLM (三层路由 LLM 适配器) ← 核心集成
├── stt_adapter.py       # SenseVoiceSTT (本地 STT, 情感+事件) ← v5.2.0 新增
├── tts_adapter.py       # CosyVoiceTTS (本地 TTS, 流式+方言) ← v5.2.0 新增
├── agent.py             # VoiceBridgeAgent (LiveKit Agent 入口)
├── docker-compose.yml   # 自托管 LiveKit Server (零注册)
├── livekit.yaml         # LiveKit Server 开发配置
└── README.md            # 本文件

test/
├── test_voice_llm_adapter.py  # LLM 适配器测试 (16 tests)
└── test_voice_stt_tts.py      # STT/TTS adapter 测试 (31 tests)
```

## STT/TTS Adapter 设计

### SenseVoice STT (stt_adapter.py)

- **继承** `livekit.agents.stt.STT`
- **Streaming 策略**: batch→streaming (音频帧缓冲 → VAD检测语音段 → batch推理 → 事件发出)
- **独有能力**: 一次推理返回 转写+语种+情感+事件 (Speech/Music/Silence)
- **标签解析**: 自动解析 `<|zh|><|HAPPY|><|Speech|>文本` 格式
- **工厂函数**: `create_sensevoice_stt()` — LiveKit 不可用时返回 None

### CosyVoice2 TTS (tts_adapter.py)

- **继承** `livekit.agents.tts.TTS`
- **Streaming 策略**: CosyVoice2 原生流式合成, chunk→AudioFrame 直转
- **独有能力**: 中文自然度第一, 18+方言, 3秒声音克隆
- **内置声音**: 10 个预定义声音 (longxiaochun/longwan/longcheng...)
- **工厂函数**: `create_cosyvoice_tts()` — LiveKit 不可用时返回 None

### 降级链

```
auto 模式:
  1. 优先尝试本地 (SenseVoice / CosyVoice2)
  2. 本地不可用 → 尝试 API (OpenAI Whisper / OpenAI TTS)
  3. API 不可用 → 文本模式 (STT/TTS = None, 仅文字交互)
```

## 铸钥匠对齐

| 原则 | VoiceBridge 实现 |
|:-----|:-----------------|
| 免费托底 | 自托管 LiveKit (Apache 2.0), 零注册零账号 |
| 自托底 | L1 本地 Ollama 离线可用, 无网也能跑 |
| 不造墙 | STT/TTS/LLM 全可插拔, 不锁定任何供应商 |
| 只铸钥 | 三层路由 + 双保障 = 给用户一把万能钥匙 |
| 全栈开源 | LiveKit/Apache2.0 · SenseVoice/Apache2.0 · CosyVoice2/Apache2.0 · Silero/MIT |

## 后续路线 (v5.2.0+)

- [x] SenseVoice STT streaming adapter (本地中文 STT + 情感识别)
- [x] CosyVoice2 TTS streaming adapter (本地中文 TTS, 18+方言)
- [x] 单元测试 47 tests passed
- [ ] 端到端联调 (Docker LiveKit + Agent Worker + 浏览器)
- [ ] 情感驱动 TTS 语气 (SenseVoice 情感 → CosyVoice 语气参数)
- [ ] 声纹个性化 (speaker_id → TTS voice 映射)
- [ ] MCP 工具调用 (语音触发工具执行)
- [ ] 千面主题状态 (语音 Agent 的 UI 状态同步)

## 生成生产密钥

```bash
docker run --rm livekit/livekit-server generate-keys
# 将输出的 API Key / Secret 写入 livekit.yaml 的 keys: 字段
```
