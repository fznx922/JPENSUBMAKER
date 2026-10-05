"""Links → local media via yt-dlp (YouTube, Dailymotion, Niconico, Bilibili, TVer, Twitter/X and ~1800 more sites)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

URL_RE = re.compile(r"^https?://\S+$", re.I)


class DownloadError(RuntimeError):
    pass


@dataclass
class Downloaded:
    path: Path
    title: str
    url: str


def is_url(text: str) -> bool:
    return bool(URL_RE.match(text.strip()))


def safe_name(title: str, limit: int = 120) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title).strip().rstrip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:limit] or "video"


def fetch(url: str, out_dir: str | Path, keep_video: bool = True,
          progress: Callable[[float, str], None] | None = None,
          cancelled: Callable[[], bool] | None = None) -> Downloaded:
    """Download one link. keep_video=True fetches a ≤1080p video (mp4/mkv) so the subtitles have something to sit
    beside; False fetches the best audio only, which is all the ASR needs and much smaller."""
    try:
        import yt_dlp
    except ImportError as e:
        raise DownloadError("yt-dlp is not installed: pip install yt-dlp") from e
    from .media import ffmpeg_exe

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, str] = {}

    def hook(d: dict) -> None:
        if cancelled and cancelled():
            raise DownloadError("cancelled")
        if d.get("status") == "downloading" and progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            speed = d.get("speed") or 0
            frac = done / total if total else 0.0
            progress(frac, f"Downloading… {done / 1e6:.0f}/{total / 1e6:.0f} MB" + (f" · {speed / 1e6:.1f} MB/s" if speed else ""))
        elif d.get("status") == "finished":
            result["file"] = d.get("filename", "")
            if progress:
                progress(1.0, "Download finished, post-processing…")

    def pp_hook(d: dict) -> None:
        if d.get("status") == "finished":
            fp = (d.get("info_dict") or {}).get("filepath")
            if fp:
                result["file"] = fp

    if keep_video:
        fmt = "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
    else:
        fmt = "ba/b"
    opts = {
        "format": fmt,
        "outtmpl": str(out_dir / "%(title).150B [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "ffmpeg_location": ffmpeg_exe(),
        "merge_output_format": "mp4/mkv",
        "restrictfilenames": False,
        "windowsfilenames": True,
        "retries": 5,
        "concurrent_fragment_downloads": 4,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError("nothing to download")
            if "entries" in info:          # a playlist slipped through: take the first entry
                info = next(e for e in info["entries"] if e)
            path = result.get("file") or ydl.prepare_filename(info)
    except DownloadError:
        raise
    except Exception as e:  # noqa: BLE001 — yt-dlp raises many types
        raise DownloadError(str(e).replace("ERROR: ", "")) from e
    p = Path(path)
    if not p.exists():
        # the merger may have changed the extension
        cands = sorted(p.parent.glob(p.stem + ".*"), key=lambda x: x.stat().st_mtime, reverse=True)
        cands = [c for c in cands if c.suffix not in (".part", ".ytdl")]
        if not cands:
            raise DownloadError(f"download finished but the file was not found: {p.name}")
        p = cands[0]
    return Downloaded(path=p, title=info.get("title") or p.stem, url=url)


def probe_title(url: str) -> str | None:
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}) as y:
            info = y.extract_info(url, download=False, process=False)
            return info.get("title") if info else None
    except Exception:  # noqa: BLE001
        return None
