"""
Embedding service using sentence-transformers.

Default model: nomic-ai/nomic-embed-text-v1.5 (768-d, 8192-token context).
The model loads once on first call and stays in memory.
Runs locally on CPU — no API calls, no data leaving the machine.

Asymmetric retrieval: queries and documents are embedded with different
task prefixes (configurable; empty prefixes = symmetric model). Prefixes
are applied at encode time only — never stored with content.
"""

import logging
from typing import Literal

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import settings

logger = logging.getLogger(__name__)

MODEL_NAME = settings.embedding_model

EmbedKind = Literal["query", "document"]

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """Load the model once, reuse on subsequent calls."""
    global _model
    if _model is None:
        logger.info("Loading embedding model: %s", settings.embedding_model)
        _model = SentenceTransformer(
            settings.embedding_model,
            trust_remote_code=settings.embedding_trust_remote_code,
        )
        if settings.embedding_max_seq_length:
            _model.max_seq_length = settings.embedding_max_seq_length
        logger.info(
            "Model loaded — dimensions=%d, max_seq_length=%d",
            settings.embedding_dimensions,
            _model.max_seq_length,
        )
    return _model


def _prefix(kind: EmbedKind) -> str:
    if kind == "query":
        return settings.embedding_query_prefix
    return settings.embedding_document_prefix


def embed(text: str, kind: EmbedKind = "document") -> list[float]:
    """Embed a single text string. Returns a list of floats."""
    model = _get_model()
    vector: np.ndarray = model.encode(_prefix(kind) + text, normalize_embeddings=True)
    return vector.tolist()


def embed_batch(
    texts: list[str],
    batch_size: int | None = None,
    kind: EmbedKind = "document",
) -> list[list[float]]:
    """Embed a list of texts. Processes in chunks of batch_size."""
    if not texts:
        return []
    model = _get_model()
    bs = batch_size or settings.embedding_batch_size
    prefix = _prefix(kind)
    vectors: np.ndarray = model.encode(
        [prefix + t for t in texts],
        batch_size=bs,
        normalize_embeddings=True,
        show_progress_bar=len(texts) > bs,
    )
    return vectors.tolist()
