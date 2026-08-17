"""
Configuration — loads from environment variables with sensible defaults.

All settings in one place. No hardcoded paths. Docker and local both work.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo

load_dotenv()


@dataclass(frozen=True)
class Settings:
    # Database
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "memory_vault")
    db_user: str = os.getenv("DB_USER", "memory_vault")
    db_password: str = os.getenv("DB_PASSWORD", "memory_vault")

    # API
    api_host: str = os.getenv("API_HOST", "0.0.0.0")  # nosec B104 — Memory Vault is designed to run inside a Docker container; binding 0.0.0.0 is required to be reachable from the host. Operators expose only :8000 from compose.
    api_port: int = int(os.getenv("API_PORT", "8000"))

    # Embedding
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "384"))
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    # Task prefixes for asymmetric retrieval models (e.g. nomic-embed-text
    # wants "search_query: " / "search_document: "). Both default to "" so
    # the stock symmetric model is unchanged. Applied at encode time only.
    embedding_query_prefix: str = os.getenv("EMBEDDING_QUERY_PREFIX", "")
    embedding_document_prefix: str = os.getenv("EMBEDDING_DOCUMENT_PREFIX", "")
    # Some models (e.g. nomic-embed-text) ship custom architecture code on
    # the HF hub; loading them requires opting in. Off by default.
    # Pin the Hub revision (commit sha) the model — and, under
    # trust_remote_code, its architecture code — is loaded from. Unset means
    # 'main': whatever the model repo currently serves.
    embedding_model_revision: str | None = os.getenv("EMBEDDING_MODEL_REVISION") or None
    embedding_trust_remote_code: bool = (
        os.getenv("EMBEDDING_TRUST_REMOTE_CODE", "false").lower() == "true"
    )
    # Cap the model's token context (0 = keep the model default). Long-context
    # models can request enormous attention buffers on big batches; a cap
    # keeps embedding memory bounded.
    embedding_max_seq_length: int = int(os.getenv("EMBEDDING_MAX_SEQ_LENGTH", "0"))

    # Search
    rrf_k: int = int(os.getenv("RRF_K", "60"))
    search_default_limit: int = int(os.getenv("SEARCH_DEFAULT_LIMIT", "10"))

    @property
    def database_url(self) -> str:
        """psycopg conninfo string for the configured database.

        Built with ``psycopg.conninfo.make_conninfo`` so URI-reserved characters
        in DB_USER or DB_PASSWORD (``/``, ``@``, ``:``, ``#``, etc.) are quoted
        correctly. The historical name is kept for API compatibility even
        though the result is a keyword=value conninfo string, not a URI.
        """
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_password,
        )


settings = Settings()
