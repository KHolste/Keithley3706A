"""Lädt mit diesem Programm erzeugte CSV-Dateien (beide Formate) zum Vergleich im Plot."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .recorder import EVENT_COLUMN


@dataclass
class LoadedSeries:
    name: str          # Spaltenname ohne Einheit, z. B. "1001 Ofen"
    channel: int | None
    unit: str
    values: np.ndarray


@dataclass
class LoadedFile:
    path: Path
    times: np.ndarray                        # Unix-Zeitstempel
    series: list[LoadedSeries]
    events: list[tuple[float, str]] = field(default_factory=list)


def _to_float(txt: str, decimal: str) -> float:
    txt = txt.strip()
    if not txt:
        return np.nan
    if decimal == ",":
        txt = txt.replace(".", "").replace(",", ".") if "," in txt else txt
    try:
        return float(txt)
    except ValueError:
        return np.nan


def load_csv(path: str | Path) -> LoadedFile:
    path = Path(path)
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        lines = fh.read().splitlines()
    header_idx = next((i for i, l in enumerate(lines) if l.startswith("Zeitstempel")), None)
    if header_idx is None:
        raise ValueError("Keine Kopfzeile 'Zeitstempel' gefunden – keine Datei dieses Programms?")
    header_line = lines[header_idx]
    delimiter = ";" if ";" in header_line else ","
    decimal = "," if delimiter == ";" else "."
    rows = list(csv.reader(lines[header_idx:], delimiter=delimiter))
    header, data = rows[0], [r for r in rows[1:] if r and r[0].strip()]
    event_col = header.index(EVENT_COLUMN) if EVENT_COLUMN in header else None
    # Zeitspalten (Zeitstempel, MJD, Zeit seit Start) überspringen – alte und neue Dateien
    value_cols = [i for i in range(1, len(header))
                  if i != event_col and not header[i].startswith(("Zeit", "MJD"))]

    times = []
    for r in data:
        try:
            # ISO mit oder ohne UTC-Offset (ältere Dateien: ohne -> Ortszeit)
            times.append(datetime.fromisoformat(r[0].strip()).timestamp())
        except ValueError:
            times.append(np.nan)
    times_arr = np.array(times, dtype=float)

    series = []
    for col in value_cols:
        title = header[col]
        m = re.match(r"^(.*?)\s*\[(.*)\]\s*$", title)
        name, unit = (m.group(1), m.group(2)) if m else (title, "")
        ch = re.match(r"^(\d{4})", name)
        vals = np.array([_to_float(r[col], decimal) if col < len(r) else np.nan for r in data])
        series.append(LoadedSeries(name, int(ch.group(1)) if ch else None, unit, vals))

    events = []
    if event_col is not None:
        for t, r in zip(times_arr, data):
            if event_col < len(r) and r[event_col].strip():
                events.append((float(t), r[event_col].strip()))
    ok = np.isfinite(times_arr)
    for s in series:
        s.values = s.values[ok]
    return LoadedFile(path, times_arr[ok], series, events)
