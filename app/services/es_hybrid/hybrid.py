"""Fusion (RRF / weighted) + hybrid_search με graceful degradation σε keyword-only."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from . import config
from . import embeddings
from . import es_store
from . import vector_store

log = logging.getLogger(__name__)


def reciprocal_rank_fusion(ranked: List[List[str]], k: int = 60) -> List[Tuple[str, float]]:
    scores: Dict[str, float] = {}
    for lst in ranked:
        for rank, did in enumerate(lst):
            if did:
                scores[str(did)] = scores.get(str(did), 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])


def weighted_score_fusion(a: List[Tuple[str, float]], b: List[Tuple[str, float]],
                          w_a: float = 0.4, w_b: float = 0.6) -> List[Tuple[str, float]]:
    m1 = max((s for _, s in a), default=0.0) or 1.0
    m2 = max((s for _, s in b), default=0.0) or 1.0
    na, nb = {i: s / m1 for i, s in a}, {i: s / m2 for i, s in b}
    out = [(i, w_a * na.get(i, 0.0) + w_b * nb.get(i, 0.0)) for i in set(na) | set(nb)]
    return sorted(out, key=lambda x: -x[1])


def hybrid_search(query: str, top_k: int = 10, filters: Optional[Dict[str, Any]] = None,
                  keyword_size: int = 80, vector_k: int = 80) -> Dict[str, Any]:
    es_hits = es_store.search_keyword(query, size=keyword_size, filters=filters, highlight=False)

    vec: List[Tuple[str, float, int]] = []
    vec_hits: List[Dict[str, Any]] = []
    degraded = None
    try:
        q_emb = embeddings.embed_query(query)
        if config.VECTOR_BACKEND == "es":  # (Mosty) dense leg = ES knn
            vec_hits = es_store.search_vector(q_emb, k=vector_k, filters=filters)
            vec = [(str(h.get("chunk_id") or h.get("_id")), h["_es_score"], i) for i, h in enumerate(vec_hits)]
        else:
            vec = vector_store.search_vector(q_emb, k=vector_k, filters=filters)
    except Exception as exc:  # timeout/provider down -> keyword-only
        degraded = f"vector leg skipped: {exc}"
        log.warning("[hybrid] %s", degraded)

    es_ids = [str(h.get("chunk_id") or h.get("_id")) for h in es_hits]
    if config.HYBRID_FUSION == "weighted":
        fused = weighted_score_fusion(
            [(str(h.get("chunk_id") or h.get("_id")), h["_es_score"]) for h in es_hits],
            [(cid, s) for cid, s, _ in vec], config.KEYWORD_WEIGHT, config.VECTOR_WEIGHT)
    else:
        fused = reciprocal_rank_fusion([es_ids, [c for c, _, _ in vec]], k=config.RRF_K)

    if config.VECTOR_BACKEND == "es":
        by_id: Dict[str, Dict[str, Any]] = {str(h.get("chunk_id") or h.get("_id")): h for h in vec_hits}
    else:
        mat, chunks = vector_store.load_all()
        by_id = {str(c.get("chunk_id")): c for c in chunks}
    for h in es_hits:  # το ES doc υπερισχύει (φρέσκο metadata)
        by_id[str(h.get("chunk_id") or h.get("_id"))] = h
    es_set = set(es_ids)
    results = []
    for cid, score in fused[:top_k]:
        row = dict(by_id.get(cid) or {"chunk_id": cid})
        row.pop("embedding", None)
        row["fusion_score"] = score
        row["__src"] = "both" if cid in es_set and cid in {c for c, _, _ in vec} else (
            "keyword" if cid in es_set else "vector")
        results.append(row)
    return {"results": results, "degraded": degraded,
            "keyword_hits": len(es_hits), "vector_hits": len(vec)}
