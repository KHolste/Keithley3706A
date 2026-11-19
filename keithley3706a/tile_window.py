"""Große Kachelansicht für Fernablesung (z. B. zweiter Monitor, Vollbild mit F11)."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from .theme import channel_color


class Tile(QFrame):
    def __init__(self, channel: int, color: str):
        super().__init__()
        self.setObjectName("Tile")
        self.channel = channel
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)
        self.name = QLabel()
        self.name.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.value = QLabel("–")
        self.value.setAlignment(Qt.AlignCenter)
        self.info = QLabel()
        self.info.setObjectName("Muted")
        self.info.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.name)
        lay.addWidget(self.value, 1)
        lay.addWidget(self.info)

    def set_state(self, alarm: bool, is_open: bool) -> None:
        changed = (self.property("alarm") != alarm) or (self.property("open") != is_open)
        self.setProperty("alarm", alarm)
        self.setProperty("open", is_open)
        if changed:
            self.style().unpolish(self)
            self.style().polish(self)

    def scale(self, height: int) -> None:
        f = QFont(self.value.font())
        f.setPointSizeF(max(14.0, height * 0.28))
        f.setBold(True)
        self.value.setFont(f)
        f2 = QFont(self.name.font())
        f2.setPointSizeF(max(10.0, height * 0.075))
        self.name.setFont(f2)
        self.info.setFont(f2)


class TileWindow(QWidget):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Kachelansicht – Keithley 3706A")
        self.resize(1100, 700)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(16, 16, 16, 16)
        self.grid.setSpacing(14)
        self.tiles: dict[int, Tile] = {}
        QShortcut(QKeySequence("F11"), self, self._toggle_full)
        QShortcut(QKeySequence("Esc"), self, lambda: self.isFullScreen() and self.showNormal())

    def _toggle_full(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def mouseDoubleClickEvent(self, _e) -> None:
        self._toggle_full()

    def set_channels(self, channels: list[int], all_channels: list[int]) -> None:
        for t in self.tiles.values():
            t.setParent(None)
            t.deleteLater()
        self.tiles = {}
        n = max(1, len(channels))
        cols = math.ceil(math.sqrt(n * 1.6))
        for i, ch in enumerate(channels):
            idx = all_channels.index(ch) if ch in all_channels else i
            tile = Tile(ch, channel_color(idx))
            self.tiles[ch] = tile
            self.grid.addWidget(tile, i // cols, i % cols)
        self._rescale()

    def update_tile(self, channel: int, name: str, value_text: str, info: str,
                    alarm: bool, is_open: bool) -> None:
        t = self.tiles.get(channel)
        if t is None:
            return
        t.name.setText(name)
        t.value.setText(value_text)
        t.info.setText(info)
        t.set_state(alarm, is_open)

    def _rescale(self) -> None:
        if not self.tiles:
            return
        h = min(t.height() for t in self.tiles.values())
        w = min(t.width() for t in self.tiles.values())
        size = min(h, w * 0.55)
        for t in self.tiles.values():
            t.scale(int(size))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._rescale()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._rescale()

    def closeEvent(self, e) -> None:
        self.closed.emit()
        super().closeEvent(e)
