"""DPAPI-based encryption for sensitive data at rest (Windows only)."""

import base64
import ctypes
import ctypes.wintypes
import sys


class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


_crypt32 = ctypes.windll.crypt32 if sys.platform == "win32" else None
_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None


def encrypt(data: bytes) -> bytes:
    if _crypt32 is None:
        return base64.b64encode(data)
    blob_in = _BLOB(len(data), (ctypes.c_ubyte * len(data))(*data))
    blob_out = _BLOB()
    if not _crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptProtectData failed")
    raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    _kernel32.LocalFree(blob_out.pbData)
    return base64.b64encode(b"DPAPI:" + raw)


def decrypt(data: bytes) -> bytes:
    raw = base64.b64decode(data)
    if not raw.startswith(b"DPAPI:"):
        return raw
    raw = raw[6:]
    if _crypt32 is None:
        raise OSError("DPAPI not available on this platform")
    blob_in = _BLOB(len(raw), (ctypes.c_ubyte * len(raw))(*raw))
    blob_out = _BLOB()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData failed")
    result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    _kernel32.LocalFree(blob_out.pbData)
    return result
