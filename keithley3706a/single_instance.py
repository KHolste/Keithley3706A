"""Sperre gegen mehrere gleichzeitige Programmexemplare.

Der Keithley verträgt nur einen VISA-Client gleichzeitig: Zwei Exemplare (z. B. start.bat und
die exe) würden sich gegenseitig die Antworten wegnehmen. Deshalb hält das laufende Programm eine
systemweite Sperre (Windows-Mutex; wird bei jedem Prozessende – auch Absturz/Abschuss – vom
Betriebssystem freigegeben). Ein zweiter GUI-Start holt nur das vorhandene Fenster nach vorn.
"""

from __future__ import annotations

import getpass
import sys

LOCK_NAME = "Keithley3706A-Monitor"
_ERROR_ALREADY_EXISTS = 183
_ERROR_ACCESS_DENIED = 5


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"{LOCK_NAME}-{''.join(c for c in user if c.isalnum())}"


class InstanceLock:
    """Systemweite Sperre. ``acquired`` ist False, wenn schon ein Exemplar läuft."""

    def __init__(self, name: str = LOCK_NAME):
        self.acquired = False
        self._handle = None
        self._lockfile = None
        if sys.platform == "win32":
            self._acquire_windows(name)
        else:
            self._acquire_lockfile(name)

    def _acquire_windows(self, name: str) -> None:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        # Global\ gilt auch über Benutzersitzungen hinweg (das Gerät gibt es nur einmal);
        # falls das nicht erlaubt ist, wenigstens innerhalb der Sitzung sperren.
        for prefix in ("Global\\", "Local\\"):
            handle = k32.CreateMutexW(None, False, prefix + name)
            err = ctypes.get_last_error()
            if handle:
                if err == _ERROR_ALREADY_EXISTS:
                    k32.CloseHandle(handle)
                    return
                self._handle = handle
                self.acquired = True
                return
            if err == _ERROR_ACCESS_DENIED:
                return  # existiert, gehört aber einem anderen Benutzer -> läuft schon
        self.acquired = True  # Mutex nicht verfügbar: lieber starten als blockieren

    def _acquire_lockfile(self, name: str) -> None:
        from PySide6.QtCore import QDir, QLockFile
        lf = QLockFile(QDir.temp().filePath(name + ".lock"))
        lf.setStaleLockTime(0)
        if lf.tryLock(100):
            self._lockfile = lf
            self.acquired = True

    def release(self) -> None:
        if self._handle:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None
        if self._lockfile is not None:
            self._lockfile.unlock()
            self._lockfile = None
        self.acquired = False


def activate_running(timeout_ms: int = 1500) -> bool:
    """Bittet das laufende Exemplar, sein Fenster nach vorn zu holen. True bei Erfolg."""
    from PySide6.QtNetwork import QLocalSocket
    if sys.platform == "win32":
        try:  # dem anderen Prozess erlauben, sich in den Vordergrund zu holen
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:
            pass
    sock = QLocalSocket()
    sock.connectToServer(_server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(b"show\n")
    ok = sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return ok


def _force_foreground(hwnd: int) -> None:
    """Fenster sichtbar über alle anderen legen, auch wenn Windows den Fokuswechsel sperrt."""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        flags = 0x0001 | 0x0002 | 0x0040  # SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW
        if not u32.SetForegroundWindow(hwnd):
            u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST
            u32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # HWND_NOTOPMOST
    except Exception:
        pass


def serve_activation(window):
    """Startet den Server, über den ein zweiter Start das Fenster nach vorn holt."""
    from PySide6.QtCore import Qt
    from PySide6.QtNetwork import QLocalServer

    server = QLocalServer(window)
    name = _server_name()
    QLocalServer.removeServer(name)  # Reste eines abgestürzten Exemplars (nur Unix relevant)
    if not server.listen(name):
        return None

    def bring_to_front() -> None:
        if window.isMinimized():
            window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
        window.show()
        window.raise_()
        window.activateWindow()
        if sys.platform == "win32":
            _force_foreground(int(window.winId()))
        from PySide6.QtWidgets import QApplication
        QApplication.alert(window, 3000)  # Taskleiste blinkt, falls Windows den Fokus verweigert

    def on_connection() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: (c.readAll(), bring_to_front()))
            conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(on_connection)
    return server
