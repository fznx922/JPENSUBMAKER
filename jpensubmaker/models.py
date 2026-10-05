"""Speech models: fetched from Hugging Face into the app's own folder on first use, with progress.

faster-whisper could download them itself, but silently; doing it here gives the GUI a progress bar, resumes broken
downloads, and keeps everything under one folder the user can find (and delete).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

import requests

from . import paths
from .fetch import FetchError, download, human

# faster-whisper's short names → Hugging Face repos (mirrors faster_whisper.utils._MODELS)
try:
    from faster_whisper.utils import _MODELS as WHISPER_REPOS
except Exception:  # noqa: BLE001
    WHISPER_REPOS = {}
WHISPER_REPOS = {**{
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v2": "Systran/faster-whisper-large-v2",
    "medium": "Systran/faster-whisper-medium",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}, **WHISPER_REPOS}

WANTED = re.compile(r"^(config\.json|preprocessor_config\.json|model\.bin|tokenizer\.json|vocabulary\..+)$")
ProgressFn = Callable[[float, str], None]


def hf_endpoint() -> str:
    return os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


def repo_for(model: str) -> str:
    return WHISPER_REPOS.get(model, model)


def local_dir(model: str) -> Path:
    return paths.models_dir() / "whisper" / repo_for(model).replace("/", "--")


def is_local_path(model: str) -> bool:
    return Path(model).is_dir()


def is_downloaded(model: str) -> bool:
    if is_local_path(model):
        return True
    d = local_dir(model)
    return (d / "model.bin").exists() and (d / "config.json").exists() and (d / ".complete").exists()


def ensure_whisper(model: str, progress: ProgressFn | None = None,
                   cancelled: Callable[[], bool] | None = None) -> str:
    """Return a local folder holding the CTranslate2 model, downloading it first if needed."""
    if is_local_path(model):
        return model
    d = local_dir(model)
    if is_downloaded(model):
        return str(d)
    repo = repo_for(model)
    sess = requests.Session()
    token = os.environ.get("HF_TOKEN")
    if token:
        sess.headers["Authorization"] = f"Bearer {token}"
    try:
        r = sess.get(f"{hf_endpoint()}/api/models/{repo}", params={"blobs": "true"}, timeout=30)
    except requests.RequestException as e:
        raise FetchError(f"Cannot reach Hugging Face to download {repo}: {e.__class__.__name__}. "
                         "Check your internet connection.") from e
    if r.status_code == 404:
        raise FetchError(f"Model '{repo}' was not found on Hugging Face")
    if r.status_code >= 400:
        raise FetchError(f"Hugging Face returned {r.status_code} for {repo}")
    files = [(s["rfilename"], int(s.get("size") or 0)) for s in r.json().get("siblings", [])
             if WANTED.match(s["rfilename"])]
    if not any(f == "model.bin" for f, _ in files):
        raise FetchError(f"{repo} is not a CTranslate2 (faster-whisper) model — it has no model.bin")
    total = sum(sz for _, sz in files) or 1
    done_before = 0
    for name, size in sorted(files, key=lambda x: x[1]):          # small files first, model.bin last
        def prog(done: int, tot: int, base=done_before) -> None:
            if progress:
                progress((base + done) / total, f"Downloading speech model {human(base + done)} / {human(total)}")
        download(f"{hf_endpoint()}/{repo}/resolve/main/{name}", d / name, prog, cancelled, session=sess,
                 expected_size=size or None)
        done_before += size
    (d / ".complete").write_text(repo)
    if progress:
        progress(1.0, "Speech model ready")
    return str(d)


def whisper_size_hint(model: str) -> str:
    return {"large-v3": "3.1 GB", "large-v2": "3.1 GB", "medium": "1.5 GB", "large-v3-turbo": "1.6 GB",
            "kotoba-tech/kotoba-whisper-v2.0-faster": "1.5 GB"}.get(model, "")
