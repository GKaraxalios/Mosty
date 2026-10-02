from __future__ import annotations

import os
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_vertex_initialized = False


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
        from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

        model = TextEmbeddingModel.from_pretrained(
            self._settings.VERTEX_EMBEDDING_MODEL
        )

        batch_size = 250
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            inputs = [
                TextEmbeddingInput(text=t, task_type="RETRIEVAL_DOCUMENT")
                for t in batch
            ]
            embeddings = model.get_embeddings(inputs)
            all_embeddings.extend([e.values for e in embeddings])
            logger.info("Embedded batch %d–%d of %d", i, i + len(batch), len(texts))

        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

        model = TextEmbeddingModel.from_pretrained(
            self._settings.VERTEX_EMBEDDING_MODEL
        )
        inputs = [TextEmbeddingInput(text=text, task_type="RETRIEVAL_QUERY")]
        embeddings = model.get_embeddings(inputs)
        return embeddings[0].values


class VertexVectorSearchService:
    """Manages vectors in Vertex AI Vector Search (Matching Engine)."""

    def __init__(self) -> None:
        _ensure_vertex_init()
        self._settings = get_settings()
        if self._vector_search_configured():
            logger.info(
                "Vertex Vector Search settings loaded: project=%s project_number=%s location=%s index_id=%s endpoint_id=%s deployed_index_id=%s",
                self._settings.GOOGLE_CLOUD_PROJECT,
                self._settings.GOOGLE_CLOUD_PROJECT_NUMBER,
                self._settings.GOOGLE_CLOUD_LOCATION,
                self._settings.VERTEX_INDEX_ID,
                self._settings.VERTEX_INDEX_ENDPOINT_ID,
                self._settings.VERTEX_DEPLOYED_INDEX_ID,
            )
        else:
            logger.warning(
                "Vertex Vector Search not fully configured (need GOOGLE_CLOUD_PROJECT, "
                "GOOGLE_CLOUD_PROJECT_NUMBER, GOOGLE_CLOUD_LOCATION, VERTEX_INDEX_ID, "
                "VERTEX_INDEX_ENDPOINT_ID, VERTEX_DEPLOYED_INDEX_ID). "
                "Dense retrieval will be skipped; hybrid search uses BM25 only."
            )

    def _vector_search_configured(self) -> bool:
        s = self._settings
        keys = (
            s.GOOGLE_CLOUD_PROJECT,
            s.GOOGLE_CLOUD_PROJECT_NUMBER,
            s.GOOGLE_CLOUD_LOCATION,
            s.VERTEX_INDEX_ID,
            s.VERTEX_INDEX_ENDPOINT_ID,
            s.VERTEX_DEPLOYED_INDEX_ID,
        )
        return all(str(v).strip() for v in keys)

    def is_configured(self) -> bool:
        """True when Vertex Matching Engine can upsert/query; False → BM25-only / chunk-store sync."""
        return self._vector_search_configured()

    def _build_index_resource_name(self) -> str:
        return (
            f"projects/{self._settings.GOOGLE_CLOUD_PROJECT_NUMBER}"
            f"/locations/{self._settings.GOOGLE_CLOUD_LOCATION}"
            f"/indexes/{self._settings.VERTEX_INDEX_ID}"
        )

    def _build_index_endpoint_resource_name(self) -> str:
        return (
            f"projects/{self._settings.GOOGLE_CLOUD_PROJECT_NUMBER}"
            f"/locations/{self._settings.GOOGLE_CLOUD_LOCATION}"
            f"/indexEndpoints/{self._settings.VERTEX_INDEX_ENDPOINT_ID}"
        )

    def _get_index(self):
        from google.cloud import aiplatform

        index_resource_name = self._build_index_resource_name()
        logger.info("Using Vertex index resource: %s", index_resource_name)

        return aiplatform.MatchingEngineIndex(index_name=index_resource_name)

    def _get_index_endpoint(self):
        from google.cloud import aiplatform

        endpoint_resource_name = self._build_index_endpoint_resource_name()
        logger.info("Using Vertex endpoint resource: %s", endpoint_resource_name)

        return aiplatform.MatchingEngineIndexEndpoint(
            index_endpoint_name=endpoint_resource_name
        )

    async def upsert_vectors(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadata_list: list[dict[str, Any]],
    ) -> bool:
        if not self._vector_search_configured():
            logger.warning(
                "Skipping Vertex upsert (%d vectors): index not configured — "
                "chunks still saved locally; set VERTEX_* ids for dense retrieval",
                len(ids),
            )
            return False
        index = self._get_index()

        datapoints = []
        for vid, emb, _meta in zip(ids, embeddings, metadata_list):
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
        if not self._vector_search_configured():
            logger.warning(
                "Skipping Vertex remove (%d ids): index not configured",
                len(ids),
            )
            return 0
        index = self._get_index()
        index.remove_datapoints(datapoint_ids=ids)
        logger.info("Removed %d vectors from index", len(ids))
        return len(ids)

    async def find_neighbors(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        if not self._vector_search_configured():
            logger.warning(
                "find_neighbors skipped: Vector Search not configured (empty dense set)"
            )
            return []

        endpoint = self._get_index_endpoint()
        deployed_index_id = self._settings.VERTEX_DEPLOYED_INDEX_ID

        logger.info(
            "Calling find_neighbors with deployed_index_id=%s top_k=%s",
            deployed_index_id,
            top_k,
        )

        response = endpoint.find_neighbors(
            deployed_index_id=deployed_index_id,
            queries=[query_embedding],
            num_neighbors=top_k,
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

        logger.info("Found %d neighbors from Vertex Vector Search", len(results))
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
        from vertexai.generative_models import GenerationConfig, GenerativeModel

        try:
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
            return _extract_generative_text(response)
        except Exception as exc:
            # Quota, network, safety, or SDK errors — chat layer can show fallback copy
            logger.exception("Vertex generate_content failed: %s", exc)
            return ""


def _extract_all_text_parts_from_response(obj: Any) -> str:
    """Concatenate text from all parts of all candidates (Gemini 2.x may use multiple parts)."""
    parts_out: list[str] = []
    try:
        for cand in getattr(obj, "candidates", None) or []:
            content = getattr(cand, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", None) or []:
                try:
                    txt = part.text
                except (AttributeError, ValueError):
                    continue
                if txt:
                    parts_out.append(str(txt))
    except Exception:
        return ""
    return "".join(parts_out)


def _extract_generative_text(response: Any) -> str:
    """Return model text; avoid ValueError when output is blocked, multi-part, or has no parts."""
    try:
        text = response.text
        if text is not None and str(text).strip():
            return str(text)
    except ValueError as exc:
        merged = _extract_all_text_parts_from_response(response)
        if merged:
            return merged
        logger.warning("Vertex generate_content produced no text: %s", exc)
        try:
            cands = getattr(response, "candidates", None) or []
            if cands:
                fr = getattr(cands[0], "finish_reason", None)
                logger.warning("First candidate finish_reason=%s", fr)
        except Exception:
            pass
        return ""
    merged = _extract_all_text_parts_from_response(response)
    return merged if merged else ""