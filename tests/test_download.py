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
