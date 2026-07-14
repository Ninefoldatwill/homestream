@echo off
REM ============================================================
REM  CosyVoice2 独立 TTS 微服务启动脚本
REM  前置: 已用 _conda.exe 创建 prefix 环境并装好 cosyvoice + 模型权重
REM  说明: Miniconda 静默安装不完整(仅 _conda.exe), 直接用其创建 prefix 环境
REM ============================================================
setlocal
REM --- 请按实际路径修改以下变量 ---
set CONDA=E:\miniconda3\_conda.exe
set PREFIX=E:\conda_envs\cosyvoice
set PROJECT_DIR=E:\九重工作室\01-工作区\澜舟工作站\03-项目空间\P0-进行中\HomeStream-开源版
set MODEL_DIR=%PROJECT_DIR%\pretrained_models\CosyVoice2-0.5B
set HOST=127.0.0.1
set PORT=50000

cd /d "%PROJECT_DIR%"
REM 直接调用 prefix 内 python (无需 activate, 规避 _conda.exe activate 在 cmd 的限制)
"%PREFIX%\python.exe" voice/cosyvoice_service.py --host %HOST% --port %PORT% --model_dir "%MODEL_DIR%"
endlocal
