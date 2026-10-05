import numpy as np

from jpensubmaker import realtime
from jpensubmaker.media import SR
from jpensubmaker.realtime import BLOCK, LiveTranslator, UtteranceSegmenter
from jpensubmaker.settings import Settings


def energy_vad(self, audio):
    """Stand-in for Silero: 'speech' wherever the 0.1 s block is loud."""
    spans, start = [], None
    for i in range(0, len(audio), BLOCK):
        loud = np.abs(audio[i:i + BLOCK]).mean() > 0.05
        if loud and start is None:
            start = i
        if not loud and start is not None:
            spans.append((start, i)); start = None
    if start is not None:
        spans.append((start, len(audio)))
    return spans


def tone(sec):
    t = np.arange(int(sec * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(sec):
    return np.zeros(int(sec * SR), dtype=np.float32)


def blocks(audio):
    for i in range(0, len(audio), BLOCK):
        yield audio[i:i + BLOCK]


def test_segmenter_cuts_at_pauses_and_max_len(monkeypatch):
    monkeypatch.setattr(UtteranceSegmenter, "_vad", energy_vad)
    seg = UtteranceSegmenter(max_len=4.0, silence_ms=500)
    audio = np.concatenate([silence(1), tone(1.5), silence(1), tone(9), silence(1)])
    utts = []
    for b in blocks(audio):
        utts += seg.feed(b)
    utts += seg.flush()
    lens = [round(len(u) / SR, 1) for u in utts]
    assert 1.4 <= lens[0] <= 1.8                  # the first phrase, cut at the pause
    assert len(utts) >= 3 and max(lens) <= 4.3    # the 9 s run was split at max_len
    assert abs(sum(lens[1:]) - 9.0) < 0.6


def test_live_translator_with_fake_engine(monkeypatch):
    monkeypatch.setattr(UtteranceSegmenter, "_vad", energy_vad)

    class Eng:
        can_translate = True
        def load(self): pass
        def close(self): pass
        def transcribe_chunk(self, audio, task, prompt="", beam_size=2):
            return "Hello there." if task == "translate" else "こんにちは"

    monkeypatch.setattr(realtime, "make_engine", lambda *a, **k: Eng())
    from jpensubmaker import pipeline
    monkeypatch.setattr(pipeline.models, "ensure_whisper", lambda m, *a, **k: m)
    audio = np.concatenate([silence(0.5), tone(1.2), silence(1.0), tone(1.0), silence(1.0)])
    it = blocks(audio)
    caps, errors = [], []
    s = Settings(live_translator="whisper", overlay_show_japanese=True)
    lt = LiveTranslator(s, on_caption=caps.append, on_error=errors.append, audio_feed=lambda: next(it, None))
    lt.start()
    for t in lt._threads:
        t.join(timeout=10)
    assert not errors
    assert [(c.ja, c.en) for c in caps] == [("こんにちは", "Hello there.")] * 2
