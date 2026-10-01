"""Typed application settings. The single place environment variables are read."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    override = os.environ.get("PRAMANA_ROOT")
    if override:
        return Path(override).resolve()
    # backend/src/pramana/shared/config.py -> repo root is four levels above "shared".
    return Path(__file__).resolve().parents[4]


REPO_ROOT = _repo_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    frontend_origin: str = "http://localhost:3000"

    # ── LLM providers ────────────────────────────────────────────────────
    google_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    cerebras_api_key: SecretStr | None = None
    ollama_base_url: str | None = None
    # Ordered "provider:model" fallback chain; providers without a key are skipped.
    llm_chain: str = (
        "google:gemini-3.5-flash-lite,google:gemini-3.1-flash-lite,google:gemma-4-31b-it,"
        "groq:openai/gpt-oss-120b,cerebras:gpt-oss-120b"
    )
    llm_timeout_seconds: float = 60.0
    llm_cache_ttl_seconds: int = 86_400
    llm_max_calls_per_query: int = 4

    # ── Datastores ───────────────────────────────────────────────────────
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "pramana"
    postgres_user: str = "pramana"
    postgres_password: SecretStr = SecretStr("")
    redis_url: str = "redis://127.0.0.1:6379/0"
    qdrant_url: str = "http://127.0.0.1:6333"
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    opa_url: str = "http://127.0.0.1:8181"

    # ── Security ─────────────────────────────────────────────────────────
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "pramana"
    encryption_key: SecretStr = SecretStr("")
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_days: int = 7
    demo_password: SecretStr | None = None
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    rate_limit_per_minute: int = 120
    auth_rate_limit_per_minute: int = 20

    # ── Local models ─────────────────────────────────────────────────────
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    sparse_model: str = "Qdrant/bm25"
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-12-v2"
    extraction_model: str = "fastino/gliner2-base-v1"
    grounding_model: str = "vectara/hallucination_evaluation_model"
    nli_model: str = "cross-encoder/nli-deberta-v3-base"
    span_model: str = "KRLabsOrg/lettucedect-base-modernbert-en-v1"
    spacy_model: str = "en_core_web_sm"

    # ── Retrieval ────────────────────────────────────────────────────────
    qdrant_collection: str = "pramana_chunks"
    qdrant_entity_collection: str = "pramana_entities"
    chunk_target_tokens: int = 320
    chunk_overlap_sentences: int = 1
    retrieval_candidates: int = 30
    retrieval_top_k: int = 8
    rrf_k: int = 60
    min_evidence_score: float = Field(
        default=0.0, description="Reranker logit below which evidence counts as irrelevant."
    )

    # ── Validation ───────────────────────────────────────────────────────
    support_threshold: float = 0.60
    uncertain_low: float = 0.30
    uncertain_high: float = 0.75
    max_correction_rounds: int = 2
    min_supported_ratio: float = 0.80

    # ── Paths ────────────────────────────────────────────────────────────
    data_dir: Path = REPO_ROOT / "data"
    cache_dir: Path = REPO_ROOT / ".cache"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "storage"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def require_secrets(self) -> None:
        """Refuse to start with missing or weak secrets."""
        problems = []
        if len(self.jwt_secret.get_secret_value()) < 32:
            problems.append("JWT_SECRET must be at least 32 characters")
        if not self.encryption_key.get_secret_value():
            problems.append("ENCRYPTION_KEY is missing")
        if not self.postgres_password.get_secret_value():
            problems.append("POSTGRES_PASSWORD is missing")
        if not self.neo4j_password.get_secret_value():
            problems.append("NEO4J_PASSWORD is missing")
        if problems:
            raise RuntimeError(
                "Invalid configuration: " + "; ".join(problems) + ". Run scripts/init_env.py."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    # Model caches live inside the repo (on the larger drive), not in the user profile.
    hf_home = settings.cache_dir / "hf"
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(settings.cache_dir / "fastembed"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    return settings
