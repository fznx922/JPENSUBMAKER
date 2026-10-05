"""Review and fix subtitles before watching: a table of time / Japanese / English, saved back to every format."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import subtitles
from ..cues import Cue
from ..subtitles import ts_srt
from . import theme
from .icons import icon


class SubtitleEditor(QDialog):
    def __init__(self, cues: list[Cue], outputs: list[Path], mode: str, width: int, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Subtitles — {title}")
        self.resize(1100, 720)
        self.cues = cues
        self.outputs = outputs
        self.mode = mode
        self.width_chars = width
        self.title = title

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        head.addWidget(t, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setFixedWidth(260)
        self.search.textChanged.connect(self._filter)
        head.addWidget(self.search)
        lay.addLayout(head)

        self.table = QTableWidget(len(cues), 4)
        self.table.setHorizontalHeaderLabels(["Start", "End", "Japanese", "English"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        for i, c in enumerate(cues):
            for col, val, editable in ((0, ts_srt(c.start), False), (1, ts_srt(c.end), False), (2, c.ja, True),
                                       (3, c.en, True)):
                it = QTableWidgetItem(val)
                if not editable:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    it.setForeground(Qt.gray)
                self.table.setItem(i, col, it)
        self.table.resizeRowsToContents()
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        hint = QLabel("Double-click a cell to edit. Saving rewrites " +
                      (", ".join(p.name for p in outputs) if outputs else "the subtitle files") + ".")
        hint.setObjectName("Faint")
        foot.addWidget(hint, 1)
        cancel = QPushButton("Close")
        cancel.clicked.connect(self.reject)
        save = QPushButton("  Save")
        save.setObjectName("Primary")
        save.setIcon(icon("save", "#ffffff", 16))
        save.clicked.connect(self._save)
        foot.addWidget(cancel)
        foot.addWidget(save)
        lay.addLayout(foot)
        self.setStyleSheet(f"QDialog {{ background: {theme.BG}; }}")

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for r in range(self.table.rowCount()):
            ja = self.table.item(r, 2).text().lower()
            en = self.table.item(r, 3).text().lower()
            self.table.setRowHidden(r, bool(text) and text not in ja and text not in en)

    def _save(self) -> None:
        for r, c in enumerate(self.cues):
            c.ja = self.table.item(r, 2).text().strip()
            c.en = self.table.item(r, 3).text().strip()
        written = []
        try:
            groups: dict[Path, list[str]] = {}
            for p in self.outputs:
                # "Episode 1.en.srt" → base "Episode 1", format "srt"
                base = p.with_name(p.name.rsplit(".", 2)[0])
                groups.setdefault(base, []).append(p.suffix.lstrip("."))
            for base, fmts in groups.items():
                written += subtitles.write(self.cues, base, fmts, mode=self.mode, width=self.width_chars,
                                           title=self.title)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        QMessageBox.information(self, "Saved", "Saved:\n" + "\n".join(str(p) for p in written))
        self.accept()
