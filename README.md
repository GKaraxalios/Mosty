# MostyAPI v2

Advanced RAG commerce platform — REST API proxy + AI shopping assistant for e-shop products.

**Data source:** Magento GraphQL (`https://mosty.com/graphql`)
**AI backend:** Google Vertex AI (embeddings, vector search, generative answers)

---

## Features

- **REST API proxy** — normalized product data from Magento GraphQL
- **Hybrid search** — dense (Vertex AI vectors) + sparse (BM25) retrieval
- **Reranking** — pluggable heuristic/LLM reranker pipeline
- **Conversational memory** — session-based shopping assistant with follow-up understanding
- **Multilingual** — Greek & English shopping assistant with language auto-detection
- **Background jobs** — async job registry for non-blocking sync operations
- **Scheduled ingestion** — APScheduler for full + incremental sync
- **Caching** — in-memory TTL cache with namespace support (Redis-ready abstraction)
- **Prompt orchestration** — separate prompts for search, recommend, compare, follow-up

## Architecture

```
Magento GraphQL → FastAPI ingestion → chunking + embeddings → Vertex AI Vector Search
                                                                      ↓
                                              BM25 sparse index ← chunk store (in-memory)
                                                                      ↓
                                              hybrid merge + metadata filter + reranking
                                                                      ↓
FastAPI chat/retrieval API ← Vertex AI generative answer ← retrieval pipeline
         ↑
  session memory + query rewriting + multilingual prompts
```

## Quick start

```bash
cd MostyAPI
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Edit with your values
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` for interactive API docs.

---

## Vertex AI setup (one-time)

```bash
python -m scripts.create_vertex_index
# Copy VERTEX_INDEX_ID, VERTEX_INDEX_ENDPOINT_ID, VERTEX_DEPLOYED_INDEX_ID to .env
```

---

## Incremental index (cron)

New products and updates from Magento are synced to the AI index **incrementally** via a cron job, without full reindexes:

```bash
# Every 10 minutes (example)
*/10 * * * * cd /path/to/MostyAPI && .venv/bin/python scripts/update_index.py >> /var/log/mosty_index_update.log 2>&1
```

- **Lock file** prevents overlapping runs.
- **Sync state** (`data/sync_state.json`) stores last successful sync; only new/updated products are processed.
- **Out-of-stock** products are removed from the index so the AI never recommends them.
- **Health check**: `python -m scripts.health_check_index` (or `--json`).

See **[docs/INCREMENTAL_INDEX.md](docs/INCREMENTAL_INDEX.md)** for architecture, configuration, testing, and rollback.

---

## API endpoints

All endpoints require header `x-mosty-api-key` (unless `MOSTY_RAG_API_KEY` is empty).

### Products (REST proxy)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/products` | List products (paginated, filterable) |
| GET | `/api/v1/products/{sku}` | Product detail |
| GET | `/api/v1/products/{sku}/live` | Live price/stock |

### RAG

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/rag/sync` | Full catalog sync (blocking) |
| POST | `/api/v1/rag/sync/{sku}` | Single product sync (blocking) |
| DELETE | `/api/v1/rag/product/{sku}` | Remove product from index |
| POST | `/api/v1/rag/search` | Semantic search (with reranking) |
| POST | `/api/v1/rag/ask` | RAG Q&A with grounded answer |
| POST | `/api/v1/rag/hybrid-search` | Hybrid dense+sparse search with filters |

### Background Jobs

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/rag/jobs/sync-all` | Start full sync as background job |
| POST | `/api/v1/rag/jobs/sync-product/{sku}` | Start single sync as background job |
| GET | `/api/v1/rag/jobs` | List all jobs |
| GET | `/api/v1/rag/jobs/{job_id}` | Get job status |
| POST | `/api/v1/rag/jobs/{job_id}/cancel` | Cancel a running job |

### Scheduler

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/rag/scheduler/status` | Scheduler status & next runs |
| POST | `/api/v1/rag/scheduler/run-now/full` | Trigger full sync immediately |
| POST | `/api/v1/rag/scheduler/run-now/incremental` | Trigger incremental sync immediately |

### Chat / AI Assistant

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/chat/session` | Create chat session |
| GET | `/api/v1/chat/session/{id}` | Get session details |
| DELETE | `/api/v1/chat/session/{id}` | Delete session |
| POST | `/api/v1/chat/ask` | Conversational Q&A (with memory) |
| POST | `/api/v1/chat/search` | Conversational search |
| POST | `/api/v1/chat/recommend` | Recommendation-focused answer |
| POST | `/api/v1/chat/compare` | Compare products by SKU |

### Health

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Health check + feature flags |
| GET | `/` | Root info |

---

## Example curl commands

### Products

```bash
# List products
curl -s -H "x-mosty-api-key: super_secret_key" \
  "http://localhost:8000/api/v1/products?page=1&per_page=10&search=φόρεμα"

# Product by SKU
curl -s -H "x-mosty-api-key: super_secret_key" \
  "http://localhost:8000/api/v1/products/BLOOMSCR"

# Live price/stock
curl -s -H "x-mosty-api-key: super_secret_key" \
  "http://localhost:8000/api/v1/products/BLOOMSCR/live"
```

### RAG sync & search

