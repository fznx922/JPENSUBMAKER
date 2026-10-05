"""Entry point. No arguments → the GUI. Files/links with --cli → batch mode in the terminal.

    python -m jpensubmaker                          # the app
    python -m jpensubmaker --cli video.mkv URL ...  # headless, uses the saved settings + overrides below
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
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
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args(argv)

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
    if args.context:
        s.context_prompt = args.context

    sources: list[str] = []
    for item in args.inputs:
        p = Path(item)
        if p.is_dir():
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
