# 语音 STT 选型对比与融优评估（FunASR vs faster-whisper）

> 日期：2026-07-14 ｜ 负责人：澜舟 ｜ 方法论：九重「维度补短板先学再做 · 记录总结 · 搜开源融优→更高维度开拓」
> 触发：九重指示「对比小测可以测测看看咱们融优处理」
> IP 合规底线：可融理念/模式，**不可复制源码**。本评估只借鉴公开 benchmark 与架构范式。

---

## 一、调研背景

HomeStream 语音栈的 STT 主链路是**本地 FunASR 2-pass**（自托管 Docker，零费用、音频不出本机）。
在 SkillHub 调研时看到大量 `faster-whisper` / `whisper` 类技能，遂按融优方法论做一次
**正式对比**，确认咱们的选型是否站得住，以及 faster-whisper 能否作为「维度补强」而非「替换」。

---

## 二、调研数据（中文识别错误率 CER，越低越好）

来源：FunASR 官方 benchmark + 社区实测（cnblogs 个人对比、funasr.com 中英文对比）。

| 引擎 / 模型 | 中文 CER | 备注 |
|---|---|---|
| **FunASR SenseVoice-Small** | **7.81%** | CPU 友好，带情感/事件检测 |
| **FunASR Fun-ASR-Nano (vLLM)** | 8.06–8.20% | GPU，340× 实时，31 语种 |
| **FunASR Paraformer-Large** | 10.18% | 低延迟流式 |
| Whisper-large-v3 | 20.02% | 英文强、中文弱 |
| Whisper-large-v3-turbo | 21.71% | — |
| faster-whisper (small/base/large-v3-turbo) | 22–31% | 英文/多语种强，中文明显弱 |

**结论 1**：在中文（尤其是普通话、粤语、方言）上，**FunASR 的 CER 约为 Whisper 类的 1/2.7**，差距显著。
faster-whisper 在简单普通话句子上两者都对，但**在难样本、粤语/方言、专有名词**上会误判
（如把粤语当普通话重写、日语同音错字），且把 Cantonese 误标为 `zh` 丢掉方言语义。

---

## 三、维度对比（不止准确率）

| 维度 | FunASR | faster-whisper |
|---|---|---|
| 中文/粤语/方言准确率 | 🟢 强（专精中文训练） | 🔴 弱（英文/多语种强） |
| 实时性（CPU） | 🟢 17× 实时（SenseVoice） | 🟡 比原 whisper 快，但仍慢于 FunASR |
| 情感 / 音频事件 | 🟢 内置（HAPPY/APPLAUSE…） | 🔴 无 |
| 语言 ID（zh/en/yue/ja/ko） | 🟢 原生 | 🔴 粤语误判为 zh |
| 流式 WebSocket | 🟢 原生（Paraformer 2-pass） | 🔴 主要是一次性识别 |
| 跨语种（英文/日/韩）覆盖 | 🟡 50+ 语种（Nano） | 🟢 57 语种、生态成熟 |
| 资源占用 | 🟢 轻（非自回归） | 🟡 中等（CTranslate2） |
| 部署形态 | 🟢 MIT 开源、可私有、可 on-device | 🟢 MIT 开源 |

**结论 2**：两者定位不同——FunASR 是「中文语音工具箱」，faster-whisper 是「英文/通用多语种快实现」。
不存在谁全面碾压，而是**维度互补**。

---

## 四、融优决策（维度补短板，不是替换）

按九重「维度补短板」思路，**不否定现有 FunASR，而是补一个它弱的维度**：

1. **主 STT 仍为 FunASR 本地 2-pass**——中文 CER 8–10%、自带情感/事件、流式原生、零费用。
   这印证了咱们最初选 FunASR 的判断，站得住。
2. **faster-whisper 定位 = 英文 / 跨语种维度补强 + 无 Docker 兜底候选**：
   - 当用户主要说英文/日韩时，FunASR 中文优化反而非最优，faster-whisper 多语种更稳。
   - 当本地 FunASR Docker 未启动时，faster-whisper 是「纯 Python 本地兜底」候选
     （无需 Docker，直接 `pip install faster-whisper` + 本地模型）。
