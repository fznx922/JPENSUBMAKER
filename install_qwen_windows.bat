@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Run install_windows.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
echo Installing PyTorch (CUDA 12.8) and Qwen3-ASR - about 3 GB...
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 || (pause & exit /b 1)
pip install -r requirements-qwen.txt || (pause & exit /b 1)
echo.
echo Done. Choose "Qwen3-ASR" as the speech model in the app.
pause
