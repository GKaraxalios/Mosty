from __future__ import annotations

import asyncio
import json
import os
import random
import time
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_vertex_initialized = False


# ── Vector Search failure classification ─────────────────────────────────────

# Categories that will not fix themselves within a retry window.
_NON_RETRYABLE = {"dns_not_found", "auth", "permission", "not_found", "invalid_config", "unknown"}


class VectorSearchUnavailable(Exception):
    """Dense retrieval could not be served. ``category`` drives retry/fallback decisions."""

    def __init__(self, category: str, detail: str, dependency: str = "vertex_vector_search") -> None:
        super().__init__(f"{dependency} unavailable ({category})")
        self.category = category
        self.detail = detail
        self.dependency = dependency

    @property
    def retryable(self) -> bool:
        return self.category not in _NON_RETRYABLE


def classify_vertex_error(exc: BaseException) -> str:
    from google.api_core import exceptions as gexc
    from google.auth import exceptions as auth_exc

    if isinstance(exc, (asyncio.TimeoutError, gexc.DeadlineExceeded)):
        return "timeout"
    if isinstance(exc, gexc.ServiceUnavailable):
        # gRPC reports NXDOMAIN as UNAVAILABLE; it persists until the endpoint DNS
        # record exists again (e.g. an index is redeployed), so do not retry it.
        msg = str(exc)
        if "Domain name not found" in msg or "hostname lookup error" in msg:
            return "dns_not_found"
        return "transient"
    if isinstance(exc, (gexc.ResourceExhausted, gexc.TooManyRequests)):
        return "rate_limited"
    if isinstance(exc, (gexc.Unauthenticated, gexc.Unauthorized, auth_exc.GoogleAuthError)):
        return "auth"
    if isinstance(exc, (gexc.PermissionDenied, gexc.Forbidden)):
        return "permission"
    if isinstance(exc, gexc.NotFound):
        return "not_found"
    if isinstance(exc, (gexc.InvalidArgument, gexc.FailedPrecondition, gexc.BadRequest)):
        return "invalid_config"
    if isinstance(exc, (gexc.ServerError, gexc.Aborted, ConnectionError)):
        return "transient"
    return "unknown"


class _CircuitBreaker:
    """Opens after a non-retryable failure or N consecutive failures; one probe after cooldown."""

    def __init__(self, failure_threshold: int = 3) -> None:
        self._failure_threshold = failure_threshold
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._last_category: str | None = None

    def allow(self, cooldown: float) -> bool:
        if self._opened_at is None:
            return True
        # Half-open: let one request through and push the window forward so
        # concurrent requests keep short-circuiting while the probe runs.
        if time.monotonic() - self._opened_at >= cooldown:
            self._opened_at = time.monotonic()
            return True
        return False

    @property
    def last_category(self) -> str | None:
        return self._last_category

    def record_success(self) -> bool:
        """Returns True when this success closes a previously open circuit."""
        recovered = self._opened_at is not None or self._consecutive_failures > 0
        self._consecutive_failures = 0
        self._opened_at = None
        self._last_category = None
        return recovered

    def record_failure(self, category: str, retryable: bool) -> None:
        self._consecutive_failures += 1
        self._last_category = category
        if not retryable or self._consecutive_failures >= self._failure_threshold:
            self._opened_at = time.monotonic()


_vector_circuit = _CircuitBreaker()
_index_endpoint_cache: Any = None


