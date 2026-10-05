"""Words → subtitle cues: sentence-sized, readable, free of the usual Whisper hallucinations."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .asr import Segment, Word, join_words


@dataclass
class Cue:
    start: float
    end: float
    ja: str = ""
    en: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def dur(self) -> float:
        return self.end - self.start


# Phrases Whisper-family models emit over music, silence and end cards (learned from YouTube captions). A cue that
# consists of nothing but one of these is dropped.
HALLUCINATIONS = [
    r"ご視聴ありがとうございました",
    r"ご視聴いただきありがとうございます",
    r"最後までご視聴",
    r"最後までご覧いただき",
    r"字幕(は|：|:|作成|提供)",
    r"Thank you for watching",
    r"Thanks for watching",
    r"Please subscribe",
    r"Subtitles by",
    r"Amara\.org",
]
_HALLU_RE = re.compile("|".join(HALLUCINATIONS), re.I)
_ONLY_PUNCT = re.compile(r"^[\s\W_ー〜~…。、！？!?]*$")
_REPEAT_RE = re.compile(r"(.{1,12}?)\1{3,}")             # the same 1–12 chars four or more times in a row
_SENT_END = ("。", "！", "？", "!", "?", "…", "♪")
_SOFT_BREAK = ("、", ",", "，")
_EN_SENT_END = (".", "!", "?", "…")


def is_hallucination(text: str) -> bool:
    t = text.strip()
    if not t or _ONLY_PUNCT.match(t):
        return True
    m = _HALLU_RE.search(t)
    if m and len(_HALLU_RE.sub("", t).strip(" 。、！？!?.")) <= 3:
        return True
    return False


def collapse_repeats(text: str) -> str:
    """"ああああああああ" → "あああ", "そうそうそうそうそう" → "そうそうそう" — Whisper's stuck-decoder loops."""
    return _REPEAT_RE.sub(lambda m: m.group(1) * 3, text)


def build_cues(segments: list[Segment], lang: str = "ja", max_dur: float = 7.0, max_chars: int | None = None,
               gap: float = 0.6, min_dur: float = 0.7) -> list[Cue]:
    """Cut the word stream into cues at sentence ends, pauses and length limits."""
    if max_chars is None:
        max_chars = 32 if lang == "ja" else 84
    words: list[Word] = [w for s in segments for w in s.words if w.text.strip()]
    cues: list[Cue] = []
    cur: list[Word] = []

    def flush() -> None:
        if not cur:
            return
        text = collapse_repeats(join_words(cur, lang))
        if not is_hallucination(text):
            c = Cue(cur[0].start, max(cur[-1].end, cur[0].start + 0.2))
            if lang == "ja":
                c.ja = text
            else:
                c.en = text
            cues.append(c)
        cur.clear()

    def text_len(ws: list[Word]) -> int:
        return len(join_words(ws, lang))

    for w in words:
        if cur:
            pause = w.start - cur[-1].end
            dur = w.end - cur[0].start
            if pause >= gap or dur > max_dur or text_len(cur + [w]) > max_chars:
                # prefer to cut at the last soft break inside the cue rather than mid-phrase
                if pause < gap:
                    k = _last_break(cur)
                    if k is not None and k < len(cur) - 1:
                        rest = cur[k + 1:]
                        del cur[k + 1:]
                        flush()
                        cur.extend(rest)
                    else:
                        flush()
                else:
                    flush()
        cur.append(w)
        t = w.text.strip()
        ends = _SENT_END if lang == "ja" else _EN_SENT_END
        if t.endswith(ends) and (cur[-1].end - cur[0].start) >= 0.8:
            flush()
    flush()
    return tidy_timing(cues, min_dur=min_dur)


def _last_break(ws: list[Word]) -> int | None:
    for i in range(len(ws) - 1, -1, -1):
        if ws[i].text.strip().endswith(_SOFT_BREAK + _SENT_END + _EN_SENT_END):
            return i
    return None


def tidy_timing(cues: list[Cue], min_dur: float = 0.7, linger: float = 0.25, min_gap: float = 0.05,
                max_cps: float = 30.0) -> list[Cue]:
    """No overlaps, a minimum display time, a little linger after speech, and implausible ones dropped."""
    cues = sorted(cues, key=lambda c: c.start)
    out: list[Cue] = []
    for c in cues:
        text = c.ja or c.en
        if c.dur > 0 and len(text) / max(c.dur, 0.1) > max_cps and len(text) > 20:
            continue                                   # 60 characters in one second is a hallucination
        out.append(c)
    for i, c in enumerate(out):
        nxt = out[i + 1].start if i + 1 < len(out) else None
        want = max(c.end + linger, c.start + min_dur)
        if nxt is not None:
            want = min(want, nxt - min_gap)
        c.end = max(c.end if nxt is None else min(c.end, nxt - min_gap), want, c.start + 0.2)
        if nxt is not None and c.end > nxt - min_gap:
            c.end = max(c.start + 0.2, nxt - min_gap)
    return out
