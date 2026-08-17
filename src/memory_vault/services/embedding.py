"""
Embedding service using sentence-transformers (all-MiniLM-L6-v2 by default).

The model loads once on first call and stays in memory.
Runs locally on CPU — no API calls, no data leaving the machine.

Asymmetric retrieval support: queries and documents can be embedded with
different task prefixes (EMBEDDING_QUERY_PREFIX / EMBEDDING_DOCUMENT_PREFIX,
e.g. "search_query: " / "search_document: " for nomic-embed-text). Both
default to "" so the stock symmetric model behaves exactly as before.
Prefixes are applied at encode time only — never stored with content.
"""

import logging
import threading
from typing import Literal

import numpy as np
from sentence_transformers import SentenceTransformer

from memory_vault.config import settings

logger = logging.getLogger(__name__)

MODEL_NAME = settings.embedding_model

EmbedKind = Literal["query", "document"]

_model: SentenceTransformer | None = None

# Guards construction of the model. Held only while loading, so callers that
# already have a model never queue behind a load.
_load_lock = threading.Lock()

# Held for the duration of every `encode` call. The model is a single shared
# object and its forward pass is not safe to run from several threads at once —
# doing so segfaults the interpreter rather than raising. Embedding runs in a
# worker thread on the async paths, so concurrent requests reach it in parallel
# and something has to serialize them.
_encode_lock = threading.Lock()


def _get_model() -> SentenceTransformer:
    """Load the model once, reuse on subsequent calls."""
    global _model
    # Fast path: no lock once the model exists, which is every call but the
    # first. The assignment below is atomic, so a reader either sees None or a
    # fully constructed model.
    if _model is not None:
        return _model

    with _load_lock:
        # Re-check under the lock: another thread may have loaded it while this
        # one waited, and constructing a second model wastes minutes and memory.
        if _model is None:
            logger.info("Loading embedding model: %s", settings.embedding_model)
            model = SentenceTransformer(
                settings.embedding_model,
                trust_remote_code=settings.embedding_trust_remote_code,
            )
            if settings.embedding_max_seq_length:
                # Attention memory scales with batch x seq_len^2; long-context
                # models (e.g. 8192 tokens) can request tens of GiB on a large
                # batch. A cap keeps memory bounded; 0 keeps the model default.
                model.max_seq_length = settings.embedding_max_seq_length
            logger.info(
                "Model loaded — dimensions=%d, max_seq_length=%d",
                settings.embedding_dimensions,
                model.max_seq_length,
            )
            _model = model
        return _model


def _prefix(kind: EmbedKind) -> str:
    if kind == "query":
        return settings.embedding_query_prefix
    return settings.embedding_document_prefix


def embed(text: str, kind: EmbedKind = "document") -> list[float]:
    """Embed a single text string. Returns a list of floats."""
    model = _get_model()
    with _encode_lock:
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
    with _encode_lock:
        vectors: np.ndarray = model.encode(
            [prefix + t for t in texts],
            batch_size=bs,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > bs,
        )
    return vectors.tolist()
