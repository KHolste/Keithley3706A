"""Treiber für das Keithley 3706A (TSP-Befehlssatz) sowie ein Simulator.

Referenz: Series 3700A Reference Manual, Abschnitt 8 (dmm.*, scan.*, printbuffer()).
Gemessen wird per Scan: alle gewählten Kanäle werden mit einer gemeinsamen
Thermoelement-Konfiguration in einen Reading-Buffer gescannt und anschließend mit
einem einzigen printbuffer()-Aufruf (Kanal, Messwert, Einheit) abgeholt.
"""

from __future__ import annotations

import math
import random
import re
import time
from dataclasses import dataclass, field

# Rückgabewert des Geräts bei Overflow bzw. offenem Thermoelement
OVERFLOW_THRESHOLD = 9.0e37

THERMOCOUPLE_TYPES = ["J", "K", "T", "E", "R", "S", "B", "N"]
REF_JUNCTIONS = {
    "INTERNAL": "Intern (Klemmenblock-CJC)",
    "SIMULATED": "Simuliert (feste Temperatur)",
    "EXTERNAL": "Extern",
}
TEMP_UNITS = {"CELSIUS": "°C", "KELVIN": "K", "FAHRENHEIT": "°F"}

UNIT_MAP = {
    "Celsius": "°C",
    "Kelvin": "K",
    "Fahrenheit": "°F",
    "Volts DC": "V",
    "Volts AC": "V AC",
    "Ohms 2wire": "Ω",
    "Ohms 4wire": "Ω",
}

CONFIG_NAME = "pyTCconfig"
BUFFER_NAME = "pyTCbuf"

SIMULATION_RESOURCE = "SIMULATION"


class InstrumentError(Exception):
    """Fehler bei der Gerätekommunikation oder ein Eintrag in der Fehlerqueue."""


@dataclass
class MeasurementConfig:
    channels: list[int] = field(default_factory=lambda: list(range(1001, 1010)))
    tc_type: str = "K"
    ref_junction: str = "INTERNAL"
    sim_ref_temp: float = 23.0
    units: str = "CELSIUS"
    nplc: float = 1.0
    open_detect: bool = True
    # abweichender Thermoelement-Typ je Kanal (sonst tc_type)
    channel_types: dict[int, str] = field(default_factory=dict)

    def type_for(self, channel: int) -> str:
        return self.channel_types.get(channel, self.tc_type)

    def type_groups(self) -> dict[str, list[int]]:
        groups: dict[str, list[int]] = {}
        for ch in self.channels:
            groups.setdefault(self.type_for(ch), []).append(ch)
        return groups


@dataclass
class Reading:
    channel: int
    value: float  # NaN wenn offen / Overflow
    unit: str
    is_open: bool


@dataclass
class ScanResult:
    timestamp: float  # time.time() des PCs nach Abschluss des Scans
    readings: list[Reading]


def parse_channel_list(text: str) -> list[int]:
    """'1001:1009, 1015' -> [1001, ..., 1009, 1015] (Reihenfolge bleibt, keine Duplikate)."""
    result: list[int] = []
    for part in re.split(r"[,;\s]+", text.strip()):
        if not part:
            continue
        if ":" in part or "-" in part:
            a, b = re.split(r"[:\-]", part, maxsplit=1)
            start, end = int(a), int(b)
            if end < start:
                start, end = end, start
            rng = range(start, end + 1)
        else:
            rng = [int(part)]
        for ch in rng:
            if not 1001 <= ch <= 6999 or ch % 1000 == 0:
                raise ValueError(f"Ungültige Kanalnummer: {ch} (Format SKKK, z. B. 1001)")
            if ch not in result:
                result.append(ch)
    if not result:
        raise ValueError("Keine Kanäle angegeben")
    return result


def format_channel_list(channels: list[int]) -> str:
    """[1001..1009, 1015] -> '1001:1009,1015' (kompakt für TSP und Anzeige)."""
    if not channels:
        return ""
    chans = sorted(channels)
    parts, start, prev = [], chans[0], chans[0]
    for ch in chans[1:] + [None]:
        if ch is not None and ch == prev + 1:
            prev = ch
            continue
        parts.append(f"{start}:{prev}" if prev > start else f"{start}")
        if ch is not None:
            start = prev = ch
    return ",".join(parts)


