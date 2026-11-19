"""Persistente Einstellungen (QSettings -> Windows-Registry, HKCU)."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

from . import ORG_NAME, SETTINGS_APP
from .channel_props import ChannelProps, props_from_json, props_to_json
from .instrument import MeasurementConfig, format_channel_list, parse_channel_list
from .notify import EmailConfig
from .secret import decrypt, encrypt


def default_folder() -> str:
    docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    return str(Path(docs) / "Keithley3706A_Messdaten")


class AppSettings:
    def __init__(self, app_name: str | None = None):
        self.qs = QSettings(ORG_NAME, app_name or SETTINGS_APP)

    # -------------------------------------------------------- generische Helfer
    def _get(self, key, default, typ=str):
        val = self.qs.value(key, default)
        if val is None:
            return default
        try:
            if typ is bool:
                return val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes")
            return typ(val)
        except (TypeError, ValueError):
            return default

    def _json(self, key, default):
        try:
            return json.loads(self.qs.value(key, "") or "null") or default
        except (TypeError, ValueError):
            return default

    def sync(self) -> None:
        self.qs.sync()

    # -------------------------------------------------------- Messkonfiguration
    def measurement_config(self) -> MeasurementConfig:
        cfg = MeasurementConfig()
        try:
            cfg.channels = parse_channel_list(self._get("meas/channels", "1001:1009"))
        except ValueError:
            pass
        cfg.tc_type = self._get("meas/tc_type", cfg.tc_type)
        cfg.ref_junction = self._get("meas/ref_junction", cfg.ref_junction)
        cfg.sim_ref_temp = self._get("meas/sim_ref_temp", cfg.sim_ref_temp, float)
        cfg.units = self._get("meas/units", cfg.units)
        cfg.nplc = self._get("meas/nplc", cfg.nplc, float)
        cfg.open_detect = self._get("meas/open_detect", cfg.open_detect, bool)
        cfg.channel_types = {ch: p.tc_type for ch, p in self.channel_props.items() if p.tc_type}
        return cfg

    def set_measurement_config(self, cfg: MeasurementConfig) -> None:
        self.qs.setValue("meas/channels", format_channel_list(cfg.channels))
        self.qs.setValue("meas/tc_type", cfg.tc_type)
        self.qs.setValue("meas/ref_junction", cfg.ref_junction)
        self.qs.setValue("meas/sim_ref_temp", cfg.sim_ref_temp)
        self.qs.setValue("meas/units", cfg.units)
        self.qs.setValue("meas/nplc", cfg.nplc)
        self.qs.setValue("meas/open_detect", cfg.open_detect)

    # -------------------------------------------------------- einfache Properties
    def _prop(key, default, typ=str):  # noqa: N805 – Descriptor-Fabrik
        def getter(self):
            return self._get(key, default, typ)

        def setter(self, value):
            self.qs.setValue(key, value)
        return property(getter, setter)

    resource = _prop("conn/resource", "")
    auto_reconnect = _prop("conn/auto_reconnect", True, bool)
    interval_s = _prop("meas/interval_s", 1.0, float)
    theme = _prop("ui/theme", "system")
    time_window_s = _prop("ui/time_window_s", 600, int)
    rate_window_s = _prop("ui/rate_window_s", 60, int)
    csv_format = _prop("csv/format", "excel_de")
    csv_prefix = _prop("csv/prefix", "Temperaturen")
    rotate_daily = _prop("csv/rotate_daily", True, bool)
    write_sidecar = _prop("csv/write_sidecar", True, bool)
    last_note = _prop("csv/last_note", "")
    alarm_hysteresis = _prop("alarm/hysteresis", 0.5, float)
    alarm_sound = _prop("alarm/sound", True, bool)
    alarm_tray = _prop("alarm/tray", True, bool)
    alarm_open = _prop("alarm/open_is_alarm", False, bool)
    alarm_device_beep = _prop("alarm/device_beep", True, bool)
    alarm_beep_freq = _prop("alarm/beep_freq", 2400, int)
    alarm_repeat_s = _prop("alarm/repeat_s", 30, int)
    last_profile_dir = _prop("ui/last_profile_dir", "")
    del _prop

    @property
    def csv_folder(self) -> str:
        return self._get("csv/folder", "") or default_folder()

    @csv_folder.setter
    def csv_folder(self, value: str) -> None:
        self.qs.setValue("csv/folder", value)

    @property
    def labels(self) -> dict[int, str]:
        return {int(k): v for k, v in self._json("ui/labels", {}).items()}

    @labels.setter
    def labels(self, value: dict[int, str]) -> None:
        self.qs.setValue("ui/labels", json.dumps({str(k): v for k, v in value.items()}))

    @property
    def hidden_channels(self) -> set[int]:
        return {int(c) for c in self._json("ui/hidden", [])}

    @hidden_channels.setter
    def hidden_channels(self, value: set[int]) -> None:
        self.qs.setValue("ui/hidden", json.dumps(sorted(value)))

    @property
    def channel_props(self) -> dict[int, ChannelProps]:
        return props_from_json(self._json("meas/channel_props", {}))

    @channel_props.setter
    def channel_props(self, value: dict[int, ChannelProps]) -> None:
        self.qs.setValue("meas/channel_props", json.dumps(props_to_json(value)))

    # -------------------------------------------------------- E-Mail
    @property
    def email(self) -> EmailConfig:
        return EmailConfig(
            enabled=self._get("email/enabled", False, bool),
            host=self._get("email/host", ""),
            port=self._get("email/port", 587, int),
            security=self._get("email/security", "starttls"),
            username=self._get("email/username", ""),
            password=decrypt(self._get("email/password", "")),
            sender=self._get("email/sender", ""),
            recipients=[r for r in self._json("email/recipients", []) if r],
            min_interval_min=self._get("email/min_interval_min", 15.0, float),
        )

    @email.setter
    def email(self, cfg: EmailConfig) -> None:
        self.qs.setValue("email/enabled", cfg.enabled)
        self.qs.setValue("email/host", cfg.host)
        self.qs.setValue("email/port", cfg.port)
        self.qs.setValue("email/security", cfg.security)
        self.qs.setValue("email/username", cfg.username)
        self.qs.setValue("email/password", encrypt(cfg.password))
        self.qs.setValue("email/sender", cfg.sender)
        self.qs.setValue("email/recipients", json.dumps(cfg.recipients))
        self.qs.setValue("email/min_interval_min", cfg.min_interval_min)
