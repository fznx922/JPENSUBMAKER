import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeLLM(BaseHTTPRequestHandler):
    """Speaks just enough of the Ollama and OpenAI APIs: every numbered line 'N. text' becomes 'EN(text)'."""
    requests = []
    drop_line = None          # 1-based line number to omit from batch answers (exercises the retry path)

    def log_message(self, *a):
        pass

    def _reply(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._reply({"models": [{"name": "fake:latest"}]})
        if self.path.endswith("/models"):
            return self._reply({"data": [{"id": "fake-model"}]})
        self._reply({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.path, body))
        if self.path == "/api/generate":
            return self._reply({"done": True})
        user = body["messages"][-1]["content"]
        part = user.split("Translate these lines:\n", 1)[1].split("\n\nAnswer", 1)[0]
        lines = [ln.split(". ", 1) for ln in part.splitlines() if ". " in ln]
        out = {n: f"EN({t})" for n, t in lines if not (len(lines) > 1 and str(type(self).drop_line) == n)}
        content = "<think>hmm</think>" + json.dumps(out, ensure_ascii=False)
        if self.path == "/api/chat":
            return self._reply({"message": {"role": "assistant", "content": content}})
        return self._reply({"choices": [{"message": {"content": content}}]})


@pytest.fixture
def video(tmp_path):
    """A 4-second clip with a tone and a black picture."""
    f = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=4", "-shortest", str(f)], check=True)
    return f


@pytest.fixture
def fake_llm():
    FakeLLM.requests = []
    FakeLLM.drop_line = None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", FakeLLM
    srv.shutdown()
