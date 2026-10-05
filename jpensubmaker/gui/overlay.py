"""The live caption overlay: a frameless, translucent, always-on-top window that floats over any app or game.

Drag it anywhere, resize from the corner. "Click-through" makes the mouse pass straight through to the window
underneath (toggle it back from the main window or the tray icon).
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizeGrip, QToolButton, QWidget

from . import theme
from .icons import icon


class CaptionOverlay(QWidget):
    geometry_changed = Signal(list)
    click_through_changed = Signal(bool)
    closed = Signal()

    CLEAR_AFTER = 9.0        # seconds without a new caption → fade out

    def __init__(self, font_size: int = 26, lines: int = 2, bg_opacity: int = 55, show_japanese: bool = False):
        super().__init__(None)
        self.setWindowTitle("JPEN Live Captions")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMinimumSize(320, 90)
        self.font_size = font_size
        self.max_lines = lines
        self.bg_opacity = bg_opacity
        self.show_japanese = show_japanese
        self.click_through = False
        self.items: list[dict] = []          # {"ja", "en", "final", "t"}
        self._drag: QPoint | None = None
        self._reflagging = False
        self._hover = False
        self._alpha = 1.0
        self._placeholder = "Live captions will appear here"

        self.grip = QSizeGrip(self)
        self.grip.setFixedSize(16, 16)
        self.grip.setStyleSheet("background: transparent;")
        self.b_lock = QToolButton(self)
        self.b_lock.setObjectName("Ghost")
        self.b_lock.setIcon(icon("lock", "#ffffff", 16))
        self.b_lock.setToolTip("Click-through: let the mouse pass through the captions")
        self.b_lock.clicked.connect(lambda: self.set_click_through(True))
        self.b_close = QToolButton(self)
        self.b_close.setObjectName("Ghost")
        self.b_close.setIcon(icon("x", "#ffffff", 16))
        self.b_close.setToolTip("Hide overlay")
        self.b_close.clicked.connect(self.hide)
        self._chrome(False)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    # ------------------------------------------------------------------ API
    def add_caption(self, ja: str, en: str, final: bool) -> None:
        if self.items and not self.items[-1]["final"]:
            self.items.pop()                     # replace the provisional (untranslated) line
        self.items.append({"ja": ja, "en": en, "final": final, "t": time.time()})
        self.items = self.items[-max(1, self.max_lines):]
        self._alpha = 1.0
        self.update()

    def clear(self) -> None:
        self.items.clear()
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def apply_style(self, font_size: int, lines: int, bg_opacity: int, show_japanese: bool) -> None:
        self.font_size, self.max_lines, self.bg_opacity, self.show_japanese = font_size, lines, bg_opacity, show_japanese
        self.items = self.items[-max(1, lines):]
        self.update()

    def set_click_through(self, on: bool) -> None:
        if on == self.click_through:
            return
        self.click_through = on
        visible = self.isVisible()
        self._reflagging = True                  # setWindowFlag hides the window; that is not a user close
        self.setWindowFlag(Qt.WindowTransparentForInput, on)
        self._chrome(False)
        if visible:
            self.show()
        self._reflagging = False
        self.click_through_changed.emit(on)

    def place_default(self) -> None:
        scr = self.screen().availableGeometry() if self.screen() else None
        if scr is None:
            self.resize(900, 140)
            return
        w = int(scr.width() * 0.6)
        h = max(120, int(self.font_size * 2.2 * (self.max_lines + (self.max_lines if self.show_japanese else 0) * 0.6)) + 40)
        self.setGeometry(scr.x() + (scr.width() - w) // 2, scr.y() + scr.height() - h - 60, w, h)

    # ------------------------------------------------------------------ internals
    def _chrome(self, on: bool) -> None:
        for w in (self.grip, self.b_lock, self.b_close):
            w.setVisible(on and not self.click_through)

    def _tick(self) -> None:
        if self.items and time.time() - self.items[-1]["t"] > self.CLEAR_AFTER:
            self._alpha = max(0.0, self._alpha - 0.08)
            if self._alpha == 0.0:
                self.items.clear()
            self.update()

    def resizeEvent(self, e):
        self.grip.move(self.width() - 18, self.height() - 18)
        self.b_close.move(self.width() - 34, 6)
        self.b_lock.move(self.width() - 62, 6)
        super().resizeEvent(e)

    def enterEvent(self, e):
        self._hover = True
        self._chrome(True)
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self._chrome(False)
        self.geometry_changed.emit([self.x(), self.y(), self.width(), self.height()])
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.geometry_changed.emit([self.x(), self.y(), self.width(), self.height()])

    def mouseDoubleClickEvent(self, e):
        self.place_default()

    def hideEvent(self, e):
        if not self._reflagging:
            self.closed.emit()
        super().hideEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg = QColor(8, 9, 14, int(255 * self.bg_opacity / 100 * (self._alpha if self.items else 1)))
        if self._hover and not self.click_through:
            bg.setAlpha(max(bg.alpha(), 150))
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, bg)
        if self._hover and not self.click_through:
            p.setPen(QPen(QColor(theme.ACCENT2), 1.5))
            p.drawPath(path)

        en_font = QFont()
        en_font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Inter", "Noto Sans", "Arial"])
        en_font.setPixelSize(self.font_size)
        en_font.setWeight(QFont.DemiBold)
        ja_font = QFont()
        ja_font.setFamilies(["Yu Gothic UI", "Meiryo UI", "Noto Sans CJK JP", "Noto Sans JP", "MS Gothic"])
        ja_font.setPixelSize(max(12, int(self.font_size * 0.62)))
        ja_font.setWeight(QFont.Medium)

        pad = 18
        width = r.width() - 2 * pad
        blocks: list[tuple[str, QFont, QColor]] = []
        if not self.items:
            blocks.append((self._placeholder, ja_font, QColor(255, 255, 255, 110)))
        for it in self.items:
            a = int(255 * self._alpha)
            if self.show_japanese and it["ja"]:
                blocks.append((it["ja"], ja_font, QColor(255, 196, 222, a)))
            if it["en"]:
                blocks.append((it["en"], en_font, QColor(255, 255, 255, a)))
            elif not it["final"] and not self.show_japanese and it["ja"]:
                blocks.append((it["ja"] + " …", ja_font, QColor(255, 255, 255, int(a * 0.6))))

        # lay out bottom-up so the newest line sits at the bottom edge
        laid = []
        for text, font, color in blocks:
            for ln in self._wrap(text, QFontMetricsF(font), width):
                laid.append((ln, font, color))
        y = r.bottom() - pad
        for ln, font, color in reversed(laid):
            fm = QFontMetricsF(font)
            if y - fm.height() < r.top() + 4:
                break
            base = y - fm.descent()
            x = r.left() + pad + (width - fm.horizontalAdvance(ln)) / 2
            tp = QPainterPath()
            tp.addText(x, base, font, ln)
            p.setPen(QPen(QColor(0, 0, 0, int(color.alpha() * 0.85)), max(2.0, font.pixelSize() / 9),
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(tp)
            p.fillPath(tp, color)
            y -= fm.height() + 2
        p.end()

    @staticmethod
    def _wrap(text: str, fm: QFontMetricsF, width: float) -> list[str]:
        if fm.horizontalAdvance(text) <= width:
            return [text]
        has_spaces = " " in text.strip()
        tokens = text.split(" ") if has_spaces else list(text)
        joiner = " " if has_spaces else ""
        lines, cur = [], ""
        for t in tokens:
            cand = (cur + joiner + t) if cur else t
            if fm.horizontalAdvance(cand) <= width or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = t
        if cur:
            lines.append(cur)
        return lines
