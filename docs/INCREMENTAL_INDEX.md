# Incremental AI Product Index (Mosty)

This document describes the **incremental indexing pipeline** that keeps the AI product recommendation index in sync with Magento without full reindexes. It is designed for cron, low impact, and production safety.

## Architecture

```
Magento GraphQL (source of truth)
        │
        ▼
┌───────────────────┐     ┌─────────────────────┐
│ Sync state        │     │ Minimal product list  │
│ (last_sync_at)    │     │ (sku, updated_at)     │
└────────┬──────────┘     └──────────┬───────────┘
         │                            │
         │  since_iso                  │  filter updated_at >= since
         ▼                            ▼
┌─────────────────────────────────────────────────┐
│  Fetch changed products (full payload by SKU)    │
└─────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│  For each product:                               │
│  • Out of stock → remove from index              │
│  • In stock     → remove old chunks,             │
│                   build enriched text,            │
│                   chunk → embed → upsert         │
└─────────────────────────────────────────────────┘
         │
         ▼
┌───────────────────┐     ┌─────────────────────┐
│ Chunk store       │     │ Vertex Vector Index │
│ (JSON, by chunk)  │     │ (streaming upsert)  │
└───────────────────┘     └─────────────────────┘
         │
         ▼
┌───────────────────┐
│ BM25 index        │  (rebuilt after sync from chunk store)
└───────────────────┘
```

- **Data source**: Magento GraphQL. We fetch minimal `(sku, updated_at, created_at, stock_status)` in pages to find changed SKUs, then fetch full product payload only for those SKUs.
- **Incremental logic**: Sync state file stores `last_successful_sync_at`. Each run only processes products with `updated_at` or `created_at` ≥ that timestamp.
- **Index updates**: Add (new), update (replace chunks by SKU), remove (out-of-stock or deleted). One active set of chunks per SKU (deduplication).
- **Stock-aware**: Out-of-stock products are removed from the index so the AI never recommends them.
- **Zero-downtime**: Vertex index uses streaming upsert/remove; chunk store is written atomically (temp file + rename). No swap of a full index.

## Configuration (.env)

| Variable | Description | Example |
|----------|-------------|---------|
| `MAGENTO_GRAPHQL_URL` | Magento GraphQL endpoint | `https://mosty.com/graphql` |
| `MAGENTO_STORE_CODE` | Store view code | `default` |
| `SYNC_STATE_PATH` | Sync state JSON file | `data/sync_state.json` |
| `CHUNK_STORE_PATH` | Chunk store JSON file | `data/chunk_store.json` |
| `INDEX_BATCH_SIZE` | Max products to process per batch | `20` |
| `INDEX_LOCK_PATH` | Lock file for cron | `data/.index_sync.lock` |
| `INDEX_LOCK_TIMEOUT_SECONDS` | Lock timeout (informational) | `3600` |
| `GOOGLE_CLOUD_PROJECT` | GCP project for Vertex | required for embeddings/index |
| `VERTEX_INDEX_ID` / `VERTEX_INDEX_ENDPOINT_ID` / `VERTEX_DEPLOYED_INDEX_ID` | Vertex Vector Search | from `create_vertex_index` |

Optional: `MAGENTO_TOKEN` if you add Bearer auth to GraphQL later.

## Cron

Run the incremental update every 10 minutes (or as needed):

```bash
# Every 10 minutes
*/10 * * * * /usr/bin/python3 /path/to/MostyAPI/scripts/update_index.py >> /var/log/mosty_index_update.log 2>&1
```

Or via crontab with project root and venv:

```bash
*/10 * * * * cd /path/to/MostyAPI && .venv/bin/python scripts/update_index.py >> /var/log/mosty_index_update.log 2>&1
```

The script:

- Is **idempotent**: safe to run repeatedly.
- Uses a **lock file**: if a previous run is still active, it exits without error and does not start a second run.
- Logs to stdout/stderr (redirect to a file as above).
- Exits 0 on success, 1 on failure (so you can alert on exit code).

## Optional: Reconcile deletes

To remove index entries for products that no longer exist in Magento (e.g. discontinued), run occasionally with `--reconcile-deletes`. This scans all Magento SKUs and removes any indexed SKU not in Magento. Slower; run weekly or after big catalog changes:

```bash
0 4 * * 0 cd /path/to/MostyAPI && .venv/bin/python scripts/update_index.py --reconcile-deletes >> /var/log/mosty_index_update.log 2>&1
```

## Testing safely

1. **Dry run** (no Magento, no index writes):
   ```bash
   python -m scripts.update_index --dry-run
   ```
   Prints the `since` timestamp that would be used and exits.

2. **First run after bootstrap**: Ensure you have run a **full sync** at least once (e.g. via your existing RAG sync API or job) so the chunk store and vector index are populated. Incremental sync then only processes products changed after `last_successful_sync_at`. If you have never run a full sync, the first incremental run will use `since = 1970-01-01` and may try to process many products; prefer doing one full sync first.

3. **Health check**:
   ```bash
   python -m scripts.health_check_index
   python -m scripts.health_check_index --json
   ```
   Shows last run, last success, indexed SKU count, chunk count, and any last error.

## Rollback / recovery

- **Index update fails**: The script does not replace the live index until updates succeed. If Magento or Vertex fails mid-run, the previous chunk store and vector index remain; sync state may record `last_error`. Fix the cause (e.g. Magento down, credentials) and run again.
- **Corrupt chunk store**: Restore `data/chunk_store.json` from backup if you have one. Optionally run a full sync to repopulate from Magento and Vertex.
- **Sync state reset**: To force “re-sync everything since a date”, edit `data/sync_state.json` and set `last_successful_sync_at` to an old ISO timestamp (e.g. `1970-01-01T00:00:00Z`) or remove it; next run will use that. Do not set it to the future.
- **Lock stuck**: If the process dies without releasing the lock, remove the lock file manually: `rm data/.index_sync.lock` (or path from `INDEX_LOCK_PATH`). Only do this when you are sure no update is running.

## Files and components

| Component | Path | Role |
|-----------|------|------|
| Incremental sync script | `scripts/update_index.py` | Cron entrypoint; lock, run sync, exit code |
| Health check | `scripts/health_check_index.py` | Sync status and counts |
| Sync state | `app/services/sync_state_service.py` | Load/save `last_sync_at`, optional indexed SKUs |
| Incremental service | `app/services/incremental_index_service.py` | Fetch changed products, add/update/remove, BM25 rebuild |
| GraphQL helpers | `app/services/graphql_service.py` | Minimal product list, changed products full payload |
| Enriched chunk text | `app/utils/transformers.py` | `build_enriched_product_text()` for fashion retrieval |
| Chunk store | `app/services/rag_ingestion_service.py` | In-memory chunk store, `save_chunk_store()` |

## Monitoring

- **indexed_count / updated_count / removed_count / failed_count**: Logged at the end of each run.
- **last_error**: Stored in sync state and shown by `health_check_index`.
- For alerts: cron can run a wrapper that checks the exit code of `update_index.py` and/or parses the log or `health_check_index --json`.
