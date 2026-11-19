"""Programmatisch gezeichnetes App-Icon (Thermometer), keine Bilddateien nötig."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _draw(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#2563eb")))
    p.drawRoundedRect(QRectF(2 * s, 2 * s, 60 * s, 60 * s), 14 * s, 14 * s)
    # Thermometer
    p.setBrush(QBrush(QColor("#ffffff")))
    path = QPainterPath()
    path.addRoundedRect(QRectF(26 * s, 10 * s, 12 * s, 32 * s), 6 * s, 6 * s)
    path.addEllipse(QRectF(21 * s, 34 * s, 22 * s, 22 * s))
    p.drawPath(path.simplified())
    p.setBrush(QBrush(QColor("#ef4444")))
    p.drawRect(QRectF(29.5 * s, 22 * s, 5 * s, 18 * s))
    p.drawEllipse(QRectF(25 * s, 38 * s, 14 * s, 14 * s))
    p.setPen(QPen(QColor("#ffffff"), 2.2 * s, Qt.SolidLine, Qt.RoundCap))
    for y in (16, 22, 28):
        p.drawLine(int(42 * s), int(y * s), int(48 * s), int(y * s))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw(size))
    return icon


def save_ico(path: str) -> None:
    """Speichert das Icon als .ico (für PyInstaller)."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    if not _draw(256).save(path, "ICO"):
        _draw(256).save(path.rsplit(".", 1)[0] + ".png", "PNG")
        raise OSError("ICO-Export nicht verfügbar")
