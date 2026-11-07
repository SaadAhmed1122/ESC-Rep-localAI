"""Scoring of qualitative (narrative) KPIs.

Two complementary local methods:

* **Similarity** — embed the narrative and the best / worst exemplars; the score
  is the relative closeness to the best exemplar (0..1).
* **Rubric (LLM judge)** — optional; asks the local LLM to grade the narrative
  against a rubric and return JSON. Used by the agents for richer feedback.
"""

from __future__ import annotations

import json

from esglocal.embeddings import Embedder, HashingEmbedder, cosine

RUBRIC = """Grade the company's ESG disclosure on a 0-100 scale using this rubric:
- Policy & commitment (0-25): formal, board-approved, aligned to recognised frameworks
- Targets & metrics (0-25): quantified, time-bound, baseline stated
- Implementation (0-25): processes, coverage, resources, accountability
- Transparency & assurance (0-25): disclosure of results, third-party assurance
Return ONLY JSON: {"score": <int>, "strengths": [..], "gaps": [..]}"""


class NarrativeScorer:
    def __init__(self, embedder: Embedder | None = None):
        self.embedder = embedder or HashingEmbedder()
        self._cache: dict[str, object] = {}

    def _vec(self, text: str):
        if text not in self._cache:
            self._cache[text] = self.embedder.embed([text])[0]
        return self._cache[text]

    def __call__(self, text: str, best: str, worst: str) -> float:
        """Return 0..1 — 1 means as close to the best exemplar as possible."""
        if not text.strip():
            return 0.0
        v = self._vec(text)
        sim_best = cosine(v, self._vec(best))
        sim_worst = cosine(v, self._vec(worst))
        # relative closeness using non-negative cosine similarity
        b, w = max(sim_best, 0.0), max(sim_worst, 0.0)
        if b + w == 0:
            return 0.5
        return max(0.0, min(1.0, b / (b + w)))


def judge_with_llm(llm, text: str, kpi_name: str) -> dict:
    """Ask the local LLM to grade a narrative. Returns {} on failure."""
    messages = [
        {"role": "system", "content": RUBRIC},
        {"role": "user", "content": f"Disclosure topic: {kpi_name}\n\nDisclosure:\n{text}"},
    ]
    try:
        reply = llm.chat(messages, json_mode=True)
        data = json.loads(reply.content)
        data["score"] = max(0, min(100, int(data.get("score", 0))))
        return data
    except Exception:  # noqa: BLE001 - malformed model output is not fatal
        return {}
