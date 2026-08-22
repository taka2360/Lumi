"""The database key and the OS secret store.

**Losing this key means losing every memory** (docs/contracts/privacy.md §3). So the
tests here are less about the happy path than about the two ways it goes wrong quietly:
regenerating a key that already exists, and accepting a blob that was protected for a
different purpose.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lumi.storage.secret import (
    KEY_SIZE,
    DpapiSecretStore,
    SecretStoreError,
    get_or_create_db_key,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="DPAPI is the Windows implementation of the secret store"
)


def test_the_key_round_trips(tmp_path: Path) -> None:
    store = DpapiSecretStore(tmp_path)
    key = get_or_create_db_key(store)
    assert len(bytes.fromhex(key)) == KEY_SIZE
    assert get_or_create_db_key(store) == key


def test_a_second_process_reads_the_same_key(tmp_path: Path) -> None:
    """A new store instance over the same directory is what the next launch looks like."""
    first = get_or_create_db_key(DpapiSecretStore(tmp_path))
    second = get_or_create_db_key(DpapiSecretStore(tmp_path))
    assert first == second


def test_the_key_is_not_stored_in_the_clear(tmp_path: Path) -> None:
    store = DpapiSecretStore(tmp_path)
    key = bytes.fromhex(get_or_create_db_key(store))
    written = b"".join(path.read_bytes() for path in tmp_path.iterdir())
    assert key not in written


def test_a_blob_protected_for_another_purpose_is_refused(tmp_path: Path) -> None:
    """Entropy is per name. **A blob cannot be moved between purposes by renaming it.**"""
    store = DpapiSecretStore(tmp_path)
    assert store.create("db-key", b"x" * KEY_SIZE)
    (tmp_path / "other.dpapi").write_bytes((tmp_path / "db-key.dpapi").read_bytes())

    with pytest.raises(SecretStoreError):
        store.load("other")


def test_a_key_of_the_wrong_length_is_not_replaced(tmp_path: Path) -> None:
    """**Never generates a replacement.** A new key would orphan every existing database,
    which is data loss wearing the costume of recovery.
    """
    store = DpapiSecretStore(tmp_path)
    assert store.create("db-key", b"short")

    with pytest.raises(SecretStoreError):
        get_or_create_db_key(store)

    assert store.load("db-key") == b"short"


def test_deleting_a_missing_secret_is_not_an_error(tmp_path: Path) -> None:
    """ "Erase everything" may well run twice."""
    DpapiSecretStore(tmp_path).delete("db-key")


def test_a_second_creator_does_not_overwrite(tmp_path: Path) -> None:
    """**The loser of the race adopts the winner's key.**

    Two Lumi processes can reach first run at the same moment. If the second one
    overwrote the key, the first would already have a database encrypted with a key
    that no longer exists anywhere.
    """
    store = DpapiSecretStore(tmp_path)
    first = get_or_create_db_key(store)

    assert store.create("db-key", b"y" * KEY_SIZE) is False
    assert get_or_create_db_key(store) == first


def test_an_interrupted_create_is_reclaimed(tmp_path: Path) -> None:
    """A create that died between making the file and writing it leaves an empty file.

    **It holds no key**, so it must not brick the install: the next start reclaims it.
    """
    store = DpapiSecretStore(tmp_path)
    (tmp_path / "db-key.dpapi").write_bytes(b"")

    key = get_or_create_db_key(store)

    assert len(bytes.fromhex(key)) == KEY_SIZE
    assert get_or_create_db_key(store) == key


def test_a_corrupted_blob_fails_loudly(tmp_path: Path) -> None:
    store = DpapiSecretStore(tmp_path)
    get_or_create_db_key(store)
    (tmp_path / "db-key.dpapi").write_bytes(b"not a DPAPI blob")

    with pytest.raises(SecretStoreError):
        store.load("db-key")
