"""Tamper-evident, append-only audit log.

Each line is JSON ``{ts, event, details, prev, hash}`` where ``hash`` is
SHA-256 over the previous hash and the entry body, forming a hash chain:
editing or deleting any past line breaks verification. Only *metadata* is
logged (event names, counts, KPI ids, model names) — never user data values.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _digest(prev: str, body: dict) -> str:
    payload = prev + json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
            os.close(fd)

    def _last_hash(self) -> str:
        last = GENESIS
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    def append(self, event: str, **details) -> dict:
        with self._lock:
            body = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "details": details,
                "prev": self._last_hash(),
            }
            entry = {**body, "hash": _digest(body["prev"], body)}
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
            return entry

    def entries(self, limit: int | None = None) -> list[dict]:
        with self.path.open("r", encoding="utf-8") as fh:
            items = [json.loads(line) for line in fh if line.strip()]
        return items[-limit:] if limit else items

    def verify(self) -> tuple[bool, int, int | None]:
        """Return (ok, entries_checked, first_broken_index)."""
        prev = GENESIS
        entries = self.entries()
        for i, e in enumerate(entries):
            body = {k: e[k] for k in ("ts", "event", "details", "prev") if k in e}
            if e.get("prev") != prev or _digest(prev, body) != e.get("hash"):
                return False, i, i
            prev = e["hash"]
        return True, len(entries), None
