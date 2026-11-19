"""CSV-Aufzeichnung der Messwerte.

Jede Zeile wird sofort geschrieben und geflusht, damit bei Absturz oder
Stromausfall höchstens die letzte Zeile verloren geht. Optional wird um
Mitternacht automatisch eine neue Datei begonnen.
"""

from __future__ import annotations

import csv
import math
from datetime import date, datetime
from pathlib import Path

from .instrument import ScanResult

CSV_FORMATS = {
    "excel_de": ("Excel (DE): Semikolon, Dezimalkomma", ";", ","),
    "standard": ("Standard: Komma, Dezimalpunkt", ",", "."),
}

EVENT_COLUMN = "Ereignis"


class CsvRecorder:
    def __init__(self, folder: str | Path, channels: list[int], labels: dict[int, str],
                 unit: str, metadata: dict[str, str], csv_format: str = "excel_de",
                 prefix: str = "Temperaturen", rotate_daily: bool = False,
                 start_time: datetime | None = None):
        self.folder = Path(folder)
        self.channels = list(channels)
        self.labels = dict(labels)
        self.unit = unit
        self.metadata = dict(metadata)
        _, self.delimiter, self.decimal = CSV_FORMATS.get(csv_format, CSV_FORMATS["excel_de"])
        self.rotate_daily = rotate_daily
        self.prefix = "".join(c for c in prefix if c.isalnum() or c in "-_ ").strip() or "Temperaturen"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.start_time = start_time or datetime.now()
        self.rows = 0            # Zeilen gesamt (über alle Dateien)
        self.files: list[Path] = []
        self._fh = None
        self._writer = None
        self._file_date: date | None = None
        self._open_file(self.start_time)

    # ------------------------------------------------------------------ Dateien
    @property
    def path(self) -> Path:
        return self.files[-1]

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        i = 2
        while True:
            candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
            if not candidate.exists():
                return candidate
            i += 1

    def _open_file(self, when: datetime) -> None:
        part = len(self.files) + 1
        name = f"{self.prefix}_{when:%Y-%m-%d_%H-%M-%S}.csv"
        path = self._unique_path(self.folder / name)
        # utf-8-sig: Excel erkennt Umlaute und °-Zeichen korrekt
        self._fh = open(path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._fh, delimiter=self.delimiter)
        self._file_date = when.date()
        self.files.append(path)
        meta = dict(self.metadata)
        meta["Aufnahmestart"] = self.start_time.isoformat(sep=" ", timespec="seconds")
        if self.rotate_daily:
            meta["Teil"] = f"{part} (neue Datei je Kalendertag)"
            if part > 1:
                meta["Vorherige Datei"] = self.files[-2].name
        for key, value in meta.items():
            self._writer.writerow([f"# {key}", value])
        header = ["Zeitstempel", "Zeit seit Start [s]"]
        for ch in self.channels:
            label = self.labels.get(ch, "").strip()
            name = f"{ch} {label}" if label else str(ch)
            header.append(f"{name} [{self.unit}]")
        header.append(EVENT_COLUMN)
        self._writer.writerow(header)
        self._fh.flush()

    # ------------------------------------------------------------------ Schreiben
    def _fmt(self, value: float, digits: int = 4) -> str:
        if value is None or math.isnan(value):
            return ""  # offenes Thermoelement / kein Wert
        txt = f"{value:.{digits}f}"
        return txt.replace(".", self.decimal) if self.decimal != "." else txt

    def write(self, result: ScanResult, event: str = "") -> None:
        if self._fh is None:
            return
        ts = datetime.fromtimestamp(result.timestamp)
        if self.rotate_daily and self._file_date is not None and ts.date() != self._file_date:
            self._fh.close()
            self._open_file(ts)
        values = {r.channel: r.value for r in result.readings}
        row = [ts.isoformat(sep=" ", timespec="milliseconds"),
               self._fmt(result.timestamp - self.start_time.timestamp(), 3)]
        row += [self._fmt(values.get(ch, float("nan"))) for ch in self.channels]
        row.append(event)
        self._writer.writerow(row)
        self._fh.flush()
        self.rows += 1

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    @property
    def is_open(self) -> bool:
        return self._fh is not None