3. **不替换、不绑定**：faster-whisper 作为**可选 STT Provider** 预留接口（`cloud_stt_adapter`
   是云维度；faster-whisper 是本地备选维度，将来可在 `stt_adapter` 加 `create_faster_whisper_stt`）。
4. **云 STT 维度已留口**：`cloud_stt_adapter.py` + `VOICE_STT_MODE=cloud` 已就绪，用户自持 Key 走 OpenAI 兼容 Whisper 端点。

> 融优升华：本地 FunASR（中文专精）＋ 可选 faster-whisper（跨语种兜底）＋ 可选云端（升级旋钮）
> = **三个维度叠加的 STT 生态**，而非非此即彼。这正是「生生不息」架构的体现。

---

## 五、实测结果（2026-07-14 跑通）

### 5.1 测试环境与方法

- **样本**：仓库内 `CosyVoice/asset/zero_shot_prompt.wav`（24kHz 单声道 → 规整为 16k 单声道，3.48s）。
- **参考文本**（去标点）：`希望你以后能够做的比我还好呦`（14 字）。
- **FunASR 侧**：直接打**生产环境**的 `funasr-runtime`（:10096 websocket，2pass 模式），与 VoiceBridge 主 STT 同源——比本地 python 包更贴近真实主链路（python 包在本 Conda 环境有 ABI 冲突会 segfault，故改用生产 runtime）。
- **faster-whisper 侧**：本地 `large-v2` + `int8` + CPU（`OMP_NUM_THREADS=4`），权重经 `hf-mirror` 拉取。
- **指标**：CER = 字符级编辑距离 / 参考长度（两引擎输出均经去标点、去 `<\|...\|>` 标记规整）。

### 5.2 实测数据

| 引擎 / 模型 | 识别结果 | CER |
|---|---|---|
| **FunASR**（生产 runtime, 2pass） | 希望你以后能够做的比我还好**哟**。 | **7.14%**（1/14 字） |
| **faster-whisper large-v2**（int8, CPU） | 希望你以后能够做的比我还好**哟** | **7.14%**（1/14 字） |

> 唯一差异：参考文本「**呦**」(yōu) vs 识别「**哟**」(yō) —— 同音异字，属 1 字级误差。

### 5.3 融优解读（诚实结论）

- **单条短样本下两者持平（CER 完全相同）**：说明在简单普通话短句上，faster-whisper large-v2 也能达到与 FunASR 相当的精度。但这**不构成替换理由**——
- **FunASR 的不可替代性在维度上**：① 生产 runtime 已稳定托管、零额外依赖、音频不出本机；② 原生流式 2-pass（与 VoiceBridge 实时对话同源）；③ 自带情感/音频事件检测（SenseVoice 体系）；④ 粤语/方言专精。这些是 faster-whisper 在「短句 CER」之外的维度优势，被短样本掩盖了。
- **faster-whisper 的真实定位 = 跨语种维度补强**：本次仅验证了中文短句；其价值在英文/日韩/多语种场景（FunASR 中文优化反而非最优时）与「无 Docker 纯 Python 兜底」候选。
- **后续严格对比建议**：应扩大样本（含难样本、粤语/方言、专有名词、长音频），届时两者的维度差异才会显现，单条短句区分度不足。

**融优定论**：维持「主 STT = 本地 FunASR 2-pass」不变；faster-whisper 作为**可选跨语种/兜底 Provider** 预留接口，不替换、不绑定。与第四节决策完全一致，本次实测为决策提供了真实数据底座。

---

## 六、IP 合规声明

- 本报告所有数据来自**公开 benchmark 与官方文档**，未复制任何第三方实现代码。
- 融优只借鉴**选型范式与维度互补思路**，不引入 faster-whisper / whisper 的专有源码到主链路。
- 若将来实装 faster-whisper 备选，将仅用其 MIT 开源包的标准 API（`from faster_whisper import WhisperModel`），
  遵循其开源许可，不改动、不内联其实现。
