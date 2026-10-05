"""One job: a file or a link → Japanese transcript → English → subtitle files.

VRAM plan for a 12 GB card: the speech model (≈2–5 GB) runs first and is unloaded, then the LLM (≈7–10 GB) gets the
card to itself, then it is unloaded so the next file's speech model fits. With vram_saver off both stay loaded.
"""
from __future__ import annotations

import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import download, media, models, ollama_manager, subtitles
from .asr import Cancelled, make_engine
from .cues import Cue, build_cues
from .settings import Settings
from .translate import LLMClient, LLMConfig, TranslatorError

ProgressFn = Callable[[float, str], None]


@dataclass
class Job:
    source: str                         # a file path or a URL
    title: str = ""
    status: str = "queued"              # queued | running | done | failed | cancelled
    progress: float = 0.0
    stage: str = ""
    error: str = ""
    outputs: list[Path] = field(default_factory=list)
    media_path: Path | None = None
    cues: list[Cue] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def is_url(self) -> bool:
        return download.is_url(self.source)

    @property
    def display_name(self) -> str:
        if self.title:
            return self.title
        return self.source if self.is_url else Path(self.source).name


class EngineCache:
    """Keeps the speech model between jobs when VRAM allows it."""

    def __init__(self):
        self._engine = None
        self._key = None

    def get(self, model: str, device: str, compute: str, log: Callable[[str], None]):
        key = (model, device, compute)
        if self._engine is not None and self._key != key:
            self.release()
        if self._engine is None:
            self._engine = make_engine(model, device=device, compute_type=compute, log=log)
            self._key = key
        self._engine.log = log
        return self._engine

    def release(self) -> None:
        if self._engine is not None:
            self._engine.close()
        self._engine = None
        self._key = None


ENGINES = EngineCache()


def llm_config(s: Settings, live: bool = False, url: str | None = None) -> LLMConfig:
    backend = (s.live_translator if live else s.translator)
    if backend == "openai":
        return LLMConfig(backend="openai", url=url or s.openai_url, model=s.openai_model, api_key=s.openai_api_key,
                         temperature=s.llm_temperature, keep_honorifics=s.keep_honorifics, context=s.context_prompt,
                         glossary=s.glossary, batch=s.llm_batch)
    return LLMConfig(backend="ollama", url=url or s.llm_url, model=(s.live_llm_model if live else s.llm_model),
                     temperature=s.llm_temperature, keep_honorifics=s.keep_honorifics, context=s.context_prompt,
                     glossary=s.glossary, batch=s.llm_batch, keep_alive="30m" if live else "10m")


