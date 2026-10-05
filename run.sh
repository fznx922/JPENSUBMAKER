#!/usr/bin/env bash
cd "$(dirname "$0")"
. .venv/bin/activate
# the pip-installed CUDA libraries (cuBLAS, cuDNN) for CTranslate2
NV=$(python - <<'PY'
import glob, os, site
dirs = []
for sp in site.getsitepackages():
    dirs += glob.glob(os.path.join(sp, "nvidia", "*", "lib"))
print(":".join(dirs))
PY
)
export LD_LIBRARY_PATH="${NV}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec python -m jpensubmaker "$@"
