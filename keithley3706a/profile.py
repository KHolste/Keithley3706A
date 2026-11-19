"""Messprofile als JSON: vollständige, reproduzierbare Beschreibung einer Messung.

Ein Profil kann in der GUI gespeichert/geladen und mit dem Headless-Modus
(``python -m keithley3706a.headless profil.json``) ohne Oberfläche ausgeführt werden.
"""

from __future__ import annotations

import json
import pprint
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import __version__
from .channel_props import ChannelProps, props_from_json, props_to_json
from .instrument import (
    REF_JUNCTIONS, TEMP_UNITS, THERMOCOUPLE_TYPES, MeasurementConfig, format_channel_list,
    parse_channel_list,
)

PROFILE_VERSION = 1


def build_profile(*, resource: str, interval_s: float, cfg: MeasurementConfig,
                  props: dict[int, ChannelProps], labels: dict[int, str],
                  hidden: set[int] | None = None, csv: dict | None = None,
                  alarm: dict | None = None, note: str = "",
                  time_sync: dict | None = None) -> dict:
    meas = asdict(cfg)
    meas["channels"] = format_channel_list(cfg.channels)
    meas.pop("channel_types", None)  # steckt in channel_props
    return {
        "profile_version": PROFILE_VERSION,
        "program_version": __version__,
        "created": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "resource": resource,
        "interval_s": interval_s,
        "measurement": meas,
        "channel_props": props_to_json(props),
        "labels": {str(k): v for k, v in labels.items() if v},
        "hidden_channels": sorted(hidden or []),
        "csv": csv or {},
        "alarm": alarm or {},
        "time": time_sync or {},
    }


def parse_profile(data: dict) -> dict:
    """Validiert ein Profil und liefert typisierte Werte zurück."""
    if not isinstance(data, dict) or "measurement" not in data:
        raise ValueError("Keine gültige Profildatei (Abschnitt 'measurement' fehlt).")
    m = data["measurement"]
    cfg = MeasurementConfig()
    cfg.channels = parse_channel_list(str(m.get("channels", "1001:1009")))
    cfg.tc_type = m.get("tc_type", cfg.tc_type)
    if cfg.tc_type not in THERMOCOUPLE_TYPES:
        raise ValueError(f"Unbekannter Thermoelement-Typ: {cfg.tc_type}")
    cfg.ref_junction = m.get("ref_junction", cfg.ref_junction)
    if cfg.ref_junction not in REF_JUNCTIONS:
        raise ValueError(f"Unbekannte Vergleichsstelle: {cfg.ref_junction}")
    cfg.sim_ref_temp = float(m.get("sim_ref_temp", cfg.sim_ref_temp))
    if not 0 <= cfg.sim_ref_temp <= 65:
        raise ValueError("Simulierte Referenztemperatur muss zwischen 0 und 65 °C liegen.")
    cfg.units = m.get("units", cfg.units)
    if cfg.units not in TEMP_UNITS:
        raise ValueError(f"Unbekannte Einheit: {cfg.units}")
    cfg.nplc = float(m.get("nplc", cfg.nplc))
    if not 0.0005 <= cfg.nplc <= 15:
        raise ValueError("NPLC muss zwischen 0,0005 und 15 liegen.")
    cfg.open_detect = bool(m.get("open_detect", cfg.open_detect))
    props = props_from_json(data.get("channel_props", {}))
    cfg.channel_types = {ch: p.tc_type for ch, p in props.items() if p.tc_type}
    interval = float(data.get("interval_s", 1.0))
    if not 0.1 <= interval <= 86400:
        raise ValueError("Messintervall muss zwischen 0,1 s und 24 h liegen.")
    return {
        "resource": data.get("resource", ""),
        "interval_s": interval,
        "config": cfg,
        "props": props,
        "labels": {int(k): v for k, v in data.get("labels", {}).items()},
        "hidden": {int(c) for c in data.get("hidden_channels", [])},
        "csv": data.get("csv", {}),
        "alarm": data.get("alarm", {}),
        "note": data.get("note", ""),
    }


def save_profile(path: str | Path, profile: dict) -> None:
    Path(path).write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


def load_profile(path: str | Path) -> dict:
    return parse_profile(json.loads(Path(path).read_text(encoding="utf-8")))


SCRIPT_TEMPLATE = '''"""Automatisch erzeugtes Messskript – {created}

Reproduziert die Messung ohne Oberfläche (Headless) mit genau diesen Einstellungen.
Aufruf:   py -3.11 "{script_name}" [--dauer SEKUNDEN] [--ordner PFAD]
Abbruch:  Strg+C (die CSV-Datei bleibt vollständig erhalten)
"""
import sys

sys.path.insert(0, r"{project_dir}")

from keithley3706a.headless import main

PROFILE = {profile_json}

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], profile=PROFILE))
'''


def export_script(path: str | Path, profile: dict, project_dir: str | Path) -> None:
    path = Path(path)
    text = SCRIPT_TEMPLATE.format(
        created=profile.get("created", ""),
        script_name=path.name,
        project_dir=str(Path(project_dir).resolve()),
        profile_json=pprint.pformat(profile, indent=1, width=100, sort_dicts=False),
    )
    path.write_text(text, encoding="utf-8")