async def validate_vector_search_config() -> dict[str, Any]:
    """Check that the configured endpoint serves the configured deployed index.

    Called once at startup so a missing deployment is reported clearly instead of
    surfacing as a DNS error on the first chat request.
    """
    settings = get_settings()
    status: dict[str, Any] = {
        "endpoint_id": settings.VERTEX_INDEX_ENDPOINT_ID,
        "deployed_index_id": settings.VERTEX_DEPLOYED_INDEX_ID,
        "ok": False,
    }
    if not (settings.GOOGLE_CLOUD_PROJECT and settings.VERTEX_INDEX_ENDPOINT_ID and settings.VERTEX_DEPLOYED_INDEX_ID):
        status["problem"] = "missing GOOGLE_CLOUD_PROJECT / VERTEX_INDEX_ENDPOINT_ID / VERTEX_DEPLOYED_INDEX_ID"
        logger.error("vertex.config.invalid %s", status["problem"])
        return status
    try:
        _ensure_vertex_init()
        from google.cloud import aiplatform

        def _describe():
            ep = aiplatform.MatchingEngineIndexEndpoint(
                index_endpoint_name=settings.VERTEX_INDEX_ENDPOINT_ID,
                project=settings.GOOGLE_CLOUD_PROJECT,
                location=settings.GOOGLE_CLOUD_LOCATION,
            )
            return ep.public_endpoint_domain_name, [d.id for d in ep.deployed_indexes]

        domain, deployed = await asyncio.wait_for(asyncio.to_thread(_describe), timeout=20)
    except Exception as exc:
        status["problem"] = f"endpoint lookup failed ({classify_vertex_error(exc)})"
        logger.error("vertex.config.invalid endpoint=%s %s: %s", settings.VERTEX_INDEX_ENDPOINT_ID, status["problem"], exc)
        return status

    status["public_endpoint_domain"] = domain
    status["deployed_indexes"] = deployed
    if settings.VERTEX_DEPLOYED_INDEX_ID not in deployed:
        status["problem"] = "deployed index missing on endpoint"
        logger.error(
            "vertex.config.invalid endpoint=%s deployed_index_id=%s is not deployed (deployed=%s). "
            "The public endpoint hostname will not resolve until an index is deployed; "
            "chat retrieval runs in lexical fallback mode.",
            settings.VERTEX_INDEX_ENDPOINT_ID,
            settings.VERTEX_DEPLOYED_INDEX_ID,
            deployed or "none",
        )
        return status
    status["ok"] = True
    logger.info(
        "vertex.config.ok endpoint=%s deployed_index_id=%s domain=%s",
        settings.VERTEX_INDEX_ENDPOINT_ID,
        settings.VERTEX_DEPLOYED_INDEX_ID,
        domain,
    )
    return status


def _ensure_vertex_init() -> None:
    global _vertex_initialized
    if _vertex_initialized:
        return
    settings = get_settings()

    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        os.environ.setdefault(
            "GOOGLE_APPLICATION_CREDENTIALS",
            settings.GOOGLE_APPLICATION_CREDENTIALS,
        )

    if not settings.GOOGLE_CLOUD_PROJECT:
        logger.warning(
            "GOOGLE_CLOUD_PROJECT not set — Vertex AI calls will fail until configured"
        )
        _vertex_initialized = True
        return

    import vertexai
    vertexai.init(
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )
    _vertex_initialized = True
    logger.info(
        "Vertex AI initialized: project=%s, location=%s",
        settings.GOOGLE_CLOUD_PROJECT,
        settings.GOOGLE_CLOUD_LOCATION,
    )


class VertexEmbeddingService:
    """Generates text embeddings via Vertex AI."""

    def __init__(self) -> None:
        _ensure_vertex_init()
        self._settings = get_settings()

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput

        model = TextEmbeddingModel.from_pretrained(self._settings.VERTEX_EMBEDDING_MODEL)

        batch_size = 250
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            inputs = [TextEmbeddingInput(text=t, task_type="RETRIEVAL_DOCUMENT") for t in batch]
            embeddings = model.get_embeddings(inputs)
            all_embeddings.extend([e.values for e in embeddings])
            logger.info("Embedded batch %d–%d of %d", i, i + len(batch), len(texts))

        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput

        model = TextEmbeddingModel.from_pretrained(self._settings.VERTEX_EMBEDDING_MODEL)
        inputs = [TextEmbeddingInput(text=text, task_type="RETRIEVAL_QUERY")]
        embeddings = model.get_embeddings(inputs)
        return embeddings[0].values


