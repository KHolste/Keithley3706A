"""Grenzwertüberwachung mit Hysterese."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .channel_props import ChannelProps
from .instrument import ScanResult


class AlarmState(Enum):
    OK = "ok"
    HIGH = "high"
    LOW = "low"
    OPEN = "open"


@dataclass
class AlarmEvent:
    timestamp: float
    channel: int
    state: AlarmState       # neuer Zustand
    previous: AlarmState
    value: float
    limit: float | None

    @property
    def is_alarm(self) -> bool:
        return self.state != AlarmState.OK


class AlarmMonitor:
    def __init__(self, hysteresis: float = 0.5, open_is_alarm: bool = False):
        self.hysteresis = hysteresis
        self.open_is_alarm = open_is_alarm
        self.states: dict[int, AlarmState] = {}

    def reset(self) -> None:
        self.states = {}

    def state(self, channel: int) -> AlarmState:
        return self.states.get(channel, AlarmState.OK)

    def evaluate(self, result: ScanResult, props: dict[int, ChannelProps]) -> list[AlarmEvent]:
        events = []
        h = self.hysteresis
        for r in result.readings:
            p = props.get(r.channel, ChannelProps())
            prev = self.state(r.channel)
            new = prev
            limit = None
            if r.is_open:
                new = AlarmState.OPEN if self.open_is_alarm else AlarmState.OK
            else:
                v = r.value
                high, low = p.alarm_high, p.alarm_low
                if high is not None and (v > high or (prev == AlarmState.HIGH and v > high - h)):
                    new, limit = AlarmState.HIGH, high
                elif low is not None and (v < low or (prev == AlarmState.LOW and v < low + h)):
                    new, limit = AlarmState.LOW, low
                else:
                    new = AlarmState.OK
                    limit = high if prev == AlarmState.HIGH else low if prev == AlarmState.LOW else None
            if new != prev:
                self.states[r.channel] = new
                events.append(AlarmEvent(result.timestamp, r.channel, new, prev, r.value, limit))
        return events

    def active(self) -> dict[int, AlarmState]:
        return {ch: s for ch, s in self.states.items() if s != AlarmState.OK}