def run_job(job: Job, s: Settings, log: Callable[[str], None], progress: ProgressFn,
            cancelled: Callable[[], bool]) -> Job:
    t0 = time.time()
    job.status = "running"
    job.error = ""

    def stage(lo: float, hi: float, name: str) -> ProgressFn:
        def cb(frac: float, msg: str = "") -> None:
            job.progress = lo + (hi - lo) * max(0.0, min(1.0, frac))
            job.stage = name
            progress(job.progress, msg or name)
        return cb

    tmp_dir: tempfile.TemporaryDirectory | None = None
    try:
        # ---------------------------------------------------------------- 1. get the media
        if job.is_url:
            p = stage(0.0, 0.15, "Downloading")
            p(0.0, "Fetching link…")
            if s.url_keep_video:
                dest = Path(s.output_dir)
            else:
                tmp_dir = tempfile.TemporaryDirectory(prefix="jpensub_")
                dest = Path(tmp_dir.name)
            got = download.fetch(job.source, dest, keep_video=s.url_keep_video, progress=p, cancelled=cancelled)
            job.media_path = got.path
            job.title = got.title
            log(f"Downloaded: {got.path.name}")
            base_lo = 0.15
        else:
            job.media_path = Path(job.source)
            if not job.media_path.exists():
                raise FileNotFoundError(f"File not found: {job.source}")
            job.title = job.title or job.media_path.stem
            base_lo = 0.0

        if cancelled():
            raise Cancelled()
        # models: downloaded on first use; the translator is checked now, not after an hour of transcription
        p = stage(base_lo, base_lo + 0.08, "Preparing models")
        llm_url = None
        if s.translator in ("ollama", "openai") and s.sub_mode != "ja":
            llm_url = prepare_llm(s, live=False, progress=lambda f, m: p(0.5 * f, m), cancelled=cancelled, log=log)
        model = s.effective_asr_model
        need_ja = s.sub_mode in ("ja", "bilingual") or s.translator in ("ollama", "openai", "none")
        if need_ja or can_whisper_translate(model):
            model_path = prepare_asr(model, progress=lambda f, m: p(0.5 + 0.5 * f, m), cancelled=cancelled)
        else:
            model_path = model                    # a transcribe-only model in Whisper-translate mode: not needed
        base_lo += 0.08

        p = stage(base_lo, base_lo + 0.04, "Extracting audio")
        p(0.0, "Extracting audio…")
        audio = media.load_audio(job.media_path)
        dur = len(audio) / media.SR
        log(f"Audio: {dur / 60:.1f} min")
        p(1.0)

        # ---------------------------------------------------------------- 2. speech recognition
        asr_lo = base_lo + 0.04
        whisper_translate = s.translator == "whisper" and s.sub_mode != "ja"
        asr_hi = 0.95 if s.translator in ("whisper", "none") else 0.62
        prompt = s.context_prompt.strip()

        ja_cues: list[Cue] = []
        en_cues: list[Cue] = []
        passes = int(need_ja) + int(whisper_translate)
        span = (asr_hi - asr_lo) / max(1, passes)
        lo = asr_lo
        if need_ja:
            eng = ENGINES.get(model_path, s.device, s.compute_type, log)
            p = stage(lo, lo + span, "Transcribing")
            p(0.0, "Loading speech model…")
            log(f"Transcribing Japanese with {model}…")
            segs = eng.transcribe(audio, task="transcribe", prompt=prompt, beam_size=s.beam_size, vad=s.vad_filter,
                                  progress=lambda f, m: p(f, f"Transcribing… {m[:60]}"), cancelled=cancelled)
            ja_cues = build_cues(segs, "ja")
            log(f"Transcribed {len(ja_cues)} lines")
            lo += span
        if whisper_translate:
            if can_whisper_translate(model):
                eng = ENGINES.get(model_path, s.device, s.compute_type, log)
            else:
                log(f"{model} cannot translate; using Whisper large-v3 for the English pass")
                tr_path = prepare_asr("large-v3", progress=lambda f, m: p(0.0, m), cancelled=cancelled)
                eng = ENGINES.get(tr_path, s.device, s.compute_type, log)
            p = stage(lo, lo + span, "Translating (Whisper)")
            p(0.0, "Translating with Whisper…")
            segs = eng.transcribe(audio, task="translate", prompt="", beam_size=s.beam_size, vad=s.vad_filter,
                                  progress=lambda f, m: p(f, f"Translating… {m[:60]}"), cancelled=cancelled)
            en_cues = build_cues(segs, "en")
            if ja_cues:
                attach_japanese(en_cues, ja_cues)
            log(f"Whisper produced {len(en_cues)} English lines")
        del audio

        # ---------------------------------------------------------------- 3. LLM translation
        if s.translator in ("ollama", "openai") and s.sub_mode != "ja" and ja_cues:
            if s.vram_saver:
                ENGINES.release()             # give the LLM the whole card
            cfg = llm_config(s, url=llm_url)
            cfg.context = "; ".join(x for x in (job.title and f"title: {job.title}", s.context_prompt.strip()) if x)
            client = LLMClient(cfg, log=log)
            log(f"Translating {len(ja_cues)} lines with {cfg.model}…")
            p = stage(asr_hi, 0.98, "Translating")
            p(0.0, f"Translating with {cfg.model}…")
            try:
                en = client.translate_lines([c.ja for c in ja_cues],
                                            progress=lambda f, m: p(f, f"Translating… {m[:60]}"), cancelled=cancelled)
            except TranslatorError as e:
                # keep the transcription: it is the expensive part
                saved = subtitles.write(ja_cues, output_base(job, s), ["srt"], mode="ja", width=s.max_line_chars)
                job.outputs = saved
                raise TranslatorError(f"{e}\nThe Japanese transcript was saved as {saved[0].name}") from e
            for c, t in zip(ja_cues, en):
                c.en = t
            missing = sum(1 for c in ja_cues if not c.en)
            if missing:
                log(f"⚠ {missing} line(s) could not be translated and keep the Japanese text")
            if s.vram_saver:
                client.unload()                   # so the next file's speech model fits
            cues = ja_cues
        elif en_cues:
            cues = en_cues
        else:
            cues = ja_cues

        # ---------------------------------------------------------------- 4. write
        job.cues = cues
        base = output_base(job, s)
        job.outputs = subtitles.write(cues, base, s.formats or ["srt"], mode=s.sub_mode, width=s.max_line_chars,
                                      title=job.title)
        for f in job.outputs:
            log(f"Saved {f}")
        job.status = "done"
        job.progress = 1.0
        job.stage = "Done"
        progress(1.0, "Done")
    except Cancelled:
        job.status = "cancelled"
        job.stage = "Cancelled"
        log("Cancelled")
    except Exception as e:  # noqa: BLE001 — every failure is shown on the job card
        job.status = "failed"
        job.error = str(e) or e.__class__.__name__
        job.stage = "Failed"
        log(f"✖ {job.error}")
        log(traceback.format_exc(limit=6))
    finally:
        if tmp_dir is not None:
            try:
                tmp_dir.cleanup()
            except OSError:
                pass
        job.elapsed = time.time() - t0
    return job


