"""Best-effort PII redaction for text that is sent to the (local) LLM or stored.

Even though the model is local, minimising personal data in prompts and in the
document index is good practice (GDPR data minimisation). Redaction is
pattern-based and will not catch everything — it is a safety net, not a
guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("IP", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    # Phone numbers need an explicit "+<country code>" or "(area) nnn-nnnn" shape so that
    # ordinary ESG figures such as "2023 1500 2000" are not redacted.
    (
        "PHONE",
        re.compile(
            r"(?<!\w)(?:\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}"
            r"|\(\d{3}\)\s?\d{3}[\s.-]\d{4})(?!\w)"
        ),
    ),
]


def _luhn_ok(digits: str) -> bool:
    nums = [int(d) for d in digits if d.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def redact(text: str) -> RedactionResult:
    counts: dict[str, int] = {}

    for label, pattern in _PATTERNS:

        def _sub(m: re.Match[str], label: str = label) -> str:
            if label == "CARD" and not _luhn_ok(m.group(0)):
                return m.group(0)
            counts[label] = counts.get(label, 0) + 1
            return f"[{label}]"

        text = pattern.sub(_sub, text)
    return RedactionResult(text=text, counts=counts)
