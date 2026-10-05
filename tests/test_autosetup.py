"""The first-run downloads: speech model from (a fake) Hugging Face, Ollama runtime from (a fake) GitHub release."""
import io
import json
import socket
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jpensubmaker import fetch, models, ollama_manager, paths

FILES = {"config.json": b'{"a": 1}', "tokenizer.json": b"{}", "vocabulary.json": b"[]",
         "model.bin": bytes(range(256)) * 12000, "README.md": b"ignored"}


class FakeHub(BaseHTTPRequestHandler):
    hits = []
    cut_once = True          # first model.bin request is cut short → exercises resume

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).hits.append((self.path, self.headers.get("Range")))
        if self.path.startswith("/api/models/"):
            sib = [{"rfilename": n, "size": len(b)} for n, b in FILES.items()]
            data = json.dumps({"siblings": sib}).encode()
            return self._send(200, data)
        if "/resolve/main/" in self.path:
            name = self.path.rsplit("/", 1)[1]
            body = FILES[name]
            rng = self.headers.get("Range")
            start = int(rng.split("=")[1].split("-")[0]) if rng else 0
            part = body[start:]
            if name == "model.bin" and type(self).cut_once and not rng:
                type(self).cut_once = False
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(part[:1_500_000])          # then drop the connection
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            return self._send(206 if rng else 200, part)
        if self.path.endswith(".zip"):
            return self._send(200, type(self).zip_bytes)
        self._send(404, b"")

    def _send(self, code, data):
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setenv("JPENSUB_DATA", str(tmp_path / "data"))
    FakeHub.hits = []
    FakeHub.cut_once = True
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("HF_ENDPOINT", url)
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    yield url
    srv.shutdown()


def test_whisper_download_resumes_and_is_cached(hub):
    seen = []
    path = models.ensure_whisper("large-v3", progress=lambda f, m: seen.append(f))
    assert path.endswith("Systran--faster-whisper-large-v3")
    for name, body in FILES.items():
        f = paths.models_dir() / "whisper" / "Systran--faster-whisper-large-v3" / name
        assert (f.read_bytes() == body) if name != "README.md" else not f.exists()
    resumed = [r for p, r in FakeHub.hits if p.endswith("model.bin") and r]
    assert resumed and resumed[0] != "bytes=0-"                  # continued where the dropped connection stopped
    assert seen[-1] == 1.0
    n = len(FakeHub.hits)
    assert models.ensure_whisper("large-v3") == path and len(FakeHub.hits) == n      # no network the second time
    assert models.is_downloaded("large-v3")


FAKE_OLLAMA = r'''#!{py}
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
host, port = os.environ["OLLAMA_HOST"].split(":")
open(os.path.join(os.path.dirname(__file__), "env.json"), "w").write(json.dumps({{"models": os.environ.get("OLLAMA_MODELS")}}))
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        d = b'{{"version": "fake"}}'
        self.send_response(200); self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
HTTPServer((host, int(port)), H).serve_forever()
'''


@pytest.mark.skipif(sys.platform == "win32", reason="uses a shebang script as the fake ollama binary")
def test_private_ollama_is_installed_and_started(hub, monkeypatch, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("bin/ollama")
        info.external_attr = 0o755 << 16
        z.writestr(info, FAKE_OLLAMA.format(py=sys.executable))
    FakeHub.zip_bytes = buf.getvalue()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(ollama_manager, "RELEASE", hub + "/rel/")
    monkeypatch.setattr(ollama_manager, "runtime_asset", lambda: "ollama-test.zip")
    monkeypatch.setattr(ollama_manager, "PRIVATE_HOST", f"127.0.0.1:{port}")
    monkeypatch.setattr(ollama_manager, "PRIVATE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(ollama_manager, "system_ollama", lambda: None)
    msgs = []
    try:
        url = ollama_manager.ensure_server("http://127.0.0.1:9", auto=True, progress=lambda f, m: msgs.append(m))
        assert url == f"http://127.0.0.1:{port}"
        assert ollama_manager.reachable(url)
        env = json.loads((paths.ollama_dir() / "runtime" / "bin" / "env.json").read_text())
        assert env["models"] == str(paths.ollama_dir() / "models")
        assert any("Downloading the translation engine" in m for m in msgs)
        assert not (paths.ollama_dir() / "ollama-test.zip").exists()          # archive removed after unpacking
    finally:
        ollama_manager.stop()
    assert not ollama_manager.reachable(f"http://127.0.0.1:{port}", 0.5)


def test_remote_url_is_never_auto_started(monkeypatch):
    from jpensubmaker.translate import TranslatorError
    with pytest.raises(TranslatorError):
        ollama_manager.ensure_server("http://10.255.255.1:11434", auto=True)
