import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from keithley3706a.alarms import AlarmMonitor, AlarmState
from keithley3706a.analysis import StatsTracker, rate_per_minute
from keithley3706a.channel_props import ChannelProps, apply_offsets, validate_props
from keithley3706a.csv_loader import load_csv
from keithley3706a.history import History
from keithley3706a.instrument import MeasurementConfig, Reading, ScanResult
from keithley3706a.profile import build_profile, export_script, parse_profile
from keithley3706a.recorder import CsvRecorder
from keithley3706a.scheduler import ScheduleRunner, Trigger
from keithley3706a.secret import decrypt, encrypt


def scan(ts, values):
    return ScanResult(ts, [Reading(ch, v, "°C", math.isnan(v)) for ch, v in values.items()])


def test_alarm_hysteresis():
    mon = AlarmMonitor(hysteresis=1.0)
    props = {1001: ChannelProps(alarm_high=50.0, alarm_low=10.0)}
    assert mon.evaluate(scan(0, {1001: 49.0}), props) == []
    ev = mon.evaluate(scan(1, {1001: 50.5}), props)
    assert ev[0].state == AlarmState.HIGH
    assert mon.evaluate(scan(2, {1001: 49.5}), props) == []        # innerhalb Hysterese
    ev = mon.evaluate(scan(3, {1001: 48.9}), props)
    assert ev[0].state == AlarmState.OK
    ev = mon.evaluate(scan(4, {1001: 9.0}), props)
    assert ev[0].state == AlarmState.LOW


def test_open_alarm_optional():
    props = {}
    assert AlarmMonitor(open_is_alarm=False).evaluate(scan(0, {1001: math.nan}), props) == []
    ev = AlarmMonitor(open_is_alarm=True).evaluate(scan(0, {1001: math.nan}), props)
    assert ev[0].state == AlarmState.OPEN


def test_rate_and_stats():
    h, st = History(), StatsTracker()
    for i in range(61):
        r = scan(1000.0 + i, {1001: 20.0 + 0.5 * i / 60})   # 0,5 K/min
        h.append(r)
        st.add(r)
    assert rate_per_minute(h, 1001, 60) == pytest.approx(0.5, abs=1e-6)
    s = st.get(1001)
    assert s.min == pytest.approx(20.0) and s.max == pytest.approx(20.5)
    assert s.mean == pytest.approx(20.25)


def test_offsets_and_validation():
    r = apply_offsets(scan(0, {1001: 20.0, 1002: math.nan}), {1001: ChannelProps(offset=0.3),
                                                              1002: ChannelProps(offset=1)})
    assert r.readings[0].value == pytest.approx(20.3)
    assert math.isnan(r.readings[1].value)
    assert validate_props({1001: ChannelProps(alarm_low=5, alarm_high=5)})


def test_type_groups():
    cfg = MeasurementConfig(channels=[1001, 1002, 1003], tc_type="K", channel_types={1002: "J"})
    assert cfg.type_groups() == {"K": [1001, 1003], "J": [1002]}


def test_scheduler_duration_and_threshold():
    now = datetime(2026, 1, 1, 12, 0, 0)
    run = ScheduleRunner(Trigger("threshold", channel=1001, compare=">", value=30),
                         Trigger("duration", duration_s=60))
    assert run.check(now, {1001: 25}) is None
    assert run.check(now, {1001: 31}) == "start"
    assert run.check(now + timedelta(seconds=30)) is None
    assert run.check(now + timedelta(seconds=61)) == "stop"


def test_recorder_rotation_events_and_loader(tmp_path):
    start = datetime(2026, 1, 1, 23, 59, 58)
    rec = CsvRecorder(tmp_path, [1001, 1002], {1001: "Ofen"}, "°C", {"Typ": "K"},
                      "excel_de", rotate_daily=True, start_time=start)
    rec.write(scan(start.timestamp() + 1, {1001: 24.5, 1002: math.nan}), "Marker: A")
    rec.write(scan(start.timestamp() + 3, {1001: 1234.5, 1002: 20.0}))
    rec.close()
    assert len(rec.files) == 2 and rec.rows == 2
    data = load_csv(rec.files[0])
    assert data.series[0].name == "1001 Ofen" and data.series[0].unit == "°C"
    assert data.series[0].values[0] == pytest.approx(24.5)
    assert data.events[0][1] == "Marker: A"
    data2 = load_csv(rec.files[1])
    assert data2.series[0].values[0] == pytest.approx(1234.5)


