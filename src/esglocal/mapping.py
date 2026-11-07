"""Automatic mapping of uploaded spreadsheet columns to KPI inputs.

Uses token-overlap + fuzzy string similarity against each input's name,
description and aliases — no LLM needed, so mapping is deterministic and fast.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher

from esglocal.catalog import KPIInput

_STOP = {"total", "the", "of", "in", "and", "per", "a", "number", "amount"}
_UNIT_TOKENS = {
    "meur": {"eur", "m", "million", "mio", "meur", "eurm"},
    "tco2e": {"tco2e", "tco2", "co2e", "tonnes", "t"},
    "mwh": {"mwh"},
    "m3": {"m3", "cubic"},
    "t": {"t", "tonnes", "tons"},
    "eur": {"eur", "euro", "euros"},
}


def _normalise(text: str) -> str:
    text = text.lower().replace("₂", "2").replace("³", "3")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _normalise(text).split() if t not in _STOP}


def _score(column: str, inp: KPIInput) -> float:
    col_norm = _normalise(column)
    col_tokens = _tokens(column)
    if col_norm == _normalise(inp.name) or col_norm.replace(" ", "_") == inp.name:
        return 1.0
    best = 0.0
    candidates = [inp.name.replace("_", " "), *inp.aliases]
    for cand in candidates:
        cand_norm = _normalise(cand)
        if not cand_norm:
            continue
        if cand_norm == col_norm:
            return 0.98
        cand_tokens = _tokens(cand)
        overlap = len(col_tokens & cand_tokens) / max(len(cand_tokens), 1)
        ratio = SequenceMatcher(None, col_norm, cand_norm).ratio()
        contains = 0.85 if (cand_norm in col_norm and len(cand_norm) > 3) else 0.0
        best = max(best, 0.55 * overlap + 0.45 * ratio, contains)
    # unit hint bonus (e.g. "Revenue (EUR m)" for revenue_meur)
    suffix = inp.name.rsplit("_", 1)[-1]
    if suffix in _UNIT_TOKENS and col_tokens & _UNIT_TOKENS[suffix]:
        best += 0.05
    return min(best, 0.97)


def suggest_mapping(
    columns: Iterable[str],
    inputs: Mapping[str, KPIInput],
    threshold: float = 0.6,
) -> dict[str, str]:
    """Return ``{input_name: column}`` for confident matches.

    Each column is used at most once (greedy assignment by descending score).
    """
    columns = [c for c in columns if str(c).strip()]
    scored: list[tuple[float, str, str]] = []
    for name, inp in inputs.items():
        for col in columns:
            s = _score(str(col), inp)
            if s >= threshold:
                scored.append((s, name, col))
    scored.sort(reverse=True)
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for _s, name, col in scored:
        if name in mapping or col in used:
            continue
        mapping[name] = col
        used.add(col)
    return mapping
