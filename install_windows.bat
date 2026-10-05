@echo off
setlocal
cd /d "%~dp0"

set "PY="
for %%V in (3.12 3.11 3.13 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10 - 3.12 is required. Get it from https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" during setup, then run this again.
  pause
  exit /b 1
)

echo Using %PY%
if not exist .venv (
  %PY% -m venv .venv || (pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation failed - see the messages above.
  pause
  exit /b 1
)
echo.
echo ============================================================
echo  Installed. Start the app with run_windows.bat
echo.
echo  For the best translations install Ollama from https://ollama.com
echo  then open a terminal and run:   ollama pull gemma4:12b-it-qat
echo  (for live LLM translation also:  ollama pull qwen3:4b)
echo.
echo  Optional, best Japanese accuracy: install_qwen_windows.bat
echo ============================================================
pause
