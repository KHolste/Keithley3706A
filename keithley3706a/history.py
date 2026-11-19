"""Speichersparender Verlauf für den Live-Plot (begrenzte Länge pro Kanal)."""

from __future__ import annotations

import numpy as np

from .instrument import ScanResult


class History:
    def __init__(self, max_points: int = 200_000):
        self.max_points = max_points
        self._t = np.empty(0)
        self._v: dict[int, np.ndarray] = {}
        self._n = 0

    def clear(self) -> None:
        self._t = np.empty(0)
        self._v = {}
        self._n = 0

    def _grow(self) -> None:
        cap = max(1024, len(self._t) * 2)
        cap = min(cap, self.max_points)
        if cap <= len(self._t):  # voll: älteste Hälfte verwerfen
            keep = self.max_points // 2
            self._t[:keep] = self._t[self._n - keep:self._n]
            for ch, arr in self._v.items():
                arr[:keep] = arr[self._n - keep:self._n]
            self._n = keep
            return
        t = np.full(cap, np.nan)
        t[:self._n] = self._t[:self._n]
        self._t = t
        for ch, arr in list(self._v.items()):
            a = np.full(cap, np.nan)
            a[:self._n] = arr[:self._n]
            self._v[ch] = a

    def append(self, result: ScanResult) -> None:
        if self._n >= len(self._t):
            self._grow()
        i = self._n
        self._t[i] = result.timestamp
        for r in result.readings:
            arr = self._v.get(r.channel)
            if arr is None:
                arr = np.full(len(self._t), np.nan)
                self._v[r.channel] = arr
            arr[i] = r.value
        self._n += 1

    @property
    def size(self) -> int:
        return self._n

    def times(self) -> np.ndarray:
        return self._t[:self._n]

    def values(self, channel: int) -> np.ndarray | None:
        arr = self._v.get(channel)
        return None if arr is None else arr[:self._n]

    def channels(self) -> list[int]:
        return sorted(self._v)
