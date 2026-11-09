"""The user's API key, kept in the system's own secret store, never in settings.json.

  macOS    Keychain, through the `security` tool
  Windows  DPAPI (CryptProtectData): a file only this Windows user can decrypt
  other    a file readable only by the user (no system store assumed)

VEULY_SECRETS=file (or the older TRANSCAPP_SECRETS) keeps them in such files of the data folder on every system.
The tests and the stand-in runner set it, so they never read, replace or delete the user's own Keychain items.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess

from . import paths

SERVICE = "Veuly.api-key"
OLD_PREFIX = "TranscApp"     # Keychain items saved before the app was renamed are read, and removed, under this name too
ACCOUNT = "transcapp"
KEY_SHAPE = re.compile(r"^[A-Za-z0-9_\-]{20,300}$")


def _store() -> str:
    """'keychain' (Mac), 'dpapi' (Windows) or 'file' (other systems, and whenever VEULY_SECRETS=file)."""
    if (os.environ.get("VEULY_SECRETS") or os.environ.get("TRANSCAPP_SECRETS") or "").lower() == "file":
        return "file"
    return {"Darwin": "keychain", "Windows": "dpapi"}.get(platform.system(), "file")


def _file():
    return paths.data_dir() / ("api-key.dpapi" if _store() == "dpapi" else "api-key")


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), buf), Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows could not protect the key.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def set_key(key: str) -> None:
    key = key.strip()
    if not KEY_SHAPE.fullmatch(key):
        raise ValueError("That does not look like an API key.")
    store = _store()
    if store == "keychain":
        # Through stdin, so the key never shows up in the process list.
        cmd = f"add-generic-password -U -a {ACCOUNT} -s {SERVICE} -w {key}\n"
        r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise OSError(f"Keychain refused the key: {r.stderr.strip()[:200]}")
    elif store == "dpapi":
        _file().write_bytes(_dpapi(key.encode(), True))
    else:
        f = _file()
        f.touch(mode=0o600)
        os.chmod(f, 0o600)
        f.write_text(key, encoding="utf-8")


def _keychain_find(name: str) -> str | None:
    """The item under the app's name, or under its old name if it was saved before the rename."""
    for prefix in ("Veuly", OLD_PREFIX):
        r = subprocess.run(["security", "find-generic-password", "-a", ACCOUNT, "-s", f"{prefix}.{name}", "-w"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def _keychain_delete(name: str) -> None:
    for prefix in ("Veuly", OLD_PREFIX):
        subprocess.run(["security", "delete-generic-password", "-a", ACCOUNT, "-s", f"{prefix}.{name}"],
                       capture_output=True, timeout=20)


def get_key() -> str | None:
    store = _store()
    try:
        if store == "keychain":
            return _keychain_find("api-key")
        if not _file().exists():
            return None
        if store == "dpapi":
            return _dpapi(_file().read_bytes(), False).decode() or None
        return _file().read_text(encoding="utf-8").strip() or None
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None


def delete_key() -> None:
    if _store() == "keychain":
        _keychain_delete("api-key")
    elif _file().exists():
        _file().unlink()


# ---- other secrets (the Confluence token): same stores, any characters

def _secret_file(name: str):
    return paths.data_dir() / (f"{name}.dpapi" if _store() == "dpapi" else f"{name}.secret")


def set_secret(name: str, value: str) -> None:
    value = value.strip()
    if not value or len(value) > 4000 or not re.fullmatch(r"[\w.-]+", name):
        raise ValueError("That does not look like a token.")
    store = _store()
    if store == "keychain":
        # As hex through stdin: no quoting problems, and the token never shows in the process list.
        cmd = f"add-generic-password -U -a {ACCOUNT} -s Veuly.{name} -X {value.encode().hex()}\n"
        r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise OSError(f"Keychain refused the token: {r.stderr.strip()[:200]}")
    elif store == "dpapi":
        _secret_file(name).write_bytes(_dpapi(value.encode(), True))
    else:
        f = _secret_file(name)
        f.touch(mode=0o600)
        os.chmod(f, 0o600)
        f.write_text(value, encoding="utf-8")


def get_secret(name: str) -> str | None:
    store = _store()
    try:
        if store == "keychain":
            return _keychain_find(name)
        if not _secret_file(name).exists():
            return None
        if store == "dpapi":
            return _dpapi(_secret_file(name).read_bytes(), False).decode() or None
        return _secret_file(name).read_text(encoding="utf-8").strip() or None
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None


def delete_secret(name: str) -> None:
    if _store() == "keychain":
        _keychain_delete(name)
    elif _secret_file(name).exists():
        _secret_file(name).unlink()


def sync_secret(name: str) -> None:
    """A secret kept in a file, really on the disk: for what is about to delete another copy of it. The system's own
    store sees to that itself. A power cut soon after the delete could otherwise leave an empty file of the new copy."""
    if _store() == "keychain":
        return
    path = _secret_file(name)
    with open(path, "r+b") as f:
        os.fsync(f.fileno())
    if os.name != "nt":             # and its name in the folder, where the file system can
        try:
            fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass


def hint(key: str | None) -> str:
    """What the UI may show: the last four characters only."""
    return f"…{key[-4:]}" if key else ""
