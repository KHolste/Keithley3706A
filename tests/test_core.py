import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from keithley3706a.history import History
from keithley3706a.instrument import (
    Reading, ScanResult, format_channel_list, parse_channel_list, parse_printbuffer,
)
from keithley3706a.recorder import CsvRecorder


def test_parse_printbuffer_real_format():
    raw = ("1001+, 2.462775e+01, Celsius, 1002+, 2.482311e+01, Celsius, "
           "1010+, 9.900000e+37, Celsius")
    r = parse_printbuffer(raw)
    assert [x.channel for x in r] == [1001, 1002, 1010]
    assert r[0].value == pytest.approx(24.62775)
    assert r[0].unit == "°C" and not r[0].is_open
    assert r[2].is_open and math.isnan(r[2].value)


def test_channel_list_roundtrip():
    chans = parse_channel_list("1001:1009, 1015;1016")
    assert chans == list(range(1001, 1010)) + [1015, 1016]
    assert format_channel_list(chans) == "1001:1009,1015:1016"
    with pytest.raises(ValueError):
        parse_channel_list("1000")


def test_recorder_excel_de(tmp_path):
    rec = CsvRecorder(tmp_path, [1001, 1002], {1001: "Ofen"}, "°C", {"Typ": "K"}, "excel_de")
    rec.write(ScanResult(rec.start_time.timestamp() + 1.5,
                         [Reading(1001, 24.5, "°C", False),
                          Reading(1002, float("nan"), "°C", True)]))
    rec.close()
    lines = rec.path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "# Typ;K"
    assert lines[1].startswith("# Aufnahmestart;")
    header = [l for l in lines if l.startswith("Zeitstempel")][0]
    assert header == ("Zeitstempel (ISO 8601, Ortszeit mit UTC-Offset);MJD (UTC);"
                      "Zeit seit Start [s];1001 Ofen [°C];1002 [°C];Ereignis")
    row = lines[lines.index(header) + 1].split(";")
    assert row[0][10] == "T" and row[0][-6] in "+-" and row[0][-3] == ":"   # ISO mit Offset
    assert row[1].startswith("6") and len(row[1].split(",")[1]) == 8        # MJD, 8 Stellen
    assert row[2:] == ["1,500", "24,5000", "", ""]


def test_history_ring():
    h = History(max_points=2048)
    for i in range(5000):
        h.append(ScanResult(float(i), [Reading(1001, float(i), "°C", False)]))
    t = h.times()
    assert t.size <= 2048 and t[-1] == 4999
    assert (h.values(1001) == t).all()
