"""The OS secret store, and the database key that lives in it.

**Lumi generates a random database key and never shows it to the user**
(docs/contracts/privacy.md §3 / ADR-038). The user creates no password and manages
nothing. That only works if the key itself is kept somewhere the OS protects, and
this module is the single window onto that.

Windows uses DPAPI (`CryptProtectData`, current-user scope). **DPAPI protects
contents, not location** — the protected blob is still an ordinary file, and it
lands under `paths.secrets_dir()`. That is why it appears in the table in
docs/contracts/privacy.md §2 like everything else written to disk.

**There is no plaintext fallback.** On a platform with no implementation, opening an
encrypted database fails and Lumi stops. Writing the key out unprotected instead
would be exactly the "silently fall back to plaintext" that ADR-038 forbids.
"""

from __future__ import annotations

import ctypes
import os
import secrets
import sys
from pathlib import Path
from typing import Final, Protocol

from lumi import logging as lumi_logging

log = lumi_logging.get_logger(__name__)

#: Size of the database key. 256 bits of entropy, generated once per user.
KEY_SIZE: Final = 32

#: The name the database key is filed under. **The databases cannot be opened without it.**
DB_KEY_NAME: Final = "db-key"


class SecretStoreError(RuntimeError):
    """The OS secret store is unavailable, or what came back cannot be used.

    **Never falls back to an unprotected key.** Callers let this propagate.
    """


class SecretStore(Protocol):
    """The window onto the OS secret store. **One implementation per OS.**"""

    def load(self, name: str) -> bytes | None:
        """The stored secret, or `None` if nothing is filed under `name`."""
        ...

    def create(self, name: str, secret: bytes) -> bool:
        """Files `secret` under `name` **only if nothing is there yet.**

        `False` means someone else got there first, and **their** secret is the one
        that counts. There is deliberately no method that overwrites: a database key
        that can be replaced is a database that can be lost.
        """
        ...

    def delete(self, name: str) -> None:
        """Removes it. **Missing is not an error** ("erase everything" may run twice)."""
        ...


def get_or_create_db_key(store: SecretStore, name: str = DB_KEY_NAME) -> str:
    """The database key, as a hex string, creating it on first run.

    **A key of the wrong length is an error, not a reason to generate a new one.**
    A replacement key would leave every existing database unopenable — data loss
    dressed up as recovery. Failing loudly leaves the databases intact.

    **Two Lumi processes may reach first run at the same moment** (a second launch
    while the first is still starting). Creation is therefore exclusive, and the
    loser of that race adopts the winner's key rather than overwriting it — the
    winner may already have created a database with it.
    """
    existing = store.load(name)
    if existing is not None:
        return _validated(existing, name)

    key = secrets.token_bytes(KEY_SIZE)
    if store.create(name, key):
        log.info("storage.db_key.created", name=name)
        return key.hex()

    stored = store.load(name)
    if stored is None:
        # Someone holds the name but nothing readable is there. **Never generate a
        # second key to get past this** — that is how two databases with two keys happen.
        raise SecretStoreError(f"The database key could not be created or read: {name}")
    log.info("storage.db_key.adopted", name=name)
    return _validated(stored, name)


def _validated(key: bytes, name: str) -> str:
    if len(key) != KEY_SIZE:
        raise SecretStoreError(
            f"Stored secret {name!r} has an unexpected length: {len(key)} bytes "
            f"(expected {KEY_SIZE}). Refusing to replace it."
        )
    return key.hex()


def _entropy(name: str) -> bytes:
    """Ties a protected blob to its purpose, so blobs cannot be swapped between them."""
    return f"lumi.secret.v1:{name}".encode()


