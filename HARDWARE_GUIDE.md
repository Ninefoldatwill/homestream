# 硬件锚点指南 / Hardware Guide

> **设计理念（九重定调）**：
> "作为技术开源，未来大家可以根据每个人的硬件锚点开拓最优适配自己的AI生态园。"
>
> HomeStream 的 L1 本地推理层不挑硬件——从 8GB 内存的轻薄本到 256GB 的工作站，
> 每个人都能找到属于自己的档位。**不造墙，只铸钥——钥匙适配每一扇门。**

---

## 六大硬件档位

HomeStream 根据你的机器配置自动检测并推荐最优模型档位。

| 档位 | RAM | VRAM | 推荐模型 | 量化 | 部署方式 | 适合谁 |
|:----:|:---:|:----:|:---------|:----:|:---------|:-------|
| **Nano** | 8GB | 无GPU | Qwen2.5-1.5B | Q4_K_M | CPU only | 轻薄本/树莓派入门体验 |
| **Micro** | 16GB | 4GB | Qwen2.5-7B | Q4_K_M | 部分GPU offload | 入门独显笔记本 |
| **Lite** | 16GB | 6GB | Qwen2.5-7B | Q4_K_M | 全GPU offload | 主流游戏本/工作站 |
| **Std** | 32GB | 8GB | Qwen3.5-9B | Q4_K_M | 全GPU offload | 中高端开发机 |
| **Pro** | 64GB | 16GB | GLM-4-9B | Q4_K_M | 全GPU offload | 专业AI工作站 |
| **Max** | 256GB+ | 48GB+ | GLM-5.2 | 2bit | 多卡/CPU+GPU混合 | 服务器/极客玩家 |

---

## 各档位详解

### Nano 档 — 8GB RAM，无GPU

**最低门槛，CPU 也能跑。**

| 项目 | 值 |
|:-----|:---|
| 推荐模型 | Qwen2.5-1.5B（Q4_K_M 量化，约1.5GB） |
| 推理方式 | llama.cpp 纯CPU推理 |
| 预估内存占用 | ~1.5GB |
| 推理速度 | 较慢（5-15 tokens/s，取决于CPU） |
| 上下文窗口 | 2048-4096 tokens |

> 这是 HomeStream 的**托底线**——即使你只有一台旧笔记本，也能跑起 AI 对话。
> 速度虽慢，但系统完整可用：EventStream、记忆演化、ICP协议、可观测性面板全部正常工作。

### Micro 档 — 16GB RAM，4GB VRAM

**入门独显，GPU 加速起步。**

| 项目 | 值 |
|:-----|:---|
| 推荐模型 | Qwen2.5-7B（Q4_K_M 量化，约4.5GB） |
| 推理方式 | llama.cpp 部分GPU offload（部分层在GPU，剩余在CPU） |
| 预估内存占用 | ~5GB（系统） + ~4GB（GPU显存） |
| 推理速度 | 中等（15-30 tokens/s） |

> 4GB 显存只能放部分模型层到 GPU，但已经比纯 CPU 快很多了。

### Lite 档 — 16GB RAM，6GB VRAM

**主流配置，性价比甜点。**

| 项目 | 值 |
|:-----|:---|
| 推荐模型 | Qwen2.5-7B（Q4_K_M 量化，约4.5GB） |
| 推理方式 | llama.cpp 全GPU offload（-ngl 99） |
| 预估内存占用 | ~5GB（系统） + ~4.5GB（GPU显存） |
| 推理速度 | 快（30-50 tokens/s） |
| 上下文窗口 | 4096-8192 tokens |

> 6GB 显存可以完整 offload 7B 模型到 GPU，推理速度流畅。
> **这是大多数用户的最优选择**——九重当前就是这个档位。

### Std 档 — 32GB RAM，8GB VRAM

**中高端开发机，9B 模型 + 128K 上下文。**

| 项目 | 值 |
|:-----|:---|
| 推荐模型 | Qwen3.5-9B（Q4_K_M 量化，约5.5GB） |
| 推理方式 | llama.cpp 全GPU offload |
| 推理速度 | 快（40-60 tokens/s） |
| 上下文窗口 | 最高 128K tokens |

> 9B 模型在 8GB 显存上全 offload，还有余量开长上下文。
> 适合需要处理长文档/代码库的开发者。

### Pro 档 — 64GB RAM，16GB VRAM

**专业AI工作站，更大模型/更高精度。**

| 项目 | 值 |
|:-----|:---|
| 推荐模型 | GLM-4-9B（Q4_K_M）或同级别 |
| 推理方式 | llama.cpp 全GPU offload |
| 推理速度 | 很快（60-80 tokens/s） |
| 特点 | 16GB 显存可跑更大模型或更高精度量化 |

### Max 档 — 256GB+ RAM，48GB+ VRAM

**服务器级，极限玩家。**

