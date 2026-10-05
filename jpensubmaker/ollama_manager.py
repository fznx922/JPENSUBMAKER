"""Makes Ollama "just work".

1. If an Ollama server already answers at the configured URL, use it.
2. Else, if Ollama is installed on this PC, start it.
3. Else download Ollama's portable build (≈1.5 GB, once) into the app's data folder and run it privately on its
   own port with its own model folder — no installer, no admin rights, stopped when the app exits.

Then ensure_model() pulls the chosen LLM with progress if it is not there yet.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from . import paths
from .asr import Cancelled
from .fetch import download, human
from .translate import TranslatorError

PRIVATE_HOST = "127.0.0.1:11435"
PRIVATE_URL = f"http://{PRIVATE_HOST}"
RELEASE = "https://github.com/ollama/ollama/releases/latest/download/"
ProgressFn = Callable[[float, str], None]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_lock = threading.Lock()
_proc: subprocess.Popen | None = None


def reachable(url: str, timeout: float = 2.0) -> bool:
    try:
        return requests.get(url.rstrip("/") + "/api/version", timeout=timeout).ok
    except requests.RequestException:
        return False


def _is_local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def system_ollama() -> str | None:
    exe = shutil.which("ollama")
    if exe:
        return exe
    if sys.platform == "win32":
        cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if cand.exists():
            return str(cand)
    return None


def private_exe() -> Path:
    d = paths.ollama_dir() / "runtime"
    return d / ("ollama.exe" if sys.platform == "win32" else "bin/ollama")


def runtime_asset() -> str:
    if sys.platform == "win32":
        return "ollama-windows-amd64.zip"
    if sys.platform.startswith("linux"):
        return "ollama-linux-amd64.tar.zst"
    raise TranslatorError("Automatic Ollama setup is only available on Windows and Linux — install it from ollama.com")


def install_runtime(progress: ProgressFn | None = None, cancelled: Callable[[], bool] | None = None) -> Path:
    exe = private_exe()
    if exe.exists():
        return exe
    asset = runtime_asset()
    root = paths.ollama_dir()
    archive = root / asset

    def prog(done: int, total: int) -> None:
        if progress:
            frac = done / total if total else 0.0
            progress(0.9 * frac, f"Downloading the translation engine {human(done)}" + (f" / {human(total)}" if total else ""))

    download(RELEASE + asset, archive, prog, cancelled)
    if progress:
        progress(0.92, "Unpacking the translation engine…")
    tmp = root / "runtime.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        if asset.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
        else:
            _extract_tar_zst(archive, tmp)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(tmp, ignore_errors=True)
        archive.unlink(missing_ok=True)                 # probably corrupt: fetch it again next time
        raise TranslatorError(f"Could not unpack Ollama: {e}") from e
    final = root / "runtime"
    shutil.rmtree(final, ignore_errors=True)
    tmp.rename(final)
    archive.unlink(missing_ok=True)
    if not exe.exists():
        raise TranslatorError(f"Ollama archive did not contain {exe.name}")
    if sys.platform != "win32":
        exe.chmod(0o755)
    if progress:
        progress(1.0, "Translation engine installed")
    return exe


def _extract_tar_zst(archive: Path, dest: Path) -> None:
    try:
        import zstandard
        with open(archive, "rb") as fh:
            with zstandard.ZstdDecompressor().stream_reader(fh) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as tf:
                    tf.extractall(dest)
        return
    except ImportError:
        pass
    tar = shutil.which("tar")
    if not tar:
        raise TranslatorError("need `tar` with zstd support (or pip install zstandard)")
    subprocess.run([tar, "--zstd", "-xf", str(archive), "-C", str(dest)], check=True)


def _start(exe: str | Path, host: str | None, models_dir: Path | None) -> None:
    global _proc
    env = dict(os.environ)
    if host:
        env["OLLAMA_HOST"] = host
    if models_dir:
        models_dir.mkdir(parents=True, exist_ok=True)
        env["OLLAMA_MODELS"] = str(models_dir)
    log = open(paths.logs_dir() / "ollama.log", "ab")
    _proc = subprocess.Popen([str(exe), "serve"], env=env, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    atexit.register(stop)


def _wait(url: str, seconds: float = 60, cancelled: Callable[[], bool] | None = None) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if cancelled and cancelled():
            raise Cancelled()
        if reachable(url, 1.0):
            return
        if _proc is not None and _proc.poll() is not None:
            raise TranslatorError(f"Ollama exited during start-up — see {paths.logs_dir() / 'ollama.log'}")
        time.sleep(0.5)
    raise TranslatorError("Ollama did not start in time")


def ensure_server(url: str, auto: bool = True, progress: ProgressFn | None = None,
                  cancelled: Callable[[], bool] | None = None) -> str:
    """Return the URL of a running Ollama server, starting or installing one if allowed."""
    with _lock:
        if reachable(url):
            return url
        if not auto or not _is_local(url):
            raise TranslatorError(f"Cannot reach Ollama at {url} — is it running?")
        if reachable(PRIVATE_URL):
            return PRIVATE_URL
        exe = system_ollama()
        if exe:
            if progress:
                progress(0.0, "Starting Ollama…")
            parsed = urlparse(url)
            _start(exe, f"{parsed.hostname}:{parsed.port or 11434}", None)
            _wait(url, cancelled=cancelled)
            return url
        exe = install_runtime(progress, cancelled)
        if progress:
            progress(0.97, "Starting the translation engine…")
        _start(exe, PRIVATE_HOST, paths.ollama_dir() / "models")
        _wait(PRIVATE_URL, cancelled=cancelled)
        return PRIVATE_URL


def current_url(url: str) -> str:
    """The URL to talk to right now: the configured one if it answers, else the private server if it runs."""
    if reachable(url, 1.0):
        return url
    if reachable(PRIVATE_URL, 1.0):
        return PRIVATE_URL
    return url


def has_model(url: str, model: str) -> bool:
    try:
        r = requests.get(url.rstrip("/") + "/api/tags", timeout=10)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
    except (requests.RequestException, ValueError, KeyError) as e:
        raise TranslatorError(f"Cannot list Ollama models: {e}") from e
    return any(n == model or n == model + ":latest" for n in names)


def ensure_model(url: str, model: str, progress: ProgressFn | None = None,
                 cancelled: Callable[[], bool] | None = None) -> None:
    if not model.strip():
        raise TranslatorError("No translation model selected")
    if has_model(url, model):
        return
    try:
        r = requests.post(url.rstrip("/") + "/api/pull", json={"model": model, "stream": True}, stream=True,
                          timeout=(15, 600))
    except requests.RequestException as e:
        raise TranslatorError(f"Could not start downloading {model}: {e}") from e
    if r.status_code >= 400:
        raise TranslatorError(f"Ollama could not pull {model}: {r.text[:200]}")
    layers: dict[str, tuple[int, int]] = {}
    last = 0.0
    with r:
        for line in r.iter_lines():
            if cancelled and cancelled():
                raise Cancelled()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                msg = ev["error"]
                if "not found" in msg or "manifest" in msg:
                    msg = f"'{model}' does not exist in the Ollama library — check the name"
                raise TranslatorError(f"Downloading {model} failed: {msg}")
            if ev.get("digest") and ev.get("total"):
                layers[ev["digest"]] = (int(ev.get("completed") or 0), int(ev["total"]))
            now = time.monotonic()
            if progress and (now - last > 0.25 or ev.get("status") == "success"):
                last = now
                done = sum(c for c, _ in layers.values())
                total = sum(t for _, t in layers.values())
                if total:
                    progress(done / total, f"Downloading {model} {human(done)} / {human(total)}")
                else:
                    progress(0.0, f"{model}: {ev.get('status', '')}")
            if ev.get("status") == "success":
                break
    if not has_model(url, model):
        raise TranslatorError(f"Download of {model} did not complete — try again")
    if progress:
        progress(1.0, f"{model} ready")


def stop() -> None:
    global _proc
    p, _proc = _proc, None
    if p is not None and p.poll() is None:
        if sys.platform == "win32":                     # take the model runner child processes down too
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                           creationflags=_NO_WINDOW)
            return
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
