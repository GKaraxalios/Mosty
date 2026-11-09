"""Elasticsearch: index/mapping, bulk upsert, keyword (BM25) search, deletes.

Μεταφέρει τη λογική του smartDocAI: custom analyzer (lowercase+asciifolding,
ώστε τα ελληνικά με/χωρίς τόνους να ταιριάζουν), idempotent bulk με _id=chunk_id,
retry σε transient σφάλματα, server-side timeout, highlight προαιρετικό.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from elasticsearch import Elasticsearch, NotFoundError
from elasticsearch.helpers import bulk

from . import config

log = logging.getLogger(__name__)

TRANSIENT_STATUSES = {429, 502, 503, 504}

# Πεδία αναζήτησης για τα chunks του Mosty (Magento προϊόντα). (name, boost, highlight)
# Το `text` είναι συνήθως πολύ σύντομο ("Product Name: ..."), το πραγματικό περιεχόμενο
# είναι σε description_text/meta_*, γι' αυτό μπαίνουν όλα.
SEARCH_FIELDS: Tuple[Tuple[str, float, bool], ...] = (
    ("text", 4.0, True),
    ("name", 3.0, False),
    ("meta_keyword", 2.0, False),
    ("categories", 1.5, False),
    ("description_text", 1.5, False),
    ("meta_description", 1.0, False),
)

INDEX_BODY: Dict[str, Any] = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "mosty_text": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                }
            }
        },
    },
    "mappings": {
        # Το διάνυσμα αποθηκεύεται για knn αλλά δεν επιστρέφεται στα hits (μεγάλο payload).
        "_source": {"excludes": ["embedding"]},
        "properties": {
            "chunk_id": {"type": "keyword"},
            "content_hash": {"type": "keyword"},
            # file_sha256 = κλειδί εγγράφου (sha256 του sku) -> χρησιμοποιείται από
            # indexer.remove_document / vector_store.doc_key χωρίς αλλαγή στη λογική του kit.
            "file_sha256": {"type": "keyword"},
            "sku": {"type": "keyword"},
            "name": {"type": "text", "analyzer": "mosty_text",
                     "fields": {"raw": {"type": "keyword", "ignore_above": 256}}},
            "url": {"type": "keyword"},
            "image_url": {"type": "keyword", "index": False},
            "categories": {"type": "text", "analyzer": "mosty_text",
                           "fields": {"raw": {"type": "keyword", "ignore_above": 256}}},
            "price": {"type": "float"},
            "stock_status": {"type": "keyword"},
            "source": {"type": "keyword"},
            "updated_at": {"type": "date", "ignore_malformed": True},
            "meta_keyword": {"type": "text", "analyzer": "mosty_text"},
            "meta_description": {"type": "text", "analyzer": "mosty_text"},
            "description_text": {"type": "text", "analyzer": "mosty_text"},
            "text": {"type": "text", "analyzer": "mosty_text",
                     "fields": {"raw": {"type": "keyword", "ignore_above": 256}}},
            # Μόνο για embeddings (δεν αναζητείται στο ES)· το διαβάζει το embeddings.prepare_input.
            "embedding_text": {"type": "text", "analyzer": "mosty_text", "index": False},
            "indexed_at": {"type": "date"},
            # Dense leg στο ES (ES_VECTOR_BACKEND=es). L2-normalized -> cosine.
            "embedding": {"type": "dense_vector", "dims": config.EMBEDDING_DIMS,
                          "index": True, "similarity": "cosine"},
        }
    },
}

_client: Optional[Elasticsearch] = None


def client() -> Elasticsearch:
    global _client
    if _client is None:
        kw: Dict[str, Any] = {
            "hosts": [config.ES_URL],
            "verify_certs": config.ES_VERIFY_SSL,
            "request_timeout": config.ES_REQUEST_TIMEOUT_S,
        }
        if config.ES_API_KEY:
            kw["api_key"] = config.ES_API_KEY
        elif config.ES_USER:
            kw["basic_auth"] = (config.ES_USER, config.ES_PASSWORD)
        _client = Elasticsearch(**kw)
    return _client


_mapping_checked = False


def _check_vector_mapping(c: Elasticsearch) -> None:
    """Αν το index υπάρχει από πριν χωρίς dense_vector, το knn θα αποτύχει: φώναξε νωρίς."""
    global _mapping_checked
    if _mapping_checked or config.VECTOR_BACKEND != "es":
        return
    _mapping_checked = True
    try:
        props = next(iter(c.indices.get_mapping(index=config.ES_INDEX).values()))["mappings"].get("properties", {})
        if props.get("embedding", {}).get("type") != "dense_vector":
            log.error("index %s has no dense_vector 'embedding' mapping -> use a new index name "
                      "or reindex (ES_VECTOR_BACKEND=es needs it)", config.ES_INDEX)
    except Exception as exc:
        log.warning("mapping check failed: %s", exc)


def ensure_index() -> bool:
    c = client()
    if c.indices.exists(index=config.ES_INDEX):
        _check_vector_mapping(c)
        return False
    c.indices.create(index=config.ES_INDEX, body=INDEX_BODY)
    log.info("created index %s", config.ES_INDEX)
    return True


def delete_index() -> None:
    try:
        client().indices.delete(index=config.ES_INDEX)
    except NotFoundError:
        pass


def bulk_upsert_chunks(docs: List[Dict[str, Any]], refresh: str = "wait_for") -> Tuple[int, int]:
    """Idempotent: _id = chunk_id. Επιστρέφει (ok, failed). Το `embedding` (αν υπάρχει) αποθηκεύεται
    μόνο όταν VECTOR_BACKEND=es· αλλιώς αφαιρείται."""
    if not docs:
        return 0, 0
    ensure_index()
    seen: set = set()
    actions: List[Dict[str, Any]] = []
    for d in docs:
        cid = str(d.get("chunk_id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        drop = ("vector", "dense_vector") + (() if config.VECTOR_BACKEND == "es" else ("embedding",))
        body = {k: v for k, v in d.items() if k not in drop and v is not None}
        body["indexed_at"] = datetime.now(tz=timezone.utc).isoformat()
        actions.append({"_op_type": "index", "_index": config.ES_INDEX, "_id": cid, "_source": body})

    ok_total, pending, failed = 0, actions, 0
    for attempt in range(3):
        ok, errs = bulk(client(), pending, refresh=refresh,
                        raise_on_error=False, raise_on_exception=False)
        ok_total += int(ok or 0)
        errs = errs if isinstance(errs, list) else []
        if not errs:
            failed = 0
            break
        retry_ids = set()
        failed = 0
        for item in errs:
            info = next(iter(item.values())) if isinstance(item, dict) and item else {}
            if info.get("status") in TRANSIENT_STATUSES:
                retry_ids.add(info.get("_id"))
            else:
                failed += 1
        if retry_ids and attempt < 2:
            pending = [a for a in pending if a["_id"] in retry_ids]
            time.sleep(1.0 + attempt)
            continue
        failed += len(retry_ids)
        break
    log.info("[CHUNK_INDEX] ok=%d failed=%d", ok_total, failed)
    return ok_total, failed


def _weighted_fields() -> List[str]:
    return [n if b == 1.0 else f"{n}^{b:g}" for n, b, _ in SEARCH_FIELDS]


def _compact(q: str, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(q or "")).strip()[:limit]


def _build_should(query: str) -> List[Dict[str, Any]]:
    q = _compact(query)
    if not q:
        return []
    fields = _weighted_fields()
    should: List[Dict[str, Any]] = [
        {"multi_match": {"query": q, "fields": fields, "type": "best_fields",
                         "fuzziness": "AUTO", "operator": "or"}},
        {"multi_match": {"query": q, "fields": fields, "type": "cross_fields",
                         "operator": "and", "boost": 1.8}},
        {"match": {"text": {"query": q, "operator": "and", "boost": 2.5}}},
    ]
    for term in re.findall(r'"([^"]+)"', query or ""):  # "quoted phrases" -> phrase boost
        should.append({"match_phrase": {"text": {"query": term, "boost": 10.0}}})
    if len(q.split()) <= 14:
        should.append({"match_phrase": {"text": {"query": q, "boost": 5.0}}})
    return should


def _filters(filters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for k, v in (filters or {}).items():
        if v in (None, "", []):
            continue
        out.append({"terms": {k: list(v)}} if isinstance(v, (list, tuple, set)) else {"term": {k: v}})
    return out


def search_keyword(query: str, size: int = 50, filters: Optional[Dict[str, Any]] = None,
                   highlight: bool = False) -> List[Dict[str, Any]]:
    """BM25. highlight=False (default) γιατί το highlight είναι το ακριβό κομμάτι
    (~9-28s vs ~0.1s για size=120)· πάρε snippets μόνο για τα τελικά hits."""
    should = _build_should(query)
    if not should:
        return []
    ensure_index()
    bool_q: Dict[str, Any] = {"should": should, "minimum_should_match": 1}
    f = _filters(filters)
    if f:
        bool_q["filter"] = f
    body: Dict[str, Any] = {
        "query": {"bool": bool_q},
        "size": min(size, 200),
        "timeout": f"{config.ES_SEARCH_TIMEOUT_S}s",  # partial results αντί για 504
    }
    if highlight:
        body["highlight"] = {"fields": {n: {"fragment_size": 240, "number_of_fragments": 2}
                                        for n, _, h in SEARCH_FIELDS if h}}
    try:
        r = client().options(
            request_timeout=min(config.ES_SEARCH_TIMEOUT_S + 5, config.ES_REQUEST_TIMEOUT_S)
        ).search(index=config.ES_INDEX, body=body)
    except NotFoundError:
        return []
    hits = []
    for h in r.get("hits", {}).get("hits", []):
        src = h.get("_source", {}) or {}
        src["_es_score"] = float(h.get("_score") or 0.0)
        src["_id"] = h.get("_id")
        hl = h.get("highlight") or {}
        if hl:
            src["highlight"] = " … ".join(s for frags in hl.values() for s in frags)
        hits.append(src)
    return hits


def _delete_by_term(field: str, value: str) -> int:
    if not value:
        return 0
    try:
        r = client().delete_by_query(index=config.ES_INDEX, body={"query": {"term": {field: value}}},
                                     refresh=True, conflicts="proceed")
        return int(r.get("deleted", 0))
    except NotFoundError:
        return 0


def delete_by_file_sha256(sha: str) -> int:
    return _delete_by_term("file_sha256", sha)


def delete_by_job_id(job_id: str) -> int:
    return _delete_by_term("job_id", job_id)


def search_vector(q_emb: Any, k: int = 80, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Dense leg: ES knn (cosine). q_emb: (1, d) ή (d,) L2-normalized. Επιστρέφει hits best-first
    με `_es_score` (cosine-derived, (1+cos)/2 στο ES)."""
    import numpy as np
    qv = np.asarray(q_emb, dtype="float32").reshape(-1).tolist()
    ensure_index()
    k = max(1, min(int(k), 200))
    knn: Dict[str, Any] = {"field": "embedding", "query_vector": qv, "k": k,
                           "num_candidates": min(max(k * 4, 200), 10000)}
    f = _filters(filters)
    if f:
        knn["filter"] = f
    body = {"knn": knn, "size": k, "timeout": f"{config.ES_SEARCH_TIMEOUT_S}s"}
    try:
        r = client().options(
            request_timeout=min(config.ES_SEARCH_TIMEOUT_S + 5, config.ES_REQUEST_TIMEOUT_S)
        ).search(index=config.ES_INDEX, body=body)
    except NotFoundError:
        return []
    hits = []
    for h in r.get("hits", {}).get("hits", []):
        src = h.get("_source", {}) or {}
        src["_es_score"] = float(h.get("_score") or 0.0)
        src["_id"] = h.get("_id")
        hits.append(src)
    return hits
