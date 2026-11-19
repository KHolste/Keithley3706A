"""Hauptfenster: Kanaltabelle, Live-Plot, CSV-Aufnahme."""

from __future__ import annotations

import math
import os
import time
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPixmap, QPainter, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSizePolicy, QSplitter, QStatusBar, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .acquisition import AcquisitionThread
from .history import History
from .instrument import (
    REF_JUNCTIONS, SIMULATION_RESOURCE, TEMP_UNITS, Reading, ScanResult,
    format_channel_list, list_visa_resources,
)
from .recorder import CsvRecorder
from .settings import AppSettings
from .settings_dialog import SettingsDialog
from .theme import Theme, apply_theme, channel_color, resolve

TIME_WINDOWS = [
    (60, "1 min"), (300, "5 min"), (900, "15 min"), (3600, "1 h"),
    (6 * 3600, "6 h"), (24 * 3600, "24 h"), (0, "Alles"),
]

COL_SHOW, COL_CHAN, COL_LABEL, COL_VALUE, COL_UNIT, COL_STATUS = range(6)


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = AppSettings()
        self.cfg = self.settings.measurement_config()
        self.labels: dict[int, str] = self.settings.labels
        self.hidden: set[int] = self.settings.hidden_channels
        self.history = History()
        self.thread: AcquisitionThread | None = None
        self.recorder: CsvRecorder | None = None
        self.idn = ""
        self.cards: dict[int, str] = {}
        self.last_scan_time: float | None = None
        self.last_readings: dict[int, Reading] = {}
        self.curves: dict[int, pg.PlotDataItem] = {}
        self._plot_dirty = False
        self._theme: Theme = resolve(self.settings.theme)

        self.setWindowTitle(f"{APP_NAME}  v{__version__}")
        self.resize(1400, 860)
        self._build_ui()
        self._rebuild_channel_table()
        self._refresh_resources()
        self._apply_theme(self.settings.theme)
        self._update_connection_ui(False)
        self._update_record_ui()

        geo = self.settings.qs.value("ui/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        split = self.settings.qs.value("ui/splitter")
        if isinstance(split, QByteArray):
            self.splitter.restoreState(split)

        self.plot_timer = QTimer(self)
        self.plot_timer.timeout.connect(self._update_plot)
        self.plot_timer.start(250)
        self.watchdog = QTimer(self)
        self.watchdog.timeout.connect(self._check_stale)
        self.watchdog.start(1000)

    # ================================================================== UI-Aufbau
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 8)
        root.setSpacing(12)
        self.setCentralWidget(central)

        root.addWidget(self._build_top_bar())

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_channel_card())
        self.splitter.addWidget(self._build_plot_card())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([520, 880])
        root.addWidget(self.splitter, 1)

        root.addWidget(self._build_record_card())

        self.setStatusBar(QStatusBar())
        self.status_info = QLabel()
        self.statusBar().addPermanentWidget(self.status_info)

    def _build_top_bar(self) -> QWidget:
        frame, lay = card()
        row = QHBoxLayout()
        row.setSpacing(10)
        lay.addLayout(row)

        title = QLabel("Keithley 3706A")
        title.setObjectName("CardTitle")
        row.addWidget(title)
        row.addSpacing(10)

        row.addWidget(QLabel("Gerät:"))
        self.resource_combo = QComboBox()
        self.resource_combo.setMinimumWidth(320)
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
        self.connect_btn.clicked.connect(self._toggle_connection)
        row.addWidget(self.connect_btn)

        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("StatusDot")
        row.addWidget(self.status_dot)
        self.status_label = QLabel("Getrennt")
        self.status_label.setObjectName("Muted")
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(self.status_label, 1)

        self.config_label = QLabel()
        self.config_label.setObjectName("Muted")
        row.addWidget(self.config_label)

        settings_btn = QPushButton("⚙  Einstellungen")
        settings_btn.clicked.connect(self._open_settings)
        row.addWidget(settings_btn)

        # Theme-Umschalter (segmentiert)
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for key, text, tip in (("system", "Auto", "Wie Windows"), ("light", "☀", "Hell"),
                               ("dark", "☾", "Dunkel")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(f"Design: {tip}")
            b.setProperty("themeKey", key)
            b.setFixedWidth(64 if key == "system" else 40)
            b.setStyleSheet("padding: 6px 4px;")
            self.theme_group.addButton(b)
            seg.addWidget(b)
            if key == self.settings.theme:
                b.setChecked(True)
        self.theme_group.buttonClicked.connect(
            lambda b: self._apply_theme(b.property("themeKey")))
        row.addLayout(seg)
        return frame

    def _build_channel_card(self) -> QWidget:
        frame, lay = card("Kanäle")
        frame.setMinimumWidth(420)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Plot", "Kanal", "Bezeichnung", "Messwert",
                                              "Einheit", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.SelectedClicked)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(COL_SHOW, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_CHAN, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_LABEL, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_VALUE, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_UNIT, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        all_btn = QPushButton("Alle anzeigen")
        all_btn.clicked.connect(lambda: self._set_all_visible(True))
        none_btn = QPushButton("Keine anzeigen")
        none_btn.clicked.connect(lambda: self._set_all_visible(False))
        btns.addWidget(all_btn)
        btns.addWidget(none_btn)
        btns.addStretch(1)
        hint = QLabel("Bezeichnung: Doppelklick")
        hint.setToolTip("Doppelklick auf eine Bezeichnung, um den Kanal zu benennen. "
                        "Namen werden gespeichert und in Plot und CSV verwendet.")
        hint.setObjectName("Muted")
        btns.addWidget(hint)
        lay.addLayout(btns)
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
        self.follow_btn.setToolTip("Automatisch mitlaufen und Y-Achse skalieren. Wird beim "
                                   "Zoomen/Verschieben mit der Maus deaktiviert.")
        self.follow_btn.toggled.connect(lambda _: self._mark_dirty())
        head.addWidget(self.follow_btn)
        clear_btn = QPushButton("Verlauf leeren")
        clear_btn.clicked.connect(self._clear_history)
        head.addWidget(clear_btn)
        export_btn = QPushButton("Verlauf exportieren…")
        export_btn.setToolTip("Den gesamten Verlauf im Speicher als CSV speichern")
        export_btn.clicked.connect(self._export_history)
        head.addWidget(export_btn)
        lay.addLayout(head)

        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.plot.showGrid(x=True, y=True)
        self.plot.setLabel("left", "Temperatur", units=None)
        self.legend = self.plot.addLegend(offset=(10, 10), labelTextSize="9pt")
        self.plot.getViewBox().sigRangeChangedManually.connect(
            lambda *_: self.follow_btn.setChecked(False))
        self.vline = pg.InfiniteLine(angle=90, movable=False)
        self.plot.addItem(self.vline, ignoreBounds=True)
        self.vline.hide()
        self.plot.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self.hover_info = QLabel(" ")
        self.hover_info.setObjectName("Muted")
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.hover_info)
        return frame

    def _build_record_card(self) -> QWidget:
        frame, lay = card()
        row = QHBoxLayout()
        row.setSpacing(10)
        t = QLabel("Aufnahme")
        t.setObjectName("CardTitle")
        row.addWidget(t)
        row.addSpacing(8)
        row.addWidget(QLabel("Ordner:"))
        self.folder_edit = QLineEdit(self.settings.csv_folder)
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setMinimumWidth(300)
        row.addWidget(self.folder_edit, 2)
        choose = QPushButton("Ordner wählen…")
        choose.clicked.connect(self._choose_folder)
        row.addWidget(choose)
        open_btn = QPushButton("Öffnen")
        open_btn.setToolTip("Ordner im Explorer öffnen")
        open_btn.clicked.connect(self._open_folder)
        row.addWidget(open_btn)
        row.addWidget(QLabel("Dateiname:"))
        self.prefix_edit = QLineEdit(self.settings.csv_prefix)
        self.prefix_edit.setMaximumWidth(180)
        self.prefix_edit.setToolTip("Präfix; Datum und Uhrzeit werden automatisch angehängt")
        self.prefix_edit.editingFinished.connect(
            lambda: setattr(self.settings, "csv_prefix", self.prefix_edit.text().strip()))
        row.addWidget(self.prefix_edit, 1)
        self.record_btn = QPushButton("●  Aufnahme starten")
        self.record_btn.setObjectName("Primary")
        self.record_btn.setMinimumWidth(190)
        self.record_btn.clicked.connect(self._toggle_recording)
        row.addWidget(self.record_btn)
        lay.addLayout(row)
        self.record_info = QLabel()
        self.record_info.setObjectName("Muted")
        lay.addWidget(self.record_info)
        return frame

    # ================================================================== Theme
    def _apply_theme(self, mode: str) -> None:
        self.settings.theme = mode
        self._theme = t = resolve(mode)
        apply_theme(QApplication.instance(), t)
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
        # Widgets mit objectName neu polieren
        for b in self.findChildren(QPushButton):
            b.style().unpolish(b)
            b.style().polish(b)
        self._update_connection_ui(self.thread is not None and self.last_scan_time is not None,
                                   keep_text=True)
        self._refresh_table_values()

    def changeEvent(self, event):  # Windows-Design gewechselt?
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.PaletteChange and self.settings.theme == "system":
            new = resolve("system")
            if new.name != self._theme.name:
                QTimer.singleShot(0, lambda: self._apply_theme("system"))
        super().changeEvent(event)

    # ================================================================== Kanaltabelle
    def _rebuild_channel_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.cfg.channels))
        for row, ch in enumerate(self.cfg.channels):
            show = QTableWidgetItem()
            show.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            show.setCheckState(Qt.Unchecked if ch in self.hidden else Qt.Checked)
            show.setIcon(color_icon(channel_color(row)))
            show.setData(Qt.UserRole, ch)
            self.table.setItem(row, COL_SHOW, show)

            chan = QTableWidgetItem(str(ch))
            chan.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, COL_CHAN, chan)

            label = QTableWidgetItem(self.labels.get(ch, ""))
            label.setFlags(Qt.ItemIsEnabled | Qt.ItemIsEditable)
            self.table.setItem(row, COL_LABEL, label)

            for col in (COL_VALUE, COL_UNIT, COL_STATUS):
                it = QTableWidgetItem("–")
                it.setFlags(Qt.ItemIsEnabled)
                align = Qt.AlignRight | Qt.AlignVCenter if col == COL_VALUE else Qt.AlignVCenter
                it.setTextAlignment(align)
                if col == COL_VALUE:
                    f = it.font()
                    f.setPointSize(f.pointSize() + 2)
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(row, col, it)
        self.table.blockSignals(False)
        self._rebuild_curves()
        self._update_config_label()
        self._refresh_table_values()

    def _rebuild_curves(self) -> None:
        for c in self.curves.values():
            self.plot.removeItem(c)
        self.curves.clear()
        self.legend.clear()
        for row, ch in enumerate(self.cfg.channels):
            curve = self.plot.plot(
                [], [], pen=pg.mkPen(channel_color(row), width=2),
                name=self._display_name(ch), connect="finite")
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

    def _display_name(self, ch: int) -> str:
        label = self.labels.get(ch, "").strip()
        return f"{ch} · {label}" if label else str(ch)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        ch = self.cfg.channels[row]
        if item.column() == COL_SHOW:
            visible = item.checkState() == Qt.Checked
            if visible:
                self.hidden.discard(ch)
            else:
                self.hidden.add(ch)
            self.settings.hidden_channels = self.hidden
            if ch in self.curves:
                self.curves[ch].setVisible(visible)
            self._sync_legend()
            self._mark_dirty()
        elif item.column() == COL_LABEL:
            text = item.text().strip()
            if text:
                self.labels[ch] = text
            else:
                self.labels.pop(ch, None)
            self.settings.labels = self.labels
            if ch in self.curves:
                self.curves[ch].opts["name"] = self._display_name(ch)
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

    def _refresh_table_values(self) -> None:
        t = self._theme
        stale = self._is_stale()
        for row, ch in enumerate(self.cfg.channels):
            r = self.last_readings.get(ch)
            v_item = self.table.item(row, COL_VALUE)
            u_item = self.table.item(row, COL_UNIT)
            s_item = self.table.item(row, COL_STATUS)
            if v_item is None:
                continue
            if r is None:
                v_item.setText("–")
                u_item.setText(TEMP_UNITS.get(self.cfg.units, ""))
                s_item.setText("keine Daten")
                color = t.text_muted
            elif r.is_open:
                v_item.setText("offen")
                u_item.setText(r.unit)
                s_item.setText("⚠ Fühlerbruch / nicht angeschlossen")
                color = t.warning
            else:
                v_item.setText(f"{r.value:.2f}")
                u_item.setText(r.unit)
                s_item.setText("veraltet" if stale else "OK")
                color = t.text_muted if stale else t.success
            v_item.setForeground(QColor(t.warning if r is not None and r.is_open
                                        else t.text_muted if stale or r is None else t.text))
            s_item.setForeground(QColor(color))

    def _update_config_label(self) -> None:
        c = self.cfg
        ref = REF_JUNCTIONS[c.ref_junction].split(" ")[0]
        self.config_label.setText(
            f"Typ {c.tc_type} · Ref. {ref} · {c.nplc:g} PLC · alle {self.settings.interval_s:g} s"
            f" · {format_channel_list(c.channels)}")
        self.plot.setLabel("left", f"Temperatur [{TEMP_UNITS[c.units]}]")

    # ================================================================== Verbindung
    def _refresh_resources(self) -> None:
        current = self.resource_combo.currentText() or self.settings.resource
        self.resource_combo.clear()
        res = list_visa_resources()
        self.resource_combo.addItems(res + [SIMULATION_RESOURCE])
        if current and current in res + [SIMULATION_RESOURCE]:
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
        self.thread = AcquisitionThread(resource, self.cfg, self.settings.interval_s,
                                        auto_reconnect=self.settings.auto_reconnect)
        self.thread.connected.connect(self._on_connected)
        self.thread.disconnected.connect(self._on_disconnected)
        self.thread.scan_done.connect(self._on_scan)
        self.thread.error.connect(self._on_error)
        self.thread.status.connect(self._on_status)
        self.thread.finished.connect(self._on_thread_finished)
        self.thread.start()
        self.connect_btn.setText("Trennen")
        self.connect_btn.setObjectName("")
        self._repolish(self.connect_btn)
        self.resource_combo.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self._set_status("Verbinde …", self._theme.warning)

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
        self.connect_btn.setEnabled(True)
        self._update_connection_ui(False)

    def _on_connected(self, idn: str, cards: dict) -> None:
        self.idn, self.cards = idn, cards
        parts = idn.split(",")
        model = f"{parts[1].strip()} (SN {parts[2].strip()}, FW {parts[3].strip()})" \
            if len(parts) >= 4 else idn
        card_txt = "; ".join(f"Slot {s}: {c.split(',')[0]}" for s, c in cards.items())
        self.status_info.setText(f"{model}   {card_txt}")
        self._update_connection_ui(True)
        self._check_cards(cards)

    def _check_cards(self, cards: dict) -> None:
        tc_cards = ("3720", "3721", "3724")
        slots = {ch // 1000 for ch in self.cfg.channels}
        missing = [s for s in slots if s not in cards]
        if missing:
            self._on_error(f"In Slot(s) {missing} steckt keine Karte – Kanalliste prüfen.")
        for s in slots:
            if s in cards and not cards[s].startswith(tc_cards):
                self._on_error(f"Karte in Slot {s} ({cards[s].split(',')[0]}) unterstützt "
                               "evtl. keine Thermoelement-Messung.")

    def _on_disconnected(self) -> None:
        self._set_status("Verbindung unterbrochen", self._theme.danger)

    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.statusBar().showMessage(text, 5000)

    def _on_error(self, msg: str) -> None:
        self.statusBar().showMessage(f"Fehler: {msg}", 15000)
        self._set_status(msg, self._theme.danger)
        if self.recorder is not None:
            self.record_info.setText(self.record_info.text() + "   ⚠ Lücke in Daten möglich")

    def _update_connection_ui(self, connected: bool, keep_text: bool = False) -> None:
        if self.thread is None:
            self.connect_btn.setText("Verbinden")
            self.connect_btn.setObjectName("Primary")
            self._repolish(self.connect_btn)
            self.resource_combo.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            self._set_status("Getrennt", self._theme.text_muted, keep_text)
        elif connected:
            self._set_status("Verbunden – Messung läuft", self._theme.success, keep_text)
        self.record_btn.setEnabled(self.thread is not None or self.recorder is not None)

    def _set_status(self, text: str, color: str, keep_text: bool = False) -> None:
        self.status_dot.setStyleSheet(f"color: {color};")
        if not keep_text:
            self.status_label.setText(text)

    @staticmethod
    def _repolish(w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # ================================================================== Messdaten
    def _on_scan(self, result: ScanResult) -> None:
        if self.last_scan_time is None or self._is_stale():
            self._set_status("Verbunden – Messung läuft", self._theme.success)
        self.last_scan_time = time.time()
        self.last_readings = {r.channel: r for r in result.readings}
        self.history.append(result)
        if self.recorder is not None:
            try:
                self.recorder.write(result)
            except OSError as exc:
                self._stop_recording()
                QMessageBox.critical(self, "Speicherfehler",
                                     f"CSV-Datei konnte nicht geschrieben werden:\n{exc}\n\n"
                                     "Die Aufnahme wurde beendet.")
        self._refresh_table_values()
        self._update_record_ui()
        self._mark_dirty()

    def _is_stale(self) -> bool:
        if self.last_scan_time is None or self.thread is None:
            return self.last_scan_time is not None
        limit = max(5.0, 3 * self.settings.interval_s + 2)
        return time.time() - self.last_scan_time > limit

    def _check_stale(self) -> None:
        if self.thread is not None and self._is_stale():
            self.status_dot.setStyleSheet(f"color: {self._theme.warning};")
            self._refresh_table_values()
        if self.recorder is not None:
            self._update_record_ui()

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
        start = 0
        if follow and window > 0:
            start = int(np.searchsorted(t, t[-1] - window))
        for ch, curve in self.curves.items():
            if not curve.isVisible():
                continue
            v = self.history.values(ch)
            if v is None:
                curve.setData([], [])
                continue
            curve.setData(t[start:], v[start:], connect="finite")
        if follow:
            x1 = t[-1]
            x0 = max(t[0], x1 - window) if window > 0 else t[0]
            if x1 - x0 < 60:  # Mindestbreite, damit die Zeitachse HH:MM:SS zeigt
                x0 = x1 - 60
            vb = self.plot.getViewBox()
            vb.setXRange(x0, x1 + max(1.0, (x1 - x0) * 0.01), padding=0)
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)

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
            if not curve.isVisible():
                continue
            v = self.history.values(ch)
            if v is not None and not math.isnan(v[i]):
                parts.append(f"{self._display_name(ch)}: {v[i]:.2f} {unit}")
        self.hover_info.setText("   ".join(parts))

    def _clear_history(self) -> None:
        self.history.clear()
        self._mark_dirty()

    def _export_history(self) -> None:
        if self.history.size == 0:
            QMessageBox.information(self, "Kein Verlauf", "Es sind noch keine Messwerte vorhanden.")
            return
        folder = self.settings.csv_folder
        try:
            rec = CsvRecorder(folder, self.cfg.channels, self.labels, TEMP_UNITS[self.cfg.units],
                              self._metadata() | {"Hinweis": "Export des Verlaufs im Speicher"},
                              self.settings.csv_format,
                              prefix=(self.prefix_edit.text().strip() or "Temperaturen") + "_Export")
            t = self.history.times()
            rec.start_time = datetime.fromtimestamp(t[0])
            unit = TEMP_UNITS[self.cfg.units]
            for i in range(t.size):
                readings = []
                for ch in self.cfg.channels:
                    v = self.history.values(ch)
                    val = float(v[i]) if v is not None else float("nan")
                    readings.append(Reading(ch, val, unit, math.isnan(val)))
                rec.write(ScanResult(float(t[i]), readings))
            rec.close()
        except OSError as exc:
            QMessageBox.critical(self, "Speicherfehler", f"Export fehlgeschlagen:\n{exc}")
            return
        self.statusBar().showMessage(f"Verlauf exportiert: {rec.path}", 10000)
        QMessageBox.information(self, "Export abgeschlossen",
                                f"{rec.rows} Zeilen gespeichert in:\n{rec.path}")

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
        return {
            "Programm": f"{APP_NAME} v{__version__}",
            "Gerät": self.idn or "-",
            "Karten": "; ".join(f"Slot {s}: {v}" for s, v in self.cards.items()) or "-",
            "Thermoelement-Typ": c.tc_type,
            "Vergleichsstelle": REF_JUNCTIONS[c.ref_junction]
            + (f" ({c.sim_ref_temp:g} °C)" if c.ref_junction == "SIMULATED" else ""),
            "Integrationszeit [PLC]": f"{c.nplc:g}",
            "Messintervall [s]": f"{self.settings.interval_s:g}",
            "Leere Zellen": "offenes Thermoelement / kein Messwert",
        }

    def _toggle_recording(self) -> None:
        if self.recorder is None:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        folder = self.settings.csv_folder
        self.settings.csv_prefix = self.prefix_edit.text().strip()
        try:
            self.recorder = CsvRecorder(folder, self.cfg.channels, self.labels,
                                        TEMP_UNITS[self.cfg.units], self._metadata(),
                                        self.settings.csv_format,
                                        prefix=self.settings.csv_prefix or "Temperaturen")
        except OSError as exc:
            QMessageBox.critical(self, "Speicherfehler",
                                 f"Datei konnte nicht angelegt werden:\n{exc}\n\n"
                                 "Bitte einen anderen Ordner wählen.")
            self.recorder = None
            return
        self._update_record_ui()

    def _stop_recording(self) -> None:
        if self.recorder is not None:
            path, rows = self.recorder.path, self.recorder.rows
            self.recorder.close()
            self.recorder = None
            self.statusBar().showMessage(f"Aufnahme gespeichert: {path} ({rows} Zeilen)", 15000)
            self.record_info.setText(f"Letzte Aufnahme: {path}  ·  {rows} Zeilen")
        self._update_record_ui()

    def _update_record_ui(self) -> None:
        rec = self.recorder
        if rec is None:
            self.record_btn.setText("●  Aufnahme starten")
            self.record_btn.setObjectName("Primary")
            if not self.record_info.text():
                self.record_info.setText("Keine Aufnahme aktiv. Beim Start wird eine neue "
                                         "CSV-Datei im gewählten Ordner angelegt.")
        else:
            self.record_btn.setText("■  Aufnahme beenden")
            self.record_btn.setObjectName("Danger")
            dur = datetime.now() - rec.start_time
            h, rem = divmod(int(dur.total_seconds()), 3600)
            m, s = divmod(rem, 60)
            self.record_info.setText(f"● Aufnahme läuft  ·  {rec.path.name}  ·  "
                                     f"{rec.rows} Zeilen  ·  {h:02d}:{m:02d}:{s:02d}")
        self._repolish(self.record_btn)
        self.record_btn.setEnabled(self.thread is not None or rec is not None)

    # ================================================================== Einstellungen
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self.settings.interval_s, self.settings.csv_format,
                             self.settings.auto_reconnect, self.recorder is not None, self)
        if dlg.exec() != SettingsDialog.Accepted:
            return
        new_cfg = dlg.result_config()
        channels_changed = new_cfg.channels != self.cfg.channels
        units_changed = new_cfg.units != self.cfg.units
        self.cfg = new_cfg
        self.settings.set_measurement_config(new_cfg)
        self.settings.interval_s = dlg.interval.value()
        self.settings.csv_format = dlg.csv_format.currentData()
        self.settings.auto_reconnect = dlg.auto_reconnect.isChecked()
        self.settings.sync()
        if channels_changed or units_changed:
            self.last_readings = {}
            self.history.clear()
            self._rebuild_channel_table()
        self._update_config_label()
        if self.thread is not None:
            self.thread.apply_config(new_cfg)
            self.thread.set_interval(self.settings.interval_s)

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
        self.settings.qs.setValue("ui/geometry", self.saveGeometry())
        self.settings.qs.setValue("ui/splitter", self.splitter.saveState())
        self.settings.sync()
        super().closeEvent(event)
