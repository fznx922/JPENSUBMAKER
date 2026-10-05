"""First-run window: downloads everything the chosen settings need, with a progress bar per item.

Nothing here is required — skipped items download on first use instead — but doing it up front means the first
video starts straight away.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from .. import hardware, models, ollama_manager, paths
from ..asr import Cancelled
from ..settings import Settings
from . import theme
from .icons import app_icon, pixmap

LLM_SIZES = {"gemma4:12b-it-qat": "7.2 GB", "qwen3:14b": "9.3 GB", "gemma3:12b": "8.1 GB", "qwen3:8b": "5.2 GB",
             "gemma4:e4b-it-qat": "6.1 GB", "qwen3:4b": "2.5 GB", "gemma3:4b": "3.3 GB"}


class _Bridge(QObject):
    update = Signal(int, float, str)       # row, fraction (-1 = busy), text
    finished = Signal(bool, str)           # ok, message


class Row(QFrame):
    def __init__(self, icon_name: str, title: str, detail: str):
        super().__init__()
        self.setObjectName("JobCard")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setFixedSize(36, 36)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background: {theme.CARD_HI}; border-radius: 10px;")
        ic.setPixmap(pixmap(icon_name, theme.ACCENT, 18))
        lay.addWidget(ic, 0, Qt.AlignTop)
        mid = QVBoxLayout()
        mid.setSpacing(5)
        top = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("JobTitle")
        top.addWidget(t, 1)
        self.size = QLabel(detail)
        self.size.setObjectName("Faint")
        top.addWidget(self.size)
        mid.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        mid.addWidget(self.bar)
        self.status = QLabel("Waiting")
        self.status.setObjectName("Faint")
        mid.addWidget(self.status)
        lay.addLayout(mid, 1)

    def set(self, frac: float, text: str) -> None:
        if frac < 0:
            self.bar.setRange(0, 0)                 # busy indicator
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
        if text:
            self.status.setText(text)


class SetupDialog(QDialog):
    def __init__(self, s: Settings, parent=None, first_run: bool = True):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Welcome to JPEN SubMaker" if first_run else "Download models")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(640)
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.bridge = _Bridge()
        self.bridge.update.connect(self._update)
        self.bridge.finished.connect(self._finished)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 22)
        lay.setSpacing(14)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(52, 52))
        head.addWidget(logo)
        ht = QVBoxLayout()
        ht.setSpacing(2)
        t = QLabel("Welcome to JPEN SubMaker" if first_run else "Download models")
        t.setObjectName("PageTitle")
        ht.addWidget(t)
        sub = QLabel(f"{hardware.summary()} — settings were chosen for this card. "
                     "One more step: the AI models (a one-time download).")
        sub.setObjectName("PageSub")
        sub.setWordWrap(True)
        ht.addWidget(sub)
        head.addLayout(ht, 1)
        lay.addLayout(head)

        self.rows: list[tuple[Row, str]] = []
        asr = s.effective_asr_model
        asr_note = "ready" if models.is_downloaded(asr) else models.whisper_size_hint(asr)
        if asr.lower() != "qwen3-asr":
            self._add_row("subs", "Speech recognition", f"{asr}  ·  {asr_note}", "asr")
        if s.translator == "ollama" or s.live_translator == "ollama":
            found = ollama_manager.reachable(s.llm_url) or ollama_manager.system_ollama() or \
                ollama_manager.private_exe().exists()
            self._add_row("settings", "Translation engine (Ollama)", "ready" if found else "≈1.5 GB", "ollama")
            wanted = []
            if s.translator == "ollama":
                wanted.append(s.llm_model)
            if s.live_translator == "ollama" and s.live_llm_model not in wanted:
                wanted.append(s.live_llm_model)
            for m in wanted:
                self._add_row("live", "Translation model", f"{m}  ·  {LLM_SIZES.get(m, '')}", "llm:" + m)

        note = QLabel(f"Everything is stored in {paths.data_dir()}. You can skip this — models then download "
                      "the first time they are needed.")
        note.setObjectName("Faint")
        note.setWordWrap(True)
        lay.addWidget(note)

        btns = QHBoxLayout()
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        btns.addWidget(self.msg, 1)
        self.b_later = QPushButton("Later")
        self.b_later.clicked.connect(self._later)
        self.b_go = QPushButton("Download now")
        self.b_go.setObjectName("Primary")
        self.b_go.clicked.connect(self._go)
        btns.addWidget(self.b_later)
        btns.addWidget(self.b_go)
        lay.addLayout(btns)
        self.setStyleSheet(f"QDialog {{ background: {theme.BG}; }}")
        if not self.rows:
            self.msg.setText("Nothing to download for the current settings.")
            self.b_go.setText("Close")

    def _add_row(self, icon_name: str, title: str, detail: str, key: str) -> None:
        r = Row(icon_name, title, detail)
        self.layout().insertWidget(len(self.rows) + 1, r)
        self.rows.append((r, key))

    # ---------------------------------------------------------------- actions
    def _later(self) -> None:
        self._cancel.set()
        self.reject()

    def _go(self) -> None:
        if self._thread is None and self.rows:
            self.b_go.setEnabled(False)
            self.b_go.setText("Downloading…")
            self.b_later.setText("Cancel")
            self._thread = threading.Thread(target=self._work, daemon=True)
            self._thread.start()
        elif self._thread is None or not self._thread.is_alive():
            self.accept()

    def closeEvent(self, e):
        self._cancel.set()
        super().closeEvent(e)

    def _update(self, i: int, frac: float, text: str) -> None:
        self.rows[i][0].set(frac, text)

    def _finished(self, ok: bool, message: str) -> None:
        self.msg.setText(f'<span style="color:{theme.OK if ok else theme.ERR}">{message}</span>')
        self.b_go.setEnabled(True)
        self.b_go.setText("Start" if ok else "Try again")
        self.b_later.setText("Close")
        if not ok:
            self._thread = None
            self._cancel.clear()

    # ---------------------------------------------------------------- the download thread
    def _work(self) -> None:
        s = self.s
        url = s.llm_url
        cancelled = self._cancel.is_set
        try:
            for i, (row, key) in enumerate(self.rows):
                def prog(frac: float, text: str, i=i) -> None:
                    self.bridge.update.emit(i, frac, text)
                prog(-1, "Starting…")
                if key == "asr":
                    models.ensure_whisper(s.effective_asr_model, prog, cancelled)
                    prog(1.0, "Ready")
                elif key == "ollama":
                    url = ollama_manager.ensure_server(s.llm_url, s.ollama_auto, prog, cancelled)
                    prog(1.0, "Running")
                elif key.startswith("llm:"):
                    ollama_manager.ensure_model(url, key[4:], prog, cancelled)
                    prog(1.0, "Ready")
        except Cancelled:
            self.bridge.finished.emit(False, "Cancelled — the rest will download when first needed.")
            return
        except Exception as e:  # noqa: BLE001
            self.bridge.finished.emit(False, str(e))
            return
        self.bridge.finished.emit(True, "All set! Drop a video in to begin.")