class DpapiSecretStore:
    """Windows DPAPI, current-user scope.

    `CRYPTPROTECT_LOCAL_MACHINE` is deliberately **not** passed: with it, every user
    on the machine could unprotect the blob, and "one user, one set of memories"
    (docs/contracts/privacy.md §1) would stop being true.
    """

    __slots__ = ("_directory",)

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def _path(self, name: str) -> Path:
        return self._directory / f"{name}.dpapi"

    def load(self, name: str) -> bytes | None:
        path = self._path(name)
        try:
            blob = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SecretStoreError(f"Cannot read the protected secret: {path}") from error
        if not blob:
            # An empty file can only come from a `create` that died between making the
            # file and writing it — which also means **no key was ever handed out under
            # this name**, so no database exists that this file was supposed to open.
            # **It is not cleared automatically.** Deleting a file that merely looks
            # empty right now is how another process's freshly written key gets
            # destroyed; see `create`.
            raise SecretStoreError(
                f"The protected secret is empty: {path}. A first run was interrupted "
                f"between creating the file and writing the key. No database was "
                f"created with it: delete this file and start again."
            )
        return _unprotect(blob, _entropy(name))

    def create(self, name: str, secret: bytes) -> bool:
        """Exclusive create. **Never overwrites, and never deletes anyone else's file.**

        `O_EXCL` is what makes two processes racing on first run safe: exactly one of
        them creates the file, and the other is told so.

        **There is no reclaim path for an existing file, empty or not.** "It looked
        empty, so I removed it" cannot be made safe without a lock: between the look
        and the removal, the process that created it may have written the key and
        opened a database with it. The only file this method ever unlinks is one that
        this very call created and then failed to write.
        """
        blob = _protect(secret, _entropy(name))
        path = self._path(name)
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise SecretStoreError(f"Cannot create the secrets directory: {path}") from error

        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        except OSError as error:
            raise SecretStoreError(f"Cannot write the protected secret: {path}") from error

        try:
            with os.fdopen(descriptor, "wb") as sink:
                sink.write(blob)
                sink.flush()
                os.fsync(sink.fileno())
        except OSError as error:
            # **Removing this one is safe**: `O_EXCL` succeeded, so no other process
            # can have written to it. Leaving it would look like a corrupted key
            # rather than a create that never finished.
            path.unlink(missing_ok=True)
            raise SecretStoreError(f"Cannot write the protected secret: {path}") from error
        return True

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink(missing_ok=True)
        except OSError as error:
            raise SecretStoreError(f"Cannot delete the protected secret: {name}") from error


def available() -> bool:
    """Whether this OS has a secret store implementation. **Checked before opening a DB.**"""
    return sys.platform == "win32"


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        """`DATA_BLOB`. Both the input and the output of the DPAPI calls."""

        _fields_ = (
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        )

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(_Blob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    )
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_Blob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    )
    _kernel32.LocalFree.restype = wintypes.HLOCAL
    _kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)

    def _input_blob(data: bytes) -> tuple[_Blob, ctypes.Array[ctypes.c_char]]:
        """The blob, **and the buffer it points at**.

        Returned together so the caller keeps a reference: dropping the buffer would
        free the memory `pbData` points at while the call is still using it.
        """
        buffer = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    def _take(blob: _Blob) -> bytes:
        """Copies the output out and frees it. **DPAPI allocates with `LocalAlloc`.**"""
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, wintypes.HLOCAL))

    def _protect(secret: bytes, entropy: bytes) -> bytes:
        source, _source_buffer = _input_blob(secret)
        salt, _salt_buffer = _input_blob(entropy)
        out = _Blob()
        # `CRYPTPROTECT_UI_FORBIDDEN` (0x1): **never prompt.** Core has no window of
        # its own; a modal here would hang startup with nothing on screen to explain it.
        if not _crypt32.CryptProtectData(
            ctypes.byref(source), None, ctypes.byref(salt), None, None, 0x1, ctypes.byref(out)
        ):
            raise SecretStoreError(f"CryptProtectData failed: error {ctypes.get_last_error()}")
        return _take(out)

    def _unprotect(blob: bytes, entropy: bytes) -> bytes:
        source, _source_buffer = _input_blob(blob)
        salt, _salt_buffer = _input_blob(entropy)
        out = _Blob()
        if not _crypt32.CryptUnprotectData(
            ctypes.byref(source), None, ctypes.byref(salt), None, None, 0x1, ctypes.byref(out)
        ):
            # Happens when the blob was written by a different Windows user, or the
            # profile was rebuilt. **The key is gone, and the databases with it**
            # (docs/contracts/privacy.md §3, "losing the key means no recovery").
            raise SecretStoreError(f"CryptUnprotectData failed: error {ctypes.get_last_error()}")
        return _take(out)

else:

    def _protect(secret: bytes, entropy: bytes) -> bytes:
        raise SecretStoreError(f"No OS secret store implementation for {sys.platform}")

    def _unprotect(blob: bytes, entropy: bytes) -> bytes:
        raise SecretStoreError(f"No OS secret store implementation for {sys.platform}")