def parse_printbuffer(raw: str) -> list[Reading]:
    """Parst 'printbuffer(1, n, buf.channels, buf.readings, buf.units)'.

    Beispiel: '1001+, 2.462775e+01, Celsius, 1002+, 9.900000e+37, Celsius'
    """
    tokens = [t.strip() for t in raw.strip().split(",")]
    tokens = [t for t in tokens if t != ""]
    if len(tokens) % 3:
        raise InstrumentError(f"Unerwartetes Antwortformat ({len(tokens)} Felder): {raw[:120]}")
    readings = []
    for i in range(0, len(tokens), 3):
        ch_txt, val_txt, unit_txt = tokens[i:i + 3]
        m = re.search(r"\d{4}", ch_txt)
        if not m:
            raise InstrumentError(f"Kanal nicht lesbar: {ch_txt!r}")
        try:
            value = float(val_txt)
        except ValueError as exc:
            raise InstrumentError(f"Messwert nicht lesbar: {val_txt!r}") from exc
        is_open = abs(value) >= OVERFLOW_THRESHOLD or math.isnan(value)
        readings.append(Reading(
            channel=int(m.group()),
            value=float("nan") if is_open else value,
            unit=UNIT_MAP.get(unit_txt, unit_txt),
            is_open=is_open,
        ))
    return readings


class Keithley3706A:
    """Dünner TSP-Treiber für Thermoelement-Scans."""

    def __init__(self, resource_name: str, timeout_ms: int = 20000):
        self.resource_name = resource_name
        self.timeout_ms = timeout_ms
        self._rm = None
        self._inst = None
        self.idn = ""
        self.cards: dict[int, str] = {}
        self._config: MeasurementConfig | None = None

    # ------------------------------------------------------------------ Verbindung
    def connect(self) -> None:
        import pyvisa

        self._rm = pyvisa.ResourceManager()
        inst = self._rm.open_resource(self.resource_name)
        inst.timeout = self.timeout_ms
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        try:
            inst.clear()
        except Exception:
            pass
        self._inst = inst
        self.idn = self.query("*IDN?")
        if "3706" not in self.idn:
            raise InstrumentError(f"Kein Keithley 3706A erkannt: {self.idn}")
        # Eventuell aktive Prompts/Errors aus früheren Sitzungen abschalten
        self.write("localnode.prompts = 0")
        self.write("errorqueue.clear()")
        self.cards = {}
        for slot in range(1, 7):
            txt = self.query(f"print(slot[{slot}].idn)")
            if txt and "Empty" not in txt:
                self.cards[slot] = txt

    def close(self) -> None:
        if self._inst is not None:
            try:
                self.write("channel.open('allslots')")
            except Exception:
                pass
            try:
                # Gerät wieder in den lokalen Modus (Frontpanel bedienbar)
                import pyvisa.constants as c
                self._inst.control_ren(c.RENLineOperation.address_gtl)
            except Exception:
                pass
            try:
                self._inst.close()
            except Exception:
                pass
        self._inst = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
        self._rm = None

    @property
    def connected(self) -> bool:
        return self._inst is not None

    # ------------------------------------------------------------------ Low level
    def write(self, cmd: str) -> None:
        if self._inst is None:
            raise InstrumentError("Nicht verbunden")
        self._inst.write(cmd)

    def query(self, cmd: str) -> str:
        if self._inst is None:
            raise InstrumentError("Nicht verbunden")
        return self._inst.query(cmd).strip()

    def check_errors(self) -> None:
        count = int(float(self.query("print(errorqueue.count)")))
        if count <= 0:
            return
        msgs = []
        for _ in range(min(count, 10)):
            msgs.append(self.query("print(errorqueue.next())").replace("\t", " "))
        raise InstrumentError("Gerätefehler: " + " | ".join(msgs))

    # ------------------------------------------------------------------ Messung
    def configure(self, cfg: MeasurementConfig) -> None:
        chan_list = format_channel_list(cfg.channels)
        cmds = [
            "errorqueue.clear()",
            "channel.open('allslots')",
            'dmm.func = "temperature"',
            "dmm.transducer = dmm.TEMP_THERMOCOUPLE",
            f"dmm.refjunction = dmm.REF_JUNCTION_{cfg.ref_junction}",
            f"dmm.units = dmm.UNITS_{cfg.units}",
            f"dmm.nplc = {cfg.nplc:g}",
            f"dmm.opendetector = {'dmm.ON' if cfg.open_detect else 'dmm.OFF'}",
        ]
        if cfg.ref_junction == "SIMULATED":
            cmds.append(f"dmm.simreftemperature = {cfg.sim_ref_temp:g}")
        # Eine DMM-Konfiguration pro Thermoelement-Typ, den Kanälen zugewiesen.
        # scan.create() verwendet dann die jeweils zugewiesene Konfiguration.
        for tc_type, chans in cfg.type_groups().items():
            name = f"{CONFIG_NAME}_{tc_type}"
            cmds += [
                f"dmm.thermocouple = dmm.THERMOCOUPLE_{tc_type}",
                f'dmm.configure.set("{name}")',
                f'dmm.setconfig("{format_channel_list(chans)}", "{name}")',
            ]
        cmds += [
            f"{BUFFER_NAME} = dmm.makebuffer({max(100, 2 * len(cfg.channels))})",
            f'scan.create("{chan_list}")',
            "format.data = format.ASCII",
            "format.asciiprecision = 7",
        ]
        for cmd in cmds:
            self.write(cmd)
        self.check_errors()
        self._config = cfg

    def play_note(self, duration: float, freq: float) -> None:
        """Ein Ton (freq > 0) oder eine Pause; kehrt erst nach Ende zurück.

        beeper.enable wird nur für den Ton eingeschaltet und danach wiederhergestellt,
        damit eine am Frontpanel gewählte Einstellung (z. B. Tastenklicks aus) erhalten bleibt.
        """
        if freq > 0:
            cmd = (f"local e = beeper.enable beeper.enable = beeper.ON "
                   f"beeper.beep({max(0.1, duration):.3f}, {freq:.1f}) beeper.enable = e")
        else:
            cmd = f"delay({max(0.0, duration):.3f})"
        old = self._inst.timeout
        self._inst.timeout = int(duration * 1000) + 5000
        try:
            self.query(cmd + " print(1)")
        finally:
            self._inst.timeout = old

    def scan(self) -> ScanResult:
        if self._config is None:
            raise InstrumentError("Nicht konfiguriert")
        self.write(
            f"{BUFFER_NAME}.clear() scan.execute({BUFFER_NAME}) "
            f"printbuffer(1, {BUFFER_NAME}.n, {BUFFER_NAME}.channels, "
            f"{BUFFER_NAME}.readings, {BUFFER_NAME}.units)"
        )
        raw = self._inst.read()
        ts = time.time()
        if not raw.strip():
            self.check_errors()
            raise InstrumentError("Leere Antwort vom Gerät (Scan fehlgeschlagen?)")
        return ScanResult(timestamp=ts, readings=parse_printbuffer(raw))


