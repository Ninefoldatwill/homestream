# CosyVoice2 环境搭建 · 第二阶段（依赖 env `cosyvoice` 已就绪）
# 流程: 克隆 CosyVoice 仓库(--recursive 拉 Matcha-TTS) -> pip 安装 -> 下载模型权重(modelscope)
# 全程 GFW 镜像回退, 日志写入 E:\cosyvoice_phase2.log
$ErrorActionPreference='Continue'
$log='E:\cosyvoice_phase2.log'
function Log($m){ Add-Content -Path $log -Value "$(Get-Date) $m" }

$conda='E:\miniconda3\_conda.exe'
$prefix='E:\conda_envs\cosyvoice'
$py="$prefix\python.exe"
$project='E:\九重工作室\01-工作区\澜舟工作站\03-项目空间\P0-进行中\HomeStream-开源版'
$cosyvoiceRepo="$project\CosyVoice"
$modelDir="$project\pretrained_models\CosyVoice2-0.5B"
$pipMirror='https://pypi.tuna.tsinghua.edu.cn/simple/'
$trusted='--trusted-host pypi.tuna.tsinghua.edu.cn'

Log "=== [1] clone CosyVoice (github, 失败回退 gitee) ==="
if(-not (Test-Path $cosyvoiceRepo)) {
  try {
    git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git $cosyvoiceRepo 2>&1 | ForEach-Object {Log $_}
  } catch {
    Log "github 克隆失败, 回退 gitee 镜像"
    git clone --recursive https://gitee.com/FunAudioLLM/CosyVoice.git $cosyvoiceRepo 2>&1 | ForEach-Object {Log $_}
  }
} else { Log "CosyVoice 已存在, 跳过克隆" }
# 确保子模块(Matcha-TTS)拉全
git -C $cosyvoiceRepo submodule update --init --recursive 2>&1 | ForEach-Object {Log $_}

Log "=== [2] pip install requirements + cosyvoice(-e) ==="
& $py -m pip install -r "$cosyvoiceRepo\requirements.txt" -i $pipMirror $trusted 2>&1 | ForEach-Object {Log $_}
& $py -m pip install -e "$cosyvoiceRepo" -i $pipMirror $trusted 2>&1 | ForEach-Object {Log $_}

Log "=== [3] git-lfs + 模型权重 (modelscope 国内源) ==="
git lfs install 2>&1 | ForEach-Object {Log $_}
if(-not (Test-Path $modelDir)) {
  git clone https://www.modelscope.cn/iic/CosyVoice2-0.5B.git $modelDir 2>&1 | ForEach-Object {Log $_}
  # 若 git-lfs 未自动拉大文件, 补拉一次
  git -C $modelDir lfs pull 2>&1 | ForEach-Object {Log $_}
} else { Log "模型目录已存在, 跳过下载" }

Log "=== [4] 验证 cosyvoice 可导入 ==="
& $py -c "from cosyvoice.cli.cosyvoice import CosyVoice2; print('cosyvoice import OK')" 2>&1 | ForEach-Object {Log $_}
Log "=== PHASE2_COMPLETE ==="
