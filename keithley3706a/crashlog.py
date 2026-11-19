"""Protokollierung und Absturzberichte.

- Laufendes Protokoll: <Log-Ordner>/app.log (rotierend, 5 × 2 MB)
- Python-Fehler (GUI und Hintergrund-Threads): crash_<Datum>.txt mit Traceback,
  Versionen, Systeminfo und den letzten Protokollzeilen
- Harte Abstürze (z. B. in Qt/VISA-Treibern): faulthandler schreibt in fatal.log;
  beim nächsten Start wird daraus ein Absturzbericht
- Unsauberes Ende (Prozess beendet, Stromausfall): Marker-Datei wird beim nächsten
  Start erkannt
"""

from __future__ import annotations

import collections
import faulthandler
import logging
import logging.handlers
import os
import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from . import APP_NAME, __version__

log = logging.getLogger("keithley3706a")
_recent: collections.deque[str] = collections.deque(maxlen=200)
_fatal_fh = None
_context_provider = None   # liefert zusätzlichen Text (z. B. Gerät, Aufnahme)
_report_callback = None    # GUI: zeigt Dialog mit Pfad an


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "Keithley3706A-Monitor" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


class _RecentHandler(logging.Handler):
    def emit(self, record):
        try:
            _recent.append(self.format(record))
        except Exception:
            pass


def _system_info() -> str:
    lines = [f"Programm: {APP_NAME} v{__version__}",
             f"Python: {sys.version.split()[0]} ({sys.executable})",
             f"System: {platform.platform()}",
             f"Rechner: {os.environ.get('COMPUTERNAME', '')}  Benutzer: {os.environ.get('USERNAME', '')}"]
    for mod in ("PySide6", "pyqtgraph", "numpy", "pyvisa"):
        try:
            m = __import__(mod)
            lines.append(f"{mod}: {getattr(m, '__version__', '?')}")
        except Exception:
            lines.append(f"{mod}: nicht ladbar")
    return "\n".join(lines)


def write_report(title: str, details: str) -> Path:
    """Schreibt einen Absturzbericht und gibt den Pfad zurück."""
    stem = f"crash_{_tag}_{datetime.now():%Y-%m-%d_%H-%M-%S}"
    path = log_dir() / f"{stem}.txt"
    i = 2
    while path.exists():          # mehrere Fehler in derselben Sekunde nicht überschreiben
        path = log_dir() / f"{stem}_{i}.txt"
        i += 1
    ctx = ""
    if _context_provider is not None:
        try:
            ctx = _context_provider()
        except Exception as exc:
            ctx = f"(Kontext nicht verfügbar: {exc})"
    text = (f"{title}\nZeit: {datetime.now().isoformat(timespec='seconds')}\n\n"
            f"=== Fehler ===\n{details}\n\n=== Zustand ===\n{ctx}\n\n"
            f"=== System ===\n{_system_info()}\n\n"
            f"=== Letzte Protokollzeilen ===\n" + "\n".join(_recent) + "\n")
    path.write_text(text, encoding="utf-8")
    return path


def _handle(title: str, exc_type, exc, tb) -> None:
    details = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("%s\n%s", title, details)
    try:
        path = write_report(title, details)
    except Exception:
        path = None
    if _report_callback is not None:
        try:
            _report_callback(title, details, path)
        except Exception:
            pass


def _excepthook(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    _handle("Unerwarteter Fehler", exc_type, exc, tb)


def _thread_excepthook(args):
    _handle(f"Fehler im Hintergrund-Thread '{args.thread.name if args.thread else '?'}'",
            args.exc_type, args.exc_value, args.exc_traceback)


_tag = "gui"


def install(context_provider=None, report_callback=None, tag: str = "gui") -> list[str]:
    """Richtet Protokoll und Absturzbehandlung ein.

    tag trennt GUI und Headless (eigene Protokoll-/Markerdateien).
    Gibt Hinweise auf Probleme der letzten Sitzung zurück (für einen Dialog beim Start).
    """
    global _fatal_fh, _context_provider, _report_callback, _tag
    _context_provider = context_provider
    _report_callback = report_callback
    _tag = tag
    d = log_dir()
    notes: list[str] = []

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logname = "app.log" if tag == "gui" else f"{tag}.log"
    fh = logging.handlers.RotatingFileHandler(d / logname, maxBytes=2_000_000, backupCount=5,
                                              encoding="utf-8", delay=True)
    fh.setFormatter(fmt)
    rh = _RecentHandler()
    rh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(rh)

    # Harter Absturz der letzten Sitzung?
    fatal = d / f"fatal_{tag}.log"
    if fatal.exists() and fatal.stat().st_size > 0:
        content = fatal.read_text(encoding="utf-8", errors="replace")
        path = write_report("Harter Absturz in der letzten Sitzung (faulthandler)", content)
        notes.append(f"Das Programm ist beim letzten Mal abgestürzt. Bericht:\n{path}")
    # Unsauberes Ende (Prozess abgeschossen, Stromausfall, Absturz)?
    marker = d / f"running_{tag}.marker"
    if marker.exists() and not notes:
        started = marker.read_text(encoding="utf-8", errors="replace").strip()
        notes.append(f"Die letzte Sitzung (gestartet {started}) wurde nicht ordnungsgemäß "
                     f"beendet (Absturz, Prozess beendet oder Stromausfall).\n"
                     f"Protokoll: {d / logname}")
    marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")

    _fatal_fh = open(fatal, "w", encoding="utf-8")
    faulthandler.enable(file=_fatal_fh, all_threads=True)
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    log.info("Start %s v%s (Python %s)", APP_NAME, __version__, sys.version.split()[0])
    for n in notes:
        log.warning(n.replace("\n", " "))
    return notes


def install_qt_message_handler() -> None:
    """Qt-Warnungen ins Protokoll (sonst bei pythonw unsichtbar)."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}
    qtlog = logging.getLogger("qt")

    def handler(mode, _ctx, message):
        qtlog.log(levels.get(mode, logging.INFO), message)

    qInstallMessageHandler(handler)


def shutdown() -> None:
    """Sauberes Ende: Marker entfernen, faulthandler-Datei leeren."""
    global _fatal_fh
    log.info("Programm ordnungsgemäß beendet")
    try:
        faulthandler.disable()
        if _fatal_fh is not None:
            _fatal_fh.close()
            _fatal_fh = None
        (log_dir() / f"fatal_{_tag}.log").write_text("", encoding="utf-8")
        (log_dir() / f"running_{_tag}.marker").unlink(missing_ok=True)
    except OSError:
        pass
    logging.shutdown()
