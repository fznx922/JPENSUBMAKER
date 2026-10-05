"""ffmpeg helpers: decode any video/audio file to 16 kHz mono float32, and probe durations.

ffmpeg is taken from PATH, else from the imageio-ffmpeg wheel (which bundles a static build — handy on Windows,
where most people have no ffmpeg installed).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

SR = 16000
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts", ".m2ts", ".flv", ".wmv", ".mpg", ".mpeg",
              ".ogv", ".3gp"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MediaError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        raise MediaError("ffmpeg not found. Install it (https://ffmpeg.org) or `pip install imageio-ffmpeg`.") from e


@lru_cache(maxsize=1)
def ffprobe_exe() -> str | None:
    exe = shutil.which("ffprobe")
    if exe:
        return exe
    cand = Path(ffmpeg_exe()).with_name("ffprobe" + Path(ffmpeg_exe()).suffix)
    return str(cand) if cand.exists() else None


def is_media(path: str | Path) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTS


def duration(path: str | Path) -> float | None:
    probe = ffprobe_exe()
    if probe:
        try:
            out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                                 capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW).stdout
            return float(json.loads(out)["format"]["duration"])
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            pass
    # ffmpeg alone: parse "Duration: 00:01:02.50" from its banner
    try:
        err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                             timeout=30, creationflags=_NO_WINDOW).stderr
    except (OSError, subprocess.SubprocessError):
        return None
    for line in err.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            t = line.split(",")[0].split("Duration:")[1].strip()
            try:
                h, m, s = t.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except ValueError:
                return None
    return None


def load_audio(path: str | Path, audio_track: int | None = None) -> np.ndarray:
    """The whole file as 16 kHz mono float32 in [-1, 1]. One hour ≈ 230 MB of RAM."""
    cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path)]
    if audio_track is not None:
        cmd += ["-map", f"0:a:{audio_track}"]
    cmd += ["-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW)
    except OSError as e:
        raise MediaError(f"could not run ffmpeg: {e}") from e
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise MediaError(f"ffmpeg could not decode {Path(path).name}: {msg[-1] if msg else 'unknown error'}")
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    if audio.size == 0:
        raise MediaError(f"{Path(path).name} has no audio track")
    return audio.copy()


def resample(audio: np.ndarray, src_rate: int, dst_rate: int = SR) -> np.ndarray:
    """Band-limited resampling good enough for speech recognition (windowed-sinc low-pass + interpolation)."""
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    if dst_rate < src_rate:
        cutoff = 0.5 * dst_rate / src_rate * 0.95
        taps = 63
        n = np.arange(taps) - (taps - 1) / 2
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        h /= h.sum()
        audio = np.convolve(audio, h, mode="same")
    t_new = np.arange(0, len(audio) * dst_rate / src_rate) * (src_rate / dst_rate)
    return np.interp(t_new, np.arange(len(audio)), audio).astype(np.float32)
