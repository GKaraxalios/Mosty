"""Pipeline ευρετηρίασης ενός εγγράφου: embed -> shard + ES (idempotent)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from . import config
from . import embeddings
from . import es_store
from . import vector_store

log = logging.getLogger(__name__)


def index_document(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """chunks: dicts με τουλάχιστον chunk_id, text, file_name (+file_sha256).
    Πάντα γράφει στο ES (keyword). Αν αποτύχουν τα embeddings -> keyword-only."""
    if not chunks:
        return {"indexed": 0}
    key = vector_store.doc_key(chunks[0])
    rows, valid = embeddings.embed_chunks(chunks)
    if rows and config.VECTOR_BACKEND == "es":
        # (Mosty) διάνυσμα μέσα στο ES doc (dense_vector). Chunks χωρίς embedding μένουν keyword-only.
        by_id = {str(c.get("chunk_id")): r for c, r in zip(valid, rows)}
        chunks = [dict(c, embedding=by_id[str(c.get("chunk_id"))].tolist())
                  if str(c.get("chunk_id")) in by_id else c for c in chunks]
    elif rows:
        vector_store.write_shard(key, valid, rows)
    else:
        log.warning("no embeddings for %s -> keyword-only", key)
    ok, failed = es_store.bulk_upsert_chunks(chunks)
    return {"doc_key": key, "es_ok": ok, "es_failed": failed,
            "embedded": len(rows), "search_status": "hybrid" if rows else "keyword_only", "vector_backend": config.VECTOR_BACKEND}


def remove_document(file_sha256: str) -> Dict[str, Any]:
    return {"es_deleted": es_store.delete_by_file_sha256(file_sha256),
            "shard_removed": vector_store.delete_shard(file_sha256)}
