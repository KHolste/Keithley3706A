"""Benachrichtigungen: Ton und E-Mail (im Hintergrund, mit Sendepause je Kanal)."""

from __future__ import annotations

import smtplib
import ssl
import sys
import threading
import time
from dataclasses import dataclass, field
from email.message import EmailMessage

from PySide6.QtCore import QObject, Signal


def play_alarm_sound() -> None:
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return
    except Exception:
        pass
    from PySide6.QtWidgets import QApplication
    QApplication.beep()


@dataclass
class EmailConfig:
    enabled: bool = False
    host: str = ""
    port: int = 587
    security: str = "starttls"   # starttls | ssl | none
    username: str = ""
    password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    min_interval_min: float = 15.0

    def is_complete(self) -> bool:
        return bool(self.host and self.sender and self.recipients)


def send_email(cfg: EmailConfig, subject: str, body: str, timeout: float = 20.0) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.recipients)
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if cfg.security == "ssl":
        server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=timeout, context=ctx)
    else:
        server = smtplib.SMTP(cfg.host, cfg.port, timeout=timeout)
    try:
        if cfg.security == "starttls":
            server.starttls(context=ctx)
        if cfg.username:
            server.login(cfg.username, cfg.password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


class EmailNotifier(QObject):
    """Verschickt Mails in einem Hintergrund-Thread; meldet Ergebnis per Signal."""

    finished = Signal(bool, str)   # ok, Meldung

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = EmailConfig()
        self._last_sent: dict[str, float] = {}

    def notify(self, key: str, subject: str, body: str, force: bool = False) -> bool:
        cfg = self.config
        if not force and (not cfg.enabled or not cfg.is_complete()):
            return False
        now = time.time()
        if not force and now - self._last_sent.get(key, 0) < cfg.min_interval_min * 60:
            return False
        self._last_sent[key] = now

        def worker():
            try:
                send_email(cfg, subject, body)
                self.finished.emit(True, f"E-Mail gesendet: {subject}")
            except Exception as exc:
                self.finished.emit(False, f"E-Mail fehlgeschlagen: {type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()
        return True
