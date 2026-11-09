"""ES hybrid search (BM25 στο Elasticsearch + NumPy vector shards + RRF). Δες service.py."""
from .service import (  # noqa: F401
    chunk_to_doc,
    doc_key_for_sku,
    enabled,
    index_chunks,
    remove_sku,
    search,
)
