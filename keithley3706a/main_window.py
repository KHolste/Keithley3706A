"""Hauptfenster: Kanaltabelle, Live-Plot, Aufnahme, Alarme, Ereignisse."""

from __future__ import annotations

import json
import math
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QByteArray, QEvent, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction, QActionGroup, QBrush, QColor, QDesktopServices, QIcon, QKeySequence, QPageLayout,
    QPageSize, QPainter, QPdfWriter, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QComboBox, QDockWidget, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QSizePolicy, QStatusBar, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .acquisition import AcquisitionThread
from .alarms import AlarmEvent, AlarmMonitor, AlarmState
from .analysis import StatsTracker, rate_per_minute
from .channel_props import ChannelProps, apply_offsets
from .csv_loader import load_csv
from .dialogs import ChannelDialog, ScheduleDialog, TextDialog, shortcut_help_html
from .history import History
from .icons import app_icon
from .instrument import (
    REF_JUNCTIONS, SIMULATION_RESOURCE, TEMP_UNITS, Reading, ScanResult, format_channel_list,
    list_visa_resources,
)
from .notify import EmailNotifier, play_alarm_sound
from .profile import build_profile, export_script, load_profile, save_profile
from .recorder import CsvRecorder
from .scheduler import ScheduleRunner
from .settings import AppSettings
from .settings_dialog import SettingsDialog
from .theme import THEME_MODES, Theme, apply_theme, channel_color, resolve
from .tile_window import TileWindow

PROJECT_DIR = Path(__file__).resolve().parents[1]

TIME_WINDOWS = [
    (60, "1 min"), (300, "5 min"), (900, "15 min"), (3600, "1 h"),
    (6 * 3600, "6 h"), (24 * 3600, "24 h"), (0, "Alles"),
]

(COL_SHOW, COL_CHAN, COL_LABEL, COL_VALUE, COL_UNIT, COL_RATE, COL_MIN, COL_MAX, COL_MEAN,
 COL_STATUS) = range(10)
HEADERS = ["Plot", "Kanal", "Bezeichnung", "Messwert", "Einheit", "Rate/min", "Min", "Max",
           "Mittel", "Status"]


