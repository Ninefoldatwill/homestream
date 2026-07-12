#!/bin/bash
# FunASR 2-pass 启动脚本 (容器内执行)
# 1. 软链接预下载的模型到 FunASR 期望的目录结构
# 2. 启动 2-pass WebSocket 服务

set -e

echo "==== Step 1: Link pre-downloaded models ===="
mkdir -p /workspace/models/damo /workspace/models/iic

for d in /workspace/models/*/; do
  [ -d "$d" ] || continue
  name=$(basename "$d")
  # "damo--speech_fsmn_vad_..." → owner=damo, rest=speech_fsmn_vad_...
  owner=${name%%--*}
  rest=${name#*--}
  target="/workspace/models/${owner}/${rest}"
  mkdir -p "$(dirname "$target")"
  if [ ! -e "$target" ]; then
    if [ -d "${d}snapshots/master" ]; then
      ln -s "${d}snapshots/master" "$target"
    elif [ -d "${d}master" ]; then
      ln -s "${d}master" "$target"
    else
      ln -s "$d" "$target"
    fi
    echo "Linked: $name -> $target"
  fi
done

echo ""
echo "==== Linked structure ===="
ls -la /workspace/models/damo/ 2>/dev/null
echo "---"
ls -la /workspace/models/iic/ 2>/dev/null
echo "==============================="

echo ""
echo "==== Step 2: Start FunASR 2-pass server ===="
cd /workspace/FunASR/runtime
# 关键: 不传 --itn-dir 和 --lm-dir, 避免容器内尝试下载 ModelScope 模型
nohup bash run_server_2pass.sh \
  --download-model-dir /workspace/models \
  --vad-dir /workspace/models/damo/speech_fsmn_vad_zh-cn-16k-common-onnx \
  --model-dir /workspace/models/damo/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx \
  --online-model-dir /workspace/models/damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx \
  --punc-dir /workspace/models/damo/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727-onnx \
  --certfile 0 \
  --hotword /workspace/models/hotwords.txt > /tmp/funasr.log 2>&1 &

# Wait a few seconds to capture early errors
sleep 5
echo "==== /tmp/funasr.log (first 30 lines) ===="
head -30 /tmp/funasr.log 2>/dev/null || echo "log not found"
echo "==============================="

# 持续等待服务起来
echo "==== Waiting for server (port 10095) ===="
for i in $(seq 1 60); do
  if curl -s http://localhost:10095/health >/dev/null 2>&1; then
    echo "FunASR server is up after ${i}s"
    break
  fi
  sleep 1
done

# 输出后续日志
tail -f /tmp/funasr.log
