import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from jpensubmaker import download


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def test_fetch_direct_link(video, tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(video.parent)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/{video.name}"
        seen = []
        got = download.fetch(url, tmp_path / "dl", keep_video=True, progress=lambda f, m: seen.append(f))
        assert got.path.exists() and got.path.stat().st_size > 0
        assert got.path.parent == tmp_path / "dl"
    finally:
        srv.shutdown()


def test_url_helpers():
    assert download.is_url("https://www.dailymotion.com/video/x8abc")
    assert not download.is_url("C:/videos/a.mp4")
    assert download.safe_name('a/b:c*?"d') == "a b c d"


def test_normalize_url():
    assert download.normalize_url("ok.ru/video/1484130554189") == "https://ok.ru/video/1484130554189"
    assert download.normalize_url("https://m.ok.ru/video/2361249957145") == "https://m.ok.ru/video/2361249957145"
    assert download.normalize_url("www.youtube.com/watch?v=abc") == "https://www.youtube.com/watch?v=abc"
    assert download.normalize_url("<https://ok.ru/videoembed/2932705602075>") == "https://ok.ru/videoembed/2932705602075"
    assert download.normalize_url("C:/videos/ep1.mkv") is None
    assert download.normalize_url("hello") is None


def test_okru_links_go_to_the_okru_extractor():
    from yt_dlp.extractor.odnoklassniki import OdnoklassnikiIE
    for u in ["https://ok.ru/video/1484130554189", "https://m.ok.ru/video/2361249957145",
              "https://ok.ru/videoembed/2932705602075", "https://ok.ru/live/1234567890",
              "https://ok.ru/dk?st.cmd=movieLayer&st.mvId=1484130554189"]:
        assert OdnoklassnikiIE.suitable(u), u


def test_friendly_errors():
    f = download.friendly_error
    assert "signed-in" in f("ERROR: [Odnoklassniki] 123: This video is only available for registered users. "
                            "Use --cookies-from-browser or --cookies for the authentication.")
    assert "blocked in your country" in f("ERROR: This video is not available in your country")
    assert "no longer exists" in f("ERROR: [Odnoklassniki] 1: Video has not been found")
    assert f("ERROR: something else") == "something else"


def test_cookie_options_reach_ytdlp(monkeypatch, tmp_path):
    import yt_dlp
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            raise yt_dlp.utils.DownloadError("ERROR: [Odnoklassniki] 1: This video requires login (--cookies)")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    import pytest
    with pytest.raises(download.DownloadError, match="Settings → Links"):
        download.fetch("https://ok.ru/video/1", tmp_path, cookies_browser="Firefox")
    assert seen["cookiesfrombrowser"] == ("firefox",)
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n")
    seen.clear()
    with pytest.raises(download.DownloadError):
        download.fetch("https://ok.ru/video/1", tmp_path, cookies_browser="firefox", cookies_file=str(jar))
    assert seen["cookiefile"] == str(jar) and "cookiesfrombrowser" not in seen
