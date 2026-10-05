"""Reusable widgets: the drop zone, job cards, the audio level meter, card containers."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QSizePolicy,
                               QToolButton, QVBoxLayout, QWidget)

from ..download import is_url
from ..media import MEDIA_EXTS, VIDEO_EXTS
from . import theme
from .icons import icon, pixmap


def card(parent: QWidget | None = None, margins: int = 18, spacing: int = 12) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(parent)
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    return f, lay


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def ghost_button(icon_name: str, tip: str, color: str = theme.MUTED, size: int = 18) -> QToolButton:
    b = QToolButton()
    b.setObjectName("Ghost")
    b.setIcon(icon(icon_name, color, size))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setAutoRaise(True)
    return b


class DropZone(QWidget):
    """Big dashed target: drop files, folders or links; click to browse."""
    dropped = Signal(list)          # list[str] of paths / URLs

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._hover = False
        self._drag = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self):
        if self._drag or self._hover:
            self._phase = (self._phase + 0.6) % 20
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            exts = " ".join(f"*{x}" for x in sorted(MEDIA_EXTS))
            files, _ = QFileDialog.getOpenFileNames(self, "Choose videos", "", f"Video and audio ({exts});;All files (*)")
            if files:
                self.dropped.emit(files)

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or (md.hasText() and is_url(md.text().strip().split()[0] if md.text().strip() else "")):
            e.acceptProposedAction()
            self._drag = True
            self.update()

    def dragLeaveEvent(self, e):
        self._drag = False
        self.update()

    def dropEvent(self, e):
        self._drag = False
        self.update()
        md = e.mimeData()
        items: list[str] = []
        if md.hasUrls():
            for u in md.urls():
                if u.isLocalFile():
                    items.append(u.toLocalFile())
                elif u.scheme() in ("http", "https"):
                    items.append(u.toString())
        elif md.hasText():
            items = [t for t in md.text().split() if is_url(t)]
        if items:
            e.acceptProposedAction()
            self.dropped.emit(items)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        active = self._drag or self._hover
        bg = QColor(theme.CARD_HI if self._drag else theme.CARD)
        path = QPainterPath()
        path.addRoundedRect(r, 16, 16)
        p.fillPath(path, bg)
        if active:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor(theme.ACCENT))
            g.setColorAt(1, QColor(theme.ACCENT2))
            pen = QPen(g, 2)
        else:
            pen = QPen(QColor(theme.BORDER), 2)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([6, 5])
        pen.setDashOffset(-self._phase if active else 0)
        p.setPen(pen)
        p.drawPath(path)

        # icon bubble
        cx = r.center().x()
        top = r.center().y() - 58
        bubble = QRectF(cx - 30, top, 60, 60)
        g = QLinearGradient(bubble.topLeft(), bubble.bottomRight())
        g.setColorAt(0, QColor(theme.ACCENT))
        g.setColorAt(1, QColor(theme.ACCENT2))
        bp = QPainterPath()
        bp.addRoundedRect(bubble, 18, 18)
        p.fillPath(bp, g)
        pm = pixmap("upload", "#ffffff", 28, 2.2)
        p.drawPixmap(int(cx - 14), int(top + 16), pm)

        p.setPen(QColor(theme.TEXT))
        f = self.font()
        f.setPointSizeF(12.5)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(r.left(), top + 72, r.width(), 26), Qt.AlignCenter,
                   "Release to add" if self._drag else "Drop videos, folders or links here")
        f.setPointSizeF(9.5)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(r.left(), top + 100, r.width(), 22), Qt.AlignCenter,
                   "or click to browse  ·  MP4 · MKV · AVI · MOV · WEBM · MP3 · and more")
        p.end()


class LevelMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self._v = 0.0
        self._shown = 0.0
        t = QTimer(self)
        t.timeout.connect(self._decay)
        t.start(40)

    def set_level(self, v: float):
        self._v = max(self._v, v)

    def _decay(self):
        self._shown = max(self._v, self._shown * 0.85)
        self._v = 0.0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        bg = QPainterPath()
        bg.addRoundedRect(r, 4, 4)
        p.fillPath(bg, QColor(theme.SURFACE))
        w = r.width() * min(1.0, self._shown)
        if w > 1:
            g = QLinearGradient(0, 0, r.width(), 0)
            g.setColorAt(0, QColor(theme.OK))
            g.setColorAt(0.7, QColor(theme.WARN))
            g.setColorAt(1, QColor(theme.ERR))
            fg = QPainterPath()
            fg.addRoundedRect(QRectF(0, 0, w, r.height()), 4, 4)
            p.fillPath(fg, g)
        p.end()


STATUS_COLORS = {"queued": theme.MUTED, "running": theme.ACCENT2, "done": theme.OK, "failed": theme.ERR,
                 "cancelled": theme.FAINT}


class JobCard(QFrame):
    cancel_requested = Signal(object)
    retry_requested = Signal(object)
    remove_requested = Signal(object)
    edit_requested = Signal(object)

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.job = job
        self.setObjectName("JobCard")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 10, 12)
        lay.setSpacing(12)

        self.kind = QLabel()
        self.kind.setFixedSize(38, 38)
        self.kind.setAlignment(Qt.AlignCenter)
        self.kind.setStyleSheet(f"background: {theme.CARD_HI}; border-radius: 10px;")
        self.kind.setPixmap(pixmap("link" if job.is_url else "film", theme.ACCENT, 20))
        lay.addWidget(self.kind, 0, Qt.AlignTop)

        mid = QVBoxLayout()
        mid.setSpacing(5)
        top = QHBoxLayout()
        self.title = QLabel(job.display_name)
        self.title.setObjectName("JobTitle")
        self.title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.title.setMinimumWidth(50)
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.title, 1)
        self.badge = QLabel()
        top.addWidget(self.badge)
        mid.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        mid.addWidget(self.bar)
        self.status = QLabel()
        self.status.setObjectName("Faint")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        mid.addWidget(self.status)
        lay.addLayout(mid, 1)

        self.btns = QHBoxLayout()
        self.btns.setSpacing(2)
        self.b_edit = ghost_button("edit", "Review and edit subtitles")
        self.b_folder = ghost_button("folder", "Show in folder")
        self.b_play = ghost_button("play", "Play video (subtitles load automatically in VLC / mpv / MPC)")
        self.b_retry = ghost_button("retry", "Retry")
        self.b_cancel = ghost_button("x", "Cancel")
        self.b_remove = ghost_button("trash", "Remove from list")
        for b in (self.b_edit, self.b_play, self.b_folder, self.b_retry, self.b_cancel, self.b_remove):
            self.btns.addWidget(b)
        lay.addLayout(self.btns)
        self.b_cancel.clicked.connect(lambda: self.cancel_requested.emit(self.job))
        self.b_retry.clicked.connect(lambda: self.retry_requested.emit(self.job))
        self.b_remove.clicked.connect(lambda: self.remove_requested.emit(self.job))
        self.b_edit.clicked.connect(lambda: self.edit_requested.emit(self.job))
        self.b_folder.clicked.connect(self._open_folder)
        self.b_play.clicked.connect(self._play)
        self.refresh()

    def _open_folder(self):
        target = self.job.outputs[0] if self.job.outputs else self.job.media_path
        if not target:
            return
        reveal(Path(target))

    def _play(self):
        if self.job.media_path and Path(self.job.media_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.job.media_path)))

    def refresh(self, message: str = ""):
        j = self.job
        self.title.setText(j.display_name)
        self.title.setToolTip(j.source)
        self.bar.setValue(int(j.progress * 1000))
        self.bar.setVisible(j.status in ("running", "queued"))
        color = STATUS_COLORS.get(j.status, theme.MUTED)
        text = {"queued": "Queued", "running": j.stage or "Working", "done": "Done", "failed": "Failed",
                "cancelled": "Cancelled"}[j.status]
        if j.status == "running":
            text += f" · {int(j.progress * 100)}%"
        self.badge.setText(f'<span style="color:{color}; font-weight:600;">● {text}</span>')
        if j.status == "failed":
            self.status.setText(f'<span style="color:{theme.ERR}">{_esc(j.error.splitlines()[0] if j.error else "")}</span>')
            self.status.setToolTip(j.error)
        elif j.status == "done":
            names = ", ".join(p.name for p in j.outputs)
            mins = j.elapsed / 60
            self.status.setText(f"{len(j.cues)} lines · {mins:.1f} min · {_esc(names)}")
            self.status.setToolTip("\n".join(str(p) for p in j.outputs))
        elif message:
            self.status.setText(_esc(message))
        elif j.status == "queued":
            self.status.setText("Waiting…")
        has_media = bool(j.media_path) and Path(j.media_path).suffix.lower() in VIDEO_EXTS \
            and Path(j.media_path).exists()
        self.b_edit.setVisible(j.status == "done" and bool(j.cues))
        self.b_folder.setVisible(j.status == "done")
        self.b_play.setVisible(j.status == "done" and bool(has_media))
        self.b_retry.setVisible(j.status in ("failed", "cancelled"))
        self.b_cancel.setVisible(j.status in ("queued", "running"))
        self.b_remove.setVisible(j.status not in ("running",))
        self.setProperty("state", j.status)
        self.style().unpolish(self)
        self.style().polish(self)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def reveal(path: Path) -> None:
    """Open the folder with the file selected where the platform allows it."""
    import subprocess
    import sys
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
            return
    except OSError:
        pass
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent if path.is_file() else path)))
