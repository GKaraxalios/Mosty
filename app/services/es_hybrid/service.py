"""Προσαρμογέας Mosty <-> ES hybrid kit.

- RAGChunk -> έγγραφο ES (SEARCH_FIELDS/INDEX_BODY στο es_store.py)
- index/remove ανά SKU (το SKU είναι το "έγγραφο", τα chunks του είναι τα chunks)
- search() με τα φίλτρα που έχει ήδη υπολογίσει το HybridSearchService (eligible chunk ids)

Το kit είναι synchronous· οι async wrappers τρέχουν σε thread (asyncio.to_thread).
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict
from typing import Any, Iterable

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import RAGChunk

logger = get_logger(__name__)

# Πάνω από αυτό το πλήθος eligible chunks δεν στέλνουμε chunk_id φίλτρο στο ES/NumPy
# (πολύ μεγάλο terms query)· κάνουμε over-fetch και post-filter από τον καλούντα.
PUSHDOWN_MAX_IDS = 5000


def enabled() -> bool:
    return bool(get_settings().ES_HYBRID_ENABLED)


def doc_key_for_sku(sku: str) -> str:
    """Σταθερό κλειδί εγγράφου (αποθηκεύεται στο πεδίο file_sha256 του kit)."""
    return hashlib.sha256(f"sku:{sku}".encode("utf-8")).hexdigest()


def _embedding_text(c: RAGChunk) -> str:
    parts: list[str] = []
    for p in (c.name, ", ".join(c.categories or []), c.meta_description, c.description_text, c.text):
        p = (p or "").strip()
        if p and p not in parts:
            parts.append(p)
    return "\n".join(parts)


def chunk_to_doc(c: RAGChunk) -> dict[str, Any]:
    """chunk_id = c.chunk_id: ήδη σταθερό hash (sha256(sku::index)[:16]) από utils/chunking.py."""
    emb_text = _embedding_text(c)
    return {
        "chunk_id": c.chunk_id,
        "content_hash": hashlib.sha256(emb_text.encode("utf-8")).hexdigest(),
        "file_sha256": doc_key_for_sku(c.sku),
        "sku": c.sku,
        "name": c.name,
        "url": c.url,
        "image_url": c.image_url,
        "categories": list(c.categories or []),
        "price": c.price,
        "stock_status": c.stock_status,
        "source": c.source,
        "updated_at": c.updated_at,
        "meta_keyword": c.meta_keyword,
        "meta_description": c.meta_description,
        "description_text": c.description_text,
        "text": c.text,
        "embedding_text": emb_text,
    }


# ── indexing (sync) ──────────────────────────────────────────────────────────

def _index_sku_sync(sku: str, chunks: list[RAGChunk]) -> dict[str, Any]:
    from . import es_store, indexer

    docs = [chunk_to_doc(c) for c in chunks]
    res = indexer.index_document(docs)
    # Αν το προϊόν έχει τώρα λιγότερα chunks, σβήσε τα παλιά από το ES (το shard
    # αντικαθίσταται ατομικά από το write_shard).
    keep = [d["chunk_id"] for d in docs]
    try:
        es_store.client().delete_by_query(
            index=es_store.config.ES_INDEX,
            body={"query": {"bool": {
                "filter": [{"term": {"file_sha256": doc_key_for_sku(sku)}}],
                "must_not": [{"terms": {"chunk_id": keep}}],
            }}},
            refresh=True, conflicts="proceed",
        )
    except Exception as exc:  # index may not exist yet / transient
        logger.warning("es_hybrid stale-chunk cleanup failed sku=%s: %s", sku, exc)
    return res


def index_chunks_sync(chunks: Iterable[RAGChunk]) -> dict[str, int]:
    by_sku: dict[str, list[RAGChunk]] = defaultdict(list)
    for c in chunks:
        by_sku[c.sku].append(c)
    ok = failed = embedded = 0
    for sku, cs in by_sku.items():
        r = _index_sku_sync(sku, cs)
        ok += r.get("es_ok", 0)
        failed += r.get("es_failed", 0)
        embedded += r.get("embedded", 0)
    return {"skus": len(by_sku), "es_ok": ok, "es_failed": failed, "embedded": embedded}


def remove_sku_sync(sku: str) -> dict[str, Any]:
    from . import indexer
    return indexer.remove_document(doc_key_for_sku(sku))


async def index_chunks(chunks: Iterable[RAGChunk]) -> None:
    """Best-effort: ένα σφάλμα στο ES ΔΕΝ πρέπει να χαλάει το υπάρχον sync."""
    if not enabled():
        return
    chunks = list(chunks)
    if not chunks:
        return
    try:
        r = await asyncio.to_thread(index_chunks_sync, chunks)
        logger.info("es_hybrid indexed %s", r)
    except Exception:
        logger.exception("es_hybrid indexing failed (non-fatal)")


async def remove_sku(sku: str) -> None:
    if not enabled():
        return
    try:
        await asyncio.to_thread(remove_sku_sync, sku)
    except Exception:
        logger.exception("es_hybrid remove failed sku=%s (non-fatal)", sku)


# ── search ───────────────────────────────────────────────────────────────────

def search_sync(
    query: str,
    top_k: int,
    eligible_ids: set[str] | None,
    total_chunks: int,
) -> dict[str, Any]:
    """Επιστρέφει το dict του hybrid.hybrid_search με fusion_score κανονικοποιημένο στο [0,1].

    eligible_ids=None -> χωρίς φίλτρο. Μικρό eligible σύνολο (<= PUSHDOWN_MAX_IDS) πηγαίνει ως
    φίλτρο chunk_id σε ES + NumPy (ακριβές). Μεγάλο -> over-fetch (ο καλών κάνει post-filter).
    """
    from . import config, hybrid

    filters = None
    keyword_size = vector_k = max(top_k * 3, 100)
    if eligible_ids is not None and len(eligible_ids) < total_chunks:
        if len(eligible_ids) <= PUSHDOWN_MAX_IDS:
            filters = {"chunk_id": sorted(eligible_ids)}
        else:
            keyword_size = vector_k = 200  # ES cap στο es_store.search_keyword

    out = hybrid.hybrid_search(query, top_k=max(top_k * 3, 60),
                               filters=filters, keyword_size=keyword_size, vector_k=vector_k)

    # RRF: μέγιστο θεωρητικό = 2/(k+1). Κανονικοποίηση ώστε να μένει στην ίδια κλίμακα
    # [0,1] με το legacy score (το rerank_service προσθέτει boosts 0.02-0.1).
    scale = (2.0 / (config.RRF_K + 1)) if config.HYBRID_FUSION != "weighted" else 1.0
    for r in out["results"]:
        r["fusion_score_norm"] = min(1.0, float(r.get("fusion_score", 0.0)) / scale)
    return out


async def search(query: str, top_k: int, eligible_ids: set[str] | None, total_chunks: int) -> dict[str, Any]:
    return await asyncio.to_thread(search_sync, query, top_k, eligible_ids, total_chunks)
