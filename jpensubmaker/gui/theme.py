"""Colours and the Qt stylesheet. One dark theme: deep navy surfaces, a sakura-pink → violet accent."""
from __future__ import annotations

BG = "#0e1016"
SURFACE = "#151821"
CARD = "#1b1f2b"
CARD_HI = "#232838"
BORDER = "#2a3042"
TEXT = "#e8eaf0"
MUTED = "#8b92a6"
FAINT = "#5c6378"
ACCENT = "#ff6b9a"
ACCENT2 = "#8b5cf6"
OK = "#34d399"
WARN = "#fbbf24"
ERR = "#f87171"

GRADIENT = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2})"

FONT_STACK = '"Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "Noto Sans JP", "Yu Gothic UI", sans-serif'

QSS = f"""
* {{
    font-family: {FONT_STACK};
    font-size: 10pt;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QDialog, #Root {{ background: {BG}; }}
QToolTip {{ background: {CARD_HI}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

/* ---------- sidebar */
#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
#Brand {{ font-size: 13pt; font-weight: 700; }}
#BrandSub {{ color: {MUTED}; font-size: 8.5pt; }}
#NavButton {{
    text-align: left; padding: 10px 14px; border-radius: 10px; border: none;
    background: transparent; color: {MUTED}; font-weight: 600;
}}
#NavButton:hover {{ background: {CARD}; color: {TEXT}; }}
#NavButton:checked {{ background: {CARD_HI}; color: {TEXT}; }}
#GpuChip {{ color: {MUTED}; font-size: 8.5pt; padding: 8px 10px; background: {CARD}; border-radius: 8px; }}

/* ---------- headings */
#PageTitle {{ font-size: 18pt; font-weight: 700; }}
#PageSub {{ color: {MUTED}; }}
#SectionTitle {{ font-size: 11pt; font-weight: 700; }}
#Muted {{ color: {MUTED}; }}
#Faint {{ color: {FAINT}; font-size: 9pt; }}
#Error {{ color: {ERR}; }}

/* ---------- cards */
#Card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
#JobCard {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
#JobCard[state="running"] {{ border: 1px solid {ACCENT2}; }}
#JobCard[state="failed"] {{ border: 1px solid #5b2a33; }}
#JobTitle {{ font-weight: 600; }}

/* ---------- inputs */
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 10px;
    selection-background-color: {ACCENT2};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT2}; }}
QLineEdit#UrlInput {{ padding: 11px 14px; font-size: 10.5pt; border-radius: 10px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("@CHEVRON@"); width: 12px; height: 12px; margin-right: 10px; }}
QComboBox QAbstractItemView {{ background: {CARD_HI}; border: 1px solid {BORDER}; selection-background-color: {ACCENT2};
    padding: 4px; border-radius: 8px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER}; background: {SURFACE}; }}
QCheckBox::indicator:checked {{ background: {ACCENT2}; border: 1px solid {ACCENT2}; image: url("@CHECK@"); }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 8px; border: 1px solid {BORDER}; background: {SURFACE}; }}
QRadioButton::indicator:checked {{ border: 1px solid {ACCENT2}; image: url("@RADIO@"); }}

/* ---------- buttons */
QPushButton {{
    background: {CARD_HI}; border: 1px solid {BORDER}; border-radius: 9px; padding: 8px 14px; font-weight: 600;
}}
QPushButton:hover {{ border: 1px solid {ACCENT2}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: {CARD_HI}; }}
QPushButton#Primary {{ background: {GRADIENT}; border: none; color: white; padding: 9px 18px; }}
QPushButton#Primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff85ad, stop:1 #9d74fa); }}
QPushButton#Primary:disabled {{ background: {CARD_HI}; color: {FAINT}; }}
QPushButton#Danger {{ background: transparent; border: 1px solid #5b2a33; color: {ERR}; }}
QPushButton#Danger:hover {{ background: #2a1519; }}
QPushButton#Ghost, QToolButton#Ghost {{ background: transparent; border: none; padding: 6px; border-radius: 8px; }}
QPushButton#Ghost:hover, QToolButton#Ghost:hover {{ background: {CARD_HI}; }}
QPushButton#BigLive {{ background: {GRADIENT}; border: none; color: white; font-size: 12pt; padding: 14px 22px; border-radius: 12px; }}
QPushButton#BigLive[live="true"] {{ background: {CARD_HI}; border: 1px solid {ERR}; color: {ERR}; }}

/* ---------- progress */
QProgressBar {{ background: {SURFACE}; border: none; border-radius: 3px; height: 6px; max-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {GRADIENT}; border-radius: 3px; }}

/* ---------- sliders */
QSlider::groove:horizontal {{ height: 6px; background: {SURFACE}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {GRADIENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }}

/* ---------- scroll */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 30px; }}

/* ---------- tables / lists */
QTableWidget, QListWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: {BORDER};
    alternate-background-color: {CARD}; }}
QTableWidget::item {{ padding: 6px; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: #2d2650; }}
QHeaderView::section {{ background: {CARD}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {CARD}; border: none; }}

QMenu {{ background: {CARD_HI}; border: 1px solid {BORDER}; padding: 6px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT2}; }}
"""


CHECK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3.2" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'
)


CHEVRON_SVG = (
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{MUTED}" stroke-width="2.6" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>'
)
RADIO_SVG = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle cx="8" cy="8" r="4.5" fill="{ACCENT2}"/></svg>'


def stylesheet() -> str:
    """The QSS with its small images written to files Qt can load (QSS cannot take inline SVG)."""
    from ..settings import config_dir
    qss = QSS
    folder = config_dir() / "assets"
    for key, name, content in (("@CHECK@", "check.svg", CHECK_SVG), ("@CHEVRON@", "chevron.svg", CHEVRON_SVG),
                               ("@RADIO@", "radio.svg", RADIO_SVG)):
        path = folder / name
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_text() != content:
                path.write_text(content)
            qss = qss.replace(key, path.as_posix())
        except OSError:
            qss = qss.replace(f'image: url("{key}");', "")
    return qss


def apply_dark_titlebar(widget) -> None:
    """Windows 10/11: dark native title bar to match the theme."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        for attr in (20, 19):            # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001
        pass
