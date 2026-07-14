$ErrorActionPreference='Continue'
$log='E:\cosyvoice_install.log'
$CV='E:\九重工作室\01-工作区\澜舟工作站\03-项目空间\P0-进行中\HomeStream-开源版'
$env='E:\conda_envs\cosyvoice'
$py="$env\python.exe"
function Log($m){ Add-Content -Path $log -Value "$(Get-Date) $m" }

Log "=== phase3 start: 装 cosyvoice 推理依赖 (env torch 已就绪) ==="

Log "=== [0] 关键: openai-whisper 构建需 setuptools<81 (含 pkg_resources) ==="
& $py -m pip install --no-input --disable-pip-version-check "setuptools<81" wheel 2>&1 | ForEach-Object { Log $_ }

Log "=== [1] install Cython (备用, Matcha-TTS 编译需要, 无 MSVC 时会失败但可忽略) ==="
& $py -m pip install --no-input --disable-pip-version-check Cython==3.0.11 2>&1 | ForEach-Object { Log $_ }

Log "=== [2] Matcha-TTS: 本机无 MSVC 编译器, 不 pip install -e, 改走 PYTHONPATH + 纯 Python 桩 ==="
Log "      纯 Python 桩已放在 $CV\CosyVoice\third_party\Matcha-TTS\matcha\utils\monotonic_align\core.py"
Log "      (该 Cython 扩展仅训练用, 推理不调用; 服务启动时会自动把 Matcha-TTS 目录加入 sys.path)"
# 若你机器有 MSVC, 可取消下面注释走正常编译:
# & $py -m pip install --no-input --disable-pip-version-check -e "$CV\CosyVoice\third_party\Matcha-TTS" --no-deps 2>&1 | ForEach-Object { Log $_ }

Log "=== [3] install curated inference deps (voice/cosyvoice_requirements.txt) ==="
& $py -m pip install --no-input --disable-pip-version-check -r "$CV\voice\cosyvoice_requirements.txt" 2>&1 | ForEach-Object { Log $_ }

Log "=== [4] openai-whisper: 必须用 --no-build-isolation, 复用本环境 setuptools<81 ==="
& $py -m pip install --no-input --disable-pip-version-check --no-build-isolation "openai-whisper==20231117" 2>&1 | ForEach-Object { Log $_ }

Log "=== [5] validate import cosyvoice + CosyVoice2 + whisper (Matcha-TTS 通过 PYTHONPATH) ==="
& $py -c "import sys; sys.path.insert(0, r'$CV\CosyVoice'); sys.path.insert(0, r'$CV\CosyVoice\third_party\Matcha-TTS'); import matcha; import whisper; from cosyvoice.cli.cosyvoice import CosyVoice2; print('ALL_OK matcha+cosyvoice+whisper')" 2>&1 | ForEach-Object { Log $_ }

Log "=== PHASE3_DONE ==="