def test_profile_roundtrip_and_script(tmp_path):
    cfg = MeasurementConfig(channels=[1001, 1002], channel_types={1002: "T"})
    prof = build_profile(resource="SIMULATION", interval_s=0.5, cfg=cfg,
                         props={1002: ChannelProps(tc_type="T", offset=0.2)},
                         labels={1001: "A"}, csv={"folder": str(tmp_path)})
    p = parse_profile(json.loads(json.dumps(prof)))
    assert p["config"].channels == [1001, 1002]
    assert p["config"].type_for(1002) == "T"
    assert p["props"][1002].offset == pytest.approx(0.2)
    script = tmp_path / "messung.py"
    export_script(script, prof, Path(__file__).resolve().parents[1])
    compile(script.read_text(encoding="utf-8"), str(script), "exec")
    with pytest.raises(ValueError):
        parse_profile({"measurement": {"channels": "1001", "nplc": 99}})


def test_headless_simulation(tmp_path):
    from keithley3706a.headless import run
    import io
    cfg = MeasurementConfig(channels=[1001, 1002])
    prof = build_profile(resource="SIMULATION", interval_s=0.1, cfg=cfg,
                         props={1001: ChannelProps(offset=100)}, labels={})
    path = run(prof, duration_s=0.6, folder=str(tmp_path), out=io.StringIO())
    data = load_csv(path)
    assert data.times.size >= 2
    assert data.series[0].values[0] > 100          # Offset angewendet
    assert path.with_suffix(".json").exists()


def test_sound_sequences():
    from keithley3706a.sounds import MIN_BEEP_S, alarm_pattern, korobeiniki, total_duration
    mel = korobeiniki()
    assert all(d >= MIN_BEEP_S for d, f in mel if f > 0)      # Gerätegrenze 0,1 s
    assert 10 < total_duration(mel) < 20
    assert alarm_pattern("high", 2000)[0][1] < alarm_pattern("high", 2000)[1][1]   # steigend
    assert alarm_pattern("low", 2000)[0][1] > alarm_pattern("low", 2000)[1][1]     # fallend


def test_mjd_and_iso():
    from datetime import timezone
    from keithley3706a.timesync import iso_local, mjd
    assert mjd(0) == pytest.approx(40587.0)
    j2000 = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    assert mjd(j2000) == pytest.approx(51544.5)
    s = iso_local(j2000)
    assert datetime.fromisoformat(s).timestamp() == pytest.approx(j2000)
    assert s[-6] in "+-"


def test_clock_correction():
    import time as _t
    from keithley3706a.timesync import Clock, SntpResult
    c = Clock()
    c.update(SntpResult("x", offset=2.5, delay=0.001, stratum=2, when=_t.time()))
    assert c.now() == pytest.approx(_t.time(), abs=0.05)        # Korrektur aus
    c.correct = True
    assert c.now() - _t.time() == pytest.approx(2.5, abs=0.05)
    pc = _t.time() - 1.0
    assert c.correct_timestamp(pc) - pc == pytest.approx(2.5, abs=0.05)


def test_sntp_packet_roundtrip():
    from keithley3706a.timesync import _from_ntp, _to_ntp
    t = 1_763_560_000.123456
    assert _from_ntp(*_to_ntp(t)) == pytest.approx(t, abs=1e-6)


def test_secret_roundtrip():
    assert decrypt(encrypt("geheim äö")) == "geheim äö"
    assert encrypt("") == "" and decrypt("") == ""


def test_instance_lock():
    from keithley3706a.single_instance import InstanceLock
    name = "Keithley3706A-Test-Lock"
    first = InstanceLock(name)
    assert first.acquired
    second = InstanceLock(name)
    assert not second.acquired
    first.release()
    third = InstanceLock(name)
    assert third.acquired
    third.release()
