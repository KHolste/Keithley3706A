"""Geplante Aufnahme: Start/Stopp nach Uhrzeit, Dauer oder Temperaturschwelle."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Trigger:
    mode: str = "now"            # Start: now|time|threshold   Stopp: manual|duration|time|threshold
    at: datetime | None = None
    duration_s: float = 3600.0
    channel: int = 1001
    compare: str = ">"           # ">" oder "<"
    value: float = 0.0

    def threshold_met(self, readings: dict[int, float] | None) -> bool:
        if readings is None:
            return False
        v = readings.get(self.channel, math.nan)
        if math.isnan(v):
            return False
        return v > self.value if self.compare == ">" else v < self.value

    def describe(self, unit: str, is_stop: bool) -> str:
        if self.mode in ("now", "manual"):
            return "manuell" if is_stop else "sofort"
        if self.mode == "time" and self.at:
            return f"um {self.at:%d.%m. %H:%M:%S}"
        if self.mode == "duration":
            h, rem = divmod(int(self.duration_s), 3600)
            m, s = divmod(rem, 60)
            return f"nach {h:d}:{m:02d}:{s:02d}"
        if self.mode == "threshold":
            return f"wenn {self.channel} {self.compare} {self.value:g} {unit}"
        return "?"


class ScheduleRunner:
    WAITING, RECORDING, DONE = "waiting", "recording", "done"

    def __init__(self, start: Trigger, stop: Trigger):
        self.start = start
        self.stop = stop
        self.state = self.WAITING
        self.started_at: datetime | None = None

    def check(self, now: datetime, readings: dict[int, float] | None = None) -> str | None:
        """Gibt 'start', 'stop' oder None zurück."""
        if self.state == self.WAITING:
            s = self.start
            go = (s.mode == "now"
                  or (s.mode == "time" and s.at is not None and now >= s.at)
                  or (s.mode == "threshold" and s.threshold_met(readings)))
            if go:
                self.state = self.RECORDING
                self.started_at = now
                return "start"
        elif self.state == self.RECORDING:
            e = self.stop
            end = ((e.mode == "duration" and self.started_at is not None
                    and now >= self.started_at + timedelta(seconds=e.duration_s))
                   or (e.mode == "time" and e.at is not None and now >= e.at)
                   or (e.mode == "threshold" and e.threshold_met(readings)))
            if end:
                self.state = self.DONE
                return "stop"
        return None

    def describe(self, unit: str) -> str:
        return (f"Start {self.start.describe(unit, False)}, "
                f"Stopp {self.stop.describe(unit, True)}")
