"""Verschlüsselung von Passwörtern mit der Windows-DPAPI (an das Benutzerkonto gebunden).

Auf anderen Systemen wird nur Base64 verwendet (keine echte Verschlüsselung).
"""

from __future__ import annotations

import base64
import sys

_PREFIX_DPAPI = "dpapi:"
_PREFIX_B64 = "b64:"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _to_blob(data: bytes) -> _Blob:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _from_blob(blob: _Blob) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(blob.pbData)

    def _protect(data: bytes) -> bytes:
        out = _Blob()
        if not _crypt32.CryptProtectData(ctypes.byref(_to_blob(data)), None, None, None, None,
                                         0x1, ctypes.byref(out)):
            raise OSError("CryptProtectData fehlgeschlagen")
        return _from_blob(out)

    def _unprotect(data: bytes) -> bytes:
        out = _Blob()
        if not _crypt32.CryptUnprotectData(ctypes.byref(_to_blob(data)), None, None, None, None,
                                           0x1, ctypes.byref(out)):
            raise OSError("CryptUnprotectData fehlgeschlagen")
        return _from_blob(out)
else:  # pragma: no cover
    _protect = _unprotect = None


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    if _protect is not None:
        try:
            return _PREFIX_DPAPI + base64.b64encode(_protect(plain.encode("utf-8"))).decode()
        except OSError:
            pass
    return _PREFIX_B64 + base64.b64encode(plain.encode("utf-8")).decode()


def decrypt(stored: str) -> str:
    if not stored:
        return ""
    try:
        if stored.startswith(_PREFIX_DPAPI) and _unprotect is not None:
            return _unprotect(base64.b64decode(stored[len(_PREFIX_DPAPI):])).decode("utf-8")
        if stored.startswith(_PREFIX_B64):
            return base64.b64decode(stored[len(_PREFIX_B64):]).decode("utf-8")
    except (OSError, ValueError):
        return ""
    return ""
