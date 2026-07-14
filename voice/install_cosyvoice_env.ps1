$ErrorActionPreference='Continue'
$log='E:\cosyvoice_env2.log'
function Log($m){ Add-Content -Path $log -Value "$(Get-Date -Format 'HH:mm:ss') $m" }
Set-Content -Path $log -Value "=== install start $(Get-Date) ==="
$conda='E:\miniconda3\_conda.exe'
$prefix='E:\conda_envs\cosyvoice'

Log "=== [1] pynini==2.1.5 (conda-forge) ==="
& $conda install --prefix $prefix -y -c conda-forge pynini==2.1.5 2>&1 | ForEach-Object { Log $_ }

Log "=== [2] pytorch 2.3.1 cu121 (pytorch/nvidia channel) ==="
& $conda install --prefix $prefix -y -c pytorch -c nvidia pytorch==2.3.1 torchaudio==2.3.1 pytorch-cuda=12.1 2>&1 | ForEach-Object { Log $_ }

Log "=== [3] verify ==="
& "$prefix\python.exe" -c "import pynini; print('pynini', pynini.__version__)" 2>&1 | ForEach-Object { Log $_ }
& "$prefix\python.exe" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())" 2>&1 | ForEach-Object { Log $_ }
Log "=== ENV_SETUP_DONE ==="
