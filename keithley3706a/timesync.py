"""Zeitabgleich per SNTP (RFC 4330) ohne Administratorrechte.

Die Systemuhr wird NICHT verstellt. Stattdessen wird die Abweichung der PC-Uhr
zum Zeitserver gemessen und – falls gewünscht – auf alle Zeitstempel angewendet.
Zwischen zwei Abfragen wird die monotone Uhr verwendet, damit Sprünge der
PC-Uhr (manuelles Stellen, Windows-Sync) die Zeitstempel nicht verfälschen.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

NTP_EPOCH_OFFSET = 2208988800  # Sekunden zwischen 1900-01-01 und 1970-01-01
MJD_UNIX_EPOCH = 40587.0       # MJD von 1970-01-01 00:00 UTC
DEFAULT_SERVERS = ["ntp1.uni-giessen.de", "ntp2.uni-giessen.de", "ptbtime1.ptb.de"]


@dataclass
class SntpResult:
    server: str
    offset: float      # Server-Zeit minus PC-Zeit [s]
    delay: float       # Laufzeit hin und zurück [s]
    stratum: int
    when: float        # PC-Zeit der Messung


def _to_ntp(t: float) -> tuple[int, int]:
    t += NTP_EPOCH_OFFSET
    sec = int(t)
    return sec, int((t - sec) * 2**32)


def _from_ntp(sec: int, frac: int) -> float:
    return sec - NTP_EPOCH_OFFSET + frac / 2**32


def query(server: str, timeout: float = 2.0) -> SntpResult:
    """Eine SNTP-Abfrage; wirft OSError/ValueError bei Fehler."""
    addr = socket.getaddrinfo(server, 123, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    packet = bytearray(48)
    packet[0] = (0 << 6) | (4 << 3) | 3          # LI=0, Version 4, Mode 3 (Client)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        t0 = time.time()
        struct.pack_into("!II", packet, 40, *_to_ntp(t0))
        s.sendto(packet, addr)
        data, _ = s.recvfrom(512)
        t3 = time.time()
    if len(data) < 48:
        raise ValueError("Antwort zu kurz")
    mode = data[0] & 0x7
    stratum = data[1]
    if mode not in (4, 5) or stratum == 0 or stratum > 15:
        raise ValueError(f"Ungültige Antwort (Modus {mode}, Stratum {stratum})")
    orig = struct.unpack_from("!II", data, 24)
    if orig != _to_ntp(t0):
        raise ValueError("Antwort passt nicht zur Anfrage")
    t1 = _from_ntp(*struct.unpack_from("!II", data, 32))   # Empfang beim Server
    t2 = _from_ntp(*struct.unpack_from("!II", data, 40))   # Sendung beim Server
    offset = ((t1 - t0) + (t2 - t3)) / 2
    delay = max(0.0, (t3 - t0) - (t2 - t1))   # Rundung kann minimal negativ ergeben
    return SntpResult(server, offset, delay, stratum, t3)


def best_of(servers: list[str], samples: int = 3) -> SntpResult:
    """Mehrere Abfragen; Ergebnis mit der kürzesten Laufzeit (genaueste Messung)."""
    results, errors = [], []
    for srv in servers:
        for _ in range(samples):
            try:
                results.append(query(srv))
            except (OSError, ValueError) as exc:
                errors.append(f"{srv}: {exc}")
                break
        if results:
            break   # erster erreichbarer Server genügt
    if not results:
        raise OSError("Kein Zeitserver erreichbar – " + "; ".join(errors))
    return min(results, key=lambda r: r.delay)


class Clock:
    """Liefert korrigierte Zeitstempel (Unix-Zeit) und verwaltet den Abgleich."""

    def __init__(self):
        self._lock = threading.Lock()
        self.correct = False
        self.last: SntpResult | None = None
        self.last_error: str = ""
        # Anker: (PC-Zeit, monotone Zeit, Offset) zum Zeitpunkt der letzten Messung
        self._anchor: tuple[float, float, float] | None = None

    def now(self) -> float:
        with self._lock:
            if not self.correct or self._anchor is None:
                return time.time()
            wall, mono, offset = self._anchor
            return wall + offset + (time.monotonic() - mono)

    def update(self, result: SntpResult) -> None:
        with self._lock:
            self.last = result
            self.last_error = ""
            self._anchor = (time.time(), time.monotonic(), result.offset)

    def synced(self, max_age_s: float = 7200) -> bool:
        return self.last is not None and time.time() - self.last.when < max_age_s

    def correct_timestamp(self, pc_time: float) -> float:
        """Rechnet einen mit time.time() genommenen Zeitstempel auf Serverzeit um."""
        if not self.correct or self._anchor is None:
            return pc_time
        return self.now() - (time.time() - pc_time)


def mjd(unix_time: float) -> float:
    """Modifiziertes Julianisches Datum (UTC) aus Unix-Zeit."""
    return unix_time / 86400.0 + MJD_UNIX_EPOCH


def iso_local(unix_time: float) -> str:
    """ISO 8601 mit Zeitzone, z. B. 2026-11-19T14:22:41.412+01:00."""
    return datetime.fromtimestamp(unix_time, tz=timezone.utc).astimezone().isoformat(
        timespec="milliseconds")
