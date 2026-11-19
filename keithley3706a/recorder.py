"""CSV-Aufzeichnung der Messwerte.

Jede Zeile wird sofort geschrieben und geflusht, damit bei Absturz oder
Stromausfall höchstens die letzte Zeile verloren geht.
"""

from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path

from .instrument import ScanResult

CSV_FORMATS = {
    "excel_de": ("Excel (DE): Semikolon, Dezimalkomma", ";", ","),
    "standard": ("Standard: Komma, Dezimalpunkt", ",", "."),
}


class CsvRecorder:
    def __init__(self, folder: str | Path, channels: list[int], labels: dict[int, str],
                 unit: str, metadata: dict[str, str], csv_format: str = "excel_de",
                 prefix: str = "Temperaturen"):
        self.folder = Path(folder)
        self.channels = list(channels)
        _, self.delimiter, self.decimal = CSV_FORMATS.get(csv_format, CSV_FORMATS["excel_de"])
        self.folder.mkdir(parents=True, exist_ok=True)
        self.start_time = datetime.now()
        safe_prefix = "".join(c for c in prefix if c.isalnum() or c in "-_ ").strip() or "Temperaturen"
        self.path = self._unique_path(
            self.folder / f"{safe_prefix}_{self.start_time:%Y-%m-%d_%H-%M-%S}.csv")
        # utf-8-sig: Excel erkennt Umlaute und °-Zeichen korrekt
        self._fh = open(self.path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._fh, delimiter=self.delimiter)
        self.rows = 0
        for key, value in metadata.items():
            self._writer.writerow([f"# {key}", value])
        header = ["Zeitstempel", "Zeit seit Start [s]"]
        for ch in self.channels:
            label = labels.get(ch, "").strip()
            name = f"{ch} {label}" if label else str(ch)
            header.append(f"{name} [{unit}]")
        self._writer.writerow(header)
        self._fh.flush()

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

    def _fmt(self, value: float, digits: int = 4) -> str:
        if value is None or math.isnan(value):
            return ""  # offenes Thermoelement / kein Wert
        txt = f"{value:.{digits}f}"
        return txt.replace(".", self.decimal) if self.decimal != "." else txt

    def write(self, result: ScanResult) -> None:
        if self._fh is None:
            return
        values = {r.channel: r.value for r in result.readings}
        ts = datetime.fromtimestamp(result.timestamp)
        row = [ts.isoformat(sep=" ", timespec="milliseconds"),
               self._fmt(result.timestamp - self.start_time.timestamp(), 3)]
        row += [self._fmt(values.get(ch, float("nan"))) for ch in self.channels]
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
