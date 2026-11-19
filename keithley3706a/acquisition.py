"""Messschleife in einem eigenen Thread, damit die GUI nie blockiert."""

from __future__ import annotations

import queue
import threading
import time

from PySide6.QtCore import QThread, Signal

from .instrument import InstrumentError, MeasurementConfig, ScanResult, make_instrument


class AcquisitionThread(QThread):
    connected = Signal(str, object)   # idn, {slot: card}
    disconnected = Signal()
    scan_started = Signal()
    scan_done = Signal(object, float)  # ScanResult, Scandauer [s]
    error = Signal(str)
    status = Signal(str)

    RECONNECT_DELAY_S = 3.0

    def __init__(self, resource: str, config: MeasurementConfig, interval_s: float,
                 auto_reconnect: bool = True, parent=None):
        super().__init__(parent)
        self._resource = resource
        self._config = config
        self._interval = max(0.1, interval_s)
        self._auto_reconnect = auto_reconnect
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._paused = threading.Event()
        self._single = threading.Event()
        self._commands: queue.Queue = queue.Queue()

    # ---------------------------------------------------------------- API (GUI-Thread)
    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def set_interval(self, seconds: float) -> None:
        self._interval = max(0.1, seconds)
        self._wake.set()

    def apply_config(self, cfg: MeasurementConfig) -> None:
        self._commands.put(("config", cfg))
        self._wake.set()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
        self._wake.set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def single_scan(self) -> None:
        self._single.set()
        self._wake.set()

    # ---------------------------------------------------------------- Thread
    def run(self) -> None:
        inst = None
        while not self._stop.is_set():
            try:
                if inst is None:
                    self.status.emit(f"Verbinde mit {self._resource} …")
                    inst = make_instrument(self._resource)
                    inst.connect()
                    inst.configure(self._config)
                    self.connected.emit(inst.idn, dict(inst.cards))

                while not self._commands.empty():
                    kind, payload = self._commands.get_nowait()
                    if kind == "config":
                        self._config = payload
                        inst.configure(payload)
                        self.status.emit("Konfiguration übernommen")

                if self._paused.is_set() and not self._single.is_set():
                    self._wake.wait(0.5)
                    self._wake.clear()
                    continue
                self._single.clear()

                t_start = time.monotonic()
                self.scan_started.emit()
                result: ScanResult = inst.scan()
                self.scan_done.emit(result, time.monotonic() - t_start)

                remaining = self._interval - (time.monotonic() - t_start)
                if remaining > 0 and not self._paused.is_set():
                    self._wake.wait(remaining)
                    self._wake.clear()
            except Exception as exc:  # VISA-Fehler, Timeout, Gerätefehler …
                msg = str(exc) if isinstance(exc, InstrumentError) else f"{type(exc).__name__}: {exc}"
                self.error.emit(msg)
                if inst is not None:
                    try:
                        inst.close()
                    except Exception:
                        pass
                    inst = None
                    self.disconnected.emit()
                if not self._auto_reconnect:
                    break
                self.status.emit(f"Neuer Verbindungsversuch in {self.RECONNECT_DELAY_S:.0f} s …")
                self._wake.wait(self.RECONNECT_DELAY_S)
                self._wake.clear()

        if inst is not None:
            try:
                inst.close()
            except Exception:
                pass
            self.disconnected.emit()
        self.status.emit("Getrennt")
