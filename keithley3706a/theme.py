"""Helles und dunkles Theme (Fusion-Style + Palette + Stylesheet)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str          # Fensterhintergrund
    surface: str     # Karten / Panels
    surface_alt: str
    border: str
    text: str
    text_muted: str
    accent: str
    accent_text: str
    danger: str
    success: str
    warning: str
    plot_bg: str
    plot_fg: str
    grid_alpha: float


LIGHT = Theme("light", bg="#f3f4f7", surface="#ffffff", surface_alt="#f7f8fa",
              border="#dfe2e8", text="#1c1f26", text_muted="#6b7280",
              accent="#2563eb", accent_text="#ffffff", danger="#dc2626",
              success="#16a34a", warning="#d97706",
              plot_bg="#ffffff", plot_fg="#374151", grid_alpha=0.18)

DARK = Theme("dark", bg="#15171c", surface="#1e2128", surface_alt="#252932",
             border="#323743", text="#e6e8ee", text_muted="#9aa1ae",
             accent="#4f8cff", accent_text="#ffffff", danger="#f05252",
             success="#31c46c", warning="#f0a43a",
             plot_bg="#1e2128", plot_fg="#c9ced8", grid_alpha=0.22)

CONTRAST = Theme("contrast", bg="#000000", surface="#0a0a0a", surface_alt="#161616",
                 border="#ffffff", text="#ffffff", text_muted="#e0e0e0",
                 accent="#ffd400", accent_text="#000000", danger="#ff3b3b",
                 success="#00ff7f", warning="#ffb000",
                 plot_bg="#000000", plot_fg="#ffffff", grid_alpha=0.35)

THEME_MODES = [("system", "Auto", "Wie Windows"), ("light", "☀", "Hell"),
               ("dark", "☾", "Dunkel"), ("contrast", "HK", "Hoher Kontrast")]

# Gut unterscheidbare Linienfarben (auf hell und dunkel lesbar)
CHANNEL_COLORS = [
    "#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899",
    "#14b8a6", "#f97316", "#84cc16", "#06b6d4", "#a855f7", "#e11d48",
    "#22c55e", "#eab308", "#6366f1", "#0ea5e9",
]


def channel_color(index: int) -> str:
    return CHANNEL_COLORS[index % len(CHANNEL_COLORS)]


def system_prefers_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


def resolve(mode: str) -> Theme:
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    if mode == "contrast":
        return CONTRAST
    return DARK if system_prefers_dark() else LIGHT


def apply_theme(app: QApplication, theme: Theme) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.Window, c(theme.bg))
    pal.setColor(QPalette.WindowText, c(theme.text))
    pal.setColor(QPalette.Base, c(theme.surface))
    pal.setColor(QPalette.AlternateBase, c(theme.surface_alt))
    pal.setColor(QPalette.Text, c(theme.text))
    pal.setColor(QPalette.Button, c(theme.surface))
    pal.setColor(QPalette.ButtonText, c(theme.text))
    pal.setColor(QPalette.ToolTipBase, c(theme.surface))
    pal.setColor(QPalette.ToolTipText, c(theme.text))
    pal.setColor(QPalette.Highlight, c(theme.accent))
    pal.setColor(QPalette.HighlightedText, c(theme.accent_text))
    pal.setColor(QPalette.PlaceholderText, c(theme.text_muted))
    pal.setColor(QPalette.Link, c(theme.accent))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c(theme.text_muted))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(theme))


def _rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {int(alpha * 255)})"


def stylesheet(t: Theme) -> str:
    return f"""
    QWidget {{ font-family: "Segoe UI Variable", "Segoe UI", sans-serif; font-size: 10pt; }}
    QMainWindow, QDialog {{ background: {t.bg}; }}
    QFrame#Card {{
        background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px;
    }}
    QLabel#CardTitle {{ font-size: 11pt; font-weight: 600; color: {t.text}; }}
    QLabel#Muted {{ color: {t.text_muted}; }}
    QLabel#BigValue {{ font-size: 18pt; font-weight: 600; }}
    QLabel#StatusDot {{ font-size: 14pt; }}

    QPushButton {{
        background: {t.surface_alt}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 7px; padding: 6px 14px; min-height: 18px;
    }}
    QPushButton:hover {{ border-color: {t.accent}; }}
    QPushButton:pressed {{ background: {t.border}; }}
    QPushButton:disabled {{ color: {t.text_muted}; background: {t.bg}; }}
    QPushButton#Primary {{ background: {t.accent}; color: {t.accent_text}; border: none; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: {QColor(t.accent).lighter(112).name()}; }}
    QPushButton#Primary:disabled {{ background: {t.border}; color: {t.text_muted}; }}
    QPushButton#Danger {{ background: {t.danger}; color: white; border: none; font-weight: 600; }}
    QPushButton#Danger:hover {{ background: {QColor(t.danger).lighter(112).name()}; }}
    QPushButton#Flat {{ background: transparent; border: none; padding: 4px 8px; }}
    QPushButton#Flat:hover {{ background: {t.surface_alt}; }}
    QPushButton:checked {{ background: {t.accent}; color: {t.accent_text}; border-color: {t.accent}; }}

    QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {{
        background: {t.surface_alt}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 6px; padding: 4px 8px; min-height: 20px;
        selection-background-color: {t.accent};
    }}
    QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {t.accent}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
        selection-background-color: {t.accent}; selection-color: {t.accent_text};
    }}

    QTableWidget {{
        background: {t.surface}; alternate-background-color: {t.surface_alt};
        border: none; gridline-color: transparent; color: {t.text};
        selection-background-color: {_rgba(t.accent, 0.2)}; selection-color: {t.text};
    }}
    QTableWidget::item {{ padding: 2px 6px; border-bottom: 1px solid {t.border}; }}
    QHeaderView::section {{
        background: {t.surface}; color: {t.text_muted}; border: none;
        border-bottom: 1px solid {t.border}; padding: 6px; font-weight: 600;
    }}
    QTableCornerButton::section {{ background: {t.surface}; border: none; }}

    QCheckBox {{ spacing: 6px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {t.border}; background: {t.surface_alt};
    }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}

    QStatusBar {{ background: {t.surface}; color: {t.text_muted}; border-top: 1px solid {t.border}; }}
    QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}
    QSplitter::handle {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 4px; min-width: 30px; }}
    QGroupBox {{ border: 1px solid {t.border}; border-radius: 8px; margin-top: 14px; padding-top: 8px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t.text_muted}; }}
    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{
        background: transparent; color: {t.text_muted}; padding: 6px 14px;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{ color: {t.text}; border-bottom: 2px solid {t.accent}; }}

    QFrame#Banner {{ background: {t.danger}; border-radius: 8px; }}
    QFrame#Banner QLabel {{ color: white; font-weight: 600; }}
    QFrame#Banner QPushButton {{ background: rgba(255,255,255,0.18); color: white; border: none; }}
    QFrame#Tile {{
        background: {t.surface}; border: 2px solid {t.border}; border-radius: 14px;
    }}
    QFrame#Tile[alarm="true"] {{ border-color: {t.danger}; background: {_rgba(t.danger, 0.12)}; }}
    QFrame#Tile[open="true"] {{ border-color: {t.warning}; }}
    QMenuBar {{ background: {t.bg}; color: {t.text}; }}
    QMenuBar::item:selected {{ background: {t.surface_alt}; border-radius: 4px; }}
    QMenu {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; padding: 4px; }}
    QMenu::item {{ padding: 5px 24px 5px 20px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
    QMenu::separator {{ height: 1px; background: {t.border}; margin: 4px 8px; }}
    QDockWidget {{ color: {t.text_muted}; font-weight: 600; }}
    QDockWidget::title {{ padding: 6px 10px; background: {t.bg}; }}
    QListWidget {{ background: {t.surface}; border: none; color: {t.text}; }}
    QListWidget::item {{ padding: 3px 4px; border-bottom: 1px solid {t.border}; }}
    QPlainTextEdit, QTextEdit {{
        background: {t.surface_alt}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px;
    }}
    QDateTimeEdit, QTimeEdit {{
        background: {t.surface_alt}; color: {t.text}; border: 1px solid {t.border};
        border-radius: 6px; padding: 4px 8px; min-height: 20px;
    }}
    """
