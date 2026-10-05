import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from jpensubmaker import pipeline  # noqa: E402
from jpensubmaker.settings import Settings  # noqa: E402
from tests.test_pipeline import FakeEngine  # noqa: E402


@pytest.fixture
def qapp(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    from PySide6.QtWidgets import QApplication
    from jpensubmaker.gui import theme
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.stylesheet())
    return app


def wait(app, cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_drop_file_runs_job(qapp, video, fake_llm, monkeypatch, tmp_path):
    url, _ = fake_llm
    eng = FakeEngine()
    monkeypatch.setattr(pipeline.models, "ensure_whisper", lambda m, *a, **k: m)
    monkeypatch.setattr(pipeline.ENGINES, "get", lambda *a, **k: eng)
    monkeypatch.setattr(pipeline.ENGINES, "release", lambda: None)
    from jpensubmaker.gui.app import MainWindow
    w = MainWindow(Settings(translator="ollama", llm_url=url, llm_model="fake"))
    try:
        w.add_sources([str(video), str(tmp_path / "notes.txt")])
        assert len(w.jobs) == 1
        job = w.jobs[0]
        assert wait(qapp, lambda: job.status == "done"), job.error
        assert (tmp_path / "clip.en.srt").exists()
        card = w.cards[id(job)]
        assert card.b_edit.isVisibleTo(card) and "2 lines" in card.status.text()
    finally:
        w.queue.stop()
        w.overlay.close()


def test_bindings_keep_pages_in_sync(qapp):
    from jpensubmaker.gui.app import MainWindow
    w = MainWindow(Settings())
    try:
        w.queue.stop()
        cp = w.create_page
        i = cp.tr.findData("whisper")
        cp.tr.setCurrentIndex(i)
        assert w.settings.translator == "whisper"
        assert not cp.llm.isVisibleTo(cp)                  # LLM picker hides for Whisper translate
        w.bind.set("sub_mode", "bilingual")
        assert cp.mode.currentData() == "bilingual"
        w.bind.set("overlay_click_through", True)
        assert w.overlay.click_through
    finally:
        w.overlay.close()
