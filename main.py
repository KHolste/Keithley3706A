"""Startpunkt: Keithley 3706A Thermoelement-Monitor."""

import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from keithley3706a import APP_NAME, ORG_NAME
from keithley3706a.icons import app_icon
from keithley3706a.main_window import MainWindow


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.stderr.write(text) if sys.stderr else None
    if QApplication.instance() is not None:
        QMessageBox.critical(None, "Unerwarteter Fehler", text[-3000:])


def main() -> int:
    sys.excepthook = _excepthook
    if sys.platform == "win32":
        try:  # eigenes Taskleisten-Icon statt Python-Icon
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JLU.Keithley3706A.TC")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setWindowIcon(app_icon())
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
