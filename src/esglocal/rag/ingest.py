"""Read and chunk the user's documents (policies, prior reports) — all local."""

from __future__ import annotations

import io
import re
from pathlib import Path

SUPPORTED = {".txt", ".md", ".pdf", ".csv"}


class IngestError(ValueError):
    pass


def read_document(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED:
        raise IngestError(f"Unsupported file type {suffix}; use one of {sorted(SUPPORTED)}")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover
            raise IngestError("PDF support needs: pip install 'esglocal[docs]'") from exc
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return data.decode("utf-8", errors="replace")


def chunk_text(text: str, max_chars: int = 900, overlap: int = 150) -> list[str]:
    """Split on paragraph boundaries, packing paragraphs up to ``max_chars``."""
    text = re.sub(r"\r\n?", "\n", text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        while len(para) > max_chars:  # hard-split very long paragraphs
            head, para = para[:max_chars], para[max_chars - overlap :]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            tail = current[-overlap:] if current and overlap else ""
            current = f"{tail}\n\n{para}" if tail else para
    if current:
        chunks.append(current)
    return chunks