class SimulatedInstrument:
    """Ersatzgerät zum Testen der Oberfläche ohne Hardware."""

    def __init__(self, *_args, **_kwargs):
        self.resource_name = SIMULATION_RESOURCE
        self.idn = "SIMULATION,MODEL 3706A,0000,sim"
        self.cards: dict[int, str] = {1: "3724,Dual 1x30 FET Multiplexer (simuliert)"}
        self._config: MeasurementConfig | None = None
        self._t0 = time.time()
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def configure(self, cfg: MeasurementConfig) -> None:
        self._config = cfg

    def play_note(self, duration: float, freq: float) -> None:
        """Simulator: Ton über den PC-Lautsprecher (Windows), sonst nur warten."""
        if freq > 0:
            try:
                import winsound
                winsound.Beep(int(min(max(freq, 37), 32767)), int(max(0.1, duration) * 1000))
                return
            except Exception:
                pass
        time.sleep(max(0.0, duration))

    def scan(self) -> ScanResult:
        cfg = self._config or MeasurementConfig()
        time.sleep(0.025 * len(cfg.channels) * max(cfg.nplc, 0.1))
        t = time.time() - self._t0
        unit = TEMP_UNITS[cfg.units]
        readings = []
        for i, ch in enumerate(cfg.channels):
            if ch % 1000 > 9:  # nicht belegte Kanäle sind "offen"
                readings.append(Reading(ch, float("nan"), unit, True))
                continue
            c = 24.0 + 1.5 * i + 3 * math.sin(t / (30 + 7 * i)) + random.gauss(0, 0.03)
            if cfg.units == "KELVIN":
                c += 273.15
            elif cfg.units == "FAHRENHEIT":
                c = c * 9 / 5 + 32
            readings.append(Reading(ch, c, unit, False))
        return ScanResult(timestamp=time.time(), readings=readings)


def list_visa_resources() -> list[str]:
    """Alle VISA-Ressourcen, Keithley-3706A-USB-Geräte zuerst."""
    try:
        import pyvisa

        rm = pyvisa.ResourceManager()
        res = list(rm.list_resources())
        rm.close()
    except Exception:
        res = []
    res.sort(key=lambda r: (0 if "0x3706" in r.lower() else 1 if r.startswith("USB") else 2, r))
    return res


def make_instrument(resource: str):
    if resource == SIMULATION_RESOURCE:
        return SimulatedInstrument()
    return Keithley3706A(resource)
