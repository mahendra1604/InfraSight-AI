"""Match extracted report entries to schedule activities.

1. Exact activity ID (written in the report)            -> confidence 1.0, status "id_match"
2. Semantic similarity (Sentence Transformers, cosine)  -> top-3 candidates
   fallback: Ollama embeddings -> lexical similarity
3. Validation decision (report section 2):
     score >= MATCH_HIGH_CONFIDENCE and unambiguous -> "suggested"   (pre-selected for the reviewer)
     score >= MATCH_LOW_CONFIDENCE                  -> "needs_mapping" (reviewer picks the activity)
     otherwise                                      -> "unmatched"   (not turned into a report)
Scores are raw similarities, not calibrated probabilities; see ml/eval_extraction.py.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

import httpx

from app.config import settings

log = logging.getLogger("infrasight.matching")

# "a" is deliberately NOT a stopword: it distinguishes "Block A" from "Block B".
STOPWORDS = {"the", "of", "and", "for", "to", "an", "in", "on", "at", "work", "works", "is", "now"}
SEMANTIC_WEIGHT = 0.75      # hybrid score = 0.75 x embedding cosine + 0.25 x token overlap
ID_CONFLICT_MARGIN = 0.15   # name matches another activity this much better than the written ID's activity


@dataclass
class MatchResult:
    activity_id: Optional[str]
    activity_name: Optional[str]
    score: float
    method: str
    status: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _tokens_match(x: str, y: str) -> bool:
    """Exact, or one word is a prefix of the other ('drain' / 'drainage'); single letters must be exact."""
    return x == y or (min(len(x), len(y)) >= 4 and (x.startswith(y) or y.startswith(x)))


def _normalise(text: str) -> str:
    return " ".join(_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS)


def lexical_similarity(a: str, b: str) -> float:
    a_n, b_n = _normalise(a), _normalise(b)
    if not a_n or not b_n:
        return 0.0
    ta, tb = set(a_n.split()), set(b_n.split())
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    common = sum(1 for t in small if any(_tokens_match(t, u) for u in large))
    jaccard = common / (len(ta) + len(tb) - common)
    containment = common / len(small)
    return round(0.4 * jaccard + 0.3 * containment + 0.3 * SequenceMatcher(None, a_n, b_n).ratio(), 4)


# ---------------------------------------------------------------------------
# Embedding backends
# ---------------------------------------------------------------------------
_st_model = None
_backend_choice: Optional[str] = None


def _st_encode(texts: list[str]) -> list[list[float]]:
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer

        _st_model = SentenceTransformer(settings.matcher_model, device="cpu")
    return _st_model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


def _ollama_encode(texts: list[str]) -> list[list[float]]:
    response = httpx.post(f"{settings.ollama_url}/api/embed", json={"model": settings.ollama_embed_model, "input": texts},
                          timeout=120, trust_env=False)
    response.raise_for_status()
    vectors = response.json()["embeddings"]
    out = []
    for v in vectors:
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


def _choose_backend() -> str:
    global _backend_choice
    wanted = settings.matcher_backend
    if wanted in {"sentence-transformers", "ollama", "lexical"}:
        return wanted
    if _backend_choice:
        return _backend_choice
    try:
        _st_encode(["probe"])
        _backend_choice = "sentence-transformers"
    except Exception as exc:
        log.warning("Sentence Transformers unavailable (%s); trying Ollama embeddings", exc)
        try:
            _ollama_encode(["probe"])
            _backend_choice = "ollama"
        except Exception as exc2:
            log.warning("Ollama embeddings unavailable (%s); using lexical matching", exc2)
            _backend_choice = "lexical"
    return _backend_choice


def backend_name() -> str:
    return _choose_backend()


def _encode(backend: str, texts: list[str]) -> list[list[float]]:
    return _st_encode(texts) if backend == "sentence-transformers" else _ollama_encode(texts)


_activity_cache: dict[tuple[str, tuple[str, ...]], list[list[float]]] = {}


class ActivityMatcher:
    def __init__(self, activities: list[tuple[str, str]]):
        """activities: [(activity_id, name), ...]"""
        self.activities = activities
        self.ids = {a_id.upper(): (a_id, name) for a_id, name in activities}
        self.backend = _choose_backend() if activities else "lexical"
        self.vectors: list[list[float]] | None = None
        if self.backend != "lexical" and activities:
            key = (self.backend, tuple(name for _, name in activities))
            try:
                if key not in _activity_cache:
                    _activity_cache[key] = _encode(self.backend, [name for _, name in activities])
                self.vectors = _activity_cache[key]
            except Exception as exc:
                log.warning("Embedding failed (%s); using lexical matching", exc)
                self.backend = "lexical"

    def _find_id(self, entry: dict[str, Any]) -> tuple[Optional[str], list[str]]:
        notes = []
        raw = (entry.get("activity_id") or "").strip().upper().replace(" ", "")
        if raw:
            if raw in self.ids:
                return self.ids[raw][0], notes
            notes.append(f"Activity ID {entry.get('activity_id')} from the report is not in the schedule.")
        haystack = f"{entry.get('activity_text', '')} {entry.get('evidence', '')}".upper()
        for key, (a_id, _) in self.ids.items():
            if re.search(rf"(?<![A-Z0-9]){re.escape(key)}(?![A-Z0-9])", haystack):
                return a_id, notes
        return None, notes

    def scores(self, text: str) -> list[tuple[str, str, float]]:
        lexical = [lexical_similarity(text, name) for _, name in self.activities]
        if self.backend != "lexical" and self.vectors is not None:
            try:
                query = _encode(self.backend, [text])[0]
                sims = [sum(q * v for q, v in zip(query, vec)) for vec in self.vectors]
                return [(a_id, name, round(SEMANTIC_WEIGHT * float(s) + (1 - SEMANTIC_WEIGHT) * lex, 4))
                        for (a_id, name), s, lex in zip(self.activities, sims, lexical)]
            except Exception as exc:
                log.warning("Embedding query failed (%s); using lexical matching", exc)
        return [(a_id, name, lex) for (a_id, name), lex in zip(self.activities, lexical)]

    def match(self, entry: dict[str, Any]) -> MatchResult:
        if not self.activities:
            return MatchResult(None, None, 0.0, "none", "unmatched", notes=["The schedule has no activities."])
        found_id, notes = self._find_id(entry)
        text = entry.get("activity_text") or entry.get("evidence") or ""
        ranked = sorted(self.scores(text), key=lambda item: item[2], reverse=True) if text else []
        candidates = [{"activity_id": a, "name": n, "score": s} for a, n, s in ranked[:3]]
        if found_id:
            name = self.ids[found_id.upper()][1]
            id_score = next((s for a, _, s in ranked if a == found_id), None)
            if ranked and id_score is not None and ranked[0][0] != found_id and ranked[0][2] - id_score >= ID_CONFLICT_MARGIN:
                best_id, best_name, best_score = ranked[0]
                notes.append(f"The report's ID {found_id} ({name}) does not fit the activity name '{text}', "
                             f"which matches {best_id} ({best_name}) better. Check before approving.")
                return MatchResult(best_id, best_name, best_score, f"{self.backend} (ID conflict)", "needs_mapping",
                                   candidates, notes)
            return MatchResult(found_id, name, 1.0, "activity_id", "id_match", candidates, notes)
        if not ranked:
            return MatchResult(None, None, 0.0, self.backend, "unmatched", candidates, notes + ["No activity text to match."])
        best_id, best_name, score = ranked[0]
        if entry.get("needs_review"):
            score = round(score * 0.95, 4)
        ambiguous = len(ranked) > 1 and ranked[0][2] - ranked[1][2] < 0.05
        if ambiguous:
            notes.append(f"Ambiguous: '{ranked[0][1]}' and '{ranked[1][1]}' score almost the same.")
        if score >= settings.match_high and not ambiguous:
            status = "suggested"
        elif score >= settings.match_low:
            status = "needs_mapping"
        else:
            status = "unmatched"
        return MatchResult(best_id, best_name, score, self.backend, status, candidates, notes)
