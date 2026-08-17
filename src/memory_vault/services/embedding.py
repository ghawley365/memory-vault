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

from __future__ import annotations

import logging
import sys
import threading
from typing import TYPE_CHECKING, Any, Literal

from memory_vault.config import settings

if TYPE_CHECKING:  # pragma: no cover
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = settings.embedding_model

EmbedKind = Literal["query", "document"]

# sentence-transformers pulls in torch (~500 MB RSS) at import time. The MCP
# server runs one process per client session and most of them never embed
# before their first tool call, so the import is deferred to first model load.
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


def __getattr__(name: str) -> Any:
    """Resolve `SentenceTransformer` on first attribute access (PEP 562).

    Keeps `embedding.SentenceTransformer` working — the name existed as a
    module-level import before, and tests patch it — while the actual
    (heavy) import still happens no earlier than first use.
    """
    if name == "SentenceTransformer":
        from sentence_transformers import SentenceTransformer as _ST

        return _ST
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _load_sentence_transformer() -> Any:
    """The SentenceTransformer class, honouring a patched module attribute."""
    return sys.modules[__name__].SentenceTransformer


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
            kwargs: dict[str, Any] = {
                "trust_remote_code": settings.embedding_trust_remote_code,
            }
            if settings.embedding_model_revision:
                kwargs["revision"] = settings.embedding_model_revision
            model = _load_sentence_transformer()(settings.embedding_model, **kwargs)
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
        vector = model.encode(_prefix(kind) + text, normalize_embeddings=True)
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
        vectors = model.encode(
            [prefix + t for t in texts],
            batch_size=bs,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > bs,
        )
    return vectors.tolist()
