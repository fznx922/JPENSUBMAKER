"""Speech recognition engines. Each returns a list of Word(text, start, end) in absolute seconds.

whisper — faster-whisper (CTranslate2): Whisper large-v3 / turbo / Kotoba-Whisper or any CTranslate2 model.
          Also does Whisper's built-in Japanese → English translation (task="translate").
qwen    — Qwen3-ASR-1.7B with Qwen3-ForcedAligner-0.6B for timestamps: the lowest Japanese error rate of the open
          models in 2026 benchmarks. Optional (needs PyTorch + `pip install qwen-asr`).

Models load on first use and are freed with close(); a 12 GB card cannot hold Whisper and a 12B LLM at once, so the
pipeline closes the engine before translating.
"""
from __future__ import annotations

import gc
import re
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .media import SR

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


class Cancelled(Exception):
    pass


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0


@dataclass
class Segment:
    """A run of words the engine returned together (one Whisper segment / one Qwen chunk)."""
    words: list[Word]
    text: str
    start: float
    end: float


def speech_spans(audio: np.ndarray, max_len: float = 28.0, min_silence_ms: int = 400,
                 pad_ms: int = 200, threshold: float = 0.5) -> list[tuple[float, float]]:
    """Silero VAD (bundled with faster-whisper, runs on the CPU) → speech spans merged into chunks ≤ max_len s."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    opts = VadOptions(threshold=threshold, min_silence_duration_ms=min_silence_ms, speech_pad_ms=pad_ms,
                      max_speech_duration_s=max_len, min_speech_duration_ms=150)
    raw = [(t["start"] / SR, t["end"] / SR) for t in get_speech_timestamps(audio, opts)]
    chunks: list[list[float]] = []
    for s, e in raw:
        if chunks and e - chunks[-1][0] <= max_len and s - chunks[-1][1] < 1.5:
            chunks[-1][1] = e
        else:
            chunks.append([s, e])
    return [(s, e) for s, e in chunks]


class WhisperEngine:
    name = "whisper"

    def __init__(self, model: str = "large-v3", device: str = "cuda", compute_type: str = "float16",
                 log: Callable[[str], None] | None = None):
        self.model_id = model
        self.device = device
        self.compute_type = compute_type
        self.log = log or (lambda m: None)
        self._model = None

    @property
    def can_translate(self) -> bool:
        m = self.model_id.lower()
        return not any(x in m for x in ("turbo", "kotoba", "distil", ".en"))

    def load(self):
        if self._model is not None:
            return self._model
        from .hardware import prepare_cuda
        prepare_cuda()
        from faster_whisper import WhisperModel
        t0 = time.time()
        compute = self.compute_type
        if self.device == "cpu" and "float16" in compute:
            compute = "int8"
        self.log(f"Loading speech model {self.model_id} ({self.device}, {compute}) — first run downloads it…")
        try:
            self._model = WhisperModel(self.model_id, device=self.device, compute_type=compute)
        except (RuntimeError, ValueError) as e:
            if self.device == "cuda" and ("CUDA" in str(e) or "cuda" in str(e) or "cudnn" in str(e).lower()):
                raise RuntimeError(
                    f"Could not start the model on the GPU: {e}\n"
                    "Install the CUDA 12 libraries: pip install nvidia-cublas-cu12 \"nvidia-cudnn-cu12==9.*\" "
                    "— or switch Device to CPU in Settings.") from e
            raise
        self.log(f"Speech model ready in {time.time() - t0:.1f}s")
        return self._model

    def transcribe(self, audio: np.ndarray, task: str = "transcribe", prompt: str = "", beam_size: int = 5,
                   vad: bool = True, progress: ProgressFn | None = None,
                   cancelled: CancelFn | None = None) -> list[Segment]:
        model = self.load()
        total = len(audio) / SR
        segments, info = model.transcribe(
            audio,
            language="ja",
            task=task,
            beam_size=beam_size,
            word_timestamps=True,
            vad_filter=vad,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300, "threshold": 0.45},
            condition_on_previous_text=False,       # stops one hallucination from echoing for minutes
            initial_prompt=prompt or None,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            hallucination_silence_threshold=2.0,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        )
        out: list[Segment] = []
        for seg in segments:                         # a generator: decoding happens as we iterate
            if cancelled and cancelled():
                raise Cancelled()
            words = [Word(w.word, float(w.start), float(w.end), float(w.probability)) for w in (seg.words or [])]
            if not words and seg.text.strip():
                words = [Word(seg.text, float(seg.start), float(seg.end))]
            out.append(Segment(words, seg.text.strip(), float(seg.start), float(seg.end)))
            if progress:
                progress(min(1.0, seg.end / max(total, 1e-6)), seg.text.strip())
        return out

    def transcribe_chunk(self, audio: np.ndarray, task: str = "transcribe", prompt: str = "",
                         beam_size: int = 2) -> str:
        """Short utterance → text, for the live translator (no timestamps, no VAD: the caller already cut it)."""
        model = self.load()
        segments, _ = model.transcribe(
            audio, language="ja", task=task, beam_size=beam_size, vad_filter=False,
            condition_on_previous_text=False, without_timestamps=True, initial_prompt=prompt or None,
            no_speech_threshold=0.6, compression_ratio_threshold=2.4, log_prob_threshold=-1.0,
            temperature=[0.0, 0.4, 0.8],
        )
        parts = []
        for s in segments:
            if s.no_speech_prob > 0.7 and s.avg_logprob < -0.8:
                continue
            parts.append(s.text.strip())
        sep = " " if task == "translate" else ""
        return sep.join(p for p in parts if p).strip()

    def close(self) -> None:
        if self._model is not None:
            self._model = None
            gc.collect()


class QwenEngine:
    """Qwen3-ASR + forced aligner. Audio is cut by the VAD into ≤ 28 s chunks; each is decoded with the language
    forced to Japanese and aligned to per-token timestamps."""
    name = "qwen"
    can_translate = False

    MODEL = "Qwen/Qwen3-ASR-1.7B"
    ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"

    def __init__(self, device: str = "cuda", log: Callable[[str], None] | None = None,
                 model: str | None = None, aligner: str | None = None):
        self.device = "cuda:0" if device == "cuda" else device
        self.model_id = model or self.MODEL
        self.aligner_id = aligner or self.ALIGNER
        self.log = log or (lambda m: None)
        self._model = None

    @staticmethod
    def available() -> bool:
        try:
            import qwen_asr  # noqa: F401
            import torch  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def load(self):
        if self._model is not None:
            return self._model
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ImportError as e:
            raise RuntimeError("Qwen3-ASR is not installed. Run the optional installer "
                               "(install_qwen_windows.bat / install.sh --qwen) or pick a Whisper model.") from e
        try:
            import transformers
            transformers.logging.set_verbosity_error()
        except Exception:  # noqa: BLE001
            pass
        dtype = torch.bfloat16 if self.device != "cpu" else torch.float32
        self.log(f"Loading {self.model_id} + {self.aligner_id} — first run downloads ≈5 GB…")
        t0 = time.time()
        self._model = Qwen3ASRModel.from_pretrained(
            self.model_id, dtype=dtype, device_map=self.device, max_inference_batch_size=1, max_new_tokens=2048,
            forced_aligner=self.aligner_id, forced_aligner_kwargs=dict(dtype=dtype, device_map=self.device),
        )
        self.log(f"Qwen3-ASR ready in {time.time() - t0:.1f}s")
        return self._model

    def transcribe(self, audio: np.ndarray, task: str = "transcribe", prompt: str = "", beam_size: int = 5,
                   vad: bool = True, progress: ProgressFn | None = None,
                   cancelled: CancelFn | None = None) -> list[Segment]:
        model = self.load()
        spans = speech_spans(audio) if vad else [(i, min(i + 28.0, len(audio) / SR))
                                                  for i in np.arange(0, len(audio) / SR, 28.0)]
        out: list[Segment] = []
        total = len(audio) / SR
        for s, e in spans:
            if cancelled and cancelled():
                raise Cancelled()
            piece = audio[int(s * SR):int(e * SR)]
            if len(piece) < SR // 4:
                continue
            kwargs = dict(audio=[(piece, SR)], language=["Japanese"], return_time_stamps=True)
            if prompt:
                kwargs["context"] = [prompt]
            r = model.transcribe(**kwargs)[0]
            text = (r.text or "").strip()
            words: list[Word] = []
            for t in (getattr(r, "time_stamps", None) or []):
                tx = (t.text or "").strip()
                if tx:
                    words.append(Word(tx, s + float(t.start_time), s + float(t.end_time)))
            words = repair_timestamps(words, s, e)
            if not words and text:
                words = [Word(text, s, e)]
            # the aligner drops punctuation; put it back from the text so the cue splitter can see sentence ends
            words = _restore_punctuation(words, text)
            if words:
                out.append(Segment(words, text, words[0].start, words[-1].end))
            if progress:
                progress(min(1.0, e / max(total, 1e-6)), text)
        return out

    def transcribe_chunk(self, audio: np.ndarray, task: str = "transcribe", prompt: str = "",
                         beam_size: int = 2) -> str:
        model = self.load()
        kwargs = dict(audio=[(audio, SR)], language=["Japanese"], return_time_stamps=False)
        if prompt:
            kwargs["context"] = [prompt]
        return (model.transcribe(**kwargs)[0].text or "").strip()

    def close(self) -> None:
        if self._model is not None:
            self._model = None
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass


_PUNCT = "。、！？!?…「」『』（）()・,.~〜ー"


def _restore_punctuation(words: list[Word], text: str) -> list[Word]:
    """Walk the decoded text alongside the aligned tokens and append any punctuation the aligner skipped to the
    preceding token."""
    if not words or not text:
        return words
    out = [Word(w.text, w.start, w.end, w.prob) for w in words]
    pos = 0
    wi = 0
    while pos < len(text) and wi < len(out):
        tok = out[wi].text
        k = text.find(tok, pos)
        if k < 0:
            wi += 1
            continue
        pos = k + len(tok)
        extra = ""
        while pos < len(text) and text[pos] in _PUNCT and (wi + 1 >= len(out) or not out[wi + 1].text.startswith(text[pos])):
            extra += text[pos]
            pos += 1
        out[wi].text += extra
        wi += 1
    return out


def repair_timestamps(words: list[Word], start: float, end: float) -> list[Word]:
    """Forced aligners sometimes collapse: every following token gets the same instant. Re-spread such runs evenly
    between the last good token and the next good one (or the chunk end)."""
    if not words:
        return words
    out = list(words)
    i = 0
    n = len(out)
    while i < n:
        j = i
        while j + 1 < n and abs(out[j + 1].start - out[i].start) < 1e-3 and abs(out[j + 1].end - out[i].end) < 1e-3:
            j += 1
        if j > i or out[i].end - out[i].start > 10:
            left = out[i - 1].end if i > 0 else start
            right = out[j + 1].start if j + 1 < n else end
            if right <= left:
                right = left + 0.2 * (j - i + 1)
            step = (right - left) / (j - i + 1)
            for k in range(i, j + 1):
                t = left + (k - i) * step
                out[k] = Word(out[k].text, round(t, 3), round(t + step, 3), out[k].prob)
        i = j + 1
    return out


def make_engine(model: str, device: str = "cuda", compute_type: str = "float16",
                log: Callable[[str], None] | None = None):
    if model.lower() in ("qwen3-asr", "qwen", "qwen3"):
        return QwenEngine(device=device, log=log)
    return WhisperEngine(model, device=device, compute_type=compute_type, log=log)


_SPACE_RE = re.compile(r"\s+")


def join_words(words: list[Word], lang: str) -> str:
    if lang == "ja":
        return "".join(w.text.strip() for w in words).strip()
    return _SPACE_RE.sub(" ", "".join(w.text for w in words)).strip()
