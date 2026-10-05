"""Resumable HTTP downloads with progress — used for speech models and the Ollama runtime."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

import requests

from .asr import Cancelled

ProgressFn = Callable[[int, int], None]          # (bytes done, bytes total — 0 if unknown)


class FetchError(RuntimeError):
    pass


def download(url: str, dest: Path, progress: ProgressFn | None = None, cancelled: Callable[[], bool] | None = None,
             session: requests.Session | None = None, expected_size: int | None = None, retries: int = 4) -> Path:
    """Download url → dest via dest.part, resuming after a dropped connection or an app restart."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
        return dest
    part = dest.with_name(dest.name + ".part")
    sess = session or requests.Session()
    attempt = 0
    while True:
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with sess.get(url, headers=headers, stream=True, timeout=(15, 60), allow_redirects=True) as r:
                if r.status_code == 416:                       # already complete
                    break
                if r.status_code not in (200, 206):
                    raise FetchError(f"HTTP {r.status_code} for {url}")
                if r.status_code == 200 and have:
                    have = 0                                   # server ignored the range: start over
                total = expected_size or 0
                if not total:
                    cl = int(r.headers.get("Content-Length") or 0)
                    total = cl + have if cl else 0
                mode = "ab" if have else "wb"
                done = have
                last = 0.0
                with open(part, mode) as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if cancelled and cancelled():
                            raise Cancelled()
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if progress and now - last > 0.2:
                            last = now
                            progress(done, total)
                if progress:
                    progress(done, total)
            break
        except (requests.RequestException, FetchError) as e:
            attempt += 1
            if attempt > retries:
                raise FetchError(f"Download failed: {e}") from e
            time.sleep(min(30, 2 ** attempt))
    if expected_size is not None and part.stat().st_size != expected_size:
        size = part.stat().st_size
        part.unlink(missing_ok=True)
        raise FetchError(f"Download of {dest.name} is incomplete ({size} of {expected_size} bytes) — try again")
    os.replace(part, dest)
    return dest


def human(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB"
