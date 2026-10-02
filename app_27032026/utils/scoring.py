"""BM25-inspired sparse scoring and utility functions for hybrid retrieval."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from app.models.schemas import RAGChunk

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


class BM25Scorer:
    """Lightweight in-memory BM25 scorer over RAGChunk texts."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._doc_freqs: Counter[str] = Counter()
        self._doc_lens: list[int] = []
        self._avg_dl: float = 0.0
        self._n_docs: int = 0
        self._corpus_tokens: list[Counter[str]] = []
        self._chunk_ids: list[str] = []

    def build(self, chunks: list[RAGChunk]) -> None:
        self._doc_freqs.clear()
        self._doc_lens.clear()
        self._corpus_tokens.clear()
        self._chunk_ids.clear()

        for chunk in chunks:
            tokens = tokenize(chunk.text + " " + chunk.name)
            tf = Counter(tokens)
            self._corpus_tokens.append(tf)
            self._doc_lens.append(len(tokens))
            self._chunk_ids.append(chunk.chunk_id)
            for term in set(tokens):
                self._doc_freqs[term] += 1

        self._n_docs = len(chunks)
        raw_avg = (
            sum(self._doc_lens) / self._n_docs if self._n_docs else 1.0
        )
        # Avoid division by zero when every document tokenizes to length 0
        self._avg_dl = max(raw_avg, 1e-9)

    def score(self, query: str) -> dict[str, float]:
        """Return {chunk_id: bm25_score} for all indexed documents."""
        query_tokens = tokenize(query)
        scores: dict[str, float] = {}

        for idx, (tf_doc, dl) in enumerate(
            zip(self._corpus_tokens, self._doc_lens)
        ):
            s = 0.0
            for qt in query_tokens:
                if qt not in self._doc_freqs:
                    continue
                df = self._doc_freqs[qt]
                idf = math.log(
                    (self._n_docs - df + 0.5) / (df + 0.5) + 1.0
                )
                freq = tf_doc.get(qt, 0)
                numerator = freq * (self._k1 + 1)
                denominator = freq + self._k1 * (
                    1 - self._b + self._b * dl / self._avg_dl
                )
                s += idf * numerator / denominator
            scores[self._chunk_ids[idx]] = s

        return scores

    @property
    def n_docs(self) -> int:
        return self._n_docs


def normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    """Min-max normalize scores to [0, 1]."""
    if not scores:
        return scores
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    span = hi - lo
    if span == 0:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / span for k, v in scores.items()}
