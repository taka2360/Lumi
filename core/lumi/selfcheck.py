"""Verify that **the bundled sidecar actually works on this PC**.

```
lumi-core.exe --self-check
```

Makes roadmap Phase 0 verification step 13 (**can sqlite-vec load from the sidecar?**)
runnable **against the actual distributable**.

Passing in the dev environment (`uv run pytest`) **does not mean it will pass after being
packed by PyInstaller.** Native extensions (sqlite-vec's `vec0.dll`, APSW's encrypted
SQLite build, PortAudio's DLL) aren't bundled automatically like Python code is, and
missing ones **don't fail at import time**. You don't find out until they're loaded.

**The memory checks run against an encrypted database**, because that is the only
configuration Lumi ever writes to disk (docs/contracts/privacy.md §3 / ADR-040).
Checking sqlite-vec against a plaintext database would confirm something Lumi never does.

Better to **fail here** than discover that "the entire memory feature doesn't work."
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import apsw

from lumi.storage import secret
from lumi.storage.events import EVENTS_SCHEMA
from lumi.storage.sqlite import CIPHER, Database, StorageError, one


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"{'✓' if self.ok else '✗'} {self.name}: {self.detail}"


#: The key the throwaway self-check database is opened with. **Not a secret**: it
#: protects a database that exists for the length of one function call. The real key
#: comes from the OS secret store, which `check_secret_store` exercises separately.
_PROBE_KEY = "00" * 32


@contextmanager
def _probe_database() -> Iterator[tuple[Database, Path]]:
    """An encrypted database in a temporary directory, torn down afterwards."""
    with tempfile.TemporaryDirectory(prefix="lumi-selfcheck-") as directory:
        path = Path(directory) / "probe.db"
        database = Database.open(path, EVENTS_SCHEMA, key=_PROBE_KEY)
        try:
            yield database, path
        finally:
            database.close()


def check_secret_store() -> CheckResult:
    """**Can the database key be handed to the OS?**

    Without this, Lumi cannot reopen its own databases on the next start. It fails at
    the first launch on a machine, so finding out here is far cheaper than finding out
    when someone's memories will not load.
    """
    if not secret.available():
        return CheckResult("OS secret store", False, f"No implementation for {sys.platform}")

    try:
        with tempfile.TemporaryDirectory(prefix="lumi-selfcheck-") as directory:
            store = secret.DpapiSecretStore(Path(directory))
            key = secret.get_or_create_db_key(store, "selfcheck")
            again = secret.get_or_create_db_key(store, "selfcheck")
            store.delete("selfcheck")
    except secret.SecretStoreError as error:
        return CheckResult("OS secret store", False, str(error))
    if key != again:
        return CheckResult("OS secret store", False, "The key did not round-trip")
    return CheckResult("OS secret store", True, "DPAPI (current user) round-trips the key")


def check_encrypted_sqlite() -> CheckResult:
    """**Is what lands on disk actually encrypted?**

    Opening it with the stdlib `sqlite3` has to fail. If it ever succeeds, the build has
    fallen back to plaintext SQLite — the outcome ADR-038 says must never happen
    quietly — and every conversation Lumi persists would be readable on disk.
    """
    try:
        with _probe_database() as (database, path):
            with database.transaction() as conn:
                conn.execute("CREATE TABLE probe (body TEXT)")
                conn.execute("INSERT INTO probe VALUES ('selfcheck')")

            plaintext = sqlite3.connect(str(path))
            try:
                plaintext.execute("SELECT count(*) FROM probe").fetchone()
            except sqlite3.DatabaseError:
                readable = False
            else:
                readable = True
            finally:
                # **Closed even on the failure path.** A refused connection still holds
                # the file open on Windows, and the temporary directory cannot be removed.
                plaintext.close()

            if readable:
                return CheckResult(
                    "Encrypted SQLite",
                    False,
                    "The database opened unencrypted (plaintext on disk)",
                )

            try:
                Database.open(path, EVENTS_SCHEMA, key="11" * 32).close()
            except StorageError:
                pass
            else:
                return CheckResult("Encrypted SQLite", False, "A wrong key opened the database")
    except (StorageError, apsw.Error, OSError) as error:
        return CheckResult("Encrypted SQLite", False, f"{type(error).__name__}: {error}")

    versions = f"APSW {apsw.apswversion()} / SQLite {apsw.sqlitelibversion()}"
    return CheckResult("Encrypted SQLite", True, f"{CIPHER} / {versions}")


def check_sqlite_vec() -> CheckResult:
    """**Load it and actually run a search**, inside an encrypted database.

    A successful import guarantees nothing, and neither does a search against a
    plaintext database: the encrypted build is a different SQLite library.
    """
    try:
        import sqlite_vec
    except ImportError as error:
        return CheckResult("sqlite-vec", False, f"Cannot import: {error}")

    try:
        with _probe_database() as (database, _path):
            database.load_extension(sqlite_vec.loadable_path())
            with database.transaction() as conn:
                version = one(conn.execute("SELECT vec_version()"))[0]
                conn.execute("CREATE VIRTUAL TABLE v USING vec0(embedding float[4])")
                vector = sqlite_vec.serialize_float32([0.1, 0.2, 0.3, 0.4])
                conn.execute("INSERT INTO v(rowid, embedding) VALUES (1, ?)", (vector,))
                hit = conn.execute(
                    "SELECT rowid FROM v WHERE embedding MATCH ? ORDER BY distance LIMIT 1",
                    (vector,),
                ).fetchone()
        if hit is None:
            return CheckResult("sqlite-vec", False, "KNN search returned nothing")
    except (StorageError, apsw.Error, AttributeError, OSError) as error:
        return CheckResult("sqlite-vec", False, f"{type(error).__name__}: {error}")
    return CheckResult("sqlite-vec", True, f"{version} / KNN search works in an encrypted DB")


def check_fts5() -> CheckResult:
    """FTS5 is a SQLite build-time option. **Some SQLite builds don't include it.**

    Checked against the encrypted build, which is a different library from the one
    CPython ships with — one of them having FTS5 says nothing about the other.
    """
    try:
        with _probe_database() as (database, _path):
            with database.transaction() as conn:
                conn.execute("CREATE VIRTUAL TABLE f USING fts5(body)")
                conn.execute("INSERT INTO f(body) VALUES ('こんにちは 世界')")
                found = one(conn.execute("SELECT count(*) FROM f WHERE f MATCH ?", ("世界",)))[0]
        if found != 1:
            return CheckResult("FTS5", False, "Search did not match")
    except (StorageError, apsw.Error, OSError) as error:
        return CheckResult("FTS5", False, f"{type(error).__name__}: {error}")
    return CheckResult("FTS5", True, "Japanese search matches in an encrypted DB")


def check_audio() -> CheckResult:
    """Whether the PortAudio DLL is bundled. **Separate from whether any devices exist.**

    Even with zero devices, this counts as success as long as the library loads
    (a PC with no input device is a normal state → docs/architecture/audio.md §8).
    """
    try:
        from lumi.audio.probe import list_devices
    except OSError as error:
        return CheckResult("PortAudio", False, f"Cannot load library: {error}")

    try:
        devices, host_apis = list_devices()
    except Exception as error:
        return CheckResult("PortAudio", False, f"{type(error).__name__}: {error}")

    inputs = sum(1 for device in devices if device.can_capture)
    outputs = sum(1 for device in devices if device.can_play)
    names = ", ".join(api.name for api in host_apis)
    return CheckResult("PortAudio", True, f"Input {inputs} / Output {outputs} / Host API: {names}")


def check_ssl() -> CheckResult:
    """Needed to fetch the TTS engine. **The certificate store is prone to breaking under
    PyInstaller.**
    """
    try:
        import ssl

        context = ssl.create_default_context()
        stats = context.cert_store_stats()
    except Exception as error:
        return CheckResult("TLS", False, f"{type(error).__name__}: {error}")
    if stats.get("x509_ca", 0) <= 0:
        return CheckResult("TLS", False, "No CA certificates found (HTTPS fetches will fail)")
    return CheckResult("TLS", True, f"CA certificates: {stats['x509_ca']}")


def check_vad() -> CheckResult:
    """**The barge-in critical path.** Loads ONNX and runs inference on one actual frame.

    The model is borrowed from faster-whisper's bundled assets (ADR-023 / docs/licensing.md §4.6),
    so **this fails if a package update changes the path.** That's the only way we'd detect it.
    """
    try:
        import numpy as np

        from lumi.audio.vad import WINDOW_SAMPLES, SileroVad, VadModelUnavailable
    except ImportError as error:
        return CheckResult("Silero VAD", False, f"Cannot import: {error}")

    try:
        vad = SileroVad()
        probability = vad.probability(np.zeros(WINDOW_SAMPLES, dtype=np.float32))
    except (VadModelUnavailable, RuntimeError, OSError, ValueError) as error:
        return CheckResult("Silero VAD", False, f"{type(error).__name__}: {error}")
    if not 0.0 <= probability <= 1.0:
        return CheckResult("Silero VAD", False, f"Probability out of range: {probability}")
    return CheckResult("Silero VAD", True, "Inference succeeds with ONNX Runtime (CPU)")


def check_stt() -> CheckResult:
    """**Checks the runtime, not the model.** The model is fetched at runtime (ADR-023).

    `ctranslate2` is a native extension, and **import fails outright if it's missing**.
    Without this check failing here, it would surface as "speaking gets no response."
    """
    try:
        import ctranslate2
        import faster_whisper
    except ImportError as error:
        return CheckResult("faster-whisper", False, f"Cannot import: {error}")
    versions = f"{faster_whisper.__version__} / CTranslate2 {ctranslate2.__version__}"
    return CheckResult("faster-whisper", True, versions)


def check_content() -> CheckResult:
    """**Lumi without a persona isn't Lumi.** Whether the bundled Content Pack can be read."""
    try:
        from lumi import paths
        from lumi.content.pack import ContentPackError, load_character
    except ImportError as error:
        return CheckResult("Content Pack", False, f"Cannot import: {error}")

    try:
        pack = load_character(paths.default_character_dir())
    except ContentPackError as error:
        return CheckResult("Content Pack", False, str(error))
    return CheckResult("Content Pack", True, f"{pack.name} / {pack.voice.credit.credit_text}")


CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_secret_store,
    check_encrypted_sqlite,
    check_sqlite_vec,
    check_fts5,
    check_audio,
    check_vad,
    check_stt,
    check_content,
    check_ssl,
)


def run() -> int:
    """Run everything and report results. **Returns 1 if even one check fails.**"""
    frozen = getattr(sys, "frozen", False)
    mode = "bundled sidecar" if frozen else "dev environment"
    print(f"Lumi Core self-check ({mode} / {sys.version})")
    results = [check() for check in CHECKS]
    for result in results:
        print(f"  {result.line()}")
    failed = [result for result in results if not result.ok]
    if failed:
        print(f"\n{len(failed)} check(s) failed. **This distribution cannot be used.**")
        return 1
    print("\nAll checks passed.")
    return 0
