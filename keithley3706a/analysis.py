"""Laufende Statistik (Min/Max/Mittel) und Änderungsrate je Kanal."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .history import History
from .instrument import ScanResult


@dataclass
class ChannelStats:
    minimum: float = math.inf
    maximum: float = -math.inf
    total: float = 0.0
    count: int = 0

    def add(self, value: float) -> None:
        if math.isnan(value):
            return
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        self.total += value
        self.count += 1

    @property
    def mean(self) -> float:
        return self.total / self.count if self.count else math.nan

    @property
    def min(self) -> float:
        return self.minimum if self.count else math.nan

    @property
    def max(self) -> float:
        return self.maximum if self.count else math.nan


class StatsTracker:
    def __init__(self):
        self.stats: dict[int, ChannelStats] = {}
        self.since: float | None = None

    def reset(self) -> None:
        self.stats = {}
        self.since = None

    def add(self, result: ScanResult) -> None:
        if self.since is None:
            self.since = result.timestamp
        for r in result.readings:
            self.stats.setdefault(r.channel, ChannelStats()).add(r.value)

    def get(self, channel: int) -> ChannelStats:
        return self.stats.get(channel, ChannelStats())


def rate_per_minute(history: History, channel: int, window_s: float,
                    min_points: int = 3) -> float:
    """Steigung einer Ausgleichsgeraden über die letzten window_s Sekunden, in Einheit/min."""
    t = history.times()
    v = history.values(channel)
    if v is None or t.size < min_points:
        return math.nan
    start = int(np.searchsorted(t, t[-1] - window_s))
    tt, vv = t[start:], v[start:]
    mask = np.isfinite(vv)
    if mask.sum() < min_points:
        return math.nan
    tt, vv = tt[mask], vv[mask]
    span = tt[-1] - tt[0]
    if span < min(5.0, window_s / 2):
        return math.nan
    slope = np.polyfit(tt - tt[0], vv, 1)[0]
    return float(slope * 60.0)
