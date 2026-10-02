# Παραδείγματα `curl` (MostyAPI)

Ορίστε μεταβλητές shell (προσαρμόστε port / URL):

```bash
export BASE="http://localhost:9199"   # ή Cloud Run: https://your-service-xxxxx.run.app
export API_KEY="your-mosty-rag-api-key"  # από .env → MOSTY_RAG_API_KEY
```

Αν το `MOSTY_RAG_API_KEY` είναι **κενό** στο server, μπορείς να αφαιρέσεις το header `x-mosty-api-key` από τις κλήσεις.

Κοινό header (όταν χρειάζεται):

```bash
H=( -H "Content-Type: application/json" -H "x-mosty-api-key: ${API_KEY}" )
```

---

## Health

```bash
curl -sS "${BASE}/api/v1/health"
```

---

## RAG — index & sync

### Κατάσταση index (chunks, τελευταίο sync)

```bash
curl -sS "${BASE}/api/v1/rag/index-status" "${H[@]}"
```

### Πλήρης sync (όλο το catalog → chunks + Vertex αν είναι ρυθμισμένο)

Μεγάλη διάρκεια — χρησιμοποίησε μεγάλο timeout:

```bash
curl -sS --max-time 3600 -X POST "${BASE}/api/v1/rag/sync" \
  -H "Content-Type: application/json" \
  -H "x-mosty-api-key: ${API_KEY}"
```

### Sync ενός SKU

```bash
SKU="YOUR-SKU"
curl -sS -X POST "${BASE}/api/v1/rag/sync/${SKU}" \
  -H "Content-Type: application/json" \
  -H "x-mosty-api-key: ${API_KEY}"
```

### Διαγραφή vectors για ένα SKU

```bash
SKU="YOUR-SKU"
curl -sS -X DELETE "${BASE}/api/v1/rag/product/${SKU}" \
  -H "x-mosty-api-key: ${API_KEY}"
```

### RAG search (απλό)

```bash
curl -sS -X POST "${BASE}/api/v1/rag/search" \
  -H "Content-Type: application/json" \
  -H "x-mosty-api-key: ${API_KEY}" \
  -d '{"query":"φόρεμα κόκκινο","top_k":10}'
```

### Hybrid search (dense + sparse + φίλτρα)

```bash
curl -sS -X POST "${BASE}/api/v1/rag/hybrid-search" \
  -H "Content-Type: application/json" \
  -H "x-mosty-api-key: ${API_KEY}" \
  -d '{
    "query": "evening dress",
    "top_k": 10,
    "filters": { "category": "Φορέματα", "max_price": 150 }
  }'
```

### RAG ask (ερώτηση με απάντηση από RAG)

```bash
curl -sS -X POST "${BASE}/api/v1/rag/ask" \
  -H "Content-Type: application/json" \
  -H "x-mosty-api-key: ${API_KEY}" \
  -d '{"query":"Τι φορέματα έχετε κάτω από 80€;","top_k":5,"temperature":0.2}'
```

---

## Chat

### Bootstrap (δημόσιο env για το UI — χωρίς κλειδί)

```bash
curl -sS "${BASE}/api/v1/chat/bootstrap"
```

### Νέο session

```bash
curl -sS -X POST "${BASE}/api/v1/chat/session?language=auto" "${H[@]}"
```

Αποθήκευσε το `session_id` από το JSON απάντησης.

### Chat ask (χρειάζεται `session_id` από πάνω)

```bash
SESSION_ID="paste-session-id-here"
curl -sS -X POST "${BASE}/api/v1/chat/ask" \
  -H "Content-Type: application/json" \
  -H "x-mosty-api-key: ${API_KEY}" \
  -d "{
    \"session_id\": \"${SESSION_ID}\",
    \"query\": \"Θέλω ένα casual φόρεμα για γάμο το καλοκαίρι.\",
    \"top_k\": 10,
    \"temperature\": 0.2,
    \"language\": \"el\"
  }"
```

---

## Χρήσιμα

| Endpoint | Method | Σημείωση |
|----------|--------|----------|
| OpenAPI | GET `/docs` | Swagger UI |
| Root | GET `/` | Λίστα βασικών paths |

Για production αντικατέστησε μόνο το `BASE`. Το API prefix προεπιλογής είναι **`/api/v1`** (`API_V1_PREFIX`).
