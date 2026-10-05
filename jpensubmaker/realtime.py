"""Live translation of whatever the computer is playing (or a microphone).

capture thread  ── 0.1 s blocks ──▶  process thread: Silero VAD cuts utterances at pauses (or at max length),
                                     Whisper transcribes / translates each, the LLM translates if chosen,
                                     and every result goes to on_caption().

System audio is captured with WASAPI loopback on Windows and PulseAudio/PipeWire monitor sources on Linux, both
through the `soundcard` package. No virtual cable is needed.
"""
from __future__ import annotations

import collections
import queue
import sys
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .asr import make_engine
from .cues import collapse_repeats, is_hallucination
from .media import SR, resample
from .settings import Settings

BLOCK = SR // 10            # 0.1 s


@dataclass
class Caption:
    ja: str
    en: str
    final: bool             # False: Japanese shown while the translation is still being made
    t: float                # wall-clock time
    latency: float = 0.0


def _com_init() -> None:
    """soundcard talks to WASAPI through COM; a fresh Python thread has COM uninitialised."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0)      # COINIT_MULTITHREADED
        except Exception:  # noqa: BLE001
            pass


def _soundcard():
    # soundcard initialises COM itself on the thread that imports it — and treats "already initialised" as an
    # error — so only threads that come after the import get their own CoInitializeEx.
    if "soundcard" in sys.modules:
        _com_init()
    try:
        import soundcard as sc
    except Exception as e:  # noqa: BLE001 — no PulseAudio server, missing libs, …
        raise RuntimeError(f"Audio capture is unavailable: {e}") from e
    warnings.filterwarnings("ignore", message="data discontinuity in recording")
    return sc


def list_devices(source: str) -> list[tuple[str, str]]:
    """[(id, name)] of capture devices. source='loopback' → one entry per speaker/output, 'mic' → microphones."""
    try:
        sc = _soundcard()
    except RuntimeError:
        return []
    try:
        if source == "loopback":
            return [(str(m.id), m.name) for m in sc.all_microphones(include_loopback=True)
                    if getattr(m, "isloopback", False)]
        return [(str(m.id), m.name) for m in sc.all_microphones(include_loopback=False)]
    except Exception:  # noqa: BLE001
        return []


def _open_device(sc, source: str, device_id: str):
    if source == "loopback":
        if device_id:
            return sc.get_microphone(device_id, include_loopback=True)
        spk = sc.default_speaker()
        return sc.get_microphone(str(spk.id), include_loopback=True)
    if device_id:
        return sc.get_microphone(device_id)
    return sc.default_microphone()


class LiveTranslator:
    def __init__(self, s: Settings,
                 on_caption: Callable[[Caption], None],
                 on_status: Callable[[str], None] = lambda m: None,
                 on_level: Callable[[float], None] = lambda v: None,
                 on_error: Callable[[str], None] = lambda m: None,
                 audio_feed: Callable[[], np.ndarray | None] | None = None):
        self.s = s.copy()
        self.on_caption = on_caption
        self.on_status = on_status
        self.on_level = on_level
        self.on_error = on_error
        self.audio_feed = audio_feed              # tests: replaces the sound card
        self._stop = threading.Event()
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._engine = None
        self._llm = None
        self.history: collections.deque[tuple[str, str]] = collections.deque(maxlen=6)

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def start(self) -> None:
        self._stop.clear()
        self._threads = [threading.Thread(target=self._capture_loop, name="live-capture", daemon=True),
                         threading.Thread(target=self._process_loop, name="live-process", daemon=True)]
        for t in self._threads:
            t.start()

    def stop(self, wait: float = 3.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=wait)
        if self._engine is not None:
            self._engine.close()
            self._engine = None

    # ------------------------------------------------------------------ capture
    def _capture_loop(self) -> None:
        try:
            if self.audio_feed is not None:
                while not self._stop.is_set():
                    block = self.audio_feed()
                    if block is None:
                        break
                    self._push(block)
                return
            sc = _soundcard()
            dev = _open_device(sc, self.s.live_source, self.s.live_device)
            self.on_status(f"Listening to: {dev.name}")
            rate = SR
            try:
                rec = dev.recorder(samplerate=rate, blocksize=BLOCK)
                rec.__enter__()
            except Exception:  # noqa: BLE001 — device refuses 16 kHz: take 48 kHz and resample
                rate = 48000
                rec = dev.recorder(samplerate=rate, blocksize=rate // 10)
                rec.__enter__()
            try:
                while not self._stop.is_set():
                    data = rec.record(numframes=rate // 10)
                    if data.ndim == 2:
                        data = data.mean(axis=1)
                    data = data.astype(np.float32)
                    if rate != SR:
                        data = resample(data, rate, SR)
                    self._push(data)
            finally:
                rec.__exit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            self.on_error(f"Audio capture failed: {e}")
            self._stop.set()

    def _push(self, block: np.ndarray) -> None:
        self._q.put(block)
        rms = float(np.sqrt(np.mean(block ** 2))) if block.size else 0.0
        self.on_level(min(1.0, rms * 8))

    # ------------------------------------------------------------------ processing
    def _load(self) -> None:
        from .pipeline import can_whisper_translate, llm_config, prepare_asr, prepare_llm
        s = self.s
        stopped = self._stop.is_set

        def status(frac: float, msg: str) -> None:
            self.on_status(msg)

        model = s.live_asr_model
        if s.live_translator == "whisper" and not can_whisper_translate(model):
            self.on_status(f"{model} cannot translate — using large-v3 instead")
            model = "large-v3"
        url = None
        if s.live_translator in ("ollama", "openai"):
            url = prepare_llm(s, live=True, progress=status, cancelled=stopped, log=self.on_status)
        path = prepare_asr(model, progress=status, cancelled=stopped)
        self.on_status(f"Loading {model}…")
        self._engine = make_engine(path, device=s.device, compute_type=s.live_compute_type, log=self.on_status)
        self._engine.load()
        if url is not None:
            from .translate import LLMClient
            self._llm = LLMClient(llm_config(s, live=True, url=url), log=self.on_status)

    def _process_loop(self) -> None:
        try:
            self._load()
        except Exception as e:  # noqa: BLE001
            self.on_error(str(e))
            self._stop.set()
            return
        self.on_status("Live — waiting for speech…")
        seg = UtteranceSegmenter(max_len=self.s.live_max_utterance, silence_ms=self.s.live_silence_ms)
        while not self._stop.is_set():
            try:
                block = self._q.get(timeout=0.2)
            except queue.Empty:
                if self.audio_feed is not None and not self._threads[0].is_alive():
                    for utt in seg.flush():
                        self._handle(utt)
                    break
                continue
            blocks = [block]
            while not self._q.empty():                   # catch up on everything that arrived meanwhile
                blocks.append(self._q.get_nowait())
            backlog = sum(len(b) for b in blocks) / SR
            if backlog > 12 and self.audio_feed is None:
                self.on_status(f"Falling behind by {backlog:.0f}s — skipping ahead")
                blocks = blocks[-int(2 * SR / BLOCK):]
            for utt in seg.feed(np.concatenate(blocks)):
                self._handle(utt)
                if self._stop.is_set():
                    break

    def _handle(self, audio: np.ndarray) -> None:
        if len(audio) < SR * 0.4 or float(np.sqrt(np.mean(audio ** 2))) < 0.003:
            return
        t0 = time.time()
        s = self.s
        eng = self._engine
        prompt = s.context_prompt.strip()
        try:
            ja = en = ""
            if s.live_translator == "whisper":
                if s.overlay_show_japanese:
                    ja = collapse_repeats(eng.transcribe_chunk(audio, "transcribe", prompt, s.live_beam_size))
                en = eng.transcribe_chunk(audio, "translate", "", s.live_beam_size)
                if is_hallucination(en) or (ja and is_hallucination(ja)):
                    return
            else:
                ja = collapse_repeats(eng.transcribe_chunk(audio, "transcribe", prompt, s.live_beam_size))
                if is_hallucination(ja):
                    return
                if self._llm is not None:
                    self.on_caption(Caption(ja, "", False, time.time(), time.time() - t0))
                    try:
                        en = self._llm.translate_live(ja, list(self.history))
                    except Exception as e:  # noqa: BLE001 — keep listening, show the Japanese
                        self.on_status(f"Translation failed: {e}")
                        en = ""
            if ja or en:
                self.history.append((ja, en))
                self.on_caption(Caption(ja, en, True, time.time(), time.time() - t0))
        except Exception as e:  # noqa: BLE001
            self.on_status(f"Recognition error: {e}")


class UtteranceSegmenter:
    """Accumulates audio and cuts it into utterances with Silero VAD: at a pause of silence_ms, or at max_len
    seconds (at the longest internal pause if there is one)."""

    def __init__(self, max_len: float = 7.0, silence_ms: int = 500, preroll: float = 0.3, check_every: float = 0.3):
        from faster_whisper.vad import VadOptions
        self.max_len = max_len
        self.silence = silence_ms / 1000
        self.preroll = int(preroll * SR)
        self.check_every = int(check_every * SR)
        self.opts = VadOptions(threshold=0.5, min_silence_duration_ms=max(100, silence_ms // 2), speech_pad_ms=120,
                               min_speech_duration_ms=200)
        self.buf = np.zeros(0, dtype=np.float32)
        self._since_check = 0

    def _vad(self, audio: np.ndarray) -> list[tuple[int, int]]:
        from faster_whisper.vad import get_speech_timestamps
        return [(t["start"], t["end"]) for t in get_speech_timestamps(audio, self.opts)]

    def feed(self, block: np.ndarray) -> list[np.ndarray]:
        self.buf = np.concatenate([self.buf, block.astype(np.float32)])
        self._since_check += len(block)
        if self._since_check < self.check_every:
            return []
        self._since_check = 0
        out: list[np.ndarray] = []
        while True:
            utt = self._cut()
            if utt is None:
                break
            out.append(utt)
        return out

    def _cut(self) -> np.ndarray | None:
        n = len(self.buf)
        if n < SR // 2:
            return None
        spans = self._vad(self.buf)
        if not spans:
            self.buf = self.buf[-self.preroll:]                     # nothing but silence: keep a short pre-roll
            return None
        first = spans[0][0]
        length = (n - first) / SR
        # the first span followed by a long enough pause ends the utterance
        for i, (_, e) in enumerate(spans):
            nxt = spans[i + 1][0] if i + 1 < len(spans) else n
            if (nxt - e) / SR >= self.silence and (e - first) / SR <= self.max_len:
                utt = self.buf[max(0, first - self.preroll // 2):e]
                self.buf = self.buf[e:]
                return utt
        if length >= self.max_len:
            # cut at the end of the last span that leaves a usable utterance, else hard cut at max_len
            cut = None
            for _, e in spans[:-1]:
                if 1.5 <= (e - first) / SR <= self.max_len:
                    cut = e
            if cut is None:
                cut = first + int(self.max_len * SR)
            utt = self.buf[max(0, first - self.preroll // 2):cut]
            self.buf = self.buf[cut:]
            return utt
        return None

    def flush(self) -> list[np.ndarray]:
        spans = self._vad(self.buf) if len(self.buf) >= SR // 4 else []
        out = [self.buf[spans[0][0]:spans[-1][1]]] if spans else []
        self.buf = np.zeros(0, dtype=np.float32)
        return out