def card(title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    if title:
        lbl = QLabel(title)
        lbl.setObjectName("CardTitle")
        lay.addWidget(lbl)
    return frame, lay


def color_icon(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, 3, 3)
    p.end()
    return QIcon(pm)


def fmt(v: float, digits: int = 2, signed: bool = False) -> str:
    if v is None or math.isnan(v):
        return "–"
    return f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"


class MainWindow(QMainWindow):
    def __init__(self, settings_app: str | None = None):
        super().__init__()
        self.setObjectName("MainWindow")
        self.settings = AppSettings(settings_app)
        self.cfg = self.settings.measurement_config()
        self.labels: dict[int, str] = self.settings.labels
        self.hidden: set[int] = self.settings.hidden_channels
        self.props: dict[int, ChannelProps] = self.settings.channel_props
        self.history = History()
        self.stats = StatsTracker()
        self.alarms = AlarmMonitor(self.settings.alarm_hysteresis, self.settings.alarm_open)
        self.mailer = EmailNotifier(self)
        self.mailer.config = self.settings.email
        self.mailer.finished.connect(lambda ok, msg: self._log(msg, "info" if ok else "error"))
        self.thread: AcquisitionThread | None = None
        self.recorder: CsvRecorder | None = None
        self.schedule: ScheduleRunner | None = None
        self.idn = ""
        self.cards: dict[int, str] = {}
        self.last_scan_time: float | None = None
        self.last_scan_duration: float | None = None
        self.last_readings: dict[int, Reading] = {}
        self.rates: dict[int, float] = {}
        self.curves: dict[int, pg.PlotDataItem] = {}
        self.markers: list[tuple[float, str, pg.InfiniteLine]] = []
        self.overlays: list[dict] = []
        self.pending_events: list[str] = []
        self._busy = False
        self._error_state = False
        self._plot_dirty = False
        self._theme: Theme = resolve(self.settings.theme)
        self.tile_window: TileWindow | None = None

        self.setWindowTitle(f"{APP_NAME}  v{__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1500, 900)
        self.setDockNestingEnabled(True)
        self._build_ui()
        self._build_menus()
        self._rebuild_channel_table()
        self._refresh_resources()
        self._default_state = self.saveState()
        self._apply_theme(self.settings.theme)
        self._update_connection_ui()
        self._update_record_ui()

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(lambda *_: (self.showNormal(), self.activateWindow()))
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        geo = self.settings.qs.value("ui/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        state = self.settings.qs.value("ui/state")
        if isinstance(state, QByteArray):
            self.restoreState(state)

        self.plot_timer = QTimer(self)
        self.plot_timer.timeout.connect(self._update_plot)
        self.plot_timer.start(250)
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._on_tick)
        self.tick.start(1000)

    # ================================================================== UI-Aufbau
    def _build_ui(self) -> None:
        # Kopfzeile als feste Toolbar (über allen Docks)
        tb = QToolBar("Gerät")
        tb.setObjectName("TopBar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.toggleViewAction().setEnabled(False)
        tb.addWidget(self._build_top_bar())
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.addToolBarBreak()
        self.banner_bar = QToolBar("Alarm")
        self.banner_bar.setObjectName("AlarmBar")
        self.banner_bar.setMovable(False)
        self.banner_bar.toggleViewAction().setEnabled(False)
        self.banner_bar.addWidget(self._build_banner())
        self.addToolBar(Qt.TopToolBarArea, self.banner_bar)
        self.banner_bar.hide()

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(self._build_plot_card())
        self.setCentralWidget(central)

        self.channel_dock = self._dock("Kanäle", "ChannelDock", self._build_channel_card(),
                                       Qt.LeftDockWidgetArea)
        self.record_dock = self._dock("Aufnahme", "RecordDock", self._build_record_card(),
                                      Qt.BottomDockWidgetArea)
        self.event_dock = self._dock("Ereignisse", "EventDock", self._build_event_card(),
                                     Qt.BottomDockWidgetArea)
        self.splitDockWidget(self.record_dock, self.event_dock, Qt.Horizontal)
        self.resizeDocks([self.channel_dock], [700], Qt.Horizontal)
        self.resizeDocks([self.record_dock, self.event_dock], [900, 500], Qt.Horizontal)

        self.setStatusBar(QStatusBar())
        self.heartbeat = QLabel()
        self.statusBar().addWidget(self.heartbeat)
        self.status_info = QLabel()
        self.statusBar().addPermanentWidget(self.status_info)

    def _dock(self, title: str, name: str, widget: QWidget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
                      | QDockWidget.DockWidgetClosable)
        self.addDockWidget(area, d)
        return d

    def _build_top_bar(self) -> QWidget:
        frame, lay = card()
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row = QHBoxLayout()
        row.setSpacing(10)
        lay.addLayout(row)

        title = QLabel("Keithley 3706A")
        title.setObjectName("CardTitle")
        row.addWidget(title)
        row.addSpacing(6)
        row.addWidget(QLabel("Gerät:"))
        self.resource_combo = QComboBox()
        self.resource_combo.setMinimumWidth(300)
        self.resource_combo.setEditable(True)
        self.resource_combo.setToolTip("VISA-Ressource (USB-Gerät wird automatisch vorgeschlagen)")
        row.addWidget(self.resource_combo)
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("Flat")
        self.refresh_btn.setToolTip("Geräteliste aktualisieren")
        self.refresh_btn.clicked.connect(self._refresh_resources)
        row.addWidget(self.refresh_btn)

        self.connect_btn = QPushButton("Verbinden")
        self.connect_btn.setObjectName("Primary")
        self.connect_btn.setToolTip("Verbinden / Trennen (Strg+K)")
        self.connect_btn.clicked.connect(self._toggle_connection)
        row.addWidget(self.connect_btn)
        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setToolTip("Messung pausieren / fortsetzen (Leertaste)")
        self.pause_btn.toggled.connect(self._set_paused)
        row.addWidget(self.pause_btn)
        self.single_btn = QPushButton("Einzelmessung")
        self.single_btn.setToolTip("Einen einzelnen Scan auslösen, z. B. während der Pause (E)")
        self.single_btn.clicked.connect(self._single_scan)
        row.addWidget(self.single_btn)

        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("StatusDot")
        row.addWidget(self.status_dot)
        self.status_label = QLabel("Getrennt")
        self.status_label.setObjectName("Muted")
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.status_label.setMinimumWidth(220)
        row.addWidget(self.status_label, 1)

        self.config_label = QLabel()
        self.config_label.setObjectName("Muted")
        row.addWidget(self.config_label)

        settings_btn = QPushButton("⚙  Einstellungen")
        settings_btn.clicked.connect(self._open_settings)
        row.addWidget(settings_btn)

        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for key, text, tip in THEME_MODES:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(f"Design: {tip}")
            b.setProperty("themeKey", key)
            b.setFixedWidth(64 if key == "system" else 40)
            b.setStyleSheet("padding: 6px 4px;")
            self.theme_group.addButton(b)
            seg.addWidget(b)
        self.theme_group.buttonClicked.connect(
            lambda b: self._apply_theme(b.property("themeKey")))
        row.addLayout(seg)
        return frame

    def _build_banner(self) -> QWidget:
        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        self.banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        h = QHBoxLayout(self.banner)
        h.setContentsMargins(14, 8, 10, 8)
        self.banner_label = QLabel()
        h.addWidget(self.banner_label, 1)
        ack = QPushButton("Quittieren (Esc)")
        ack.clicked.connect(self._ack_banner)
        h.addWidget(ack)
        return self.banner

    def _build_channel_card(self) -> QWidget:
        frame, lay = card()
        frame.setMinimumWidth(380)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        hdr = self.table.horizontalHeader()
        for col in range(len(HEADERS)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_LABEL, QHeaderView.Stretch)
        hdr.setMinimumSectionSize(40)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.horizontalHeaderItem(COL_RATE).setToolTip(
            "Änderungsrate (Ausgleichsgerade über das eingestellte Fenster)")
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        for text, slot in (("Alle", lambda: self._set_all_visible(True)),
                           ("Keine", lambda: self._set_all_visible(False)),
                           ("Kanal-Einstellungen…", self._open_channel_dialog),
                           ("Statistik zurücksetzen", self._reset_stats)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.stats_label = QLabel()
        self.stats_label.setObjectName("Muted")
        lay.addWidget(self.stats_label)
        return frame

    def _build_plot_card(self) -> QWidget:
        frame, lay = card()
        head = QHBoxLayout()
        t = QLabel("Live-Verlauf")
        t.setObjectName("CardTitle")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(QLabel("Zeitfenster:"))
        self.window_combo = QComboBox()
        for sec, text in TIME_WINDOWS:
            self.window_combo.addItem(text, sec)
        idx = next((i for i, (s, _) in enumerate(TIME_WINDOWS)
                    if s == self.settings.time_window_s), 1)
        self.window_combo.setCurrentIndex(idx)
        self.window_combo.currentIndexChanged.connect(self._on_window_changed)
        head.addWidget(self.window_combo)
        self.follow_btn = QPushButton("Live folgen")
        self.follow_btn.setCheckable(True)
        self.follow_btn.setChecked(True)
        self.follow_btn.setToolTip("Automatisch mitlaufen und Y-Achse skalieren (L)")
        self.follow_btn.toggled.connect(lambda _: self._mark_dirty())
        head.addWidget(self.follow_btn)
        self.cursor_btn = QPushButton("Messcursor")
        self.cursor_btn.setCheckable(True)
        self.cursor_btn.setToolTip("Zwei verschiebbare Cursor mit Δt/ΔT-Anzeige (C)")
        self.cursor_btn.toggled.connect(self._toggle_cursors)
        head.addWidget(self.cursor_btn)
        self.zoom_btn = QPushButton("Rechteck-Zoom")
        self.zoom_btn.setCheckable(True)
        self.zoom_btn.setToolTip("Bereich mit der Maus aufziehen und hineinzoomen (Z). "
                                 "Rechtsklick → 'View All' zeigt wieder alles.")
        self.zoom_btn.toggled.connect(self._toggle_rect_zoom)
        head.addWidget(self.zoom_btn)
        marker_btn = QPushButton("⚑ Marker")
        marker_btn.setToolTip("Ereignis mit Kommentar markieren (M)")
        marker_btn.clicked.connect(self._add_marker)
        head.addWidget(marker_btn)
        lay.addLayout(head)

        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.plot.showGrid(x=True, y=True)
        self.legend = self.plot.addLegend(offset=(10, 10), labelTextSize="9pt")
        vb = self.plot.getViewBox()
        vb.sigRangeChangedManually.connect(lambda *_: self.follow_btn.setChecked(False))
        self.vline = pg.InfiniteLine(angle=90, movable=False)
        self.plot.addItem(self.vline, ignoreBounds=True)
        self.vline.hide()
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.cursor_a = pg.InfiniteLine(angle=90, movable=True, label="A",
                                        labelOpts={"position": 0.95})
        self.cursor_b = pg.InfiniteLine(angle=90, movable=True, label="B",
                                        labelOpts={"position": 0.95})
        for c in (self.cursor_a, self.cursor_b):
            c.sigPositionChanged.connect(self._update_cursor_info)
            self.plot.addItem(c, ignoreBounds=True)
            c.hide()
        lay.addWidget(self.plot, 1)
        self.hover_info = QLabel(" ")
        self.hover_info.setObjectName("Muted")
        self.hover_info.setWordWrap(True)
        lay.addWidget(self.hover_info)
        self.cursor_info = QLabel()
        self.cursor_info.setWordWrap(True)
        self.cursor_info.hide()
        lay.addWidget(self.cursor_info)
        return frame

    def _build_record_card(self) -> QWidget:
        frame, lay = card()
        row = QHBoxLayout()
        row.addWidget(QLabel("Ordner:"))
        self.folder_edit = QLineEdit(self.settings.csv_folder)
        self.folder_edit.setReadOnly(True)
        row.addWidget(self.folder_edit, 1)
        choose = QPushButton("Ordner wählen…")
        choose.clicked.connect(self._choose_folder)
        row.addWidget(choose)
        open_btn = QPushButton("Öffnen")
        open_btn.setToolTip("Ordner im Explorer öffnen")
        open_btn.clicked.connect(self._open_folder)
        row.addWidget(open_btn)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Dateiname:"))
        self.prefix_edit = QLineEdit(self.settings.csv_prefix)
        self.prefix_edit.setMaximumWidth(170)
        self.prefix_edit.setToolTip("Präfix; Datum und Uhrzeit werden automatisch angehängt")
        self.prefix_edit.editingFinished.connect(
            lambda: setattr(self.settings, "csv_prefix", self.prefix_edit.text().strip()))
        row2.addWidget(self.prefix_edit)
        row2.addWidget(QLabel("Notiz:"))
        self.note_edit = QLineEdit(self.settings.last_note)
        self.note_edit.setPlaceholderText("z. B. Probe, Aufbau, Bearbeiter … (wird in die Datei "
                                          "geschrieben)")
        self.note_edit.editingFinished.connect(
            lambda: setattr(self.settings, "last_note", self.note_edit.text().strip()))
        row2.addWidget(self.note_edit, 1)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.record_btn = QPushButton("●  Aufnahme starten")
        self.record_btn.setObjectName("Primary")
        self.record_btn.setMinimumWidth(190)
        self.record_btn.setToolTip("Aufnahme starten / beenden (Strg+R)")
        self.record_btn.clicked.connect(self._toggle_recording)
        row3.addWidget(self.record_btn)
        self.schedule_btn = QPushButton("⏲ Planen…")
        self.schedule_btn.setToolTip("Start/Stopp nach Uhrzeit, Dauer oder Temperaturschwelle")
        self.schedule_btn.clicked.connect(self._plan_recording)
        row3.addWidget(self.schedule_btn)
        self.record_info = QLabel()
        self.record_info.setObjectName("Muted")
        self.record_info.setWordWrap(True)
        row3.addWidget(self.record_info, 1)
        lay.addLayout(row3)
        return frame

    def _build_event_card(self) -> QWidget:
        frame, lay = card()
        self.event_list = QListWidget()
        self.event_list.setWordWrap(True)
        lay.addWidget(self.event_list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        save = QPushButton("Speichern…")
        save.clicked.connect(self._save_event_log)
        clear = QPushButton("Leeren")
        clear.clicked.connect(self.event_list.clear)
        row.addWidget(save)
        row.addWidget(clear)
        lay.addLayout(row)
        return frame

    # ================================================================== Menüs
    def _act(self, menu, text: str, slot=None, shortcut: str | None = None,
             checkable: bool = False, tip: str | None = None) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
        if checkable:
            a.setCheckable(True)
        if slot:
            (a.toggled if checkable else a.triggered).connect(slot)
        if tip:
            a.setStatusTip(tip)
        menu.addAction(a)
        return a

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&Datei")
        self._act(m, "Profil laden…", self._load_profile, "Ctrl+O",
                  tip="Alle Mess- und Anzeigeeinstellungen aus einer JSON-Datei laden")
        self._act(m, "Profil speichern…", self._save_profile, "Ctrl+S",
                  tip="Alle Einstellungen als JSON speichern (reproduzierbar)")
        self._act(m, "Messskript exportieren…", self._export_script,
                  tip="Python-Skript, das diese Messung ohne Oberfläche wiederholt")
        m.addSeparator()
        self._act(m, "Verlauf als CSV exportieren…", self._export_history)
        self._act(m, "Plot exportieren (PNG/SVG/PDF)…", self._export_plot, "Ctrl+P")
        m.addSeparator()
        self._act(m, "Vergleichsdatei laden…", self._load_overlay,
                  tip="Ältere CSV-Aufnahme gestrichelt im Plot einblenden")
        self._act(m, "Vergleichsdateien entfernen", self._clear_overlays)
        m.addSeparator()
        self._act(m, "Beenden", self.close, "Ctrl+Q")

        m = mb.addMenu("&Messung")
        self._act(m, "Verbinden / Trennen", self._toggle_connection, "Ctrl+K")
        self.pause_action = self._act(m, "Pause", None, "Space")
        self.pause_action.triggered.connect(lambda: self.pause_btn.toggle())
        self._act(m, "Einzelmessung", self._single_scan, "E")
        m.addSeparator()
        self._act(m, "Einstellungen…", self._open_settings, "Ctrl+E")
        self._act(m, "Kanal-Einstellungen…", self._open_channel_dialog, "Ctrl+Shift+K")
        self._act(m, "Statistik zurücksetzen", self._reset_stats)

        m = mb.addMenu("&Aufnahme")
        self._act(m, "Aufnahme starten / beenden", self._toggle_recording, "Ctrl+R")
        self._act(m, "Aufnahme planen…", self._plan_recording)
        self._act(m, "Plan abbrechen", self._cancel_schedule)
        m.addSeparator()
        self._act(m, "Marker setzen…", self._add_marker, "M")
        self._act(m, "Marker im Plot entfernen", self._clear_markers)
        self._act(m, "Speicherordner öffnen", self._open_folder)

        m = mb.addMenu("&Ansicht")
        self._act(m, "Kachelansicht", self._show_tiles, "T")
        self._act(m, "Vollbild", self._toggle_fullscreen, "F11")
        m.addSeparator()
        self._act(m, "Live folgen", lambda: self.follow_btn.toggle(), "L")
        self._act(m, "Messcursor", lambda: self.cursor_btn.toggle(), "C")
        self._act(m, "Rechteck-Zoom", lambda: self.zoom_btn.toggle(), "Z")
        self._act(m, "Verlauf leeren", self._clear_history)
        m.addSeparator()
        for dock in (self.channel_dock, self.record_dock, self.event_dock):
            m.addAction(dock.toggleViewAction())
        self._act(m, "Anordnung zurücksetzen", self._reset_layout)
        m.addSeparator()
        theme_menu = m.addMenu("Design")
        grp = QActionGroup(self)
        self.theme_actions = {}
        for key, text, tip in THEME_MODES:
            a = self._act(theme_menu, tip, checkable=True)
            a.triggered.connect(lambda _=False, k=key: self._apply_theme(k))
            grp.addAction(a)
            self.theme_actions[key] = a

        m = mb.addMenu("&Hilfe")
        self._act(m, "Tastenkürzel…", self._show_shortcuts, "F1")
        self._act(m, "Über…", self._show_about)

        esc = QAction(self)
        esc.setShortcut(QKeySequence("Esc"))
        esc.triggered.connect(self._ack_banner)
        self.addAction(esc)

    # ================================================================== Theme
    def _apply_theme(self, mode: str) -> None:
        self.settings.theme = mode
        self._theme = t = resolve(mode)
        apply_theme(QApplication.instance(), t)
        for b in self.theme_group.buttons():
            b.setChecked(b.property("themeKey") == mode)
        if mode in getattr(self, "theme_actions", {}):
            self.theme_actions[mode].setChecked(True)
        self.plot.setBackground(t.plot_bg)
        for name in ("left", "bottom"):
            ax = self.plot.getAxis(name)
            ax.setPen(pg.mkPen(t.border))
            ax.setTextPen(pg.mkPen(t.plot_fg))
        self.plot.showGrid(x=True, y=True, alpha=t.grid_alpha)
        self.legend.setLabelTextColor(t.plot_fg)
        self.legend.setBrush(pg.mkBrush(QColor(t.surface).name() + "cc"))
        self.legend.setPen(pg.mkPen(t.border))
        self.vline.setPen(pg.mkPen(t.text_muted, style=Qt.DashLine))
        for c, col in ((self.cursor_a, t.accent), (self.cursor_b, t.warning)):
            c.setPen(pg.mkPen(col, width=2))
            c.label.setColor(col)
        for _, _, line in self.markers:
            self._style_marker(line)
        for b in self.findChildren(QPushButton):
            self._repolish(b)
        self._update_status_dot()
        self._refresh_table_values()

    def changeEvent(self, event):
        if event.type() == QEvent.PaletteChange and self.settings.theme == "system":
            new = resolve("system")
            if new.name != self._theme.name:
                QTimer.singleShot(0, lambda: self._apply_theme("system"))
        super().changeEvent(event)

    @staticmethod
    def _repolish(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # ================================================================== Kanaltabelle
    def _rebuild_channel_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.cfg.channels))
        for row, ch in enumerate(self.cfg.channels):
            show = QTableWidgetItem()
            show.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            show.setCheckState(Qt.Unchecked if ch in self.hidden else Qt.Checked)
            show.setIcon(color_icon(channel_color(row)))
            self.table.setItem(row, COL_SHOW, show)
            chan = QTableWidgetItem(str(ch))
            chan.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, COL_CHAN, chan)
            label = QTableWidgetItem(self.labels.get(ch, ""))
            label.setFlags(Qt.ItemIsEnabled | Qt.ItemIsEditable)
            label.setToolTip("Doppelklick zum Umbenennen")
            self.table.setItem(row, COL_LABEL, label)
            for col in range(COL_VALUE, len(HEADERS)):
                it = QTableWidgetItem("–")
                it.setFlags(Qt.ItemIsEnabled)
                right = col in (COL_VALUE, COL_RATE, COL_MIN, COL_MAX, COL_MEAN)
                it.setTextAlignment((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
                if col == COL_VALUE:
                    f = it.font()
                    f.setPointSize(f.pointSize() + 2)
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(row, col, it)
        self.table.blockSignals(False)
        self._update_channel_tooltips()
        self._rebuild_curves()
        self._update_config_label()
        self._refresh_table_values()
        if self.tile_window is not None:
            self._setup_tiles()

    def _update_channel_tooltips(self) -> None:
        unit = TEMP_UNITS[self.cfg.units]
        for row, ch in enumerate(self.cfg.channels):
            p = self.props.get(ch, ChannelProps())
            parts = [f"Kanal {ch}", f"Typ {self.cfg.type_for(ch)}"]
            if p.offset:
                parts.append(f"Offset {p.offset:+g} {unit}")
            if p.alarm_low is not None:
                parts.append(f"Alarm < {p.alarm_low:g} {unit}")
            if p.alarm_high is not None:
                parts.append(f"Alarm > {p.alarm_high:g} {unit}")
            self.table.item(row, COL_CHAN).setToolTip("\n".join(parts))

    def _rebuild_curves(self) -> None:
        for c in self.curves.values():
            self.plot.removeItem(c)
        self.curves.clear()
        for row, ch in enumerate(self.cfg.channels):
            curve = self.plot.plot([], [], pen=pg.mkPen(channel_color(row), width=2),
                                   name=self._display_name(ch), connect="finite")
            # Darstellung wird ausgedünnt, gespeichert wird immer in voller Auflösung
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)
            curve.setVisible(ch not in self.hidden)
            self.curves[ch] = curve
        self._sync_legend()
        self._mark_dirty()

    def _sync_legend(self) -> None:
        self.legend.clear()
        for ch, curve in self.curves.items():
            if curve.isVisible():
                self.legend.addItem(curve, self._display_name(ch))
        for ov in self.overlays:
            for item, name in ov["curves"]:
                self.legend.addItem(item, name)

    def _display_name(self, ch: int) -> str:
        label = self.labels.get(ch, "").strip()
        return f"{ch} · {label}" if label else str(ch)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        ch = self.cfg.channels[item.row()]
        if item.column() == COL_SHOW:
            visible = item.checkState() == Qt.Checked
            (self.hidden.discard if visible else self.hidden.add)(ch)
            self.settings.hidden_channels = self.hidden
            self.curves[ch].setVisible(visible)
            self._sync_legend()
            self._mark_dirty()
            if self.tile_window is not None:
                self._setup_tiles()
        elif item.column() == COL_LABEL:
            text = item.text().strip()
            if text:
                self.labels[ch] = text
            else:
                self.labels.pop(ch, None)
            self.settings.labels = self.labels
            self._sync_legend()

    def _set_all_visible(self, visible: bool) -> None:
        self.table.blockSignals(True)
        for row, ch in enumerate(self.cfg.channels):
            self.table.item(row, COL_SHOW).setCheckState(Qt.Checked if visible else Qt.Unchecked)
            self.curves[ch].setVisible(visible)
        self.table.blockSignals(False)
        self.hidden = set() if visible else set(self.cfg.channels)
        self.settings.hidden_channels = self.hidden
        self._sync_legend()
        self._mark_dirty()
        if self.tile_window is not None:
            self._setup_tiles()

    def _refresh_table_values(self) -> None:
        t = self._theme
        stale = self._is_stale()
        alarm_bg = QColor(t.danger)
        alarm_bg.setAlpha(45)
        for row, ch in enumerate(self.cfg.channels):
            r = self.last_readings.get(ch)
            items = [self.table.item(row, c) for c in range(len(HEADERS))]
            if items[COL_VALUE] is None:
                continue
            st = self.stats.get(ch)
            state = self.alarms.state(ch)
            unit = r.unit if r else TEMP_UNITS.get(self.cfg.units, "")
            items[COL_UNIT].setText(unit)
            items[COL_RATE].setText(fmt(self.rates.get(ch, math.nan), 2, signed=True))
            items[COL_MIN].setText(fmt(st.min))
            items[COL_MAX].setText(fmt(st.max))
            items[COL_MEAN].setText(fmt(st.mean))
            if r is None:
                items[COL_VALUE].setText("–")
                status, color = "keine Daten", t.text_muted
            elif r.is_open:
                items[COL_VALUE].setText("offen")
                status, color = "⚠ Fühlerbruch / nicht angeschlossen", t.warning
            else:
                items[COL_VALUE].setText(f"{r.value:.2f}")
                if state == AlarmState.HIGH:
                    status, color = "▲ ALARM oben", t.danger
                elif state == AlarmState.LOW:
                    status, color = "▼ ALARM unten", t.danger
                elif stale:
                    status, color = "veraltet", t.text_muted
                else:
                    status, color = "OK", t.success
            items[COL_STATUS].setText(status)
            items[COL_STATUS].setForeground(QColor(color))
            vcol = (t.danger if state in (AlarmState.HIGH, AlarmState.LOW)
                    else t.warning if r is not None and r.is_open
                    else t.text_muted if stale or r is None else t.text)
            items[COL_VALUE].setForeground(QColor(vcol))
            in_alarm = state != AlarmState.OK
            for it in items:
                if it is not None:
                    it.setBackground(QBrush(alarm_bg) if in_alarm else QBrush())
        if self.stats.since:
            self.stats_label.setText(
                f"Statistik seit {datetime.fromtimestamp(self.stats.since):%d.%m. %H:%M:%S} · "
                f"Rate über {self.settings.rate_window_s} s")
        self._update_tiles()

    def _update_config_label(self) -> None:
        c = self.cfg
        ref = REF_JUNCTIONS[c.ref_junction].split(" ")[0]
        types = sorted(set(c.type_for(ch) for ch in c.channels))
        self.config_label.setText(
            f"Typ {'/'.join(types)} · Ref. {ref} · {c.nplc:g} PLC · {self.settings.interval_s:g} s")
        self.config_label.setToolTip(f"Kanäle: {format_channel_list(c.channels)}")
        self.plot.setLabel("left", f"Temperatur [{TEMP_UNITS[c.units]}]")

    def _reset_stats(self) -> None:
        self.stats.reset()
        self._log("Statistik zurückgesetzt")
        self._refresh_table_values()

    # ================================================================== Kacheln
    def _show_tiles(self) -> None:
        if self.tile_window is None:
            self.tile_window = TileWindow()
            self.tile_window.setWindowIcon(app_icon())
            self.tile_window.closed.connect(lambda: setattr(self, "tile_window", None))
            self._setup_tiles()
        self.tile_window.show()
        self.tile_window.raise_()
        self.tile_window.activateWindow()

    def _setup_tiles(self) -> None:
        visible = [ch for ch in self.cfg.channels if ch not in self.hidden]
        self.tile_window.set_channels(visible, self.cfg.channels)
        self._update_tiles()

    def _update_tiles(self) -> None:
        tw = self.tile_window
        if tw is None:
            return
        for ch in tw.tiles:
            r = self.last_readings.get(ch)
            state = self.alarms.state(ch)
            if r is None:
                val, info = "–", "keine Daten"
            elif r.is_open:
                val, info = "offen", "Fühlerbruch"
            else:
                val = f"{r.value:.1f} {r.unit}"
                rate = self.rates.get(ch, math.nan)
                info = f"{fmt(rate, 2, True)} {r.unit}/min" if not math.isnan(rate) else ""
                if state == AlarmState.HIGH:
                    info = "▲ ALARM  " + info
                elif state == AlarmState.LOW:
                    info = "▼ ALARM  " + info
            tw.update_tile(ch, self._display_name(ch), val, info,
                           state in (AlarmState.HIGH, AlarmState.LOW, AlarmState.OPEN),
                           r is not None and r.is_open)

    # ================================================================== Verbindung
    def _refresh_resources(self) -> None:
        current = self.resource_combo.currentText() or self.settings.resource
        self.resource_combo.clear()
        res = list_visa_resources()
        items = res + [SIMULATION_RESOURCE]
        self.resource_combo.addItems(items)
        if current and current in items:
            self.resource_combo.setCurrentText(current)
        elif res:
            self.resource_combo.setCurrentIndex(0)
        elif current:
            self.resource_combo.setCurrentText(current)

    def _toggle_connection(self) -> None:
        if self.thread is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self) -> None:
        resource = self.resource_combo.currentText().strip()
        if not resource:
            QMessageBox.warning(self, "Kein Gerät", "Bitte eine VISA-Ressource auswählen.")
            return
        self.settings.resource = resource
        self._error_state = False
        self.thread = AcquisitionThread(resource, self.cfg, self.settings.interval_s,
                                        auto_reconnect=self.settings.auto_reconnect)
        self.thread.connected.connect(self._on_connected)
        self.thread.disconnected.connect(self._on_disconnected)
        self.thread.scan_started.connect(self._on_scan_started)
        self.thread.scan_done.connect(self._on_scan)
        self.thread.error.connect(self._on_error)
        self.thread.status.connect(self._on_status)
        self.thread.finished.connect(self._on_thread_finished)
        self.thread.set_paused(self.pause_btn.isChecked())
        self.thread.start()
        self._log(f"Verbinde mit {resource}")
        self._update_connection_ui()

    def _disconnect(self) -> None:
        if self.recorder is not None:
            ret = QMessageBox.question(
                self, "Aufnahme läuft",
                "Eine Aufnahme läuft noch. Aufnahme beenden und Verbindung trennen?")
            if ret != QMessageBox.Yes:
                return
            self._stop_recording()
        if self.thread is not None:
            self.connect_btn.setEnabled(False)
            self.thread.stop()

    def _on_thread_finished(self) -> None:
        self.thread = None
        self._busy = False
        self.connect_btn.setEnabled(True)
        self._log("Verbindung getrennt")
        self._update_connection_ui()

    def _on_connected(self, idn: str, cards: dict) -> None:
        self.idn, self.cards = idn, cards
        self._error_state = False
        parts = idn.split(",")
        model = (f"{parts[1].strip()} (SN {parts[2].strip()}, FW {parts[3].strip()})"
                 if len(parts) >= 4 else idn)
        card_txt = "; ".join(f"Slot {s}: {c.split(',')[0]}" for s, c in cards.items())
        self.status_info.setText(f"{model}   {card_txt}")
        self.status_label.setText("Verbunden – Messung läuft")
        self._log(f"Verbunden: {model}, {card_txt}", "ok")
        self._check_cards(cards)
        self._update_connection_ui()

    def _check_cards(self, cards: dict) -> None:
        tc_cards = ("3720", "3721", "3724")
        slots = {ch // 1000 for ch in self.cfg.channels}
        for s in sorted(slots):
            if s not in cards:
                self._log(f"In Slot {s} steckt keine Karte – Kanalliste prüfen.", "warn")
            elif not cards[s].startswith(tc_cards):
                self._log(f"Karte in Slot {s} ({cards[s].split(',')[0]}) unterstützt evtl. "
                          "keine Thermoelement-Messung.", "warn")

    def _on_disconnected(self) -> None:
        self._error_state = True
        self._update_status_dot()

    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_error(self, msg: str) -> None:
        self._error_state = True
        self._log(f"Fehler: {msg}", "error")
        self.status_label.setText(msg)
        self._update_status_dot()
        if self.recorder is not None:
            self.pending_events.append(f"Verbindungsfehler: {msg[:80]}")

    def _update_connection_ui(self) -> None:
        connected = self.thread is not None
        self.connect_btn.setText("Trennen" if connected else "Verbinden")
        self.connect_btn.setObjectName("" if connected else "Primary")
        self._repolish(self.connect_btn)
        self.resource_combo.setEnabled(not connected)
        self.refresh_btn.setEnabled(not connected)
        self.single_btn.setEnabled(connected)
        if not connected:
            self.status_label.setText("Getrennt")
            self.heartbeat.setText("")
        self._update_status_dot()
        self._update_record_ui()

    def _update_status_dot(self) -> None:
        t = self._theme
        if self.thread is None:
            color, tip = t.text_muted, "Getrennt"
        elif self._error_state:
            color, tip = t.danger, "Fehler / Verbindung unterbrochen"
        elif self.last_scan_time is None:
            color, tip = t.warning, "Verbinde …"
        elif self.pause_btn.isChecked():
            color, tip = t.warning, "Pausiert"
        elif self._is_stale():
            color, tip = t.warning, "Keine neuen Messwerte"
        elif self._busy:
            color, tip = t.accent, "Scan läuft"
        else:
            color, tip = t.success, "Verbunden – Messung läuft"
        self.status_dot.setStyleSheet(f"color: {color};")
        self.status_dot.setToolTip(tip)

    # ================================================================== Messdaten
    def _set_paused(self, paused: bool) -> None:
        self.pause_btn.setText("▶ Weiter" if paused else "⏸ Pause")
        if self.thread is not None:
            self.thread.set_paused(paused)
            self._log("Messung pausiert" if paused else "Messung fortgesetzt")
            if paused and self.recorder is not None:
                self.pending_events.append("Pause")
        self._update_status_dot()

    def _single_scan(self) -> None:
        if self.thread is not None:
            self.thread.single_scan()

    def _on_scan_started(self) -> None:
        self._busy = True
        self._update_status_dot()

    def _on_scan(self, raw: ScanResult, duration: float) -> None:
        self._busy = False
        self._error_state = False
        result = apply_offsets(raw, self.props)
        self.last_scan_time = time.time()
        self.last_scan_duration = duration
        self.last_readings = {r.channel: r for r in result.readings}
        self.history.append(result)
        self.stats.add(result)
        win = self.settings.rate_window_s
        self.rates = {ch: rate_per_minute(self.history, ch, win) for ch in self.cfg.channels}

        for ev in self.alarms.evaluate(result, self.props):
            self._handle_alarm(ev)

        if self.schedule is not None:
            values = {r.channel: r.value for r in result.readings}
            self._run_schedule(values)

        if self.recorder is not None:
            try:
                self.recorder.write(result, " | ".join(self.pending_events))
                self.pending_events.clear()
            except OSError as exc:
                self._stop_recording()
                self._log(f"CSV-Schreibfehler: {exc}", "error")
                QMessageBox.critical(self, "Speicherfehler",
                                     f"CSV-Datei konnte nicht geschrieben werden:\n{exc}\n\n"
                                     "Die Aufnahme wurde beendet.")
        else:
            # Ohne Aufnahme nicht ansammeln (sonst unbegrenztes Wachstum und alte
            # Ereignisse in der ersten Zeile der nächsten Aufnahme)
            self.pending_events.clear()
        self._update_status_dot()
        self._refresh_table_values()
        self._update_record_ui()
        self._update_heartbeat()
        self._mark_dirty()

    def _is_stale(self) -> bool:
        if self.last_scan_time is None or self.thread is None or self.pause_btn.isChecked():
            return False
        limit = max(5.0, 3 * self.settings.interval_s + 2)
        return time.time() - self.last_scan_time > limit

    def _update_heartbeat(self) -> None:
        if self.thread is None or self.last_scan_time is None:
            return
        age = time.time() - self.last_scan_time
        dur = f" · Scandauer {self.last_scan_duration:.2f} s" if self.last_scan_duration else ""
        self.heartbeat.setText(f"♥ Letzter Scan vor {age:.1f} s{dur} · "
                               f"{self.history.size} Punkte im Speicher")

    def _on_tick(self) -> None:
        if self.thread is not None:
            self._update_heartbeat()
            if self._is_stale():
                self._update_status_dot()
                self._refresh_table_values()
        if self.schedule is not None:
            self._run_schedule(None)
        if self.recorder is not None or self.schedule is not None:
            self._update_record_ui()
        self._tick_count = getattr(self, "_tick_count", 0) + 1
        if self.recorder is not None and self._tick_count % 60 == 0:
            self._check_disk_space()

    # ================================================================== Speicherplatz
    DISK_WARN_BYTES = 1_000_000_000     # 1 GB: Warnung
    DISK_STOP_BYTES = 100_000_000       # 100 MB: Aufnahme sicher beenden

    def _free_bytes(self) -> int | None:
        try:
            return shutil.disk_usage(self.settings.csv_folder).free
        except OSError:
            return None

    def _check_disk_space(self) -> None:
        free = self._free_bytes()
        if free is None:
            return
        if free < self.DISK_STOP_BYTES:
            self._stop_recording()
            self._log(f"Aufnahme beendet: nur noch {free / 1e6:.0f} MB frei", "error")
            QMessageBox.critical(self, "Speicherplatz",
                                 f"Auf dem Laufwerk sind nur noch {free / 1e6:.0f} MB frei.\n"
                                 "Die Aufnahme wurde sicher beendet, die Datei ist vollständig.")
        elif free < self.DISK_WARN_BYTES and not getattr(self, "_disk_warned", False):
            self._disk_warned = True
            self._log(f"Wenig Speicherplatz: {free / 1e9:.2f} GB frei", "warn")

    # ================================================================== Alarme
    def _handle_alarm(self, ev: AlarmEvent) -> None:
        unit = TEMP_UNITS[self.cfg.units]
        name = self._display_name(ev.channel)
        if ev.state == AlarmState.HIGH:
            text = f"ALARM {name}: {ev.value:.2f} {unit} > {ev.limit:g} {unit}"
        elif ev.state == AlarmState.LOW:
            text = f"ALARM {name}: {ev.value:.2f} {unit} < {ev.limit:g} {unit}"
        elif ev.state == AlarmState.OPEN:
            text = f"ALARM {name}: Fühlerbruch / offenes Thermoelement"
        else:
            text = f"Alarm beendet {name}" + ("" if math.isnan(ev.value)
                                              else f": {ev.value:.2f} {unit}")
        self.pending_events.append(text)
        self._log(text, "alarm" if ev.is_alarm else "ok")
        if ev.is_alarm:
            if self.settings.alarm_sound:
                play_alarm_sound()
            if self.settings.alarm_tray and self.tray.isVisible():
                self.tray.showMessage("Temperatur-Alarm", text, QSystemTrayIcon.Warning, 8000)
            self.mailer.notify(
                f"ch{ev.channel}", f"[Keithley 3706A] {text}",
                f"{text}\n\nZeit: {datetime.fromtimestamp(ev.timestamp):%d.%m.%Y %H:%M:%S}\n"
                f"Gerät: {self.idn}\nRechner: {os.environ.get('COMPUTERNAME', '')}\n")
        self._update_banner()

    def _update_banner(self) -> None:
        active = self.alarms.active()
        if not active:
            self.banner_bar.hide()
            return
        unit = TEMP_UNITS[self.cfg.units]
        parts = []
        for ch, st in sorted(active.items()):
            r = self.last_readings.get(ch)
            val = "offen" if r is None or r.is_open else f"{r.value:.2f} {unit}"
            arrow = {"high": "▲", "low": "▼", "open": "⚠"}[st.value]
            parts.append(f"{arrow} {self._display_name(ch)}: {val}")
        self.banner_label.setText("⚠  ALARM   " + "    ".join(parts))
        self.banner_bar.show()

    def _ack_banner(self) -> None:
        self.banner_bar.hide()

    # ================================================================== Ereignisprotokoll
    def _log(self, text: str, kind: str = "info") -> None:
        t = self._theme
        color = {"error": t.danger, "alarm": t.danger, "warn": t.warning, "ok": t.success,
                 "marker": t.accent}.get(kind)
        item = QListWidgetItem(f"{datetime.now():%H:%M:%S}  {text}")
        if color:
            item.setForeground(QColor(color))
        self.event_list.addItem(item)
        if self.event_list.count() > 2000:
            self.event_list.takeItem(0)
        self.event_list.scrollToBottom()
        self.statusBar().showMessage(text, 8000)

    def _save_event_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Ereignisprotokoll speichern",
            str(Path(self.settings.csv_folder) / f"Ereignisse_{datetime.now():%Y-%m-%d_%H-%M}.txt"),
            "Text (*.txt)")
        if path:
            Path(path).write_text("\n".join(self.event_list.item(i).text()
                                            for i in range(self.event_list.count())),
                                  encoding="utf-8")

    # ================================================================== Plot
    def _mark_dirty(self) -> None:
        self._plot_dirty = True

    def _on_window_changed(self) -> None:
        self.settings.time_window_s = int(self.window_combo.currentData())
        self.follow_btn.setChecked(True)
        self._mark_dirty()

    def _update_plot(self) -> None:
        if not self._plot_dirty:
            return
        self._plot_dirty = False
        t = self.history.times()
        if t.size == 0:
            for c in self.curves.values():
                c.setData([], [])
            return
        window = int(self.window_combo.currentData())
        follow = self.follow_btn.isChecked()
        start = int(np.searchsorted(t, t[-1] - window)) if follow and window > 0 else 0
        for ch, curve in self.curves.items():
            if not curve.isVisible():
                continue
            v = self.history.values(ch)
            if v is None:
                curve.setData([], [])
            else:
                curve.setData(t[start:], v[start:], connect="finite")
        if follow:
            x1 = t[-1]
            x0 = max(t[0], x1 - window) if window > 0 else t[0]
            if x1 - x0 < 60:  # Mindestbreite, damit die Zeitachse HH:MM:SS zeigt
                x0 = x1 - 60
            vb = self.plot.getViewBox()
            vb.setXRange(x0, x1 + max(1.0, (x1 - x0) * 0.01), padding=0)
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        if self.cursor_btn.isChecked():
            self._update_cursor_info()

    def _value_at(self, ch: int, x: float) -> float:
        t = self.history.times()
        v = self.history.values(ch)
        if v is None or t.size == 0:
            return math.nan
        tol = max(2.0, 2 * self.settings.interval_s)
        if x < t[0] - tol or x > t[-1] + tol:   # Cursor außerhalb der Messdaten
            return math.nan
        i = int(np.clip(np.searchsorted(t, x), 0, t.size - 1))
        if i > 0 and abs(t[i - 1] - x) < abs(t[i] - x):
            i -= 1
        return float(v[i])

    def _on_mouse_moved(self, pos) -> None:
        vb = self.plot.getViewBox()
        if not self.plot.sceneBoundingRect().contains(pos) or self.history.size == 0:
            self.vline.hide()
            return
        x = vb.mapSceneToView(pos).x()
        t = self.history.times()
        i = int(np.clip(np.searchsorted(t, x), 0, t.size - 1))
        if i > 0 and abs(t[i - 1] - x) < abs(t[i] - x):
            i -= 1
        self.vline.setPos(t[i])
        self.vline.show()
        unit = TEMP_UNITS[self.cfg.units]
        parts = [datetime.fromtimestamp(t[i]).strftime("%H:%M:%S")]
        for ch, curve in self.curves.items():
            v = self.history.values(ch)
            if curve.isVisible() and v is not None and not math.isnan(v[i]):
                parts.append(f"{self._display_name(ch)}: {v[i]:.2f} {unit}")
        self.hover_info.setText("   ".join(parts))

    def _toggle_cursors(self, on: bool) -> None:
        if on:
            (x0, x1), _ = self.plot.getViewBox().viewRange()
            self.cursor_a.setPos(x0 + (x1 - x0) / 3)
            self.cursor_b.setPos(x0 + 2 * (x1 - x0) / 3)
            self.follow_btn.setChecked(False)
        self.cursor_a.setVisible(on)
        self.cursor_b.setVisible(on)
        self.cursor_info.setVisible(on)
        self._update_cursor_info()

    def _update_cursor_info(self) -> None:
        if not self.cursor_btn.isChecked():
            return
        a, b = self.cursor_a.value(), self.cursor_b.value()
        unit = TEMP_UNITS[self.cfg.units]
        dt = b - a
        parts = [f"<b>A</b> {datetime.fromtimestamp(a):%H:%M:%S}  <b>B</b> "
                 f"{datetime.fromtimestamp(b):%H:%M:%S}  <b>Δt</b> {dt:.1f} s"]
        shown = 0
        for ch, curve in self.curves.items():
            if not curve.isVisible():
                continue
            va, vb_ = self._value_at(ch, a), self._value_at(ch, b)
            if math.isnan(va) or math.isnan(vb_):
                continue
            shown += 1
            d = vb_ - va
            rate = f" ({d / dt * 60:+.3f} {unit}/min)" if abs(dt) > 0.5 else ""
            parts.append(f"{self._display_name(ch)}: ΔT {d:+.3f} {unit}{rate}")
        if not shown:
            parts.append("Cursor A und B auf Messdaten schieben, um ΔT zu sehen")
        self.cursor_info.setText("   ·   ".join(parts))

    def _toggle_rect_zoom(self, on: bool) -> None:
        vb = self.plot.getViewBox()
        vb.setMouseMode(pg.ViewBox.RectMode if on else pg.ViewBox.PanMode)
        if on:
            self.follow_btn.setChecked(False)

    def _clear_history(self) -> None:
        self.history.clear()
        self._mark_dirty()
        self._log("Verlauf geleert")

    # --------------------------------------------------------------- Marker
    def _style_marker(self, line: pg.InfiniteLine) -> None:
        t = self._theme
        line.setPen(pg.mkPen(t.accent, width=1.5, style=Qt.DotLine))
        line.label.setColor(t.accent)
        line.label.fill = pg.mkBrush(QColor(t.surface).name() + "dd")

    def _add_marker(self) -> None:
        dlg = TextDialog("Marker setzen", "Kommentar zum Ereignis (optional):", parent=self)
        if dlg.exec() != TextDialog.Accepted:
            return
        text = dlg.text() or f"Marker {len(self.markers) + 1}"
        ts = time.time()
        line = pg.InfiniteLine(pos=ts, angle=90, movable=False, label=f"⚑ {text}",
                               labelOpts={"position": 0.97, "anchors": [(1, 0), (1, 0)]})
        self._style_marker(line)
        self.plot.addItem(line, ignoreBounds=True)
        self.markers.append((ts, text, line))
        self.pending_events.append(f"Marker: {text}")
        self._log(f"Marker: {text}", "marker")

    def _clear_markers(self) -> None:
        for _, _, line in self.markers:
            self.plot.removeItem(line)
        self.markers.clear()

    # --------------------------------------------------------------- Vergleichsdateien
    def _load_overlay(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Vergleichsdatei laden",
                                              self.settings.csv_folder, "CSV (*.csv)")
        if not path:
            return
        try:
            data = load_csv(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Laden fehlgeschlagen", str(exc))
            return
        if data.times.size == 0:
            QMessageBox.warning(self, "Leere Datei", "Die Datei enthält keine Messwerte.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Zeitachse")
        box.setText("Wie soll die Vergleichsdatei angezeigt werden?")
        orig = box.addButton("Originalzeit", QMessageBox.AcceptRole)
        align = box.addButton("Start am aktuellen Verlauf ausrichten", QMessageBox.AcceptRole)
        box.addButton("Abbrechen", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() not in (orig, align):
            return
        shift = 0.0
        if box.clickedButton() is align:
            ref = self.history.times()[0] if self.history.size else time.time()
            shift = ref - data.times[0]
        tag = Path(path).stem[-19:]
        curves = []
        for s in data.series:
            idx = self.cfg.channels.index(s.channel) if s.channel in self.cfg.channels else \
                len(curves) + len(self.cfg.channels)
            pen = pg.mkPen(channel_color(idx), width=1.5, style=Qt.DashLine)
            item = self.plot.plot(data.times + shift, s.values, pen=pen, connect="finite")
            item.setDownsampling(auto=True, method="peak")
            item.setClipToView(True)
            curves.append((item, f"{s.name} ({tag})"))
        self.overlays.append({"path": path, "curves": curves})
        self._sync_legend()
        self.follow_btn.setChecked(False)
        self.plot.getViewBox().autoRange()
        self._log(f"Vergleichsdatei geladen: {Path(path).name} ({len(curves)} Kanäle"
                  f"{', verschoben' if shift else ''})")

    def _clear_overlays(self) -> None:
        for ov in self.overlays:
            for item, _ in ov["curves"]:
                self.plot.removeItem(item)
        self.overlays.clear()
        self._sync_legend()

    # --------------------------------------------------------------- Export
    def _export_plot(self) -> None:
        default = Path(self.settings.csv_folder) / f"Plot_{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
        path, flt = QFileDialog.getSaveFileName(
            self, "Plot exportieren", str(default),
            "PNG-Bild (*.png);;SVG-Vektorgrafik (*.svg);;PDF-Dokument (*.pdf)")
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            if suffix == ".svg":
                # Qt-eigener SVG-Generator (pyqtgraphs SVGExporter ist mit Qt 6.12 defekt)
                from PySide6.QtSvg import QSvgGenerator
                gen = QSvgGenerator()
                gen.setFileName(path)
                gen.setSize(self.plot.size())
                gen.setViewBox(self.plot.rect())
                gen.setTitle(APP_NAME)
                painter = QPainter(gen)
                self.plot.render(painter)
                painter.end()
            elif suffix == ".pdf":
                writer = QPdfWriter(path)
                writer.setPageSize(QPageSize(QPageSize.A4))
                writer.setPageOrientation(QPageLayout.Landscape)
                writer.setResolution(150)
                painter = QPainter(writer)
                rect = painter.viewport()
                title = (f"{APP_NAME} – {datetime.now():%d.%m.%Y %H:%M}   "
                         f"{self.note_edit.text().strip()}")
                painter.drawText(rect.adjusted(20, 10, -20, 0), Qt.AlignTop | Qt.AlignLeft, title)
                target = QRectF(rect.adjusted(20, 70, -20, -20))
                self.plot.render(painter, target)
                painter.end()
            else:
                from pyqtgraph.exporters import ImageExporter
                exp = ImageExporter(self.plot.plotItem)
                exp.parameters()["width"] = max(1600, int(self.plot.width() * 2))
                exp.export(path)
        except Exception as exc:
            QMessageBox.critical(self, "Export fehlgeschlagen", str(exc))
            return
        self._log(f"Plot exportiert: {path}")

    def _export_history(self) -> None:
        if self.history.size == 0:
            QMessageBox.information(self, "Kein Verlauf", "Es sind noch keine Messwerte vorhanden.")
            return
        try:
            rec = CsvRecorder(self.settings.csv_folder, self.cfg.channels, self.labels,
                              TEMP_UNITS[self.cfg.units],
                              self._metadata() | {"Hinweis": "Export des Verlaufs im Speicher"},
                              self.settings.csv_format,
                              prefix=(self.prefix_edit.text().strip() or "Temperaturen") + "_Export",
                              start_time=datetime.fromtimestamp(self.history.times()[0]))
            t = self.history.times()
            unit = TEMP_UNITS[self.cfg.units]
            marker_rows = {}
            for ts, text, _ in self.markers:
                i = int(np.clip(np.searchsorted(t, ts), 0, t.size - 1))
                marker_rows.setdefault(i, []).append(f"Marker: {text}")
            for i in range(t.size):
                readings = []
                for ch in self.cfg.channels:
                    v = self.history.values(ch)
                    val = float(v[i]) if v is not None else math.nan
                    readings.append(Reading(ch, val, unit, math.isnan(val)))
                rec.write(ScanResult(float(t[i]), readings), " | ".join(marker_rows.get(i, [])))
            rec.close()
        except OSError as exc:
            QMessageBox.critical(self, "Speicherfehler", f"Export fehlgeschlagen:\n{exc}")
            return
        self._log(f"Verlauf exportiert: {rec.path} ({rec.rows} Zeilen)")
        QMessageBox.information(self, "Export abgeschlossen",
                                f"{rec.rows} Zeilen gespeichert in:\n{rec.path}")

    # ================================================================== Profile
    def _profile(self) -> dict:
        return build_profile(
            resource=self.resource_combo.currentText().strip(),
            interval_s=self.settings.interval_s, cfg=self.cfg, props=self.props,
            labels=self.labels, hidden=self.hidden,
            csv={"folder": self.settings.csv_folder, "format": self.settings.csv_format,
                 "prefix": self.prefix_edit.text().strip(),
                 "rotate_daily": self.settings.rotate_daily},
            alarm={"hysteresis": self.settings.alarm_hysteresis,
                   "open_is_alarm": self.settings.alarm_open},
            note=self.note_edit.text().strip())

    def _profile_dir(self) -> str:
        return self.settings.last_profile_dir or self.settings.csv_folder

    def _save_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Profil speichern",
                                              str(Path(self._profile_dir()) / "Messprofil.json"),
                                              "Messprofil (*.json)")
        if not path:
            return
        save_profile(path, self._profile())
        self.settings.last_profile_dir = str(Path(path).parent)
        self._log(f"Profil gespeichert: {path}")

    def _load_profile(self) -> None:
        if self.recorder is not None:
            QMessageBox.information(self, "Aufnahme läuft",
                                    "Profile können nur ohne laufende Aufnahme geladen werden.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Profil laden", self._profile_dir(),
                                              "Messprofil (*.json)")
        if not path:
            return
        try:
            p = load_profile(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Profil ungültig", str(exc))
            return
        self.settings.last_profile_dir = str(Path(path).parent)
        self.cfg, self.props, self.labels, self.hidden = (p["config"], p["props"], p["labels"],
                                                          p["hidden"])
        self.settings.set_measurement_config(self.cfg)
        self.settings.channel_props = self.props
        self.settings.labels = self.labels
        self.settings.hidden_channels = self.hidden
        self.settings.interval_s = p["interval_s"]
        csv = p["csv"]
        if csv.get("folder"):
            self.settings.csv_folder = csv["folder"]
            self.folder_edit.setText(csv["folder"])
        if csv.get("format"):
            self.settings.csv_format = csv["format"]
        if csv.get("prefix"):
            self.prefix_edit.setText(csv["prefix"])
            self.settings.csv_prefix = csv["prefix"]
        if "rotate_daily" in csv:
            self.settings.rotate_daily = bool(csv["rotate_daily"])
        al = p["alarm"]
        if "hysteresis" in al:
            self.settings.alarm_hysteresis = float(al["hysteresis"])
        if "open_is_alarm" in al:
            self.settings.alarm_open = bool(al["open_is_alarm"])
        self.alarms = AlarmMonitor(self.settings.alarm_hysteresis, self.settings.alarm_open)
        if p["note"]:
            self.note_edit.setText(p["note"])
        if p["resource"] and self.thread is None:
            self.resource_combo.setCurrentText(p["resource"])
        self._after_config_change(channels_changed=True)
        self._log(f"Profil geladen: {Path(path).name}")

    def _export_script(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Messskript exportieren",
            str(Path(self._profile_dir()) / f"messung_{datetime.now():%Y%m%d}.py"),
            "Python-Skript (*.py)")
        if not path:
            return
        export_script(path, self._profile(), PROJECT_DIR)
        self.settings.last_profile_dir = str(Path(path).parent)
        self._log(f"Messskript exportiert: {path}")
        QMessageBox.information(
            self, "Messskript exportiert",
            f"Das Skript wiederholt diese Messung ohne Oberfläche:\n\n"
            f'py -3.11 "{path}" --dauer 3600\n\n'
            "Ohne --dauer läuft es bis Strg+C. Die GUI darf dabei nicht verbunden sein.")

    # ================================================================== Aufnahme
    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Speicherordner wählen",
                                                  self.settings.csv_folder)
        if folder:
            self.settings.csv_folder = folder
            self.folder_edit.setText(folder)
            self.settings.sync()

    def _open_folder(self) -> None:
        folder = self.settings.csv_folder
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _metadata(self) -> dict[str, str]:
        c = self.cfg
        unit = TEMP_UNITS[c.units]
        special = []
        for ch in c.channels:
            p = self.props.get(ch)
            if p is None:
                continue
            bits = []
            if p.tc_type:
                bits.append(f"Typ {p.tc_type}")
            if p.offset:
                bits.append(f"Offset {p.offset:+g} {unit}")
            if p.alarm_low is not None or p.alarm_high is not None:
                bits.append(f"Alarm {fmt(p.alarm_low, 1)}…{fmt(p.alarm_high, 1)}")
            if bits:
                special.append(f"{ch}: {', '.join(bits)}")
        return {
            "Programm": f"{APP_NAME} v{__version__}",
            "Gerät": self.idn or "-",
            "Karten": "; ".join(f"Slot {s}: {v}" for s, v in self.cards.items()) or "-",
            "Thermoelement-Typ": c.tc_type,
            "Vergleichsstelle": REF_JUNCTIONS[c.ref_junction]
            + (f" ({c.sim_ref_temp:g} °C)" if c.ref_junction == "SIMULATED" else ""),
            "Integrationszeit [PLC]": f"{c.nplc:g}",
            "Messintervall [s]": f"{self.settings.interval_s:g}",
            "Kanal-Konfiguration": "; ".join(special) or "Standard (keine Offsets)",
            "Rechner / Benutzer": f"{os.environ.get('COMPUTERNAME', '')} / "
                                  f"{os.environ.get('USERNAME', '')}",
            "Notiz": self.note_edit.text().strip(),
            "Leere Zellen": "offenes Thermoelement / kein Messwert",
        }

    def _toggle_recording(self) -> None:
        if self.recorder is None:
            if self.thread is None:
                QMessageBox.information(self, "Nicht verbunden",
                                        "Bitte zuerst mit dem Gerät verbinden.")
                return
            self._cancel_schedule(quiet=True)
            self._start_recording()
        else:
            self._cancel_schedule(quiet=True)
            self._stop_recording()

    def _start_recording(self) -> bool:
        folder = self.settings.csv_folder
        self.settings.csv_prefix = self.prefix_edit.text().strip()
        self.settings.last_note = self.note_edit.text().strip()
        free = self._free_bytes()
        if free is not None and free < self.DISK_STOP_BYTES:
            QMessageBox.critical(self, "Speicherplatz",
                                 f"Im Zielordner sind nur {free / 1e6:.0f} MB frei. "
                                 "Bitte einen anderen Ordner wählen.")
            return False
        self._disk_warned = False
        try:
            self.recorder = CsvRecorder(folder, self.cfg.channels, self.labels,
                                        TEMP_UNITS[self.cfg.units], self._metadata(),
                                        self.settings.csv_format,
                                        prefix=self.settings.csv_prefix or "Temperaturen",
                                        rotate_daily=self.settings.rotate_daily)
            self._write_sidecar()
        except OSError as exc:
            QMessageBox.critical(self, "Speicherfehler",
                                 f"Datei konnte nicht angelegt werden:\n{exc}\n\n"
                                 "Bitte einen anderen Ordner wählen.")
            self.recorder = None
            return False
        self.pending_events.append("Aufnahme gestartet")
        self._log(f"Aufnahme gestartet: {self.recorder.path}", "ok")
        self._update_record_ui()
        return True

    def _write_sidecar(self, final: bool = False) -> None:
        rec = self.recorder
        if rec is None or not self.settings.write_sidecar:
            return
        data = {"profile": self._profile(), "metadata": self._metadata(),
                "files": [p.name for p in rec.files],
                "start": rec.start_time.isoformat(timespec="seconds")}
        if final:
            data["end"] = datetime.now().isoformat(timespec="seconds")
            data["rows"] = rec.rows
            data["markers"] = [{"time": datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
                                "text": text} for ts, text, _ in self.markers
                               if ts >= rec.start_time.timestamp()]
        rec.files[0].with_suffix(".json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _stop_recording(self) -> None:
        if self.recorder is not None:
            rec = self.recorder
            try:
                self._write_sidecar(final=True)
            except OSError:
                pass
            rec.close()
            self.recorder = None
            files = ", ".join(p.name for p in rec.files)
            self._log(f"Aufnahme beendet: {files} ({rec.rows} Zeilen)", "ok")
            self.record_info.setText(f"Letzte Aufnahme: {rec.path}  ·  {rec.rows} Zeilen")
        self._update_record_ui()

    def _update_record_ui(self) -> None:
        rec = self.recorder
        if rec is None:
            self.record_btn.setText("●  Aufnahme starten")
            self.record_btn.setObjectName("Primary")
            if self.schedule is not None:
                self.record_info.setText(f"⏲ Geplant: {self.schedule.describe(TEMP_UNITS[self.cfg.units])}"
                                         " – wartet auf Startbedingung")
            elif not self.record_info.text() or self.record_info.text().startswith("⏲"):
                self.record_info.setText("Keine Aufnahme aktiv. Beim Start wird eine neue "
                                         "CSV-Datei im gewählten Ordner angelegt.")
        else:
            self.record_btn.setText("■  Aufnahme beenden")
            self.record_btn.setObjectName("Danger")
            dur = datetime.now() - rec.start_time
            h, rem = divmod(int(dur.total_seconds()), 3600)
            m, s = divmod(rem, 60)
            extra = (f"  ·  ⏲ {self.schedule.stop.describe(TEMP_UNITS[self.cfg.units], True)}"
                     if self.schedule is not None else "")
            part = f" (Datei {len(rec.files)})" if len(rec.files) > 1 else ""
            self.record_info.setText(f"● Aufnahme läuft  ·  {rec.path.name}{part}  ·  "
                                     f"{rec.rows} Zeilen  ·  {h:02d}:{m:02d}:{s:02d}{extra}")
        self._repolish(self.record_btn)
        self.record_btn.setEnabled(self.thread is not None or rec is not None)

    # --------------------------------------------------------------- Zeitplan
    def _plan_recording(self) -> None:
        if self.recorder is not None:
            QMessageBox.information(self, "Aufnahme läuft",
                                    "Bitte die laufende Aufnahme zuerst beenden.")
            return
        dlg = ScheduleDialog(self.cfg.channels, TEMP_UNITS[self.cfg.units], self)
        if dlg.exec() != ScheduleDialog.Accepted:
            return
        self.schedule = ScheduleRunner(dlg.start_trigger, dlg.stop_trigger)
        self._log(f"Aufnahme geplant: {self.schedule.describe(TEMP_UNITS[self.cfg.units])}")
        if self.thread is None:
            self._log("Hinweis: Für die geplante Aufnahme muss das Gerät verbunden sein.", "warn")
        self._run_schedule(None)
        self._update_record_ui()

    def _cancel_schedule(self, quiet: bool = False) -> None:
        if self.schedule is not None:
            self.schedule = None
            if not quiet:
                self._log("Geplante Aufnahme abgebrochen")
            self._update_record_ui()

    def _run_schedule(self, readings: dict[int, float] | None) -> None:
        sch = self.schedule
        if sch is None:
            return
        if sch.state == ScheduleRunner.WAITING and self.thread is None:
            return
        action = sch.check(datetime.now(), readings)
        if action == "start":
            self._log("Geplante Aufnahme startet")
            if not self._start_recording():
                self.schedule = None
        elif action == "stop":
            self._log("Geplante Aufnahme endet")
            self.schedule = None
            self._stop_recording()
        if sch.stop.mode == "manual" and sch.state == ScheduleRunner.RECORDING:
            self.schedule = None  # ab jetzt normale, manuell zu beendende Aufnahme
        self._update_record_ui()

    # ================================================================== Einstellungen
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self.cfg, self.recorder is not None, self)
        if dlg.exec() != SettingsDialog.Accepted:
            return
        new_cfg = dlg.result_config(self.cfg.channel_types)
        changed = new_cfg.channels != self.cfg.channels or new_cfg.units != self.cfg.units
        dlg.store(self.settings)
        self.mailer.config = self.settings.email
        self.alarms.hysteresis = self.settings.alarm_hysteresis
        self.alarms.open_is_alarm = self.settings.alarm_open
        self.cfg = new_cfg
        self.settings.set_measurement_config(new_cfg)
        self._after_config_change(changed)

    def _open_channel_dialog(self) -> None:
        dlg = ChannelDialog(self.cfg.channels, self.labels, self.props, self.cfg.tc_type,
                            TEMP_UNITS[self.cfg.units], self)
        if dlg.exec() != ChannelDialog.Accepted:
            return
        old_types = dict(self.cfg.channel_types)
        old_offsets = {ch: p.offset for ch, p in self.props.items()}
        self.labels = {**{ch: v for ch, v in self.labels.items() if ch not in self.cfg.channels},
                       **dlg.result_labels}
        self.props = {**{ch: p for ch, p in self.props.items() if ch not in self.cfg.channels},
                      **dlg.result_props}
        self.settings.labels = self.labels
        self.settings.channel_props = self.props
        self.cfg.channel_types = {ch: p.tc_type for ch, p in self.props.items() if p.tc_type}
        new_offsets = {ch: p.offset for ch, p in self.props.items()}
        if old_types != self.cfg.channel_types or old_offsets != new_offsets:
            # Messwerte springen -> Statistik/Rate würden verfälscht
            self.stats.reset()
            self.rates = {}
            if self.recorder is not None:
                self.pending_events.append("Kanal-Konfiguration geändert (Typ/Offset)")
        self.alarms.reset()
        self._update_banner()
        self._after_config_change(channels_changed=False)
        self._sync_legend()
        self._log("Kanal-Einstellungen übernommen")

    def _after_config_change(self, channels_changed: bool) -> None:
        self.settings.sync()
        if channels_changed:
            self.last_readings = {}
            self.history.clear()
            self.stats.reset()
            self.alarms.reset()
            self._update_banner()
            self._rebuild_channel_table()
        else:
            self._update_channel_tooltips()
        self._update_config_label()
        self._refresh_table_values()
        if self.thread is not None:
            self.thread.apply_config(self.cfg)
            self.thread.set_interval(self.settings.interval_s)

    def _reset_layout(self) -> None:
        self.restoreState(self._default_state)
        for d in (self.channel_dock, self.record_dock, self.event_dock):
            d.show()

    def _toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    # ================================================================== Hilfe
    def _show_shortcuts(self) -> None:
        QMessageBox.information(self, "Tastenkürzel", shortcut_help_html())

    def _show_about(self) -> None:
        QMessageBox.about(
            self, "Über",
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            "Thermoelement-Erfassung für Keithley 3706A über VISA/TSP.<br>"
            f"Gerät: {self.idn or 'nicht verbunden'}<br><br>"
            "Headless: <code>py -3.11 -m keithley3706a.headless profil.json</code>")

    # ================================================================== Beenden
    def closeEvent(self, event) -> None:
        if self.recorder is not None:
            ret = QMessageBox.question(self, "Aufnahme läuft",
                                       "Eine Aufnahme läuft noch. Beenden und Programm schließen?")
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self._stop_recording()
        if self.thread is not None:
            self.thread.stop()
            self.thread.wait(30000)
        if self.tile_window is not None:
            self.tile_window.close()
        self.tray.hide()
        self.settings.qs.setValue("ui/geometry", self.saveGeometry())
        self.settings.qs.setValue("ui/state", self.saveState())
        self.settings.sync()
        super().closeEvent(event)