```bash
# Full sync (blocking)
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  "http://localhost:8000/api/v1/rag/sync"

# Full sync (background job)
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  "http://localhost:8000/api/v1/rag/jobs/sync-all"

# Check job status
curl -s -H "x-mosty-api-key: super_secret_key" \
  "http://localhost:8000/api/v1/rag/jobs"

# Semantic search
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "μπεζ φόρεμα για καλοκαίρι", "top_k": 5}' \
  "http://localhost:8000/api/v1/rag/search"

# Hybrid search with filters
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "μπεζ καλοκαιρινό φόρεμα", "top_k": 10, "filters": {"min_price": 50, "max_price": 250}}' \
  "http://localhost:8000/api/v1/rag/hybrid-search"

# RAG Q&A
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "Ποιο φόρεμα προτείνεις για καλοκαιρινό γάμο;", "top_k": 5, "temperature": 0.2}' \
  "http://localhost:8000/api/v1/rag/ask"
```

### Chat (with conversational memory)

```bash
# Create session
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  "http://localhost:8000/api/v1/chat/session?language=el"

# Chat ask (1st message)
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "SESSION_ID", "query": "Θέλω ένα φόρεμα για γάμο"}' \
  "http://localhost:8000/api/v1/chat/ask"

# Chat ask (follow-up — system understands context)
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "SESSION_ID", "query": "Σε μπεζ και κάτω από 200 ευρώ"}' \
  "http://localhost:8000/api/v1/chat/ask"

# Recommendations
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "casual summer outfit under 100 euros", "language": "en"}' \
  "http://localhost:8000/api/v1/chat/recommend"

# Compare products
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  -H "Content-Type: application/json" \
  -d '{"skus": ["BLOOMSCR", "HYDRANGEASCR"], "language": "el"}' \
  "http://localhost:8000/api/v1/chat/compare"
```

### Scheduler

```bash
# Check scheduler status
curl -s -H "x-mosty-api-key: super_secret_key" \
  "http://localhost:8000/api/v1/rag/scheduler/status"

# Trigger full sync now
curl -s -X POST -H "x-mosty-api-key: super_secret_key" \
  "http://localhost:8000/api/v1/rag/scheduler/run-now/full"
```

---

## Project structure

```
MostyAPI/
├── app/
│   ├── main.py                          # FastAPI app factory + scheduler lifecycle
│   ├── core/
│   │   ├── config.py                    # All settings from .env
│   │   └── logging.py                   # Structured logging
│   ├── models/
│   │   └── schemas.py                   # 40+ Pydantic schemas
│   ├── routers/
│   │   ├── health.py                    # Health + feature flags
│   │   ├── products.py                  # Product REST endpoints
│   │   ├── rag.py                       # RAG + hybrid search endpoints
│   │   ├── jobs.py                      # Background job endpoints
│   │   ├── scheduler.py                 # Scheduler control endpoints
│   │   └── chat.py                      # Chat / assistant endpoints
│   ├── services/
│   │   ├── auth_service.py              # API key verification
│   │   ├── cache_service.py             # In-memory TTL cache (Redis-ready)
│   │   ├── chat_service.py              # Chat orchestration
│   │   ├── graphql_service.py           # Magento GraphQL client (cached)
│   │   ├── hybrid_search_service.py     # Dense + sparse + metadata filtering
│   │   ├── incremental_index_service.py # Incremental add/update/remove index
│   │   ├── sync_state_service.py        # Sync state persistence (last_sync_at)
│   │   ├── job_service.py               # Background job registry & worker
│   │   ├── memory_service.py            # Session conversational memory
│   │   ├── product_service.py           # Product business logic
│   │   ├── rag_ingestion_service.py     # RAG ingestion pipeline
│   │   ├── rag_query_service.py         # RAG search & Q&A (cached)
│   │   ├── rerank_service.py            # Pluggable reranker interface
│   │   ├── scheduler_service.py         # APScheduler integration
│   │   └── vertex_service.py            # Vertex AI client
│   └── utils/
│       ├── chunking.py                  # Document chunking
│       ├── language.py                  # Language detection
│       ├── prompt_builder.py            # Multilingual prompt templates
│       ├── scoring.py                   # BM25 scorer
│       └── transformers.py              # Data normalization
├── docs/
│   └── INCREMENTAL_INDEX.md             # Incremental index architecture & cron
├── scripts/
│   ├── create_vertex_index.py           # One-time Vertex index setup
│   ├── update_index.py                  # Cron: incremental index update (lock, safe)
│   ├── health_check_index.py            # Sync status & counts
│   ├── sync_all_products.py             # CLI full sync trigger
│   └── run_incremental_sync.py          # CLI incremental sync trigger
├── .env.example
├── requirements.txt
└── README.md
```

---

## Abstraction layers (ready for production swap)

| Component | Current | Production option |
|-----------|---------|-------------------|
| Cache | In-memory TTL | Redis / Memorystore |
| Chunk store | In-memory dict | Firestore / Cloud SQL |
| Job registry | In-memory + asyncio | Celery / Cloud Tasks / PubSub |
| Session memory | In-memory TTL | Redis / Firestore |
| Reranker | Heuristic | Cross-encoder / Vertex Reranker |
| Scheduler | APScheduler | Cloud Scheduler + PubSub |
