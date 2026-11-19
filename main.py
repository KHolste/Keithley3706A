"""Startpunkt: Keithley 3706A Thermoelement-Monitor."""

import sys

from keithley3706a import crashlog

_window = None


def _context() -> str:
    """Zustand für Absturzberichte (Gerät, Verbindung, Aufnahme)."""
    w = _window
    if w is None:
        return "Hauptfenster noch nicht erstellt"
    rec = w.recorder
    return "\n".join([
        f"Gerät: {w.idn or '-'}",
        f"Verbunden: {w.thread is not None}",
        f"Kanäle: {w.cfg.channels}",
        f"Aufnahme: {rec.path if rec else 'keine'}"
        + (f" ({rec.rows} Zeilen, bis zum Fehler vollständig gespeichert)" if rec else ""),
        f"Punkte im Verlauf: {w.history.size}",
    ])


def _report(title: str, details: str, path) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox
    if QApplication.instance() is None:
        sys.stderr.write(details) if sys.stderr else None
        return
    box = QMessageBox(QMessageBox.Critical, title,
                      "Es ist ein unerwarteter Fehler aufgetreten. Das Programm läuft weiter; "
                      "eine laufende Aufnahme ist bis hierhin vollständig gespeichert.\n\n"
                      + (f"Absturzbericht:\n{path}" if path else "Bericht konnte nicht "
                                                                 "geschrieben werden."))
    box.setDetailedText(details[-6000:])
    if path:
        open_btn = box.addButton("Bericht öffnen", QMessageBox.ActionRole)
    box.addButton(QMessageBox.Ok)
    box.exec()
    if path and box.clickedButton() is open_btn:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def main() -> int:
    global _window
    notes = crashlog.install(context_provider=_context, report_callback=_report)

    from PySide6.QtWidgets import QApplication, QMessageBox

    from keithley3706a import APP_NAME, ORG_NAME
    from keithley3706a.icons import app_icon
    from keithley3706a.main_window import MainWindow

    if sys.platform == "win32":
        try:  # eigenes Taskleisten-Icon statt Python-Icon
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JLU.Keithley3706A.TC")
        except Exception:
            pass
    app = QApplication(sys.argv)
    crashlog.install_qt_message_handler()
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setWindowIcon(app_icon())
    _window = MainWindow()
    _window.show()
    for note in notes:
        _window._log(note.split("\n")[0], "warn")
        QMessageBox.warning(_window, "Letzte Sitzung", note)
    code = app.exec()
    crashlog.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
