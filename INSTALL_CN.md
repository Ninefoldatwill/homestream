# 国内安装指南

> 本指南帮助中国大陆用户顺畅安装和配置 HomeStream，解决网络限制导致的下载/安装问题。
>
> **核心原则**：HomeStream 的 L1 本地推理层（llama.cpp）零外部依赖、零网络需求，
> 即使完全断网也可运行。本指南仅帮助你更顺畅地完成初始安装和可选的 API 配置。

---

## 1. Python 依赖安装（PyPI 镜像）

中国大陆访问 PyPI 官方源可能不稳定，推荐使用清华镜像源：

### 临时使用

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 永久配置（推荐）

```bash
# 设置默认镜像源
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 验证
pip config list
```

### 其他可用镜像源

| 镜像源 | 地址 | 说明 |
|:------|:-----|:-----|
| 清华大学 | `https://pypi.tuna.tsinghua.edu.cn/simple` | 最稳定，推荐 |
| 阿里云 | `https://mirrors.aliyun.com/pypi/simple/` | 速度快 |
| 中科大 | `https://pypi.mirrors.ustc.edu.cn/simple/` | 备选 |
| 腾讯云 | `https://mirrors.cloud.tencent.com/pypi/simple` | 备选 |

---

## 2. 本地模型下载（ModelScope 魔搭替代 HuggingFace）

HuggingFace 在中国大陆受 GFW 阻断，推荐使用阿里 **ModelScope（魔搭）** 下载模型。

### 方式一：ModelScope 魔搭（推荐）

```bash
# 安装 modelscope
pip install modelscope -i https://pypi.tuna.tsinghua.edu.cn/simple

# 下载 Qwen2.5-3B-Instruct（GGUF格式，L1层推荐）
python -c "
from modelscope import snapshot_download
model_dir = snapshot_download('Qwen/Qwen2.5-3B-Instruct-GGUF', revision='master')
print(f'模型已下载到: {model_dir}')
"
```

### 方式二：HuggingFace 镜像（hf-mirror.com）

```bash
# 设置环境变量
set HF_ENDPOINT=https://hf-mirror.com

# 使用 huggingface-cli 下载
pip install huggingface_hub -i https://pypi.tuna.tsinghua.edu.cn/simple
huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF --local-dir ./models/qwen-3b
```

### 常用模型推荐

| 模型 | 大小 | 适用场景 | ModelScope ID |
|:-----|:-----|:--------|:-------------|
| Qwen2.5-3B-Instruct | ~2GB | L1 轻量对话（推荐入门） | `Qwen/Qwen2.5-3B-Instruct-GGUF` |
| Qwen2.5-7B-Instruct | ~4.5GB | L1 标准对话 | `Qwen/Qwen2.5-7B-Instruct-GGUF` |
| Phi-3.5-mini | ~2.2GB | L1 轻量备选 | `LLM-Research/Phi-3.5-mini-instruct-GGUF` |

---

## 3. 推理引擎安装

### 方式一：llama.cpp（推荐，零外部依赖）

HomeStream 的 L1 层原生支持 llama.cpp，无需安装 Ollama：

```bash
# Windows: 下载预编译版本
# 从 https://github.com/ggerganov/llama.cpp/releases 下载 llama-*-bin-win-*.zip

# 解压后设置路径
set LLAMA_CPP_PATH=C:\path\to\llama.cpp

# 或使用 HomeStream 内置的 llama_cpp_provider.py 直接调用
```

### 方式二：Ollama（通过镜像安装）

Ollama 官方下载地址在中国大陆可能被阻断，可通过镜像安装：

```bash
# 方式A: 使用 gh-proxy 镜像下载安装包
# 访问 https://gh-proxy.com/https://github.com/ollama/ollama/releases/latest
# 下载对应平台的安装包

# 方式B: 配置 Ollama 模型镜像（安装后）
set OLLAMA_HOST=127.0.0.1:11434

# 拉取模型（ModelScope下载后导入）
ollama create qwen2.5:3b -f Modelfile
```

### 方式三：已有模型文件直接使用

