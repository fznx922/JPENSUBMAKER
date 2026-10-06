"""The main window."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton,
                               QScrollArea, QSlider, QSpinBox, QStackedWidget, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, hardware
from ..download import COOKIE_BROWSERS, normalize_url
from ..media import is_media
from ..pipeline import Job
from ..realtime import Caption, list_devices
from ..settings import ASR_MODELS, LLM_SUGGESTIONS, SUB_MODES, TRANSLATORS, Settings
from . import theme
from .editor import SubtitleEditor
from .icons import app_icon, icon
from .overlay import CaptionOverlay
from .widgets import DropZone, JobCard, LevelMeter, card, label
from .workers import JobQueue, LiveController

LIVE_TRANSLATORS = {
    "whisper": "Whisper direct translate — lowest latency, no LLM",
    "ollama": "Ollama LLM — better English, ~1 s more",
    "openai": "OpenAI-compatible server",
    "none": "Japanese captions only",
}
COMPUTE_TYPES = {
    "float16": "float16 — best quality",
    "int8_float16": "int8_float16 — half the VRAM, near-identical quality",
    "int8": "int8 — CPU / very low VRAM",
}


def recommended(vram_gb: float) -> dict:
    """Defaults by card size. The file pipeline runs ASR and LLM one after the other, so each may use most of the card;
    the live mode keeps both resident."""
    if vram_gb <= 0:
        return dict(device="cpu", compute_type="int8", asr_model="medium", translator="whisper",
                    live_asr_model="medium", live_compute_type="int8", live_translator="whisper")
    if vram_gb < 7:
        return dict(device="cuda", compute_type="int8_float16", asr_model="kotoba-tech/kotoba-whisper-v2.0-faster",
                    translator="ollama", llm_model="qwen3:4b", live_asr_model="large-v3",
                    live_compute_type="int8_float16", live_translator="whisper", live_llm_model="qwen3:4b")
    if vram_gb < 10:
        return dict(device="cuda", compute_type="int8_float16", asr_model="large-v3", translator="ollama",
                    llm_model="qwen3:8b", live_asr_model="large-v3", live_compute_type="int8_float16",
                    live_translator="whisper", live_llm_model="qwen3:4b")
    if vram_gb < 20:   # RTX 3060 12 GB, 4070, 3080 12 GB, 4060 Ti 16 GB …
        return dict(device="cuda", compute_type="float16", asr_model="large-v3", translator="ollama",
                    llm_model="gemma4:12b-it-qat", live_asr_model="large-v3", live_compute_type="int8_float16",
                    live_translator="whisper", live_llm_model="qwen3:4b", vram_saver=True)
    return dict(device="cuda", compute_type="float16", asr_model="large-v3", translator="ollama",
                llm_model="qwen3:14b", live_asr_model="large-v3", live_compute_type="float16",
                live_translator="ollama", live_llm_model="qwen3:8b", vram_saver=False)


# ====================================================================== settings binding
class Bindings(QObject):
    """Two-way binding between widgets and Settings fields. Several widgets may show the same field (the quick
    options on the Create page and the Settings page); a change in one updates the others."""
    changed = Signal(str)

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self._widgets: dict[str, list[tuple[QWidget, callable]]] = {}
        self._save = QTimer(self)
        self._save.setSingleShot(True)
        self._save.setInterval(400)
        self._save.timeout.connect(self.s.save)

    def _register(self, key: str, w: QWidget, setter) -> None:
        self._widgets.setdefault(key, []).append((w, setter))
        setter(getattr(self.s, key))

    def set(self, key: str, value, source: QWidget | None = None) -> None:
        if getattr(self.s, key) == value:
            return
        setattr(self.s, key, value)
        for w, setter in self._widgets.get(key, []):
            if w is not source:
                w.blockSignals(True)
                setter(value)
                w.blockSignals(False)
        self._save.start()
        self.changed.emit(key)

    def refresh_all(self) -> None:
        for key, items in self._widgets.items():
            for w, setter in items:
                w.blockSignals(True)
                setter(getattr(self.s, key))
                w.blockSignals(False)

    # ---------------------------------------------------------------- widget factories
    def combo(self, key: str, options: dict[str, str], editable: bool = False) -> QComboBox:
        cb = QComboBox()
        cb.setEditable(editable)
        cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(14)
        for v, lab in options.items():
            cb.addItem(lab, v)

        def setter(v):
            i = cb.findData(v)
            if i >= 0:
                cb.setCurrentIndex(i)
            elif editable:
                cb.setEditText(str(v))

        if editable:
            cb.currentTextChanged.connect(lambda t: self.set(key, t.strip(), cb))
        else:
            cb.currentIndexChanged.connect(lambda i: self.set(key, cb.itemData(i), cb))
        self._register(key, cb, setter)
        return cb

    def check(self, key: str, text: str) -> QCheckBox:
        c = QCheckBox(text)
        c.toggled.connect(lambda v: self.set(key, bool(v), c))
        self._register(key, c, lambda v: c.setChecked(bool(v)))
        return c

    def spin(self, key: str, lo: int, hi: int, suffix: str = "") -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        if suffix:
            sp.setSuffix(suffix)
        sp.valueChanged.connect(lambda v: self.set(key, int(v), sp))
        self._register(key, sp, lambda v: sp.setValue(int(v)))
        return sp

    def dspin(self, key: str, lo: float, hi: float, step: float, suffix: str = "") -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(1 if step >= 0.1 else 2)
        if suffix:
            sp.setSuffix(suffix)
        sp.valueChanged.connect(lambda v: self.set(key, float(v), sp))
        self._register(key, sp, lambda v: sp.setValue(float(v)))
        return sp

    def line(self, key: str, placeholder: str = "", password: bool = False) -> QLineEdit:
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        if password:
            le.setEchoMode(QLineEdit.Password)
        le.textChanged.connect(lambda t: self.set(key, t, le))
        self._register(key, le, lambda v: le.setText(str(v)) if le.text() != str(v) else None)
        return le

    def text(self, key: str, placeholder: str = "", height: int = 80) -> QPlainTextEdit:
        te = QPlainTextEdit()
        te.setPlaceholderText(placeholder)
        te.setFixedHeight(height)
        te.textChanged.connect(lambda: self.set(key, te.toPlainText(), te))
        self._register(key, te, lambda v: te.setPlainText(str(v)) if te.toPlainText() != str(v) else None)
        return te

    def slider(self, key: str, lo: int, hi: int, value_label: QLabel | None = None, fmt: str = "{}") -> QSlider:
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)

        def setter(v):
            sl.setValue(int(v))
            if value_label:
                value_label.setText(fmt.format(int(v)))

        def on(v):
            if value_label:
                value_label.setText(fmt.format(v))
            self.set(key, int(v), sl)

        sl.valueChanged.connect(on)
        self._register(key, sl, setter)
        return sl

    def formats(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        boxes = {}
        for f in ("srt", "ass", "vtt"):
            c = QCheckBox(f.upper())
            boxes[f] = c
            lay.addWidget(c)

        def on(_):
            chosen = [f for f, c in boxes.items() if c.isChecked()] or ["srt"]
            self.set("formats", chosen, w)

        def setter(v):
            for f, c in boxes.items():
                c.blockSignals(True)
                c.setChecked(f in (v or ["srt"]))
                c.blockSignals(False)

        for c in boxes.values():
            c.toggled.connect(on)
        lay.addStretch(1)
        w.setMinimumWidth(sum(c.sizeHint().width() for c in boxes.values()) + 14 * 2)
        self._register("formats", w, setter)
        return w


def form_row(grid: QGridLayout, row: int, text: str, widget: QWidget, hint: str = "") -> None:
    lb = QLabel(text)
    lb.setObjectName("Muted")
    grid.addWidget(lb, row, 0, Qt.AlignTop | Qt.AlignLeft)
    if hint:
        box = QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(widget)
        h = QLabel(hint)
        h.setObjectName("Faint")
        h.setWordWrap(True)
        box.addWidget(h)
        grid.addLayout(box, row, 1)
    else:
        grid.addWidget(widget, row, 1)


def make_grid() -> QGridLayout:
    g = QGridLayout()
    g.setHorizontalSpacing(18)
    g.setVerticalSpacing(12)
    g.setColumnMinimumWidth(0, 170)
    g.setColumnStretch(1, 1)
    return g


def page_header(title: str, sub: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 4)
    lay.setSpacing(2)
    lay.addWidget(label(title, "PageTitle"))
    lay.addWidget(label(sub, "PageSub", wrap=True))
    return w


def scroll_page(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setWidget(inner)
    return sa


# ====================================================================== health checks
class Health(QObject):
    result = Signal(list)          # list[(level, text)]

    def check(self, s: Settings) -> None:
        threading.Thread(target=self._run, args=(s.copy(),), daemon=True).start()

    def _run(self, s: Settings) -> None:
        notes: list[tuple[str, str]] = []
        try:
            from ..media import ffmpeg_exe
            ffmpeg_exe()
        except Exception as e:  # noqa: BLE001
            notes.append(("err", str(e)))
        if s.device == "cuda":
            hardware.prepare_cuda()
            if not hardware.cuda_available():
                notes.append(("warn", "CUDA is not available to the speech engine — check the NVIDIA driver, or set "
                                      "Device to CPU in Settings (much slower)."))
        if s.translator == "ollama" and not s.ollama_auto:
            from .. import ollama_manager
            if not ollama_manager.reachable(s.llm_url):
                notes.append(("warn", f"Ollama is not reachable at {s.llm_url}. Start it, or turn on "
                                      "“Set up Ollama automatically” in Settings."))
        if s.asr_model == "qwen3-asr" and not s.asr_custom_model:
            from ..asr import QwenEngine
            if not QwenEngine.available():
                notes.append(("warn", "Qwen3-ASR is selected but not installed — run the optional Qwen installer, "
                                      "or choose a Whisper model."))
        self.result.emit(notes)


# ====================================================================== pages
class CreatePage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main
        b = main.bind
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(16)
        lay.addWidget(page_header("Create subtitles",
                                  "Drop Japanese videos or paste links — get English subtitle files beside them."))

        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        self.banner.setTextFormat(Qt.RichText)
        self.banner.setStyleSheet(f"background: #2a2213; border: 1px solid #4a3a17; border-radius: 10px; "
                                  f"padding: 10px 12px; color: {theme.WARN};")
        lay.addWidget(self.banner)

        self.drop = DropZone()
        self.drop.dropped.connect(main.add_sources)
        lay.addWidget(self.drop)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.url = QLineEdit()
        self.url.setObjectName("UrlInput")
        self.url.setPlaceholderText("Paste a YouTube, Dailymotion, OK.ru, Niconico, Bilibili … link (several at once is fine)")
        self.url.addAction(icon("link", theme.MUTED, 18), QLineEdit.LeadingPosition)
        self.url.returnPressed.connect(self._add_urls)
        row.addWidget(self.url, 1)
        add = QPushButton("  Add link")
        add.setObjectName("Primary")
        add.setIcon(icon("plus", "#ffffff", 16))
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._add_urls)
        row.addWidget(add)
        lay.addLayout(row)

        # quick options
        opts, ol = card(margins=14, spacing=10)
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8)
        g.addWidget(label("Speech model", "Faint"), 0, 0)
        g.addWidget(label("Translation", "Faint"), 0, 1)
        self.llm_label = label("LLM model", "Faint")
        g.addWidget(self.llm_label, 0, 2)
        g.addWidget(label("Subtitles", "Faint"), 0, 3)
        g.addWidget(label("Formats", "Faint"), 0, 4)
        short_asr = {k: v.split(" — ")[0] for k, v in ASR_MODELS.items()}
        short_tr = {"ollama": "Ollama LLM", "openai": "OpenAI-compatible", "whisper": "Whisper translate",
                    "none": "None (Japanese)"}
        self.asr = b.combo("asr_model", short_asr)
        self.asr.setToolTip("\n".join(ASR_MODELS.values()))
        self.tr = b.combo("translator", short_tr)
        self.tr.setToolTip("\n".join(TRANSLATORS.values()))
        self.llm = b.combo("llm_model", {m: m for m in LLM_SUGGESTIONS}, editable=True)
        self.llm.setToolTip("Any model you have pulled in Ollama")
        self.mode = b.combo("sub_mode", SUB_MODES)
        g.addWidget(self.asr, 1, 0)
        g.addWidget(self.tr, 1, 1)
        g.addWidget(self.llm, 1, 2)
        g.addWidget(self.mode, 1, 3)
        g.addWidget(b.formats(), 1, 4)
        for c in range(4):
            g.setColumnStretch(c, 1)
        ol.addLayout(g)
        lay.addWidget(opts)
        b.changed.connect(self._on_setting)
        self._on_setting("translator")

        # queue
        qh = QHBoxLayout()
        qh.addWidget(label("Queue", "SectionTitle"))
        self.count = label("", "Faint")
        qh.addWidget(self.count)
        qh.addStretch(1)
        clear = QPushButton("Clear finished")
        clear.setObjectName("Ghost")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(main.clear_finished)
        qh.addWidget(clear)
        lay.addLayout(qh)

        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 6, 0)
        self.list_lay.setSpacing(8)
        self.empty = label("Nothing queued yet. Jobs start as soon as you add them, one at a time.", "Faint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setMinimumHeight(60)
        self.list_lay.addWidget(self.empty)
        self.list_lay.addStretch(1)
        sa = scroll_page(self.list_host)
        sa.setMinimumHeight(140)
        lay.addWidget(sa, 1)

    def _on_setting(self, key: str) -> None:
        if key == "translator":
            s = self.main.settings
            show = s.translator == "ollama"
            self.llm.setVisible(show)
            self.llm_label.setVisible(show)

    def _add_urls(self) -> None:
        items = [t for t in self.url.text().split() if t.strip()]
        bad = [t for t in items if not normalize_url(t)]
        good = [normalize_url(t) for t in items if normalize_url(t)]
        if good:
            self.main.add_sources(good)
            self.url.clear()
        if bad:
            self.main.toast(f"Not a link: {bad[0]}")

    def add_card(self, cardw: JobCard) -> None:
        self.empty.setVisible(False)
        self.list_lay.insertWidget(self.list_lay.count() - 1, cardw)
        self.update_count()

    def update_count(self) -> None:
        jobs = self.main.jobs
        n = len(jobs)
        done = sum(1 for j in jobs if j.status == "done")
        self.count.setText(f"  {done}/{n} done" if n else "")
        self.empty.setVisible(n == 0)

    def show_health(self, notes: list) -> None:
        if not notes:
            self.banner.setVisible(False)
            return
        html = "<br>".join(f"{'✖' if lvl == 'err' else '⚠'} {t}" for lvl, t in notes)
        self.banner.setText(html)
        self.banner.setVisible(True)


class LivePage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main
        b = main.bind
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(16)
        outer.addWidget(scroll_page(inner))

        head = QHBoxLayout()
        head.addWidget(page_header("Live translator",
                                   "Captions for whatever is playing — streams, anime, games, calls — floating over "
                                   "your screen."), 1)
        self.go = QPushButton("  Start live captions")
        self.go.setObjectName("BigLive")
        self.go.setCursor(Qt.PointingHandCursor)
        self.go.setIcon(icon("live", "#ffffff", 20))
        self.go.clicked.connect(main.toggle_live)
        head.addWidget(self.go, 0, Qt.AlignTop)
        lay.addLayout(head)

        cols = QHBoxLayout()
        cols.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(16)
        right = QVBoxLayout()
        right.setSpacing(16)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        lay.addLayout(cols)

        # --- source
        c, cl = card()
        cl.addWidget(label("Audio source", "SectionTitle"))
        rb = QHBoxLayout()
        self.r_loop = QRadioButton("System audio (what you hear)")
        self.r_mic = QRadioButton("Microphone")
        grp = QButtonGroup(self)
        grp.addButton(self.r_loop)
        grp.addButton(self.r_mic)
        rb.addWidget(self.r_loop)
        rb.addWidget(self.r_mic)
        rb.addStretch(1)
        cl.addLayout(rb)
        self.r_loop.setChecked(main.settings.live_source != "mic")
        self.r_mic.setChecked(main.settings.live_source == "mic")
        self.r_loop.toggled.connect(lambda on: self._source("loopback" if on else "mic"))
        dr = QHBoxLayout()
        self.device = QComboBox()
        self.device.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.device.setMinimumContentsLength(14)
        self.device.currentIndexChanged.connect(
            lambda i: b.set("live_device", self.device.itemData(i) or "", self.device))
        dr.addWidget(self.device, 1)
        ref = QPushButton()
        ref.setIcon(icon("refresh", theme.TEXT, 16))
        ref.setToolTip("Refresh devices")
        ref.clicked.connect(self.load_devices)
        dr.addWidget(ref)
        cl.addLayout(dr)
        self.meter = LevelMeter()
        cl.addWidget(self.meter)
        self.status = label("Idle", "Faint", wrap=True)
        cl.addWidget(self.status)
        left.addWidget(c)

        # --- engine
        c, cl = card()
        cl.addWidget(label("Recognition & translation", "SectionTitle"))
        g = make_grid()
        g.setColumnMinimumWidth(0, 120)
        self.lmodel = b.combo("live_asr_model", {k: v for k, v in ASR_MODELS.items()})
        form_row(g, 0, "Speech model", self.lmodel)
        form_row(g, 1, "Precision", b.combo("live_compute_type", COMPUTE_TYPES))
        self.ltr = b.combo("live_translator", LIVE_TRANSLATORS)
        form_row(g, 2, "Translation", self.ltr)
        self.lllm = b.combo("live_llm_model", {m: m for m in ["qwen3:4b", "gemma4:e4b-it-qat", "qwen3:8b",
                                                               "gemma3:4b"]}, editable=True)
        self.lllm_row = QLabel("LLM model")
        self.lllm_row.setObjectName("Muted")
        g.addWidget(self.lllm_row, 3, 0)
        g.addWidget(self.lllm, 3, 1)
        form_row(g, 4, "Max phrase length", b.dspin("live_max_utterance", 2.0, 15.0, 0.5, " s"),
                 "Shorter = captions appear sooner but sentences get chopped.")
        form_row(g, 5, "Pause to end phrase", b.spin("live_silence_ms", 200, 1500, " ms"))
        cl.addLayout(g)
        tip = label("Live mode keeps the speech model and the LLM loaded together. On a 12 GB card use int8_float16 "
                    "and a small LLM (≤ 4B), or Whisper direct translate.", "Faint", wrap=True)
        cl.addWidget(tip)
        left.addWidget(c)
        left.addStretch(1)

        # --- overlay
        c, cl = card()
        cl.addWidget(label("Overlay", "SectionTitle"))
        g = make_grid()
        g.setColumnMinimumWidth(0, 120)
        fs_val = label("", "Faint")
        row = QHBoxLayout()
        row.addWidget(b.slider("overlay_font_size", 14, 64, fs_val, "{} px"), 1)
        row.addWidget(fs_val)
        w = QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        form_row(g, 0, "Text size", w)
        op_val = label("", "Faint")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(b.slider("overlay_bg_opacity", 0, 100, op_val, "{} %"), 1)
        row.addWidget(op_val)
        w = QWidget()
        w.setLayout(row)
        form_row(g, 1, "Background", w)
        form_row(g, 2, "Lines shown", b.spin("overlay_lines", 1, 6))
        g.addWidget(b.check("overlay_show_japanese", "Show the Japanese above the English"), 3, 1)
        self.ct = b.check("overlay_click_through", "Click-through (mouse passes through the captions)")
        g.addWidget(self.ct, 4, 1)
        cl.addLayout(g)
        br = QHBoxLayout()
        show = QPushButton("  Show overlay")
        show.setIcon(icon("eye", theme.TEXT, 16))
        show.clicked.connect(main.show_overlay)
        reset = QPushButton("Reset position")
        reset.clicked.connect(lambda: (main.overlay.place_default(), main.show_overlay()))
        br.addWidget(show)
        br.addWidget(reset)
        br.addStretch(1)
        cl.addLayout(br)
        cl.addWidget(label("Drag the overlay to move it, resize from its corner, double-click to re-centre. "
                           "Ctrl+Shift+L toggles click-through.", "Faint", wrap=True))
        right.addWidget(c)

        # --- transcript
        c, cl = card()
        th = QHBoxLayout()
        th.addWidget(label("Transcript", "SectionTitle"))
        th.addStretch(1)
        save = QPushButton("Save as SRT…")
        save.clicked.connect(self._save)
        clr = QPushButton("Clear")
        clr.clicked.connect(self._clear)
        th.addWidget(save)
        th.addWidget(clr)
        cl.addLayout(th)
        self.history = QListWidget()
        self.history.setWordWrap(True)
        self.history.setMinimumHeight(220)
        cl.addWidget(self.history, 1)
        right.addWidget(c, 1)

        self.records: list[tuple[float, float, str, str]] = []     # (t_start, t_end, ja, en) relative to start
        self.t0 = 0.0
        b.changed.connect(self._on_setting)
        self._on_setting("live_translator")
        self._devices_loaded = False

    def showEvent(self, e):
        if not self._devices_loaded:
            self._devices_loaded = True
            QTimer.singleShot(50, self.load_devices)
        super().showEvent(e)

    def _source(self, src: str) -> None:
        self.main.bind.set("live_source", src)
        self.main.bind.set("live_device", "")
        self.load_devices()

    def load_devices(self) -> None:
        src = self.main.settings.live_source
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem("System default" + (" output" if src == "loopback" else " microphone"), "")
        for dev_id, name in list_devices(src):
            self.device.addItem(name, dev_id)
        i = self.device.findData(self.main.settings.live_device)
        self.device.setCurrentIndex(max(0, i))
        self.device.blockSignals(False)
        if self.device.count() == 1:
            self.status.setText("No capture devices found (on Linux, PulseAudio/PipeWire is required).")

    def _on_setting(self, key: str) -> None:
        if key == "live_translator":
            show = self.main.settings.live_translator in ("ollama", "openai")
            self.lllm.setVisible(show and self.main.settings.live_translator == "ollama")
            self.lllm_row.setVisible(show and self.main.settings.live_translator == "ollama")

    def set_running(self, on: bool) -> None:
        self.go.setText("  Stop live captions" if on else "  Start live captions")
        self.go.setIcon(icon("stop" if on else "live", theme.ERR if on else "#ffffff", 20))
        self.go.setProperty("live", "true" if on else "false")
        self.go.style().unpolish(self.go)
        self.go.style().polish(self.go)
        if on:
            self.t0 = time.time()

    def add_caption(self, c: Caption) -> None:
        if not c.final:
            return
        end = c.t - self.t0
        start = max(0.0, end - max(1.5, c.latency + 1.5))
        self.records.append((start, end, c.ja, c.en))
        it = QListWidgetItem((c.en or c.ja) + (f"\n{c.ja}" if c.ja and c.en else ""))
        it.setToolTip(f"{c.latency:.1f}s after speech ended")
        self.history.addItem(it)
        if self.history.count() > 500:
            self.history.takeItem(0)
        self.history.scrollToBottom()

    def _clear(self) -> None:
        self.history.clear()
        self.records.clear()

    def _save(self) -> None:
        if not self.records:
            self.main.toast("Nothing to save yet")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save transcript", str(Path(self.main.settings.output_dir) /
                                              time.strftime("live-%Y%m%d-%H%M.srt")), "SubRip (*.srt)")
        if not path:
            return
        from ..cues import Cue
        from ..subtitles import to_srt
        cues = [Cue(s, e, ja=ja, en=en) for s, e, ja, en in self.records]
        Path(path).write_text(to_srt(cues, "bilingual"), encoding="utf-8-sig")
        self.main.toast(f"Saved {Path(path).name}")


class SettingsPage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main
        b = main.bind
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 28, 32, 32)
        lay.setSpacing(16)
        outer.addWidget(scroll_page(inner))
        lay.addWidget(page_header("Settings", "Saved automatically."))

        # --- hardware
        c, cl = card()
        hr = QHBoxLayout()
        hw = QVBoxLayout()
        hw.addWidget(label("Your hardware", "SectionTitle"))
        hw.addWidget(label(hardware.summary(), "Muted"))
        hr.addLayout(hw, 1)
        preset = QPushButton("Apply recommended settings")
        preset.setObjectName("Primary")
        preset.clicked.connect(self._preset)
        dl = QPushButton("Download models now")
        dl.clicked.connect(lambda: self.main.open_setup(first_run=False))
        hr.addWidget(dl, 0, Qt.AlignVCenter)
        hr.addWidget(preset, 0, Qt.AlignVCenter)
        cl.addLayout(hr)
        lay.addWidget(c)

        # --- ASR
        c, cl = card()
        cl.addWidget(label("Speech recognition", "SectionTitle"))
        g = make_grid()
        form_row(g, 0, "Model", b.combo("asr_model", ASR_MODELS),
                 "Downloaded automatically on first use into the Hugging Face cache.")
        form_row(g, 1, "Custom model", b.line("asr_custom_model", "optional: a CTranslate2 folder or HF repo id"),
                 "Overrides the model above, e.g. a fine-tuned anime/JAV Whisper converted with ct2-transformers-converter.")
        form_row(g, 2, "Device", b.combo("device", {"cuda": "NVIDIA GPU (CUDA)", "cpu": "CPU (slow)"}))
        form_row(g, 3, "Precision", b.combo("compute_type", COMPUTE_TYPES))
        form_row(g, 4, "Beam size", b.spin("beam_size", 1, 10), "5 is most accurate; 1–2 is faster.")
        g.addWidget(b.check("vad_filter", "Skip silence and music with voice activity detection"), 5, 1)
        form_row(g, 6, "Context", b.text("context_prompt", "e.g. Anime 'Frieren'. Names: Frieren, Fern, Stark, Himmel.", 64),
                 "Names and topic. Biases recognition toward the right spellings and is given to the translator.")
        cl.addLayout(g)
        lay.addWidget(c)

        # --- translation
        c, cl = card()
        cl.addWidget(label("Translation", "SectionTitle"))
        g = make_grid()
        form_row(g, 0, "Translator", b.combo("translator", TRANSLATORS))
        form_row(g, 1, "Ollama URL", b.line("llm_url", "http://127.0.0.1:11434"),
                 "Used when an Ollama server is already running there.")
        g.addWidget(b.check("ollama_auto", "Set up Ollama automatically (start it, or download a private copy, "
                                           "and download models as needed)"), 10, 1)
        mr = QHBoxLayout()
        self.llm_combo = b.combo("llm_model", {m: m for m in LLM_SUGGESTIONS}, editable=True)
        mr.addWidget(self.llm_combo, 1)
        refresh = QPushButton("  Installed models")
        refresh.setIcon(icon("refresh", theme.TEXT, 16))
        refresh.clicked.connect(self._list_models)
        mr.addWidget(refresh)
        test = QPushButton("Test")
        test.clicked.connect(self._test_llm)
        mr.addWidget(test)
        w = QWidget()
        mr.setContentsMargins(0, 0, 0, 0)
        w.setLayout(mr)
        form_row(g, 2, "Ollama model", w,
                 "12 GB card: gemma4:12b-it-qat or qwen3:14b (Q4) fit once the speech model is unloaded. "
                 "Install with `ollama pull <name>`.")
        form_row(g, 3, "OpenAI-compatible URL", b.line("openai_url", "http://127.0.0.1:1234/v1"),
                 "LM Studio, llama.cpp server, vLLM, KoboldCpp, or a cloud API.")
        form_row(g, 4, "Model name", b.line("openai_model", "model id on that server"))
        form_row(g, 5, "API key", b.line("openai_api_key", "only for cloud APIs", password=True))
        g.addWidget(b.check("keep_honorifics", "Keep honorifics (-san, -chan, -senpai …)"), 6, 1)
        form_row(g, 7, "Glossary", b.text("glossary", "One per line:  先輩 = Senpai   ·   魔法少女 = magical girl", 80),
                 "Fixed renderings for names and terms.")
        form_row(g, 8, "Lines per request", b.spin("llm_batch", 1, 60), "Bigger batches give more context; "
                 "smaller ones are more reliable with small models.")
        form_row(g, 9, "Temperature", b.dspin("llm_temperature", 0.0, 1.5, 0.05))
        cl.addLayout(g)
        self.test_result = label("", "Faint", wrap=True)
        cl.addWidget(self.test_result)
        lay.addWidget(c)

        # --- output
        c, cl = card()
        cl.addWidget(label("Output", "SectionTitle"))
        g = make_grid()
        form_row(g, 0, "Subtitles", b.combo("sub_mode", SUB_MODES))
        form_row(g, 1, "Formats", b.formats(), "SRT for any player · ASS for styled bilingual subs · VTT for the web.")
        g.addWidget(b.check("save_next_to_video", "Save subtitles next to the video (players load them automatically)"), 2, 1)
        orow = QHBoxLayout()
        orow.setContentsMargins(0, 0, 0, 0)
        orow.addWidget(b.line("output_dir"), 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        orow.addWidget(browse)
        w = QWidget()
        w.setLayout(orow)
        form_row(g, 3, "Output folder", w, "Used for links, and for files when the option above is off.")
        g.addWidget(b.check("url_keep_video", "For links, keep the downloaded video (≤1080p) beside its subtitles"), 4, 1)
        form_row(g, 5, "Line width", b.spin("max_line_chars", 24, 80, " chars"))
        g.addWidget(b.check("vram_saver", "VRAM saver: unload the speech model before translating (needed on ≤ 16 GB)"), 6, 1)
        cl.addLayout(g)
        lay.addWidget(c)

        # --- links
        c, cl = card()
        cl.addWidget(label("Links", "SectionTitle"))
        cl.addWidget(label("YouTube, Dailymotion, OK.ru, Niconico, Bilibili, TVer, X and ~1,800 other sites work out of "
                           "the box. Some videos need you to be signed in (private or adult-flagged OK.ru videos, "
                           "age-restricted YouTube); for those, sign in on the site in your browser and pick it here.",
                           "Faint", wrap=True))
        g = make_grid()
        browsers = {"": "Nobody — don't use sign-in cookies"}
        browsers.update({x: x.capitalize() for x in COOKIE_BROWSERS})
        form_row(g, 0, "Use sign-in from", b.combo("cookies_browser", browsers),
                 "Firefox is the most reliable. Chrome/Edge may need to be closed while downloading.")
        crow = QHBoxLayout()
        crow.setContentsMargins(0, 0, 0, 0)
        crow.addWidget(b.line("cookies_file", "optional: a cookies.txt exported with a browser extension"), 1)
        cb = QPushButton("Browse…")
        cb.clicked.connect(self._browse_cookies)
        crow.addWidget(cb)
        w = QWidget()
        w.setLayout(crow)
        form_row(g, 1, "Cookies file", w, "Used instead of the browser when set.")
        cl.addLayout(g)
        lay.addWidget(c)
        lay.addStretch(1)

    def _browse_cookies(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "cookies.txt", "", "Cookies (*.txt);;All files (*)")
        if f:
            self.main.bind.set("cookies_file", f)

    def _preset(self) -> None:
        gpus = hardware.detect_gpus()
        rec = recommended(gpus[0].vram_gb if gpus else 0)
        for k, v in rec.items():
            self.main.bind.set(k, v)
        self.main.toast("Applied settings for " + (gpus[0].name if gpus else "CPU"))

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.main.settings.output_dir)
        if d:
            self.main.bind.set("output_dir", d)

    def _client(self):
        from .. import ollama_manager
        from ..pipeline import llm_config
        from ..translate import LLMClient
        s = self.main.settings
        url = ollama_manager.current_url(s.llm_url) if s.translator != "openai" else None
        return LLMClient(llm_config(s, url=url))

    def _list_models(self) -> None:
        from ..translate import TranslatorError
        try:
            models = self._client().list_models()
        except TranslatorError:
            self.test_result.setText("Ollama is not running yet — it starts (or downloads) automatically on the "
                                     "first job, or press “Download models now” above.")
            return
        cur = self.main.settings.llm_model
        self.llm_combo.blockSignals(True)
        self.llm_combo.clear()
        for m in models or LLM_SUGGESTIONS:
            self.llm_combo.addItem(m, m)
        self.llm_combo.setEditText(cur)
        self.llm_combo.blockSignals(False)
        self.test_result.setText(f"{len(models)} model(s) installed: {', '.join(models[:12])}" if models else
                                 "No models downloaded yet — they download automatically on first use.")
        self.llm_combo.showPopup()

    def _test_llm(self) -> None:
        self.test_result.setText("Testing…")
        client = self._client()

        def work():
            from ..pipeline import prepare_llm
            from ..translate import TranslatorError
            t0 = time.time()
            try:
                if self.main.settings.translator == "ollama":
                    client.cfg.url = prepare_llm(self.main.settings, progress=lambda f, m: self.main.ui_call.emit(
                        lambda m=m: self.test_result.setText(m)))
                out = client.translate_lines(["お前はもう死んでいる。", "えっ、マジで？"])
                msg = f"✔ {time.time() - t0:.1f}s · " + "  /  ".join(out)
                color = theme.OK
            except TranslatorError as e:
                msg, color = f"✖ {e}", theme.ERR
            self.main.ui_call.emit(lambda: self.test_result.setText(f'<span style="color:{color}">{msg}</span>'))

        threading.Thread(target=work, daemon=True).start()


class LogPage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(page_header("Activity", "Everything the engines report."), 1)
        clr = QPushButton("Clear")
        head.addWidget(clr, 0, Qt.AlignBottom)
        lay.addLayout(head)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        f = QFont("Cascadia Mono")
        f.setStyleHint(QFont.Monospace)
        f.setPointSizeF(9.5)
        self.text.setFont(f)
        lay.addWidget(self.text, 1)
        clr.clicked.connect(self.text.clear)

    def append(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        for line in msg.rstrip().splitlines() or [""]:
            self.text.appendPlainText(f"{stamp}  {line}")


# ====================================================================== main window
class MainWindow(QMainWindow):
    ui_call = Signal(object)            # run a callable on the GUI thread

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.bind = Bindings(settings)
        self.jobs: list[Job] = []
        self.cards: dict[int, JobCard] = {}
        self.ui_call.connect(lambda fn: fn())

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(980, 680)
        if settings.window_geometry and len(settings.window_geometry) == 4:
            self.setGeometry(*settings.window_geometry)
        else:
            self.resize(1240, 860)

        root = QWidget()
        root.setObjectName("Root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.log_page = LogPage(self)
        self.create_page = CreatePage(self)
        self.live_page = LivePage(self)
        self.settings_page = SettingsPage(self)
        for p in (self.create_page, self.live_page, self.settings_page, self.log_page):
            self.stack.addWidget(p)
        self.nav_buttons[0].setChecked(True)

        # workers
        self.queue = JobQueue(lambda: self.settings.copy(), self)
        self.queue.job_changed.connect(self._job_changed)
        self.queue.log.connect(self.log_page.append)
        self.queue.start()

        self.overlay = CaptionOverlay(settings.overlay_font_size, settings.overlay_lines, settings.overlay_bg_opacity,
                                      settings.overlay_show_japanese)
        if len(settings.overlay_geometry) == 4:
            self.overlay.setGeometry(*settings.overlay_geometry)
        else:
            self.overlay.place_default()
        self.overlay.geometry_changed.connect(lambda g: self.bind.set("overlay_geometry", g))
        self.overlay.click_through_changed.connect(lambda on: self.bind.set("overlay_click_through", on))
        if settings.overlay_click_through:
            self.overlay.set_click_through(True)

        self.live = LiveController(self)
        self.live.caption.connect(self._caption)
        self.live.status.connect(self._live_status)
        self.live.level.connect(self.live_page.meter.set_level)
        self.live.error.connect(self._live_error)
        self.live.running_changed.connect(self.live_page.set_running)
        self.live.running_changed.connect(
            lambda on: self.tray and self.a_live.setText("Stop live captions" if on else "Start live captions"))

        self.bind.changed.connect(self._setting_changed)
        self.health = Health()
        self.health.result.connect(self.create_page.show_health)
        self.health.check(self.settings)
        self._health_timer = QTimer(self)
        self._health_timer.setSingleShot(True)
        self._health_timer.setInterval(1200)
        self._health_timer.timeout.connect(lambda: self.health.check(self.settings))

        QShortcut(QKeySequence("Ctrl+Shift+L"), self, activated=self._toggle_click_through)
        paste = QShortcut(QKeySequence.Paste, self.create_page, activated=self._paste)
        paste.setContext(Qt.WidgetWithChildrenShortcut)
        self._tray()
        self._toast = QLabel(self)
        self._toast.setStyleSheet(f"background: {theme.CARD_HI}; border: 1px solid {theme.BORDER}; border-radius: 10px;"
                                  f" padding: 10px 16px;")
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)
        self.log_page.append(f"{APP_NAME} {__version__} · {hardware.summary()}")

    # ---------------------------------------------------------------- chrome
    def _sidebar(self) -> QWidget:
        sb = QFrame()
        sb.setObjectName("Sidebar")
        sb.setFixedWidth(230)
        lay = QVBoxLayout(sb)
        lay.setContentsMargins(16, 22, 16, 16)
        lay.setSpacing(6)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(40, 40))
        brand.addWidget(logo)
        bt = QVBoxLayout()
        bt.setSpacing(0)
        bt.addWidget(label("JPEN SubMaker", "Brand"))
        bt.addWidget(label("日本語 → English", "BrandSub"))
        brand.addLayout(bt, 1)
        lay.addLayout(brand)
        lay.addSpacing(22)
        self.nav_buttons = []
        grp = QButtonGroup(self)
        for i, (ic, text) in enumerate([("subs", "Create subtitles"), ("live", "Live translator"),
                                        ("settings", "Settings"), ("log", "Activity")]):
            b = QPushButton("  " + text)
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setIcon(icon(ic, theme.MUTED, 18))
            b.clicked.connect(lambda _=False, i=i: self.stack.setCurrentIndex(i))
            grp.addButton(b)
            lay.addWidget(b)
            self.nav_buttons.append(b)
        lay.addStretch(1)
        chip = label(hardware.summary(), "GpuChip", wrap=True)
        lay.addWidget(chip)
        lay.addWidget(label(f"v{__version__}", "Faint"))
        return sb

    def _tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        m = QMenu()
        a_show = QAction("Open window", self)
        a_show.triggered.connect(lambda: (self.showNormal(), self.activateWindow()))
        self.a_live = QAction("Start live captions", self)
        self.a_live.triggered.connect(self.toggle_live)
        a_ct = QAction("Toggle overlay click-through", self)
        a_ct.triggered.connect(self._toggle_click_through)
        a_ov = QAction("Show overlay", self)
        a_ov.triggered.connect(self.show_overlay)
        a_quit = QAction("Quit", self)
        a_quit.triggered.connect(self.close)
        for a in (a_show, self.a_live, a_ov, a_ct):
            m.addAction(a)
        m.addSeparator()
        m.addAction(a_quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: (self.showNormal(), self.activateWindow())
                                    if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        self._tray_menu = m

    def open_setup(self, first_run: bool = False) -> None:
        from .setup_dialog import SetupDialog
        SetupDialog(self.settings.copy(), self, first_run=first_run).exec()
        self.health.check(self.settings)

    def toast(self, text: str, ms: int = 2800) -> None:
        self._toast.setText(text)
        self._toast.adjustSize()
        self._toast.move((self.width() - self._toast.width()) // 2 + 115, self.height() - self._toast.height() - 26)
        self._toast.raise_()
        self._toast.show()
        self._toast_timer.start(ms)

    def showEvent(self, e):
        super().showEvent(e)
        theme.apply_dark_titlebar(self)

    # ---------------------------------------------------------------- jobs
    def add_sources(self, items: list[str]) -> None:
        added = 0
        for it in items:
            p = Path(it)
            link = None if p.exists() else normalize_url(it)
            if link:
                self._add_job(Job(link))
                added += 1
                continue
            if p.is_dir():
                files = [f for f in sorted(p.rglob("*")) if f.is_file() and is_media(f)]
                for f in files:
                    self._add_job(Job(str(f)))
                added += len(files)
            elif p.is_file():
                if is_media(p):
                    self._add_job(Job(str(p)))
                    added += 1
                else:
                    self.toast(f"Not a video or audio file: {p.name}")
        if added:
            self.stack.setCurrentIndex(0)
            self.nav_buttons[0].setChecked(True)
            self.toast(f"Added {added} item{'s' if added != 1 else ''} to the queue")
            if self.live.running:
                self.toast("Note: live captions are running and share the GPU with the queue")

    def _add_job(self, job: Job) -> None:
        self.jobs.append(job)
        c = JobCard(job)
        c.cancel_requested.connect(self.queue.cancel)
        c.retry_requested.connect(self.queue.retry)
        c.remove_requested.connect(self._remove_job)
        c.edit_requested.connect(self._edit_job)
        self.cards[id(job)] = c
        self.create_page.add_card(c)
        self.queue.add(job)

    def _remove_job(self, job: Job) -> None:
        if job.status == "running":
            return
        self.queue.remove(job)
        if job.status == "queued":
            job.status = "cancelled"
        c = self.cards.pop(id(job), None)
        if c:
            c.setParent(None)
            c.deleteLater()
        if job in self.jobs:
            self.jobs.remove(job)
        self.create_page.update_count()

    def clear_finished(self) -> None:
        for j in [j for j in self.jobs if j.status in ("done", "failed", "cancelled")]:
            self._remove_job(j)

    def _job_changed(self, job: Job, msg: str) -> None:
        c = self.cards.get(id(job))
        if c:
            c.refresh(msg)
        self.create_page.update_count()
        if job.status == "done" and not msg and self.tray and not self.isActiveWindow():
            self.tray.showMessage("Subtitles ready", job.display_name, app_icon(), 4000)

    def _edit_job(self, job: Job) -> None:
        dlg = SubtitleEditor(job.cues, job.outputs, self.settings.sub_mode, self.settings.max_line_chars,
                             job.display_name, self)
        dlg.exec()

    def _paste(self) -> None:
        fw = QApplication.focusWidget()
        if isinstance(fw, (QLineEdit, QPlainTextEdit)):
            fw.paste()
            return
        text = QGuiApplication.clipboard().text().strip()
        links = [normalize_url(t) for t in text.split() if normalize_url(t)]
        if links:
            self.add_sources(links)
        else:
            files = [t for t in text.splitlines() if Path(t.strip('"')).exists()]
            if files:
                self.add_sources([f.strip('"') for f in files])

    # ---------------------------------------------------------------- live
    def toggle_live(self) -> None:
        if self.live.running:
            self.live.stop()
            self.overlay.set_placeholder("Live captions stopped")
            self.live_page.status.setText("Stopped")
            if self.tray:
                self.a_live.setText("Start live captions")
            return
        if self.queue.busy:
            r = QMessageBox.question(self, "GPU busy", "A subtitle job is running and is using the GPU. Live "
                                     "captions may run out of VRAM or be slow. Start anyway?")
            if r != QMessageBox.Yes:
                return
        self.overlay.clear()
        self.overlay.set_placeholder("Loading models…")
        self.show_overlay()
        self.live.start(self.settings.copy())
        if self.tray:
            self.a_live.setText("Stop live captions")

    def show_overlay(self) -> None:
        self.overlay.apply_style(self.settings.overlay_font_size, self.settings.overlay_lines,
                                 self.settings.overlay_bg_opacity, self.settings.overlay_show_japanese)
        self.overlay.show()
        self.overlay.raise_()

    def _caption(self, c: Caption) -> None:
        self.overlay.add_caption(c.ja, c.en, c.final)
        self.live_page.add_caption(c)

    def _live_status(self, msg: str) -> None:
        self.live_page.status.setText(msg)
        if msg.startswith("Live"):
            self.overlay.set_placeholder("Listening…")
        self.log_page.append(f"[live] {msg}")

    def _live_error(self, msg: str) -> None:
        self.log_page.append(f"[live] ✖ {msg}")
        self.live_page.status.setText(f'<span style="color:{theme.ERR}">{msg}</span>')
        self.overlay.set_placeholder("Live captions stopped — see the app for details")
        if self.live.running:
            self.live.stop()

    def _toggle_click_through(self) -> None:
        self.bind.set("overlay_click_through", not self.settings.overlay_click_through)

    # ---------------------------------------------------------------- settings
    def _setting_changed(self, key: str) -> None:
        if key.startswith("overlay_") and key != "overlay_geometry":
            if key == "overlay_click_through":
                self.overlay.set_click_through(self.settings.overlay_click_through)
            else:
                self.overlay.apply_style(self.settings.overlay_font_size, self.settings.overlay_lines,
                                         self.settings.overlay_bg_opacity, self.settings.overlay_show_japanese)
                if not self.overlay.items and self.overlay.isVisible():
                    self.overlay.update()
        if key in ("translator", "llm_url", "llm_model", "device", "asr_model", "asr_custom_model"):
            self._health_timer.start()

    def closeEvent(self, e):
        if self.queue.busy:
            r = QMessageBox.question(self, "Quit?", "A job is still running. Quit and cancel it?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        g = self.geometry()
        self.settings.window_geometry = [g.x(), g.y(), g.width(), g.height()]
        if self.overlay.isVisible() or self.overlay.geometry().width() > 0:
            og = self.overlay.geometry()
            self.settings.overlay_geometry = [og.x(), og.y(), og.width(), og.height()]
        self.settings.save()
        self.live.stop()
        self.overlay.close()
        self.queue.stop()
        from .. import ollama_manager
        ollama_manager.stop()                 # only stops a server this app started
        if self.tray:
            self.tray.hide()
        e.accept()
        QApplication.instance().quit()


def run(initial: list[str] | None = None) -> int:
    if sys.platform == "win32":
        try:   # own taskbar icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("jpensubmaker.app")
        except Exception:  # noqa: BLE001
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.stylesheet())
    app.setWindowIcon(app_icon())
    s = Settings.load()
    first = not s.first_run_done
    if first:                                 # pick settings for this PC's GPU before anything is shown
        gpus = hardware.detect_gpus()
        for k, v in recommended(gpus[0].vram_gb if gpus else 0).items():
            setattr(s, k, v)
        s.first_run_done = True
        s.save()
    w = MainWindow(s)
    w.show()
    if first:
        QTimer.singleShot(300, lambda: w.open_setup(first_run=True))
    if initial:
        QTimer.singleShot(200, lambda: w.add_sources(initial))
    return app.exec()
