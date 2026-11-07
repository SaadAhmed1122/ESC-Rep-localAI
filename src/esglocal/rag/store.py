"""SQLite vector store with optional encryption of chunk text at rest.

Brute-force cosine search in numpy is plenty fast for the few thousand chunks
a company's ESG documents produce, and avoids another heavy dependency.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from esglocal.embeddings import Embedder
from esglocal.rag.ingest import chunk_text


@dataclass
class Hit:
    doc: str
    chunk: int
    text: str
    score: float


class DocumentStore:
    def __init__(
        self,
        path: str | Path,
        embedder: Embedder,
        encrypt: Callable[[bytes], bytes] | None = None,
        decrypt: Callable[[bytes], bytes] | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._enc = encrypt or (lambda b: b)
        self._dec = decrypt or (lambda b: b)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA secure_delete = ON")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS docs (
                id INTEGER PRIMARY KEY, name TEXT, sha TEXT UNIQUE, added TEXT, embedder TEXT
            );
            CREATE TABLE IF NOT EXISTS chunks (
                doc_id INTEGER, idx INTEGER, text BLOB, vec BLOB,
                FOREIGN KEY(doc_id) REFERENCES docs(id) ON DELETE CASCADE
            );
            """
        )
        self._db.commit()

    def add(self, name: str, text: str) -> int:
        """Index a document; returns number of chunks (0 if already indexed)."""
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if self._db.execute("SELECT 1 FROM docs WHERE sha = ?", (sha,)).fetchone():
            return 0
        chunks = chunk_text(text)
        if not chunks:
            return 0
        vecs = self.embedder.embed(chunks).astype(np.float32)
        with self._db:
            cur = self._db.execute(
                "INSERT INTO docs (name, sha, added, embedder) VALUES (?, ?, ?, ?)",
                (name, sha, datetime.now(timezone.utc).isoformat(), self.embedder.name),
            )
            doc_id = cur.lastrowid
            self._db.executemany(
                "INSERT INTO chunks (doc_id, idx, text, vec) VALUES (?, ?, ?, ?)",
                [
                    (doc_id, i, self._enc(c.encode("utf-8")), v.tobytes())
                    for i, (c, v) in enumerate(zip(chunks, vecs, strict=True))
                ],
            )
        return len(chunks)

    def documents(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT d.id, d.name, d.added, d.embedder, COUNT(c.idx) FROM docs d "
            "LEFT JOIN chunks c ON c.doc_id = d.id GROUP BY d.id ORDER BY d.added"
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "added": r[2], "embedder": r[3], "chunks": r[4]}
            for r in rows
        ]

    def remove(self, doc_id: int) -> None:
        with self._db:
            self._db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            self._db.execute("DELETE FROM docs WHERE id = ?", (doc_id,))

    def reencrypt(
        self, decrypt_old: Callable[[bytes], bytes], encrypt_new: Callable[[bytes], bytes]
    ) -> None:
        """Re-encrypt all chunk text (used when the vault passphrase changes)."""
        rows = self._db.execute("SELECT rowid, text FROM chunks").fetchall()
        with self._db:
            self._db.executemany(
                "UPDATE chunks SET text = ? WHERE rowid = ?",
                [(encrypt_new(decrypt_old(text)), rowid) for rowid, text in rows],
            )

    def clear(self) -> None:
        with self._db:
            self._db.execute("DELETE FROM chunks")
            self._db.execute("DELETE FROM docs")
        self._db.execute("VACUUM")

    def search(self, query: str, k: int = 4) -> list[Hit]:
        rows = self._db.execute(
            "SELECT d.name, c.idx, c.text, c.vec, d.embedder FROM chunks c "
            "JOIN docs d ON d.id = c.doc_id"
        ).fetchall()
        rows = [r for r in rows if r[4] == self.embedder.name]  # same vector space only
        if not rows or not query.strip():
            return []
        q = self.embedder.embed([query])[0].astype(np.float32)
        mat = np.vstack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
        if mat.shape[1] != q.shape[0]:
            return []
        scores = mat @ q / (np.linalg.norm(mat, axis=1) * (np.linalg.norm(q) or 1.0) + 1e-9)
        top = np.argsort(-scores)[:k]
        return [
            Hit(
                doc=rows[i][0],
                chunk=int(rows[i][1]),
                text=self._dec(rows[i][2]).decode("utf-8"),
                score=float(scores[i]),
            )
            for i in top
            if scores[i] > 0
        ]

    def close(self) -> None:
        self._db.close()
