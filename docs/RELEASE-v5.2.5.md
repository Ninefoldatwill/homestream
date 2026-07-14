# HomeStream 开源版 v5.2.5 Release Notes

> 铸钥匠的钥匙，又亮了一分。

**发布日期**：2026-07-14  
**GitHub**：[Ninefoldatwill/homestream](https://github.com/Ninefoldatwill/homestream) ｜ **Gitee**：[the-warrior-king/homestream](https://gitee.com/the-warrior-king/homestream)  
**标签**：`v5.2.5`（待打）  
**许可证**：MIT

---

## 🎯 一句话总结

v5.2.5 是 VoiceBridge 语音模块的**增强版**：把 CosyVoice2 从 Worker 进程内拆出来做**独立 TTS 微服务**（Conda 3.10），Worker 通过 HTTP 调用，解决了 Python 3.13 venv 无法加载 CosyVoice2 原生库的根本矛盾；同时补齐了**云端可选接口**和**STT 融优评估**，让「免费托底」与「生态开放」并存。

---

## ✅ 新增能力

### 1. CosyVoice2 独立 TTS 微服务

| 文件 | 作用 |
|:-----|:-----|
| `voice/cosyvoice_service.py` | 独立微服务（Python 标准库 `http.server`），端点 `/health`、`/voices`、`/synthesize` |
| `voice/tts_adapter.py` | Worker 端 HTTP 客户端 + LiveKit 1.6.5 `ChunkedStream`，自动探活切 CosyVoice2 |
| `voice/cosyvoice_requirements.txt` | 推理必需依赖清单（含踩坑说明） |
| `voice/start_cosyvoice_service.bat` | Windows 一键启动脚本 |
| `voice/test_cosyvoice_service.py` | 端到端合成验证脚本 |
| `voice/install_cosyvoice_*.ps1` | 环境/依赖/仓库安装脚本 |

**关键设计**：
- **进程隔离**：CosyVoice2 依赖 pynini / Matcha-TTS / kaldifst 原生库，必须 Python 3.10 Conda；Worker 用 Python 3.13 venv（livekit.agents 1.6.5）。两者用 HTTP 桥接，互不污染。
- **无 MSVC 编译器也能跑**：Matcha-TTS 的 Cython 扩展 `monotonic_align.core` 仅训练用，推理不调用；已放纯 Python stub，服务通过 `sys.path` 直接引入 Matcha-TTS 目录。
- **spk2info.pt 缺失自动兜底**：官方 CosyVoice2-0.5B 发布不含 `spk2info.pt`，服务自动检测 `list_available_spks()`，为空时走 `inference_zero_shot`（自带 `zero_shot_prompt.wav` + 提示文本），保证出声。
- **多句不丢**：修复只保留最后一句音频 chunk 的 bug，改为 `np.concatenate` 拼接所有 chunk。

### 2. 云端可选接口（生态留口，默认关闭）

| 文件 | 作用 |
|:-----|:-----|
| `voice/cloud_tts_adapter.py` | OpenAI 兼容 TTS 云接口（整段合成 mp3 直推） |
| `voice/cloud_stt_adapter.py` | OpenAI 兼容 STT 云接口（缓冲后 POST `/audio/transcriptions`） |
| `voice/config.py` / `voice/agent.py` | 配置与接线，`stt_mode`/`tts_mode` 为 `"cloud"` 且 Key 齐全才启用 |
| `docs/语音云对接说明.md` | 哲学、启用方式、Provider 表、成本/隐私/延迟权衡、IP 合规 |

**设计原则**：
- 默认 `local`，无 Key 绝不外发；
- 纯 `httpx` 调 OpenAI 兼容 REST，不引入任何云 SDK；
- 不绑死厂商（OpenAI / Azure / MiniMax / custom 四档 provider）。

### 3. STT 融优评估

| 文件 | 作用 |
|:-----|:-----|
| `docs/语音STT对比-融优评估.md` | 调研数据、维度对比、融优决策、实测计划、IP 合规 |
| `voice/bench_stt_cn.py` | 用 `CosyVoice/asset/zero_shot_prompt.wav` 跑 FunASR vs faster-whisper 真实 CER |

**融优结论**：
- **主 STT 仍 FunASR**（中文 CER 7–10%，情感+流式+零费）；
- **faster-whisper** = 英文/跨语种维度补强 + 无 Docker 本地兜底候选；
- **云端 STT** = 已留接口，按需启用。

---

## 🔧 修复与优化

- **多轮对话断流**：FunASR STT 流跨轮复用问题修复，`receiver()` 持久化 + `is_speaking` re-arm，已验证 3 轮连续。
- **LLM 冷启动超时**：AgentSession 默认 10s 不够 Ollama qwen2.5:3b 冷启动 ~24s，子类化 `lk_llm.LLM` 覆写 `chat()` 强制默认 60s。
- **VAD 调优**：threshold=0.65、min_speech=0.5s、min_silence=1.0s，UI 加 0.5s 去抖。
- **依赖踩坑**：补齐 `diffusers`/`lightning`/`rich`/`gdown`/`wget`/`matplotlib`/`tensorboard`/`pyarrow` 等被误当"演示依赖"剔除、实际推理必需的包；`openai-whisper` 必须用 `--no-build-isolation` + `setuptools<81`。

---

## 📊 验证结果

| 测试项 | 结果 |
|:-------|:-----|
| `test_cosyvoice_service.py` | ✅ 合成成功：284KB WAV，约 5.92s 音频 |
| `test_multiturn.py --turns 3` | ✅ 3 轮连续正常，每轮转写非空 |
| Worker `create_tts` 探活 | ✅ CosyVoice2 服务在线时自动切换 |
| 双源推送 | ✅ GitHub + Gitee main 已同步 |
| v5.2.0 tag | ✅ 已补打并推送双源 |

---

## 🗺️ 路线更新

README 路线图已同步为：

- **V5.0.0 / V5.1.0 / V5.2.0**：已发布 ✅
- **V5.2.5**：当前版本 🔄（本版本）
- **V5.3.0**：Tauri 2.0 跨平台
- **V5.3.5**：可视化工作流编排器 + 可观测性主题面板
- **V5.4.0**：浏览器自动化插件市场
- **V5.4.5**：好玩项目（书阁 / 创意场景）
- **V6-V7**：维度自用（不开源）
- **终极**：星际网络

> 原 V5.1.0 中的 Tauri 2.0、原 V5.2.0 中的编排器/浏览器自动化插件市场，已按实际执行节奏迁移到 V5.3-V5.4。

---

## 🙏 致谢

感谢开源社区提供的 FunASR、CosyVoice、LiveKit、Ollama、EdgeTTS 等优秀项目，让"免费托底"成为可能。HomeStream 只做钥匙，门由用户自己选。

> 不造一面墙，只铸千万门。
