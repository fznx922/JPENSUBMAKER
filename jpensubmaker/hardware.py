"""GPU discovery and CUDA library setup.

faster-whisper (CTranslate2) needs cuBLAS and cuDNN 9 for CUDA 12. The installers pull them in as the pip packages
nvidia-cublas-cu12 / nvidia-cudnn-cu12; on Windows their DLLs live in site-packages/nvidia/*/bin, which is not on
the DLL search path, so prepare_cuda() adds those folders before any model is loaded.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_prepared = False


def prepare_cuda() -> None:
    global _prepared
    if _prepared:
        return
    _prepared = True
    dirs: list[Path] = []
    roots = list(sys.path)
    if getattr(sys, "_MEIPASS", None):           # inside the packaged .exe
        roots.insert(0, sys._MEIPASS)
    for p in map(Path, roots):
        nv = p / "nvidia"
        if nv.is_dir():
            for sub in nv.iterdir():
                for leaf in ("bin", "lib"):
                    d = sub / leaf
                    if d.is_dir():
                        dirs.append(d)
    if not dirs:
        return
    if sys.platform == "win32":
        for d in dirs:
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass
        os.environ["PATH"] = os.pathsep.join([str(d) for d in dirs] + [os.environ.get("PATH", "")])
    # On Linux, CTranslate2 finds the pip-installed libraries itself when torch is absent only if they are on the
    # loader path; preloading them by absolute path is the reliable way.
    else:
        import ctypes
        for d in dirs:
            for pattern in ("libcublasLt.so.12", "libcublas.so.12", "libcudnn*.so.9"):
                for f in sorted(d.glob(pattern)):
                    try:
                        ctypes.CDLL(str(f), mode=ctypes.RTLD_GLOBAL)
                    except OSError:
                        pass


@dataclass
class GPU:
    name: str
    vram_mb: int

    @property
    def vram_gb(self) -> float:
        return self.vram_mb / 1024


def detect_gpus() -> list[GPU]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        try:
            name, mem = [x.strip() for x in line.rsplit(",", 1)]
            gpus.append(GPU(name, int(float(mem))))
        except ValueError:
            continue
    return gpus


def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 — missing libraries show up as any kind of error
        return False


def summary() -> str:
    gpus = detect_gpus()
    if not gpus:
        return "No NVIDIA GPU detected — running on CPU will be slow"
    g = gpus[0]
    return f"{g.name} · {g.vram_gb:.0f} GB VRAM"
