"""Cue lists → .srt / .vtt / .ass files (English, Japanese, or both stacked)."""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

from .cues import Cue


def ts_srt(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_vtt(t: float) -> str:
    return ts_srt(t).replace(",", ".")


def ts_ass(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def wrap_en(text: str, width: int = 42) -> list[str]:
    """At most two balanced lines when the text fits; longer text simply wraps."""
    text = " ".join(text.split())
    if len(text) <= width:
        return [text] if text else []
    if len(text) <= width * 2:
        # split near the middle, preferring after punctuation
        mid = len(text) // 2
        best = None
        for i, ch in enumerate(text):
            if ch == " ":
                score = abs(i - mid) - (6 if i > 0 and text[i - 1] in ",.;:!?" else 0)
                if max(i, len(text) - i - 1) <= width and (best is None or score < best[0]):
                    best = (score, i)
        if best:
            i = best[1]
            return [text[:i].strip(), text[i + 1:].strip()]
    return textwrap.wrap(text, width) or [text]


def wrap_ja(text: str, width: int = 24) -> list[str]:
    text = text.strip()
    if len(text) <= width:
        return [text] if text else []
    mid = len(text) // 2
    cut = None
    for d in range(0, mid):
        for i in (mid + d, mid - d):
            if 0 < i < len(text) and text[i - 1] in "、。！？!?…　 ":
                cut = i
                break
        if cut:
            break
    cut = cut or mid
    return [text[:cut].strip(), text[cut:].strip()]


def cue_lines(c: Cue, mode: str, width: int = 42) -> list[str]:
    en = wrap_en(c.en, width) if c.en.strip() else []
    ja = wrap_ja(c.ja) if c.ja.strip() else []
    if mode == "ja":
        return ja or en
    if mode == "bilingual":
        return en + ja if en else ja
    return en or ja                       # untranslated lines fall back to Japanese rather than vanish


def to_srt(cues: list[Cue], mode: str = "en", width: int = 42) -> str:
    out = []
    n = 0
    for c in cues:
        lines = cue_lines(c, mode, width)
        if not lines:
            continue
        n += 1
        out.append(f"{n}\n{ts_srt(c.start)} --> {ts_srt(c.end)}\n" + "\n".join(lines) + "\n")
    return "\n".join(out)


def to_vtt(cues: list[Cue], mode: str = "en", width: int = 42) -> str:
    out = ["WEBVTT\n"]
    for c in cues:
        lines = cue_lines(c, mode, width)
        if lines:
            out.append(f"{ts_vtt(c.start)} --> {ts_vtt(c.end)}\n" + "\n".join(lines) + "\n")
    return "\n".join(out)


ASS_HEADER = """[Script Info]
Title: {title}
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1920
PlayResY: 1080
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: English,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3.2,1.2,2,80,80,60,1
Style: Japanese,Noto Sans JP,46,&H00E8D7FF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2.6,1,8,80,80,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def to_ass(cues: list[Cue], mode: str = "en", title: str = "", width: int = 42) -> str:
    """English at the bottom; in bilingual mode the Japanese sits at the top of the frame, so the two never fight
    for the same space."""
    rows = [ASS_HEADER.format(title=title or "JPEN SubMaker")]
    for c in cues:
        t0, t1 = ts_ass(c.start), ts_ass(c.end)
        en = "\\N".join(_ass_escape(x) for x in wrap_en(c.en, width)) if c.en.strip() else ""
        ja = "\\N".join(_ass_escape(x) for x in wrap_ja(c.ja)) if c.ja.strip() else ""
        if mode == "ja":
            if ja or en:
                rows.append(f"Dialogue: 0,{t0},{t1},English,,0,0,0,,{ja or en}")
        elif mode == "en":
            if en or ja:
                rows.append(f"Dialogue: 0,{t0},{t1},English,,0,0,0,,{en or ja}")
        else:
            if en:
                rows.append(f"Dialogue: 0,{t0},{t1},English,,0,0,0,,{en}")
            if ja:
                rows.append(f"Dialogue: 0,{t0},{t1},Japanese,,0,0,0,,{ja}")
    return "\n".join(rows) + "\n"


def write(cues: list[Cue], base: Path, formats: list[str], mode: str = "en", width: int = 42,
          title: str = "") -> list[Path]:
    """base is the path without extension (e.g. "/videos/Episode 1"); returns the files written.
    The language tag goes in the name (Episode 1.en.srt) so players pick the track up and label it."""
    tag = {"en": "en", "ja": "ja", "bilingual": "en-ja"}.get(mode, "en")
    written = []
    for fmt in formats:
        fmt = fmt.lower().lstrip(".")
        if fmt == "srt":
            text = to_srt(cues, mode, width)
        elif fmt == "vtt":
            text = to_vtt(cues, mode, width)
        elif fmt == "ass":
            text = to_ass(cues, mode, title, width)
        else:
            continue
        path = base.with_name(f"{base.name}.{tag}.{fmt}")
        path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8 with BOM for .srt: some Windows players otherwise read Japanese as mojibake
        path.write_text(text, encoding="utf-8-sig" if fmt == "srt" else "utf-8")
        written.append(path)
    return written


_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_JA_RE = re.compile(r"[぀-ヿ一-鿿ｦ-ﾟ]")


def parse_srt(text: str) -> list[Cue]:
    """Read an .srt back (for the editor). Lines containing kana/kanji go to .ja, the rest to .en."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").lstrip("﻿")):
        lines = [ln for ln in block.strip().split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            m = _SRT_TIME.search(ln)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                body = lines[i + 1:]
                ja = "".join(x.strip() for x in body if _JA_RE.search(x))
                en = " ".join(x.strip() for x in body if not _JA_RE.search(x))
                cues.append(Cue(start, end, ja=ja, en=en))
                break
    return cues
