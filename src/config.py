"""
Configuration — loads from environment variables with sensible defaults.

All settings in one place. No hardcoded paths. Docker and local both work.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

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
    # nomic-embed-text-v1.5: 768-d, 8192-token context (vs 256 for MiniLM),
    # asymmetric retrieval via task prefixes. Prefixes are applied at encode
    # time only — stored content never includes them. Set both prefixes to ""
    # for symmetric models (e.g. all-MiniLM-L6-v2, BAAI/bge-m3).
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "768"))
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
    embedding_query_prefix: str = os.getenv("EMBEDDING_QUERY_PREFIX", "search_query: ")
    embedding_document_prefix: str = os.getenv(
        "EMBEDDING_DOCUMENT_PREFIX", "search_document: "
    )
    # nomic's custom architecture ships model code on the HF hub.
    embedding_trust_remote_code: bool = (
        os.getenv("EMBEDDING_TRUST_REMOTE_CODE", "true").lower() == "true"
    )
    # Cap token context: full 8192-token attention explodes memory on large
    # batches (33 GiB MPS buffers); 2048 covers nearly all notes at 16x less.
    embedding_max_seq_length: int = int(os.getenv("EMBEDDING_MAX_SEQ_LENGTH", "2048"))

    # Search
    rrf_k: int = int(os.getenv("RRF_K", "60"))
    search_default_limit: int = int(os.getenv("SEARCH_DEFAULT_LIMIT", "10"))

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()