class VertexVectorSearchService:
    """Manages vectors in Vertex AI Vector Search (Matching Engine)."""

    def __init__(self) -> None:
        _ensure_vertex_init()
        self._settings = get_settings()

    def _get_index(self):
        from google.cloud import aiplatform
        return aiplatform.MatchingEngineIndex(
            index_name=self._settings.VERTEX_INDEX_ID,
            project=self._settings.GOOGLE_CLOUD_PROJECT,
            location=self._settings.GOOGLE_CLOUD_LOCATION,
        )

    def _get_index_endpoint(self):
        # Constructing the endpoint object performs a metadata GET; reuse it across requests.
        global _index_endpoint_cache
        if _index_endpoint_cache is None:
            from google.cloud import aiplatform
            _index_endpoint_cache = aiplatform.MatchingEngineIndexEndpoint(
                index_endpoint_name=self._settings.VERTEX_INDEX_ENDPOINT_ID,
                project=self._settings.GOOGLE_CLOUD_PROJECT,
                location=self._settings.GOOGLE_CLOUD_LOCATION,
            )
        return _index_endpoint_cache

    async def upsert_vectors(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadata_list: list[dict[str, Any]],
    ) -> bool:
        """Upsert vectors into the deployed index."""
        from google.cloud import aiplatform

        index = self._get_index()

        datapoints = []
        for vid, emb, meta in zip(ids, embeddings, metadata_list):
            restricts = []
            for key, value in meta.items():
                if isinstance(value, list):
                    for v in value:
                        restricts.append(
                            aiplatform.matching_engine.matching_engine_index_endpoint.Namespace(
                                name=key, allow_tokens=[str(v)]
                            )
                        )
                elif isinstance(value, (str, int, float)):
                    restricts.append(
                        aiplatform.matching_engine.matching_engine_index_endpoint.Namespace(
                            name=key, allow_tokens=[str(value)]
                        )
                    )

            datapoints.append(
                {
                    "datapoint_id": vid,
                    "feature_vector": emb,
                }
            )

        index.upsert_datapoints(datapoints=datapoints)
        logger.info("Upserted %d vectors to index", len(ids))
        return True

    async def remove_vectors(self, ids: list[str]) -> int:
        index = self._get_index()
        index.remove_datapoints(datapoint_ids=ids)
        logger.info("Removed %d vectors from index", len(ids))
        return len(ids)

    async def find_neighbors(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Query the deployed index.

        Raises ``VectorSearchUnavailable`` (never a raw transport error) so callers can
        fall back. Transient failures are retried with jittered backoff; persistent ones
        (DNS NXDOMAIN, auth, missing resources) open the circuit immediately.
        """
        global _index_endpoint_cache
        s = self._settings
        if not _vector_circuit.allow(s.VERTEX_CIRCUIT_COOLDOWN_SECONDS):
            raise VectorSearchUnavailable(
                _vector_circuit.last_category or "circuit_open", "circuit open; skipping call"
            )

        deployed_index_id = s.VERTEX_DEPLOYED_INDEX_ID
        attempts = 1 + max(0, s.VERTEX_SEARCH_MAX_RETRIES)
        t0 = time.perf_counter()
        logger.debug("vertex.search.started endpoint=%s top_k=%d", s.VERTEX_INDEX_ENDPOINT_ID, top_k)
        for attempt in range(attempts):
            try:
                endpoint = self._get_index_endpoint()
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        endpoint.find_neighbors,
                        deployed_index_id=deployed_index_id,
                        queries=[query_embedding],
                        num_neighbors=top_k,
                    ),
                    timeout=s.VERTEX_SEARCH_TIMEOUT_SECONDS,
                )
                break
            except Exception as exc:
                category = classify_vertex_error(exc)
                err = VectorSearchUnavailable(category, str(exc))
                if category in ("not_found", "invalid_config"):
                    # Endpoint may have been recreated; refetch metadata on the next probe.
                    _index_endpoint_cache = None
                last = attempt == attempts - 1 or not err.retryable
                logger.warning(
                    "vertex.search.failed endpoint=%s deployed_index_id=%s category=%s attempt=%d/%d "
                    "duration_ms=%.1f final=%s error=%s",
                    s.VERTEX_INDEX_ENDPOINT_ID,
                    deployed_index_id,
                    category,
                    attempt + 1,
                    attempts,
                    (time.perf_counter() - t0) * 1000,
                    last,
                    str(exc)[:300],
                )
                if last:
                    _vector_circuit.record_failure(category, err.retryable)
                    raise err from exc
                await asyncio.sleep(0.25 * (2 ** attempt) + random.uniform(0, 0.25))

        if _vector_circuit.record_success():
            logger.info(
                "vertex.search.recovered endpoint=%s duration_ms=%.1f",
                s.VERTEX_INDEX_ENDPOINT_ID,
                (time.perf_counter() - t0) * 1000,
            )

        results: list[dict[str, Any]] = []
        for neighbor_group in response:
            for neighbor in neighbor_group:
                results.append(
                    {
                        "id": neighbor.id,
                        "score": neighbor.distance,
                    }
                )
        return results


class VertexGenerativeService:
    """Generates text answers via Vertex AI generative models."""

    def __init__(self) -> None:
        _ensure_vertex_init()
        self._settings = get_settings()

    async def generate(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> str:
        from vertexai.generative_models import GenerativeModel, GenerationConfig

        model = GenerativeModel(
            model_name=self._settings.VERTEX_GENERATIVE_MODEL,
            system_instruction=system_instruction,
        )

        config = GenerationConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

        response = model.generate_content(
            prompt,
            generation_config=config,
        )
        return response.text