| 项目 | 值 |
|:-----|:---|
| 推荐模型 | GLM-5.2（2bit dynamic 量化） |
| 推理方式 | 多卡 / CPU+GPU 混合 |
| 预估内存 | ~245GB |
| 特点 | 需要 256GB 内存，82% 精度保留 |

---

## 三层路由与硬件的关系

HomeStream 的三层模型路由让硬件档位变得**灵活**——你的硬件决定了 L1 本地层的能力上限，但 L2/L3 API 层可以随时增强：

```
L1 本地推理层（你的硬件锚点）
  ├─ Nano/Micro：1.5B-7B 模型，基础对话可用
  ├─ Lite/Std：7B-9B 模型，流畅对话+简单推理
  └─ Pro/Max：9B+ 模型，复杂推理+长上下文

L2 免费/低价 API 层（GLM/通义千问，需联网）
  └─ 能力增强：更准确的回答、更大的上下文、更强推理

L3 付费强 API 层（DeepSeek/通义千问Max，需联网+付费）
  └─ 能力飞跃：最强模型、复杂任务、专业领域
```

**核心理念**：L1 是你的**托底根基**（免费、离线可用、数据不出本地），L2/L3 是**能力增强**（按需付费、逐级递增）。硬件越好，L1 越强；但即使硬件弱，L2/L3 也能补上。

---

## 自动检测你的档位

HomeStream 内置硬件自动检测，运行以下命令查看你的档位：

```bash
python hardware_profile.py
```

输出示例：

```
============================================================
硬件锚点报告
============================================================
项目                  值
------------------------------------------------------------
操作系统              windows
CPU核心数             8
总内存                15.7 GB
可用内存              8.2 GB
GPU型号              NVIDIA GeForce GTX 1660 Ti
GPU显存              6.0 GB
GPU可用显存          5.8 GB
------------------------------------------------------------
推荐档位              LITE
推荐模型              Qwen2.5-7B
量化方式              Q4_K_M
部署方式              llama.cpp 全GPU offload (-ngl 99)
全GPU offload         是
备注                  6GB VRAM可全offload，推理速度好
============================================================
```

你也可以在代码中调用：

```python
from hardware_profile import detect_hardware, recommend_tier, get_model_recommendation

info = detect_hardware()
tier = recommend_tier(info)
rec = get_model_recommendation(tier)

print(f"你的档位: {tier.value.upper()}")
print(f"推荐模型: {rec.model_name} ({rec.quantization})")
print(f"部署方式: {rec.deployment_method}")
```

---

## 模型下载

### ModelScope 魔搭（国内推荐）

```bash
pip install modelscope -i https://pypi.tuna.tsinghua.edu.cn/simple

python -c "
from modelscope import snapshot_download
model_dir = snapshot_download('Qwen/Qwen2.5-3B-Instruct-GGUF', revision='master')
print(f'模型已下载到: {model_dir}')
"
```

### 常用模型速查

| 模型 | 大小 | 档位 | ModelScope ID |
|:-----|:-----|:----:|:-------------|
| Qwen2.5-1.5B-Instruct | ~1.5GB | Nano | `Qwen/Qwen2.5-1.5B-Instruct-GGUF` |
| Qwen2.5-3B-Instruct | ~2GB | Nano+ | `Qwen/Qwen2.5-3B-Instruct-GGUF` |
| Qwen2.5-7B-Instruct | ~4.5GB | Micro/Lite | `Qwen/Qwen2.5-7B-Instruct-GGUF` |
| Phi-3.5-mini | ~2.2GB | Nano+ | `LLM-Research/Phi-3.5-mini-instruct-GGUF` |

详见 [INSTALL_CN.md](INSTALL_CN.md) 了解完整的国内安装指南。

---

## 常见问题

**Q: 没有 NVIDIA GPU 能用吗？**
A: 可以。Nano 档位纯 CPU 推理，AMD GPU 也可通过 llama.cpp 的 ROCm/HIP 后端支持。

**Q: Mac (Apple Silicon) 能用吗？**
A: 可以。llama.cpp 支持 Metal 后端，M1/M2/M3 的统一内存天然适合跑大模型。M1 16GB 约等于 Lite 档位。

**Q: 内存不够怎么办？**
A: 降低模型参数量（7B→3B→1.5B），或减少上下文窗口（8192→4096→2048）。L1 降级后，L2 免费 API 可以补上能力。

**Q: 可以同时跑多个模型吗？**
A: 取决于显存。6GB VRAM 通常只能跑一个 7B 模型。如需多模型并行，需要 Pro 档位以上。

---

*硬件锚点是你的AI生态园的地基——地基越宽，园子越大。但即使地基很小，HomeStream 也能让你先种下一棵树。*

*文档版本：1.0 | 最后更新：2026-07-10*
*Copyright (c) 2026 九重工作室 (JiuChong Studio) — Licensed under MIT License.*