如果你已通过 ModelScope 下载了 GGUF 格式模型，可直接配置：

```python
# config.py 中配置
LLAMA_CPP_CONFIG = {
    "model_path": "./models/qwen-3b/qwen2.5-3b-instruct-q4_k_m.gguf",
    "context_size": 4096,
    "threads": 4,
}
```

---

## 4. API 密钥获取（国产备选方案）

HomeStream 的三层路由支持多种 API，当某一 API 不可用时可自动降级。

### L2 免费API层

| API | 注册地址 | 说明 |
|:----|:--------|:-----|
| 智谱GLM | https://open.bigmodel.cn | 免费额度充足，默认 L2 |
| 通义千问 | https://dashscope.console.aliyun.com | 阿里云国产API，抗卡脖优选 |

### L3 付费API层

| API | 注册地址 | 说明 |
|:----|:--------|:-----|
| DeepSeek | https://platform.deepseek.com | 性价比之王 ($0.28/M tokens) |
| 通义千问 Max | https://dashscope.console.aliyun.com | 国产最强，专业场景备选 |

### 配置示例（config.py）

```python
# 推荐配置：L1本地 + L2国产API + L3混合
API_CONFIG = {
    # L1: 本地推理（零成本，离线可用）
    "llama_cpp": {"model_path": "./models/qwen-3b.gguf"},

    # L2: 免费API（主选GLM，备选通义千问）
    "glm": {"api_key": "your-glm-key"},
    "qwen_turbo": {"api_key": "your-qwen-key"},  # 抗卡脖备选

    # L3: 付费API（主选DeepSeek，备选通义千问Max）
    "deepseek": {"api_key": "your-deepseek-key"},
    "qwen_max": {"api_key": "your-qwen-key"},    # 国产备选
}
```

---

## 5. 前端资源（已本地化，无需配置）

HomeStream 的可观测性仪表盘使用 ECharts 可视化库，**已内置本地化文件**：

- `assets/echarts.min.js` — 本地自托管，不依赖任何 CDN
- 如果本地文件缺失，会自动回退到 `cdn.staticfile.org`（国内可用CDN）

**无需任何额外配置**，仪表盘在国内可直接访问。

---

## 6. 代码仓库（双源托管）

HomeStream 采用 GitHub + Gitee 双源托管策略：

| 平台 | 地址 | 说明 |
|:-----|:-----|:-----|
| GitHub | `https://github.com/Ninefoldatwill/HomeStream` | 主仓库（国际） |
| Gitee | `https://gitee.com/ninefoldatwill/HomeStream` | 镜像仓库（国内） |

**国内用户推荐使用 Gitee 克隆**：

```bash
git clone https://gitee.com/ninefoldatwill/HomeStream.git
```

---

## 7. 网络诊断

如果遇到连接问题，可运行内置诊断：

```bash
# 诊断各层服务连通性
python -c "
from providers import LlamaCppProvider, GLMProvider, DeepSeekProvider, QwenProvider
print('L1 (本地): 永远可用')
print('L2 (GLM):', '检查 https://open.bigmodel.cn 连通性')
print('L2 (通义千问):', '检查 https://dashscope.aliyuncs.com 连通性')
print('L3 (DeepSeek):', '检查 https://api.deepseek.com 连通性')
"
```

---

## 技术主权保障

HomeStream 的架构设计确保**任何单一外部服务被阻断，系统仍然可用**：

```
全离线场景降级链：
  L1 llama.cpp 本地推理 → AI对话可用 ✅
  SQLite 本地存储 → 数据持久化 ✅
  ICP/A2A 本地通信 → Agent协作可用 ✅
  ECharts 本地化 → 监控面板可用 ✅
  仅 L2/L3 付费API不可用 → 退回L1本地模型（能力降级但系统可用）
```

详见 [TECH_SOVEREIGNTY_ASSESSMENT.md](TECH_SOVEREIGNTY_ASSESSMENT.md) 了解完整的技术主权评估。

---

_铸钥匠不造墙，也不靠墙。HomeStream 是通往AI世界的那把钥匙，不依赖任何单一的技术高墙。_
