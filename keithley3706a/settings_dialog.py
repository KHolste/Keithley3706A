"""Dialog für Mess- und Speicher-Einstellungen."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QMessageBox, QVBoxLayout,
)

from .instrument import (
    REF_JUNCTIONS, TEMP_UNITS, THERMOCOUPLE_TYPES, MeasurementConfig,
    format_channel_list, parse_channel_list,
)
from .recorder import CSV_FORMATS

NPLC_PRESETS = [
    (0.1, "0,1 PLC – schnell, mehr Rauschen"),
    (0.5, "0,5 PLC"),
    (1.0, "1 PLC – unterdrückt 50-Hz-Brummen (empfohlen)"),
    (5.0, "5 PLC – sehr rauscharm"),
    (10.0, "10 PLC – maximal rauscharm, langsam"),
]


class SettingsDialog(QDialog):
    def __init__(self, cfg: MeasurementConfig, interval_s: float, csv_format: str,
                 auto_reconnect: bool, recording: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.setMinimumWidth(480)
        self._recording = recording
        self._orig_channels = list(cfg.channels)
        self._orig_units = cfg.units

        layout = QVBoxLayout(self)

        # --- Messung
        g_meas = QGroupBox("Messung")
        f = QFormLayout(g_meas)
        self.channels = QLineEdit(format_channel_list(cfg.channels))
        self.channels.setPlaceholderText("z. B. 1001:1009, 1015")
        self.channels.setToolTip("Kanäle im Format SKKK (Slot + Kanal), Bereiche mit ':'")
        f.addRow("Kanäle:", self.channels)

        self.tc_type = QComboBox()
        self.tc_type.addItems([f"Typ {t}" for t in THERMOCOUPLE_TYPES])
        self.tc_type.setCurrentIndex(THERMOCOUPLE_TYPES.index(cfg.tc_type))
        f.addRow("Thermoelement:", self.tc_type)

        self.ref = QComboBox()
        for key, text in REF_JUNCTIONS.items():
            self.ref.addItem(text, key)
        self.ref.setCurrentIndex(list(REF_JUNCTIONS).index(cfg.ref_junction))
        f.addRow("Vergleichsstelle:", self.ref)

        self.sim_temp = QDoubleSpinBox()
        self.sim_temp.setRange(0, 65)
        self.sim_temp.setDecimals(2)
        self.sim_temp.setSuffix(" °C")
        self.sim_temp.setValue(cfg.sim_ref_temp)
        f.addRow("Simulierte Ref.-Temp.:", self.sim_temp)
        self.ref.currentIndexChanged.connect(self._update_enabled)

        self.units = QComboBox()
        for key, sym in TEMP_UNITS.items():
            self.units.addItem(sym, key)
        self.units.setCurrentIndex(list(TEMP_UNITS).index(cfg.units))
        f.addRow("Einheit:", self.units)

        self.nplc = QComboBox()
        for val, text in NPLC_PRESETS:
            self.nplc.addItem(text, val)
        idx = min(range(len(NPLC_PRESETS)), key=lambda i: abs(NPLC_PRESETS[i][0] - cfg.nplc))
        self.nplc.setCurrentIndex(idx)
        f.addRow("Integrationszeit:", self.nplc)

        self.open_detect = QCheckBox("Offene Thermoelemente erkennen (Fühlerbruch)")
        self.open_detect.setChecked(cfg.open_detect)
        f.addRow("", self.open_detect)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 3600)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" s")
        self.interval.setValue(interval_s)
        self.interval.setToolTip("Zeit zwischen zwei Scans. Ist der Scan selbst länger, wird "
                                 "ohne Pause gemessen.")
        f.addRow("Messintervall:", self.interval)
        layout.addWidget(g_meas)

        # --- Speichern / Verbindung
        g_misc = QGroupBox("Speichern & Verbindung")
        f2 = QFormLayout(g_misc)
        self.csv_format = QComboBox()
        for key, (text, _d, _p) in CSV_FORMATS.items():
            self.csv_format.addItem(text, key)
        self.csv_format.setCurrentIndex(max(0, list(CSV_FORMATS).index(csv_format)
                                            if csv_format in CSV_FORMATS else 0))
        f2.addRow("CSV-Format:", self.csv_format)
        self.auto_reconnect = QCheckBox("Bei Verbindungsabbruch automatisch neu verbinden")
        self.auto_reconnect.setChecked(auto_reconnect)
        f2.addRow("", self.auto_reconnect)
        layout.addWidget(g_misc)

        if recording:
            note = QLabel("Während einer laufenden Aufnahme können Kanäle und Einheit nicht "
                          "geändert werden.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            layout.addWidget(note)
            self.channels.setEnabled(False)
            self.units.setEnabled(False)
            self.csv_format.setEnabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Übernehmen")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_enabled()

    def _update_enabled(self) -> None:
        self.sim_temp.setEnabled(self.ref.currentData() == "SIMULATED")

    def _accept(self) -> None:
        try:
            parse_channel_list(self.channels.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Ungültige Kanalliste", str(exc))
            return
        self.accept()

    def result_config(self) -> MeasurementConfig:
        return MeasurementConfig(
            channels=parse_channel_list(self.channels.text()),
            tc_type=THERMOCOUPLE_TYPES[self.tc_type.currentIndex()],
            ref_junction=self.ref.currentData(),
            sim_ref_temp=self.sim_temp.value(),
            units=self.units.currentData(),
            nplc=float(self.nplc.currentData()),
            open_detect=self.open_detect.isChecked(),
        )
