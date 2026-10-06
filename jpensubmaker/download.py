"""Links → local media via yt-dlp (YouTube, Dailymotion, OK.ru, Niconico, Bilibili, TVer, Twitter/X and ~1800 more
sites)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

URL_RE = re.compile(r"^https?://\S+$", re.I)
# "ok.ru/video/123", "www.youtube.com/watch?v=…" — a link pasted without its scheme
BARE_URL_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*$", re.I)

# Browsers yt-dlp can borrow sign-in cookies from (for videos that need an account: some OK.ru, age-gated YouTube…)
COOKIE_BROWSERS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium"]


class DownloadError(RuntimeError):
    pass


@dataclass
class Downloaded:
    path: Path
    title: str
    url: str


def is_url(text: str) -> bool:
    return bool(URL_RE.match(text.strip()))


def normalize_url(text: str) -> str | None:
    """A link as typed or pasted → a full https URL, or None if it is not a link."""
    t = text.strip().strip("<>\"'")
    if URL_RE.match(t):
        return t
    if BARE_URL_RE.match(t) and not t.lower().endswith(tuple(f".{x}" for x in ("mp4", "mkv", "avi", "mov"))):
        return "https://" + t
    return None


def friendly_error(msg: str, url: str = "") -> str:
    """yt-dlp's messages → what to do about them."""
    m = msg.replace("ERROR: ", "").strip()
    low = m.lower()
    if any(k in low for k in ("login required", "log in", "sign in", "--cookies", "authentication", "members-only",
                              "private video", "age-restricted", "confirm your age")):
        return ("This video needs a signed-in account. Sign in on the site in your browser, then choose that browser "
                "under Settings → Links → “Use sign-in from”. (" + m[:160] + ")")
    if "not available in your country" in low or "geo" in low and "restrict" in low:
        return "This video is blocked in your country by the site. (" + m[:160] + ")"
    if "video has not been found" in low or "has been removed" in low or "not found" in low and "404" in low:
        return "The site says this video no longer exists or was removed. (" + m[:160] + ")"
    if "could not copy" in low and "cookie" in low or "failed to decrypt" in low and "cookie" in low:
        return ("Could not read that browser's cookies (close the browser and retry, or use Firefox, or export a "
                "cookies.txt file and pick it under Settings → Links). (" + m[:160] + ")")
    if "unsupported url" in low:
        return "This site or page type is not supported for downloading. (" + m[:160] + ")"
    return m


def safe_name(title: str, limit: int = 120) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", title).strip().rstrip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:limit] or "video"


def fetch(url: str, out_dir: str | Path, keep_video: bool = True,
          progress: Callable[[float, str], None] | None = None,
          cancelled: Callable[[], bool] | None = None,
          cookies_browser: str = "", cookies_file: str = "") -> Downloaded:
    """Download one link. keep_video=True fetches a ≤1080p video (mp4/mkv) so the subtitles have something to sit
    beside; False fetches the best audio only, which is all the ASR needs and much smaller.
    cookies_browser / cookies_file let yt-dlp use the user's sign-in for videos that need an account."""
    from .asr import Cancelled
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
            raise Cancelled()
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
    if cookies_file.strip():
        if not Path(cookies_file).is_file():
            raise DownloadError(f"Cookies file not found: {cookies_file}")
        opts["cookiefile"] = cookies_file
    elif cookies_browser.strip():
        opts["cookiesfrombrowser"] = (cookies_browser.strip().lower(),)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise DownloadError("nothing to download")
            if "entries" in info:          # a playlist slipped through: take the first entry
                info = next(e for e in info["entries"] if e)
            path = result.get("file") or ydl.prepare_filename(info)
    except (DownloadError, Cancelled):
        raise
    except Exception as e:  # noqa: BLE001 — yt-dlp raises many types
        if isinstance(e.__context__, Cancelled) or "Cancelled" in type(e).__name__:
            raise Cancelled() from e
        raise DownloadError(friendly_error(str(e), url)) from e
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
