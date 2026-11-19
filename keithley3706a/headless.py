"""Messung ohne Oberfläche anhand eines Profils (JSON).

    py -3.11 -m keithley3706a.headless profil.json [--dauer 3600] [--ordner D:\\Daten]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from . import APP_NAME, __version__
from .channel_props import apply_offsets
from .instrument import (REF_JUNCTIONS, SIMULATION_RESOURCE, TEMP_UNITS, ScanResult,
                         make_instrument)
from .profile import parse_profile
from .recorder import CsvRecorder
from .single_instance import InstanceLock
from .timesync import DEFAULT_SERVERS, Clock, best_of


def run(profile: dict, duration_s: float | None = None, folder: str | None = None,
        resource: str | None = None, out=sys.stdout) -> Path:
    p = parse_profile(profile)
    resource = resource or p["resource"]
    if not resource:
        raise ValueError("Keine VISA-Ressource im Profil – mit --ressource angeben.")
    lock = None
    if resource != SIMULATION_RESOURCE:
        lock = InstanceLock()
        if not lock.acquired:
            raise RuntimeError("Der Monitor läuft bereits (GUI oder andere Headless-Messung). "
                               "Der Keithley kann nur von einem Programm gleichzeitig "
                               "ausgelesen werden.")
    try:
        return _run(p, profile, resource, duration_s, folder, out)
    finally:
        if lock is not None:
            lock.release()


def _run(p: dict, profile: dict, resource: str, duration_s: float | None,
         folder: str | None, out) -> Path:
    cfg = p["config"]
    csv_opts = p["csv"]
    folder = folder or csv_opts.get("folder") or "."
    clock = Clock()
    time_basis = "PC-Uhr, nicht mit Zeitserver abgeglichen"
    servers = profile.get("time", {}).get("ntp_servers", DEFAULT_SERVERS)
    if profile.get("time", {}).get("ntp_enabled", True):
        try:
            r = best_of(servers)
            clock.update(r)
            clock.correct = bool(profile.get("time", {}).get("ntp_correct", True))
            time_basis = (f"PC-Uhr {'korrigiert' if clock.correct else 'unkorrigiert'} per SNTP "
                          f"({r.server}, Abweichung {r.offset * 1000:+.1f} ms)")
            print(f"Zeitabgleich {r.server}: {r.offset * 1000:+.1f} ms", file=out)
        except OSError as exc:
            print(f"Zeitabgleich fehlgeschlagen: {exc}", file=out)
    inst = make_instrument(resource)
    inst.connect()
    try:
        inst.configure(cfg)
        meta = {
            "Programm": f"{APP_NAME} v{__version__} (headless)",
            "Gerät": inst.idn,
            "Thermoelement-Typ": cfg.tc_type,
            "Vergleichsstelle": REF_JUNCTIONS[cfg.ref_junction],
            "Integrationszeit [PLC]": f"{cfg.nplc:g}",
            "Messintervall [s]": f"{p['interval_s']:g}",
            "Notiz": p["note"],
            "Zeitbasis": time_basis,
        }
        rec = CsvRecorder(folder, cfg.channels, p["labels"], TEMP_UNITS[cfg.units], meta,
                          csv_opts.get("format", "excel_de"),
                          prefix=csv_opts.get("prefix", "Temperaturen"),
                          rotate_daily=bool(csv_opts.get("rotate_daily", True)),
                          start_time=datetime.fromtimestamp(clock.now()))
        rec.path.with_suffix(".json").write_text(
            json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Aufnahme: {rec.path}", file=out)
        t_end = time.monotonic() + duration_s if duration_s else math.inf
        try:
            while time.monotonic() < t_end:
                t0 = time.monotonic()
                result = apply_offsets(inst.scan(), p["props"])
                result = ScanResult(clock.correct_timestamp(result.timestamp), result.readings)
                rec.write(result)
                vals = "  ".join(f"{r.channel}:{'offen' if r.is_open else f'{r.value:.2f}'}"
                                 for r in result.readings)
                print(f"{datetime.now():%H:%M:%S}  {vals}", file=out, flush=True)
                time.sleep(max(0.0, p["interval_s"] - (time.monotonic() - t0)))
        except KeyboardInterrupt:
            print("Abbruch durch Benutzer.", file=out)
        finally:
            rec.close()
        print(f"{rec.rows} Zeilen gespeichert.", file=out)
        return rec.path
    finally:
        inst.close()


def main(argv: list[str] | None = None, profile: dict | None = None) -> int:
    ap = argparse.ArgumentParser(description="Keithley 3706A Thermoelement-Messung ohne GUI")
    if profile is None:
        ap.add_argument("profil", help="Profildatei (.json), in der GUI unter Datei → "
                                       "Profil speichern erzeugt")
    ap.add_argument("--dauer", type=float, default=None, help="Messdauer in Sekunden")
    ap.add_argument("--ordner", default=None, help="Zielordner für die CSV-Datei")
    ap.add_argument("--ressource", default=None, help="VISA-Ressource (überschreibt Profil)")
    args = ap.parse_args(argv)
    from . import crashlog
    for note in crashlog.install(tag="headless"):
        print("Hinweis:", note, file=sys.stderr)
    if profile is None:
        profile = json.loads(Path(args.profil).read_text(encoding="utf-8"))
    try:
        run(profile, args.dauer, args.ordner, args.ressource)
    except Exception as exc:
        path = crashlog.write_report("Fehler im Headless-Modus",
                                     "".join(__import__("traceback").format_exc()))
        print(f"Fehler: {exc}\nBericht: {path}", file=sys.stderr)
        return 1
    finally:
        crashlog.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
