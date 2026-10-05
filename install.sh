#!/usr/bin/env bash
# Linux installer: a venv in .venv with everything the app needs.  ./install.sh --qwen  adds Qwen3-ASR.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for v in python3.12 python3.11 python3.13 python3.10 python3; do
    if command -v "$v" >/dev/null && "$v" -c 'import sys; assert sys.version_info >= (3,10)' 2>/dev/null; then PY=$v; break; fi
  done
fi
[ -n "$PY" ] || { echo "Python 3.10+ is required"; exit 1; }
echo "Using $PY"
[ -d .venv ] || "$PY" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
if [ "${1:-}" = "--qwen" ]; then
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
  pip install -r requirements-qwen.txt
fi
cat <<MSG

Installed. Start with ./run.sh
Qt needs a few system libraries; on Debian/Ubuntu:  sudo apt install libxcb-cursor0 libegl1 ffmpeg
For translation install Ollama (https://ollama.com) and run:  ollama pull gemma4:12b-it-qat
MSG
