"""Tonfolgen für den Signalgeber des 3706A (beeper.beep) bzw. den Simulator.

Eine Tonfolge ist eine Liste von (Dauer [s], Frequenz [Hz]); Frequenz 0 = Pause.
Das Gerät akzeptiert beeper.beep() erst ab 0,1 s und blockiert für die Tondauer.
"""

from __future__ import annotations

Note = tuple[float, float]

MIN_BEEP_S = 0.1

_NOTES = {"A4": 440.00, "B4": 493.88, "C5": 523.25, "D5": 587.33, "E5": 659.25,
          "F5": 698.46, "G5": 783.99, "A5": 880.00, "R": 0.0}


def alarm_pattern(kind: str, freq: float) -> list[Note]:
    """Unterscheidbare Kurzsignale: oben steigend, unten fallend, Fühlerbruch dreifach."""
    lo = freq * 0.75
    if kind == "high":
        return [(0.15, lo), (0.15, freq)]
    if kind == "low":
        return [(0.15, freq), (0.15, lo)]
    if kind == "open":
        return [(0.1, freq), (0.08, 0), (0.1, freq), (0.08, 0), (0.1, freq)]
    return [(0.2, freq)]


def test_pattern(freq: float) -> list[Note]:
    return [(0.15, freq * 0.75), (0.15, freq), (0.1, 0), (0.3, freq)]


# Korobeiniki (russisches Volkslied, 19. Jh., gemeinfrei) – bekannt als Tetris-Melodie.
# Notenwerte in Vierteln.
_KOROBEINIKI = [
    # Teil A
    ("E5", 1), ("B4", .5), ("C5", .5), ("D5", 1), ("C5", .5), ("B4", .5),
    ("A4", 1), ("A4", .5), ("C5", .5), ("E5", 1), ("D5", .5), ("C5", .5),
    ("B4", 1.5), ("C5", .5), ("D5", 1), ("E5", 1),
    ("C5", 1), ("A4", 1), ("A4", 1), ("R", 1),
    # Teil B
    ("R", .5), ("D5", 1), ("F5", .5), ("A5", 1), ("G5", .5), ("F5", .5),
    ("E5", 1.5), ("C5", .5), ("E5", 1), ("D5", .5), ("C5", .5),
    ("B4", 1), ("B4", .5), ("C5", .5), ("D5", 1), ("E5", 1),
    ("C5", 1), ("A4", 1), ("A4", 1), ("R", 1),
]


def korobeiniki(bpm: float = 144, transpose: float = 2.0, legato: float = 0.88) -> list[Note]:
    """Melodie als Tonfolge. transpose=2 -> eine Oktave höher (Piezo klingt dort lauter)."""
    beat = 60.0 / bpm
    out: list[Note] = []
    for name, beats in _KOROBEINIKI:
        d = beats * beat
        f = _NOTES[name] * transpose
        if f <= 0:
            out.append((d, 0))
            continue
        tone = max(MIN_BEEP_S, d * legato)
        out.append((tone, f))
        if d - tone > 0.005:
            out.append((d - tone, 0))   # kurze Lücke trennt gleiche Töne
    return out


def total_duration(notes: list[Note]) -> float:
    return sum(d for d, _ in notes)
