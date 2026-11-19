"""Weitere Dialoge: Kanal-Einstellungen, Aufnahme planen, Marker, Tastenkürzel."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, Qt, QTime
from PySide6.QtWidgets import (
    QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QRadioButton, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from .channel_props import ChannelProps, validate_props
from .instrument import THERMOCOUPLE_TYPES
from .scheduler import Trigger


def _buttons(dlg: QDialog, ok_text: str = "Übernehmen", on_ok=None) -> QDialogButtonBox:
    b = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    b.button(QDialogButtonBox.Ok).setText(ok_text)
    b.button(QDialogButtonBox.Cancel).setText("Abbrechen")
    b.accepted.connect(on_ok or dlg.accept)
    b.rejected.connect(dlg.reject)
    return b


def _opt(text: str) -> float | None:
    text = text.strip().replace(",", ".")
    if not text:
        return None
    return float(text)


# ============================================================================ Kanäle
class ChannelDialog(QDialog):
    COLS = ["Kanal", "Bezeichnung", "Thermoelement", "Offset", "Alarm unten", "Alarm oben"]

    def __init__(self, channels: list[int], labels: dict[int, str],
                 props: dict[int, ChannelProps], global_type: str, unit: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kanal-Einstellungen")
        self.resize(820, 520)
        self.channels = channels
        lay = QVBoxLayout(self)
        info = QLabel(f"Offset wird zum Messwert addiert (Kalibrierung). Alarmgrenzen in {unit}; "
                      "leer = kein Alarm. Typ „Standard“ = globaler Typ "
                      f"({global_type}).")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        lay.addWidget(info)

        self.table = QTableWidget(len(channels), len(self.COLS))
        self.table.setHorizontalHeaderLabels(
            [c if c not in ("Offset", "Alarm unten", "Alarm oben") else f"{c} [{unit}]"
             for c in self.COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self._type_boxes: list[QComboBox] = []
        self._offsets: list[QDoubleSpinBox] = []
        for row, ch in enumerate(channels):
            p = props.get(ch, ChannelProps())
            it = QTableWidgetItem(str(ch))
            it.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, it)
            self.table.setItem(row, 1, QTableWidgetItem(labels.get(ch, "")))
            box = QComboBox()
            box.addItem(f"Standard ({global_type})", None)
            for t in THERMOCOUPLE_TYPES:
                box.addItem(f"Typ {t}", t)
            box.setCurrentIndex(0 if p.tc_type is None else THERMOCOUPLE_TYPES.index(p.tc_type) + 1)
            self.table.setCellWidget(row, 2, box)
            self._type_boxes.append(box)
            off = QDoubleSpinBox()
            off.setRange(-50, 50)
            off.setDecimals(3)
            off.setSingleStep(0.05)
            off.setValue(p.offset)
            self.table.setCellWidget(row, 3, off)
            self._offsets.append(off)
            self.table.setItem(row, 4, QTableWidgetItem("" if p.alarm_low is None
                                                        else f"{p.alarm_low:g}"))
            self.table.setItem(row, 5, QTableWidgetItem("" if p.alarm_high is None
                                                        else f"{p.alarm_high:g}"))
        lay.addWidget(self.table, 1)
        lay.addWidget(_buttons(self, on_ok=self._accept))
        self.result_labels: dict[int, str] = {}
        self.result_props: dict[int, ChannelProps] = {}

    def _accept(self) -> None:
        labels, props = {}, {}
        for row, ch in enumerate(self.channels):
            try:
                low = _opt(self.table.item(row, 4).text())
                high = _opt(self.table.item(row, 5).text())
            except ValueError:
                QMessageBox.warning(self, "Ungültiger Wert",
                                    f"Kanal {ch}: Alarmgrenze ist keine Zahl.")
                return
            text = self.table.item(row, 1).text().strip()
            if text:
                labels[ch] = text
            props[ch] = ChannelProps(self._type_boxes[row].currentData(),
                                     self._offsets[row].value(), low, high)
        errors = validate_props(props)
        if errors:
            QMessageBox.warning(self, "Ungültige Einstellungen", "\n".join(errors))
            return
        self.result_labels = labels
        self.result_props = {ch: p for ch, p in props.items() if not p.is_default()}
        self.accept()


# ============================================================================ Zeitplan
class _TriggerBox(QGroupBox):
    def __init__(self, title: str, modes: list[tuple[str, str]], channels: list[int],
                 unit: str, trigger: Trigger | None):
        super().__init__(title)
        t = trigger or Trigger(mode=modes[0][0])
        f = QFormLayout(self)
        self.radios: dict[str, QRadioButton] = {}
        for key, text in modes:
            rb = QRadioButton(text)
            self.radios[key] = rb
            f.addRow(rb)
            if key == "time":
                self.at = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
                self.at.setDisplayFormat("dd.MM.yyyy HH:mm:ss")
                self.at.setCalendarPopup(True)
                if t.at:
                    self.at.setDateTime(QDateTime(t.at))
                f.addRow("    Zeitpunkt:", self.at)
            elif key == "duration":
                self.duration = QTimeEdit(QTime(1, 0, 0))
                self.duration.setDisplayFormat("HH:mm:ss")
                secs = int(t.duration_s)
                self.duration.setTime(QTime(min(secs // 3600, 23), secs % 3600 // 60, secs % 60))
                f.addRow("    Dauer (h:min:s):", self.duration)
            elif key == "threshold":
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                self.channel = QComboBox()
                for ch in channels:
                    self.channel.addItem(str(ch), ch)
                if t.channel in channels:
                    self.channel.setCurrentIndex(channels.index(t.channel))
                self.compare = QComboBox()
                self.compare.addItems([">", "<"])
                self.compare.setCurrentText(t.compare)
                self.value = QDoubleSpinBox()
                self.value.setRange(-300, 2000)
                self.value.setDecimals(2)
                self.value.setSuffix(f" {unit}")
                self.value.setValue(t.value)
                h.addWidget(self.channel)
                h.addWidget(self.compare)
                h.addWidget(self.value, 1)
                f.addRow("    Bedingung:", row)
        self.radios.get(t.mode, next(iter(self.radios.values()))).setChecked(True)

    def trigger(self) -> Trigger:
        mode = next(k for k, rb in self.radios.items() if rb.isChecked())
        t = Trigger(mode=mode)
        if mode == "time":
            t.at = self.at.dateTime().toPython()
        elif mode == "duration":
            q = self.duration.time()
            t.duration_s = q.hour() * 3600 + q.minute() * 60 + q.second()
        elif mode == "threshold":
            t.channel = self.channel.currentData()
            t.compare = self.compare.currentText()
            t.value = self.value.value()
        return t


class ScheduleDialog(QDialog):
    def __init__(self, channels: list[int], unit: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Aufnahme planen")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        self.start_box = _TriggerBox(
            "Start", [("now", "Sofort"), ("time", "Zu einem Zeitpunkt"),
                      ("threshold", "Wenn Temperaturschwelle erreicht")], channels, unit, None)
        self.stop_box = _TriggerBox(
            "Stopp", [("manual", "Manuell"), ("duration", "Nach einer Dauer"),
                      ("time", "Zu einem Zeitpunkt"),
                      ("threshold", "Wenn Temperaturschwelle erreicht")], channels, unit,
            Trigger(mode="duration", duration_s=3600))
        lay.addWidget(self.start_box)
        lay.addWidget(self.stop_box)
        lay.addWidget(_buttons(self, "Planen", self._accept))

    def _accept(self) -> None:
        s, e = self.start_box.trigger(), self.stop_box.trigger()
        now = datetime.now()
        if s.mode == "time" and s.at <= now:
            QMessageBox.warning(self, "Ungültig", "Der Startzeitpunkt liegt in der Vergangenheit.")
            return
        if e.mode == "time" and e.at <= (s.at if s.mode == "time" else now):
            QMessageBox.warning(self, "Ungültig", "Der Stoppzeitpunkt muss nach dem Start liegen.")
            return
        if e.mode == "duration" and e.duration_s < 1:
            QMessageBox.warning(self, "Ungültig", "Die Dauer muss größer als 0 sein.")
            return
        self.start_trigger, self.stop_trigger = s, e
        self.accept()


# ============================================================================ Notiz/Marker
class TextDialog(QDialog):
    def __init__(self, title: str, label: str, text: str = "", multiline: bool = False,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(label))
        if multiline:
            self.edit = QPlainTextEdit(text)
            self.edit.setMinimumHeight(110)
        else:
            self.edit = QLineEdit(text)
            self.edit.selectAll()
        lay.addWidget(self.edit)
        lay.addWidget(_buttons(self, "OK"))

    def text(self) -> str:
        e = self.edit
        return (e.toPlainText() if isinstance(e, QPlainTextEdit) else e.text()).strip()


SHORTCUTS = [
    ("Leertaste", "Messung pausieren / fortsetzen"),
    ("E", "Einzelmessung (bei Pause)"),
    ("M", "Marker / Ereignis setzen"),
    ("Strg+R", "Aufnahme starten / beenden"),
    ("Strg+K", "Verbinden / Trennen"),
    ("Strg+E", "Einstellungen"),
    ("Strg+Shift+K", "Kanal-Einstellungen"),
    ("C", "Messcursor ein/aus"),
    ("Z", "Rechteck-Zoom ein/aus"),
    ("L", "Live folgen"),
    ("T", "Kachelansicht"),
    ("Strg+S", "Profil speichern"),
    ("Strg+O", "Profil laden"),
    ("Strg+P", "Plot exportieren"),
    ("F11", "Vollbild"),
    ("Esc", "Alarm quittieren (Banner aus, Tonwiederholung stoppen)"),
    ("Strg+M", "Alle Töne stummschalten / wieder einschalten"),
]


def shortcut_help_html() -> str:
    rows = "".join(f"<tr><td style='padding:3px 14px 3px 0'><b>{k}</b></td><td>{v}</td></tr>"
                   for k, v in SHORTCUTS)
    return f"<table>{rows}</table>"
