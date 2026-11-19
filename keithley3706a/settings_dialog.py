"""Dialog für Mess-, Aufnahme-, Alarm- und E-Mail-Einstellungen."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
    QLineEdit, QMessageBox, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .instrument import (
    REF_JUNCTIONS, TEMP_UNITS, THERMOCOUPLE_TYPES, MeasurementConfig,
    format_channel_list, parse_channel_list,
)
from .notify import EmailConfig, EmailNotifier
from .recorder import CSV_FORMATS

NPLC_PRESETS = [
    (0.1, "0,1 PLC – schnell, mehr Rauschen"),
    (0.5, "0,5 PLC"),
    (1.0, "1 PLC – unterdrückt 50-Hz-Brummen (empfohlen)"),
    (5.0, "5 PLC – sehr rauscharm"),
    (10.0, "10 PLC – maximal rauscharm, langsam"),
]


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setWordWrap(True)
    return lbl


class SettingsDialog(QDialog):
    def __init__(self, settings, cfg: MeasurementConfig, recording: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.setMinimumWidth(560)
        self._settings = settings
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ------------------------------------------------------------ Messung
        w = QWidget()
        f = QFormLayout(w)
        self.channels = QLineEdit(format_channel_list(cfg.channels))
        self.channels.setPlaceholderText("z. B. 1001:1009, 1015")
        self.channels.setToolTip("Kanäle im Format SKKK (Slot + Kanal), Bereiche mit ':'")
        f.addRow("Kanäle:", self.channels)

        self.tc_type = QComboBox()
        self.tc_type.addItems([f"Typ {t}" for t in THERMOCOUPLE_TYPES])
        self.tc_type.setCurrentIndex(THERMOCOUPLE_TYPES.index(cfg.tc_type))
        f.addRow("Thermoelement (Standard):", self.tc_type)
        f.addRow("", _muted("Abweichende Typen je Kanal: Messung → Kanal-Einstellungen."))

        self.ref = QComboBox()
        for key, text in REF_JUNCTIONS.items():
            self.ref.addItem(text, key)
        self.ref.setCurrentIndex(list(REF_JUNCTIONS).index(cfg.ref_junction))
        f.addRow("Vergleichsstelle:", self.ref)

        self.sim_temp = QDoubleSpinBox()
        self.sim_temp.setRange(0, 65)          # Gerätegrenzen laut Manual
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
        self.interval.setValue(settings.interval_s)
        self.interval.setToolTip("Zeit zwischen zwei Scans. Ist der Scan selbst länger, wird "
                                 "ohne Pause gemessen.")
        f.addRow("Messintervall:", self.interval)

        self.rate_window = QSpinBox()
        self.rate_window.setRange(10, 3600)
        self.rate_window.setSuffix(" s")
        self.rate_window.setValue(settings.rate_window_s)
        self.rate_window.setToolTip("Zeitraum für die Ausgleichsgerade der Änderungsrate")
        f.addRow("Fenster Änderungsrate:", self.rate_window)

        self.auto_reconnect = QCheckBox("Bei Verbindungsabbruch automatisch neu verbinden")
        self.auto_reconnect.setChecked(settings.auto_reconnect)
        f.addRow("", self.auto_reconnect)
        tabs.addTab(w, "Messung")

        # ------------------------------------------------------------ Aufnahme
        w = QWidget()
        f = QFormLayout(w)
        self.csv_format = QComboBox()
        for key, (text, _d, _p) in CSV_FORMATS.items():
            self.csv_format.addItem(text, key)
        keys = list(CSV_FORMATS)
        self.csv_format.setCurrentIndex(keys.index(settings.csv_format)
                                        if settings.csv_format in keys else 0)
        f.addRow("CSV-Format:", self.csv_format)
        self.rotate_daily = QCheckBox("Um Mitternacht automatisch neue Datei beginnen")
        self.rotate_daily.setChecked(settings.rotate_daily)
        f.addRow("", self.rotate_daily)
        self.sidecar = QCheckBox("Zu jeder CSV eine JSON-Datei mit allen Einstellungen schreiben")
        self.sidecar.setChecked(settings.write_sidecar)
        self.sidecar.setToolTip("Maschinenlesbare Metadaten; kann mit dem Headless-Modus erneut "
                                "ausgeführt werden.")
        f.addRow("", self.sidecar)
        tabs.addTab(w, "Aufnahme")

        # ------------------------------------------------------------ Alarme
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(_muted("Grenzwerte werden je Kanal unter Messung → Kanal-Einstellungen "
                        "festgelegt."))
        self.hysteresis = QDoubleSpinBox()
        self.hysteresis.setRange(0, 20)
        self.hysteresis.setDecimals(2)
        self.hysteresis.setValue(settings.alarm_hysteresis)
        self.hysteresis.setToolTip("Ein Alarm endet erst, wenn der Wert um diesen Betrag "
                                   "zurück im erlaubten Bereich ist (verhindert Flattern).")
        f.addRow("Hysterese:", self.hysteresis)
        self.alarm_sound = QCheckBox("Warnton abspielen")
        self.alarm_sound.setChecked(settings.alarm_sound)
        f.addRow("", self.alarm_sound)
        self.alarm_tray = QCheckBox("Windows-Benachrichtigung anzeigen")
        self.alarm_tray.setChecked(settings.alarm_tray)
        f.addRow("", self.alarm_tray)
        self.alarm_open = QCheckBox("Fühlerbruch (offenes Thermoelement) als Alarm behandeln")
        self.alarm_open.setChecked(settings.alarm_open)
        f.addRow("", self.alarm_open)
        tabs.addTab(w, "Alarme")

        # ------------------------------------------------------------ E-Mail
        em = settings.email
        w = QWidget()
        f = QFormLayout(w)
        self.mail_enabled = QCheckBox("Bei Alarm E-Mail senden")
        self.mail_enabled.setChecked(em.enabled)
        f.addRow("", self.mail_enabled)
        self.mail_host = QLineEdit(em.host)
        self.mail_host.setPlaceholderText("smtp.beispiel.de")
        f.addRow("SMTP-Server:", self.mail_host)
        self.mail_port = QSpinBox()
        self.mail_port.setRange(1, 65535)
        self.mail_port.setValue(em.port)
        f.addRow("Port:", self.mail_port)
        self.mail_security = QComboBox()
        for key, text in (("starttls", "STARTTLS (Port 587)"), ("ssl", "SSL/TLS (Port 465)"),
                          ("none", "Keine Verschlüsselung")):
            self.mail_security.addItem(text, key)
        self.mail_security.setCurrentIndex(
            max(0, [self.mail_security.itemData(i) for i in range(3)].index(em.security)
                if em.security in ("starttls", "ssl", "none") else 0))
        f.addRow("Verschlüsselung:", self.mail_security)
        self.mail_user = QLineEdit(em.username)
        f.addRow("Benutzername:", self.mail_user)
        self.mail_pass = QLineEdit(em.password)
        self.mail_pass.setEchoMode(QLineEdit.Password)
        self.mail_pass.setToolTip("Wird mit der Windows-DPAPI verschlüsselt gespeichert "
                                  "(nur unter diesem Benutzerkonto lesbar).")
        f.addRow("Passwort:", self.mail_pass)
        self.mail_sender = QLineEdit(em.sender)
        f.addRow("Absender:", self.mail_sender)
        self.mail_to = QLineEdit(", ".join(em.recipients))
        self.mail_to.setPlaceholderText("a@beispiel.de, b@beispiel.de")
        f.addRow("Empfänger:", self.mail_to)
        self.mail_interval = QDoubleSpinBox()
        self.mail_interval.setRange(1, 1440)
        self.mail_interval.setSuffix(" min")
        self.mail_interval.setValue(em.min_interval_min)
        self.mail_interval.setToolTip("Mindestabstand zwischen zwei Mails pro Kanal")
        f.addRow("Sendepause je Kanal:", self.mail_interval)
        from PySide6.QtWidgets import QPushButton
        test_btn = QPushButton("Test-Mail senden")
        test_btn.clicked.connect(self._send_test)
        f.addRow("", test_btn)
        self._mailer = EmailNotifier(self)
        self._mailer.finished.connect(
            lambda ok, msg: (QMessageBox.information if ok else QMessageBox.warning)(
                self, "Test-Mail", msg))
        tabs.addTab(w, "E-Mail")

        if recording:
            layout.addWidget(_muted("Während einer laufenden Aufnahme können Kanäle, Einheit und "
                                    "CSV-Format nicht geändert werden."))
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

    def email_config(self) -> EmailConfig:
        return EmailConfig(
            enabled=self.mail_enabled.isChecked(),
            host=self.mail_host.text().strip(),
            port=self.mail_port.value(),
            security=self.mail_security.currentData(),
            username=self.mail_user.text().strip(),
            password=self.mail_pass.text(),
            sender=self.mail_sender.text().strip(),
            recipients=[r.strip() for r in self.mail_to.text().replace(";", ",").split(",")
                        if r.strip()],
            min_interval_min=self.mail_interval.value(),
        )

    def _send_test(self) -> None:
        cfg = self.email_config()
        if not cfg.is_complete():
            QMessageBox.warning(self, "Test-Mail", "Bitte Server, Absender und Empfänger angeben.")
            return
        self._mailer.config = cfg
        self._mailer.notify("test", "Keithley 3706A – Test-Mail",
                            "Diese Test-Mail bestätigt die E-Mail-Einstellungen.", force=True)

    def _accept(self) -> None:
        try:
            parse_channel_list(self.channels.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Ungültige Kanalliste", str(exc))
            return
        em = self.email_config()
        if em.enabled and not em.is_complete():
            QMessageBox.warning(self, "E-Mail unvollständig",
                                "Für E-Mail-Alarme werden Server, Absender und Empfänger benötigt.")
            return
        bad = [r for r in em.recipients if "@" not in r]
        if bad:
            QMessageBox.warning(self, "Ungültige Empfänger", "Ungültig: " + ", ".join(bad))
            return
        self.accept()

    def result_config(self, channel_types: dict[int, str]) -> MeasurementConfig:
        return MeasurementConfig(
            channels=parse_channel_list(self.channels.text()),
            tc_type=THERMOCOUPLE_TYPES[self.tc_type.currentIndex()],
            ref_junction=self.ref.currentData(),
            sim_ref_temp=self.sim_temp.value(),
            units=self.units.currentData(),
            nplc=float(self.nplc.currentData()),
            open_detect=self.open_detect.isChecked(),
            channel_types=dict(channel_types),
        )

    def store(self, settings) -> None:
        settings.interval_s = self.interval.value()
        settings.rate_window_s = self.rate_window.value()
        settings.auto_reconnect = self.auto_reconnect.isChecked()
        settings.csv_format = self.csv_format.currentData()
        settings.rotate_daily = self.rotate_daily.isChecked()
        settings.write_sidecar = self.sidecar.isChecked()
        settings.alarm_hysteresis = self.hysteresis.value()
        settings.alarm_sound = self.alarm_sound.isChecked()
        settings.alarm_tray = self.alarm_tray.isChecked()
        settings.alarm_open = self.alarm_open.isChecked()
        settings.email = self.email_config()
