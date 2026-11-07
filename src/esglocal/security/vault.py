"""Encrypted local storage for workspaces, reports and chat history.

* Encryption: Fernet (AES-128-CBC + HMAC-SHA256, authenticated).
* Key: derived from a user passphrase with scrypt (n=2**15, r=8, p=1) and a
  random per-vault salt, **or** — when no passphrase is set — a random key kept
  in ``<home>/vault.key`` with 0600 permissions.
* A verifier token detects a wrong passphrase without exposing data.
* ``purge()`` deletes all records and VACUUMs the database file.

Record *names* (e.g. ``workspace:default``) are stored in clear text; record
*contents* are always encrypted.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_VERIFIER = b"esglocal-vault-v1"


class VaultError(RuntimeError):
    pass


class WrongPassphraseError(VaultError):
    pass


KDF_N = 2**17  # scrypt cost (~0.25 s, 128 MiB); stored per vault so it can be raised later
Rekey = Callable[[Callable[[bytes], bytes], Callable[[bytes], bytes]], None]


def derive_key(passphrase: str, salt: bytes, n: int = KDF_N) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=n, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)  # also tighten a pre-existing file
    except (AttributeError, OSError):  # pragma: no cover - Windows
        pass
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


class Vault:
    def __init__(self, path: str | Path, passphrase: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_db = not self.path.exists()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA secure_delete = ON")  # overwrite deleted content
        if new_db:
            try:
                os.chmod(self.path, 0o600)
            except OSError:  # pragma: no cover
                pass
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v BLOB)")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS records (name TEXT PRIMARY KEY, blob BLOB, updated TEXT)"
        )
        self._db.commit()
        self.passphrase_protected = passphrase is not None
        self._fernet = Fernet(self._load_key(passphrase))
        self._check_verifier()

    # ------------------------------------------------------------------- keys
    def _meta(self, key: str) -> bytes | None:
        row = self._db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: bytes) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (key, value))
        self._db.commit()

    def _load_key(self, passphrase: str | None) -> bytes:
        mode = self._meta("mode")
        if passphrase is not None:
            if mode == b"keyfile":
                raise VaultError(
                    "This vault is protected by a key file. Unset ESGLOCAL_PASSPHRASE, or open "
                    "it without a passphrase and call change_passphrase() to migrate."
                )
            salt = self._meta("salt")
            if salt is None:
                salt = os.urandom(16)
                self._set_meta("salt", salt)
                self._set_meta("kdf_n", str(KDF_N).encode())
            self._set_meta("mode", b"passphrase")
            return derive_key(passphrase, salt, self._kdf_n())
        if mode == b"passphrase":
            raise WrongPassphraseError(
                "This vault is passphrase-protected; a passphrase is required"
            )
        keyfile = self.path.with_name("vault.key")
        if not keyfile.exists():
            _write_private(keyfile, Fernet.generate_key())
        self._set_meta("mode", b"keyfile")
        return keyfile.read_bytes().strip()

    def _kdf_n(self) -> int:
        raw = self._meta("kdf_n")
        return int(raw) if raw else 2**15  # vaults created before the parameter was stored

    def _check_verifier(self) -> None:
        token = self._meta("verifier")
        if token is None:
            self._set_meta("verifier", self._fernet.encrypt(_VERIFIER))
            return
        try:
            if self._fernet.decrypt(token) != _VERIFIER:
                raise WrongPassphraseError("Vault verifier mismatch")
        except InvalidToken as exc:
            raise WrongPassphraseError("Wrong passphrase or key for this vault") from exc

    # ---------------------------------------------------------------- records
    def put(self, name: str, value: Any) -> None:
        blob = self._fernet.encrypt(json.dumps(value, default=str).encode("utf-8"))
        self._db.execute(
            "INSERT OR REPLACE INTO records (name, blob, updated) VALUES (?, ?, ?)",
            (name, blob, datetime.now(timezone.utc).isoformat()),
        )
        self._db.commit()

    def get(self, name: str, default: Any = None) -> Any:
        row = self._db.execute("SELECT blob FROM records WHERE name = ?", (name,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(self._fernet.decrypt(row[0]))
        except InvalidToken as exc:
            raise VaultError(f"Record {name!r} could not be decrypted (tampered?)") from exc

    def delete(self, name: str) -> None:
        self._db.execute("DELETE FROM records WHERE name = ?", (name,))
        self._db.commit()

    def names(self, prefix: str = "") -> list[str]:
        rows = self._db.execute(
            "SELECT name FROM records WHERE substr(name, 1, ?) = ? ORDER BY name",
            (len(prefix), prefix),
        ).fetchall()
        return [r[0] for r in rows]

    def encrypt_bytes(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        return self._fernet.decrypt(token)

    def change_passphrase(self, new_passphrase: str, rekey: Iterable[Rekey] = ()) -> None:
        """Re-encrypt every record under a new passphrase-derived key.

        ``rekey`` callbacks receive ``(decrypt_old, encrypt_new)`` so that other stores
        encrypted with this vault's key (e.g. the document index) are re-encrypted too.
        """
        if not new_passphrase:
            raise VaultError("Passphrase must not be empty")
        records = {n: self.get(n) for n in self.names()}
        salt = os.urandom(16)
        old = self._fernet
        fernet = Fernet(derive_key(new_passphrase, salt, KDF_N))
        for hook in rekey:  # before committing, so a failing hook leaves the vault unchanged
            hook(old.decrypt, fernet.encrypt)
        with self._db:  # single transaction: all-or-nothing re-encryption
            self._db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('salt', ?)", (salt,))
            self._db.execute(
                "INSERT OR REPLACE INTO meta (k, v) VALUES ('kdf_n', ?)", (str(KDF_N).encode(),)
            )
            self._db.execute(
                "INSERT OR REPLACE INTO meta (k, v) VALUES ('mode', ?)", (b"passphrase",)
            )
            self._db.execute(
                "INSERT OR REPLACE INTO meta (k, v) VALUES ('verifier', ?)",
                (fernet.encrypt(_VERIFIER),),
            )
            for n, v in records.items():
                self._db.execute(
                    "UPDATE records SET blob = ? WHERE name = ?",
                    (fernet.encrypt(json.dumps(v, default=str).encode("utf-8")), n),
                )
        self._fernet = fernet
        self.passphrase_protected = True

    def purge(self) -> int:
        """Delete all records and compact the file so deleted pages are not recoverable."""
        count = self._db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        self._db.execute("DELETE FROM records")
        self._db.commit()
        self._db.execute("VACUUM")
        return int(count)

    def close(self) -> None:
        self._db.close()
