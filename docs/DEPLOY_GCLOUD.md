# MostyAPI — Deploy to Google Cloud Run

Step-by-step guide for deploying MostyAPI to **Cloud Run** with **Cloud SQL** (MySQL) and **GCS** for state persistence.

---

## Architecture

```
Cloud Scheduler (cron) ──HTTP──▶ Cloud Run (FastAPI)
                                     │
                                     ├── Cloud SQL (MySQL, Unix socket)
                                     ├── GCS Bucket (chunk_store.json, sync_state.json)
                                     ├── Vertex AI (embeddings, reranking, generation)
                                     └── Magento (GraphQL)
```

- **Cloud Run**: Runs the FastAPI container, scales 0→N automatically.
- **Cloud SQL**: Managed MySQL for sessions, commerce data, evaluations.
- **GCS**: Persistent storage for RAG chunk store and sync state.
- **Cloud Scheduler**: Replaces in-process APScheduler (which doesn't survive scale-to-zero).
- **Secret Manager**: Stores API keys, DB passwords, tokens.

---

## Prerequisites

1. **Google Cloud account** with billing enabled
2. **gcloud CLI** installed and authenticated:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```
3. **Docker** installed (or use `--cloud-build` flag for remote builds)

---

## Step 1: Create Cloud SQL Instance

```bash
# Create MySQL 8.0 instance
gcloud sql instances create mosty-db \
    --database-version=MYSQL_8_0 \
    --tier=db-f1-micro \
    --region=europe-west1 \
    --storage-size=10GB \
    --storage-auto-increase

# Set root password
gcloud sql users set-password root \
    --instance=mosty-db \
    --password=A7fK9Lm2QwX8RzP4bVt6HsJ3YdE5cU1N0gWqZr8TxCk9Fv2M6pD7B1hS4LxRj

# Create app user
gcloud sql users create mosty \
    --instance=mosty-db \
    --password=Z3mQ8Xv2PjL5sK1Tn9CwF6aB4R0Yd7HqUeE2gVxJ8rM3tN5pWk6S9cD1hA4f

# Create database
gcloud sql databases create mosty \
    --instance=mosty-db \
    --charset=utf8mb4 \
    --collation=utf8mb4_unicode_ci
```

Note the **connection name** (format: `PROJECT:REGION:INSTANCE`):
```bash
gcloud sql instances describe mosty-db --format="value(connectionName)"
(connectionName =  mosty-eshop-agent:europe-west1:mosty-db)
```

---

## Step 2: Create GCS Bucket

```bash
# Create bucket for state files
gsutil mb -l europe-west1 gs://mosty-eshop-agent-mostyapi-state

# Verify
gsutil ls gs://mosty-eshop-agent-mostyapi-state/
```

---

## Step 3: Store Secrets in Secret Manager

```bash
# API key
echo -n "4f9c2e7b8a1d6c3f9e0b7a5c1d8e2f6a9b3c7d1e5f8a2c6b9d0e4f7a1c3b5d8" | gcloud secrets create mosty-api-key --data-file=-

# Admin key
echo -n "A7fK9xP2LmQ8Zr4Tn6WbH3yC5dV1sX0uE9JkR7pG2tM4aN8oF6iL3qB5cD1vY" | gcloud secrets create mosty-admin-key --data-file=-

# DB password
echo -n "Z3mQ8Xv2PjL5sK1Tn9CwF6aB4R0Yd7HqUeE2gVxJ8rM3tN5pWk6S9cD1hA4f" | gcloud secrets create mosty-db-password --data-file=-

# Magento token (if used)
echo -n "your_magento_token" | gcloud secrets create mosty-magento-token --data-file=-
```

Grant Cloud Run access:
```bash
PROJECT_NUMBER=$(gcloud projects describe mosty-eshop-agen --format="value(projectNumber)")

for secret in mosty-api-key mosty-admin-key mosty-db-password mosty-magento-token; do
    gcloud secrets add-iam-policy-binding $secret \
        --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/secretmanager.secretAccessor"
done

No Mosty Token
PROJECT_NUMBER=$(gcloud projects describe mosty-eshop-agent --format="value(projectNumber)")

echo "Project number: $PROJECT_NUMBER"

for secret in mosty-api-key mosty-admin-key mosty-db-password; do
    gcloud secrets add-iam-policy-binding $secret \
        --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/secretmanager.secretAccessor"
done
```

---

## Step 4: Configure Environment

```bash
cp .env.gcloud.example .env.gcloud
```

Edit `.env.gcloud` and fill in:
- `GOOGLE_CLOUD_PROJECT`
- `DATABASE_URL_PRODUCTION` — use Unix socket format:
  ```
  mysql+pymysql://mosty:PASSWORD@/mosty?unix_socket=/cloudsql/PROJECT:REGION:mosty-db&charset=utf8mb4
  ```
- `CHUNK_STORE_PATH=gs://YOUR_BUCKET/mostyapi/chunk_store.json`
- `SYNC_STATE_PATH=gs://YOUR_BUCKET/mostyapi/sync_state.json`
- `CORS_ORIGINS` — your frontend domain(s)
- Vertex AI index IDs

---

## Step 5: Deploy

### Option A: Using the deploy script (recommended)

```bash
# Set Cloud SQL connection
export GCP_CLOUD_SQL_CONN="mosty-eshop-agent:europe-west1:mosty-db"

# Deploy
./scripts/deploy_gcloud.sh
```

The script will:
1. Enable required GCP APIs
2. Create Artifact Registry repository (if needed)
3. Build and push the Docker image
4. Deploy to Cloud Run with env vars from `.env.gcloud`
5. Print the service URL

### Option B: Using Cloud Build

```bash
export GCP_CLOUD_SQL_CONN="YOUR_PROJECT:europe-west1:mosty-db"
./scripts/deploy_gcloud.sh --cloud-build
```

### Option C: Manual gcloud commands

```bash
# Build and push
docker build -t europe-west1-docker.pkg.dev/YOUR_PROJECT/mostyapi/mostyapi:latest .
docker push europe-west1-docker.pkg.dev/YOUR_PROJECT/mostyapi/mostyapi:latest

# Deploy
gcloud run deploy mostyapi \
    --image europe-west1-docker.pkg.dev/YOUR_PROJECT/mostyapi/mostyapi:latest \
    --region europe-west1 \
    --platform managed \
    --port 8080 \
    --memory 1Gi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 4 \
    --add-cloudsql-instances YOUR_PROJECT:europe-west1:mosty-db \
    --allow-unauthenticated \
    --set-env-vars "APP_ENV=production,ENABLE_SCHEDULER=false,..."
```

---

## Step 6: Attach Secrets to Cloud Run

```bash
gcloud run services update mostyapi --region europe-west1 \
    --set-secrets="4f9c2e7b8a1d6c3f9e0b7a5c1d8e2f6a9b3c7d1e5f8a2c6b9d0e4f7a1c3b5d8=mosty-api-key:latest" \
    --set-secrets="A7fK9xP2LmQ8Zr4Tn6WbH3yC5dV1sX0uE9JkR7pG2tM4aN8oF6iL3qB5cD1vY=mosty-admin-key:latest" \
    --set-secrets="Z3mQ8Xv2PjL5sK1Tn9CwF6aB4R0Yd7HqUeE2gVxJ8rM3tN5pWk6S9cD1hA4f=mosty-db-password:latest"
```

---

## Step 7: Set Up Cloud Scheduler

Replace the in-process APScheduler with Cloud Scheduler HTTP jobs:

```bash
SERVICE_URL=$(gcloud run services describe mostyapi \
    --region europe-west1 --format="value(status.url)")

# Full sync — daily at 03:00
gcloud scheduler jobs create http mostyapi-full-sync \
    --location=europe-west1 \
    --schedule="0 3 * * *" \
    --uri="${SERVICE_URL}/api/v1/rag/scheduler/run-now/full" \
    --http-method=POST \
    --headers="x-mosty-api-key=YOUR_API_KEY" \
    --attempt-deadline=1800s \
    --time-zone="Europe/Athens"

# Incremental sync — every 30 minutes
gcloud scheduler jobs create http mostyapi-incr-sync \
    --location=europe-west1 \
    --schedule="*/30 * * * *" \
    --uri="${SERVICE_URL}/api/v1/rag/scheduler/run-now/incremental" \
    --http-method=POST \
    --headers="x-mosty-api-key=YOUR_API_KEY" \
    --attempt-deadline=600s \
    --time-zone="Europe/Athens"
```

---

## Step 8: Verify Deployment

```bash
SERVICE_URL=$(gcloud run services describe mostyapi \
    --region europe-west1 --format="value(status.url)")

# Health check
curl -s "${SERVICE_URL}/api/v1/health" | python3 -m json.tool

# Swagger docs
echo "Open in browser: ${SERVICE_URL}/docs"

# Test search (replace YOUR_KEY)
curl -s -X POST "${SERVICE_URL}/api/v1/rag/hybrid-search" \
    -H "Content-Type: application/json" \
    -H "x-mosty-api-key: YOUR_KEY" \
    -d '{"query": "black dress", "top_k": 3}' | python3 -m json.tool
```

---

## Custom Domain (Optional)

```bash
# Map domain
gcloud run domain-mappings create \
    --service=mostyapi \
    --domain=api.mosty.com \
    --region=europe-west1

# Get DNS records to configure
gcloud run domain-mappings describe \
    --domain=api.mosty.com \
    --region=europe-west1
```

---

## Cost Estimates (Monthly)

| Service | Spec | Approx. Cost |
|---------|------|-------------|
| Cloud Run | Scale to zero, up to 4 instances | $0–20 |
| Cloud SQL | db-f1-micro, 10GB | $7–10 |
| GCS | ~1MB state files | ~$0.02 |
| Cloud Scheduler | 2 jobs | Free (3 free/month) |
| Vertex AI | Per-request pricing | Usage-based |

---

## Troubleshooting

### Container crashes on startup
```bash
gcloud run services logs read mostyapi --region europe-west1 --limit 50
```

### Database connection errors
- Check Cloud SQL connection name: `gcloud sql instances describe mosty-db --format="value(connectionName)"`
- Verify the Cloud Run service has `--add-cloudsql-instances` configured
- Ensure Unix socket path matches: `/cloudsql/PROJECT:REGION:INSTANCE`

### State files not persisting
- Verify GCS paths start with `gs://`
- Check bucket permissions: the Cloud Run service account needs `storage.objects.create` and `storage.objects.get`
- Grant access: `gsutil iam ch serviceAccount:ACCOUNT@PROJECT.iam.gserviceaccount.com:objectAdmin gs://BUCKET`

### Scheduler jobs not running
- Check Cloud Scheduler job status: `gcloud scheduler jobs describe mostyapi-full-sync --location=europe-west1`
- Verify the API key in headers matches `MOSTY_RAG_API_KEY`
- Check that the Cloud Run service allows unauthenticated requests (or configure OIDC auth)

### Scale-to-zero and cold starts
- Cold start takes ~5–10s for the first request
- Set `--min-instances 1` if cold starts are unacceptable
- Set `--cpu-boost` flag for faster cold starts

SERVICE_URL=$(gcloud run services describe mostyapi \
    --region europe-west1 --format="value(status.url)")
curl -X POST "${SERVICE_URL}/api/v1/rag/scheduler/run-now/full" \
    -H "x-mosty-api-key: 4f9c2e7b8a1d6c3f9e0b7a5c1d8e2f6a9b3c7d1e5f8a2c6b9d0e4f7a1c3b5d8" \
    -H "Content-Type: application/json"