"""Entry point. No arguments → the GUI. Files/links with --cli → batch mode in the terminal.

    python -m jpensubmaker                          # the app
    python -m jpensubmaker --cli video.mkv URL ...  # headless, uses the saved settings + overrides below
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def _quiet_streams() -> None:
    """The windowed .exe has no console: sys.stdout/stderr are None and any print would crash. Send them to a log."""
    if sys.stdout is None or sys.stderr is None:
        from .paths import logs_dir
        f = open(logs_dir() / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def selftest() -> int:
    """Checks that a build has everything it needs (used by CI on the packaged .exe)."""
    import json
    import traceback
    results: dict[str, str] = {}

    def check(name, fn, critical=True):
        try:
            results[name] = "ok " + str(fn() or "")
        except Exception as e:  # noqa: BLE001
            results[name] = ("FAIL " if critical else "warn ") + f"{type(e).__name__}: {e}"
            traceback.print_exc()

    def qt():
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from .gui.app import MainWindow  # noqa: F401 — imports every GUI module
        QApplication.instance() or QApplication([])
        from .gui.icons import app_icon
        return f"{len(app_icon().availableSizes())} icon sizes"

    def whisper():
        import numpy as np
        import ctranslate2
        from .hardware import prepare_cuda
        prepare_cuda()
        from faster_whisper.vad import VadOptions, get_speech_timestamps
        get_speech_timestamps(np.zeros(16000, dtype=np.float32), VadOptions())
        return f"ctranslate2 {ctranslate2.__version__}, {ctranslate2.get_cuda_device_count()} CUDA device(s)"

    def cuda_libs():
        import ctypes
        import sys as _s
        from .hardware import prepare_cuda
        prepare_cuda()
        if _s.platform != "win32":
            return "skipped (not Windows)"
        for dll in ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_cnn64_9.dll"):
            ctypes.WinDLL(dll)
        return "cuBLAS + cuDNN load"

    def ffmpeg():
        import subprocess
        from .media import ffmpeg_exe
        out = subprocess.run([ffmpeg_exe(), "-version"], capture_output=True, text=True).stdout
        return out.splitlines()[0][:40]

    def ytdlp():
        import yt_dlp
        from yt_dlp.extractor import gen_extractor_classes
        names = {ie.IE_NAME for ie in gen_extractor_classes()}
        assert {"youtube", "dailymotion", "Odnoklassniki"} <= names, "extractors missing"
        return yt_dlp.version.__version__

    def tls():
        import requests
        return requests.get("https://huggingface.co/api/models/Systran/faster-whisper-large-v3", timeout=20).status_code

    def audio():
        import threading
        from .realtime import _soundcard, list_devices
        _soundcard()                                      # raises if the capture backend cannot load
        err = []                                          # …and again from a second thread, as live mode does
        t = threading.Thread(target=lambda: err.append(None) if _soundcard() else None)
        t.start()
        t.join()
        if not err:
            raise RuntimeError("audio backend failed in a worker thread")
        return f"{len(list_devices('loopback'))} loopback / {len(list_devices('mic'))} mic devices"

    check("gui", qt)
    check("speech engine", whisper)
    check("cuda libraries", cuda_libs)
    check("ffmpeg", ffmpeg)
    check("yt-dlp", ytdlp)
    check("audio capture", audio, critical=False)
    check("https", tls, critical=False)
    print(json.dumps(results, indent=2))
    return 1 if any(v.startswith("FAIL") for v in results.values()) else 0


def probe_link(url: str) -> int:
    """Download one link with the app's own downloader and decode its audio (CI check for link sites)."""
    import tempfile
    import time
    from .download import fetch, normalize_url
    from .media import SR, load_audio
    t0 = time.time()
    with tempfile.TemporaryDirectory() as d:
        got = fetch(normalize_url(url) or url, d, keep_video=True)
        audio = load_audio(got.path)
        print(f"ok: {got.title!r} → {got.path.name} ({got.path.stat().st_size / 1e6:.1f} MB), "
              f"{len(audio) / SR:.1f} s of audio, {time.time() - t0:.1f} s")
    return 0


def main(argv: list[str] | None = None) -> int:
    _quiet_streams()
    if argv is None and "--selftest" in sys.argv[1:]:
        return selftest()
    ap = argparse.ArgumentParser(prog="jpensubmaker", description="Japanese → English subtitle generator")
    ap.add_argument("inputs", nargs="*", help="video/audio files, folders, or links")
    ap.add_argument("--cli", action="store_true", help="run without the GUI")
    ap.add_argument("--model", help="speech model (large-v3, kotoba-tech/kotoba-whisper-v2.0-faster, qwen3-asr, …)")
    ap.add_argument("--translator", choices=["ollama", "openai", "whisper", "none"])
    ap.add_argument("--llm", help="LLM model tag (e.g. gemma4:12b-it-qat)")
    ap.add_argument("--mode", choices=["en", "bilingual", "ja"], help="subtitle content")
    ap.add_argument("--format", action="append", choices=["srt", "vtt", "ass"], help="output format (repeatable)")
    ap.add_argument("--device", choices=["cuda", "cpu"])
    ap.add_argument("--out", help="output folder (default: beside each video)")
    ap.add_argument("--context", help="what the video is about, names… (helps ASR and translation)")
    ap.add_argument("--cookies-from", metavar="BROWSER", help="use this browser's sign-in for links (firefox, chrome, edge…)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--probe-link", metavar="URL", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.probe_link:
        return probe_link(args.probe_link)

    if not args.cli:
        from .gui.app import run
        return run(args.inputs)

    from .media import is_media
    from .pipeline import Job, run_job
    from .settings import Settings
    s = Settings.load()
    if args.model:
        s.asr_model, s.asr_custom_model = args.model, ""
    if args.translator:
        s.translator = args.translator
    if args.llm:
        s.llm_model = args.llm
        s.openai_model = args.llm
    if args.mode:
        s.sub_mode = args.mode
    if args.format:
        s.formats = args.format
    if args.device:
        s.device = args.device
    if args.out:
        s.output_dir, s.save_next_to_video = args.out, False
    if args.cookies_from:
        s.cookies_browser = args.cookies_from
    if args.context:
        s.context_prompt = args.context

    from .download import normalize_url
    sources: list[str] = []
    for item in args.inputs:
        p = Path(item)
        if not p.exists() and normalize_url(item):
            sources.append(normalize_url(item))
        elif p.is_dir():
            sources += [str(f) for f in sorted(p.rglob("*")) if f.is_file() and is_media(f)]
        else:
            sources.append(item)
    if not sources:
        ap.error("no inputs")

    failures = 0
    for i, src in enumerate(sources):
        job = Job(src)
        print(f"\n[{i + 1}/{len(sources)}] {job.display_name}", flush=True)
        last = [-1]

        def progress(frac: float, msg: str) -> None:
            pct = int(frac * 100)
            if pct != last[0]:
                last[0] = pct
                print(f"\r  {pct:3d}%  {msg[:70]:<70}", end="", flush=True)

        run_job(job, s, log=lambda m: print(f"\n  {m}", flush=True), progress=progress, cancelled=lambda: False)
        print()
        if job.status != "done":
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
