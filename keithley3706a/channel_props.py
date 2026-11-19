"""Kanalbezogene Einstellungen: Thermoelement-Typ, Kalibrier-Offset, Alarmgrenzen."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .instrument import THERMOCOUPLE_TYPES, Reading, ScanResult


@dataclass
class ChannelProps:
    tc_type: str | None = None       # None = globaler Typ aus den Einstellungen
    offset: float = 0.0              # wird auf den Messwert addiert (Anzeigeeinheit)
    alarm_low: float | None = None   # None = kein Alarm
    alarm_high: float | None = None

    def is_default(self) -> bool:
        return self == ChannelProps()

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "ChannelProps":
        def opt_float(v):
            try:
                return None if v is None or v == "" else float(v)
            except (TypeError, ValueError):
                return None

        t = data.get("tc_type")
        return cls(
            tc_type=t if t in THERMOCOUPLE_TYPES else None,
            offset=opt_float(data.get("offset")) or 0.0,
            alarm_low=opt_float(data.get("alarm_low")),
            alarm_high=opt_float(data.get("alarm_high")),
        )


def apply_offsets(result: ScanResult, props: dict[int, ChannelProps]) -> ScanResult:
    """Kalibrier-Offsets anwenden (offene Kanäle bleiben NaN)."""
    out = []
    for r in result.readings:
        p = props.get(r.channel)
        if p is not None and p.offset and not r.is_open:
            r = Reading(r.channel, r.value + p.offset, r.unit, r.is_open)
        out.append(r)
    return ScanResult(result.timestamp, out)


def validate_props(props: dict[int, ChannelProps]) -> list[str]:
    """Plausibilitätsprüfung; liefert Fehlermeldungen."""
    errors = []
    for ch, p in sorted(props.items()):
        if p.alarm_low is not None and p.alarm_high is not None and p.alarm_low >= p.alarm_high:
            errors.append(f"Kanal {ch}: untere Alarmgrenze muss kleiner als die obere sein.")
        if abs(p.offset) > 50:
            errors.append(f"Kanal {ch}: Offset {p.offset:g} ist unplausibel groß (max. ±50).")
    return errors


def props_to_json(props: dict[int, ChannelProps]) -> dict[str, dict]:
    return {str(ch): p.to_json() for ch, p in props.items() if not p.is_default()}


def props_from_json(data: dict) -> dict[int, ChannelProps]:
    out = {}
    for k, v in (data or {}).items():
        try:
            out[int(k)] = ChannelProps.from_json(v or {})
        except (TypeError, ValueError):
            continue
    return out