def can_whisper_translate(model: str) -> bool:
    """Can this speech model do Whisper's built-in Japanese → English translation?"""
    m = model.lower()
    return not any(x in m for x in ("turbo", "kotoba", "distil", ".en", "qwen"))


def prepare_asr(model: str, progress: ProgressFn | None = None, cancelled: Callable[[], bool] | None = None) -> str:
    """The speech model as something the engine can load: a local folder (downloaded now if needed) or, for
    Qwen3-ASR, its name (that engine downloads its own weights)."""
    if model.lower() in ("qwen3-asr", "qwen", "qwen3"):
        return model
    return models.ensure_whisper(model, progress, cancelled)


def prepare_llm(s: Settings, live: bool = False, progress: ProgressFn | None = None,
                cancelled: Callable[[], bool] | None = None, log: Callable[[str], None] | None = None) -> str | None:
    """Make the translator usable: start or install Ollama and pull the model if needed. Returns its URL."""
    backend = s.live_translator if live else s.translator
    if backend == "openai":
        if not s.openai_model.strip():
            raise TranslatorError("Set the model name for the OpenAI-compatible server in Settings")
        return s.openai_url
    if backend != "ollama":
        return None
    model = (s.live_llm_model if live else s.llm_model).strip()
    url = ollama_manager.ensure_server(s.llm_url, s.ollama_auto,
                                       progress=(lambda f, m: progress(0.4 * f, m)) if progress else None,
                                       cancelled=cancelled)
    if log and url != s.llm_url:
        log(f"Using the app's own Ollama at {url}")
    ollama_manager.ensure_model(url, model, progress=(lambda f, m: progress(0.4 + 0.6 * f, m)) if progress else None,
                                cancelled=cancelled)
    return url


def output_base(job: Job, s: Settings) -> Path:
    mp = job.media_path or Path(job.source)
    if job.is_url:
        name = download.safe_name(job.title or mp.stem)
        if s.url_keep_video and mp.exists():
            return mp.with_suffix("")             # beside the downloaded video, same name, so players load it
        return Path(s.output_dir) / name
    if s.save_next_to_video:
        return mp.with_suffix("")
    return Path(s.output_dir) / mp.stem


def attach_japanese(en_cues: list[Cue], ja_cues: list[Cue]) -> None:
    """Give each English cue (from Whisper's translate pass) the Japanese said during it."""
    for c in en_cues:
        parts = [j.ja for j in ja_cues if c.start - 0.3 <= (j.start + j.end) / 2 <= c.end + 0.3]
        c.ja = "".join(parts)
