"""End-to-end smoke test του ES hybrid (προσαρμογή του smoke_test.py του kit στο schema Mosty).

    ES_EMBEDDING_PROVIDER=none python scripts/es_hybrid_smoke_test.py      # μόνο keyword
    python scripts/es_hybrid_smoke_test.py                                 # + Vertex embeddings

Χρησιμοποιεί ΞΕΧΩΡΙΣΤΟ index (ELASTICSEARCH_INDEX=mosty_chunks_smoke) και cache dir (tmp),
και τα σβήνει στο τέλος· δεν αγγίζει τα πραγματικά δεδομένα.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ELASTICSEARCH_INDEX", "mosty_chunks_smoke")
_tmp = tempfile.mkdtemp(prefix="es_hybrid_smoke_")
os.environ["ES_HYBRID_CACHE_DIR"] = _tmp

from app.models.schemas import RAGChunk  # noqa: E402
from app.services.es_hybrid import es_store, hybrid, indexer, service  # noqa: E402

chunks = [
    RAGChunk(chunk_id="c1", sku="SMK-1", name="Μπλούζα βαμβακερή ριγέ", categories=["Γυναικεία", "Μπλούζες"],
             price=29.9, text="Product Name: Μπλούζα βαμβακερή ριγέ",
             description_text="Καλοκαιρινή μπλούζα από 100% βαμβάκι με ριγέ σχέδιο."),
    RAGChunk(chunk_id="c2", sku="SMK-2", name="Sneakers λευκά", categories=["Παπούτσια"],
             price=59.0, text="Product Name: Sneakers λευκά",
             description_text="Αθλητικά παπούτσια καθημερινής χρήσης, λευκό χρώμα."),
    RAGChunk(chunk_id="c3", sku="SMK-3", name="Scrunchie με print", categories=["Αξεσουάρ Μαλλιών"],
             price=5.0, text="Product Name: Scrunchie με print",
             description_text="Scrunchie μαλλιών με μοναδικό print."),
]
try:
    print(json.dumps(service.index_chunks_sync(chunks), ensure_ascii=False, indent=2))
    for q in ("μπλουζα βαμβακι", "λευκα παπουτσια", "scrunchie"):
        out = service.search_sync(q, top_k=3, eligible_ids=None, total_chunks=len(chunks))
        print(f"\n== {q!r}  degraded={out['degraded']} kw={out['keyword_hits']} vec={out['vector_hits']}")
        for r in out["results"]:
            print(f"  {r['chunk_id']} {r['name']!r} score={r['fusion_score_norm']:.3f} src={r['__src']}")
    # pushdown φίλτρο: μόνο c2
    out = service.search_sync("λευκα", top_k=3, eligible_ids={"c2"}, total_chunks=3)
    assert [r["chunk_id"] for r in out["results"]] == ["c2"], out["results"]
    # διαγραφή
    print("\nremove:", service.remove_sku_sync("SMK-1"))
finally:
    try:
        es_store.delete_index()
    except Exception as exc:
        print("cleanup skipped:", exc)
    shutil.rmtree(_tmp, ignore_errors=True)
