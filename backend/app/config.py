from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppSettings:
    app_env: str
    aws_region: str
    secrets_stage: str
    app_secret_name: str
    azure_openai_secret_name: str
    langfuse_secret_name: str
    s3_bucket: str
    s3_raw_prefix: str
    s3_manifest_key: str
    opensearch_endpoint: str
    opensearch_index: str
    dynamodb_chat_table: str
    chat_history_backend: str
    cors_origins: tuple[str, ...]
    prompt_label: str
    max_history_chars: int
    azure_openai_deployment: str = ""
    document_manifest_cache_ttl_seconds: int = 0
    langfuse_prompt_cache_ttl_seconds: int = 0
    chat_fast_rag_enabled: bool = False
    chat_fast_rag_min_query_terms: int = 3
    max_graph_llm_calls: int = 5
    rag_top_k: int = 10
    rag_neighbor_chunks: int = 1
    ingestion_chunk_size: int = 1500
    ingestion_chunk_overlap: int = 250
    rag_parallel_search_enabled: bool = False
    chat_background_history_save_enabled: bool = False
    chat_response_guardrail_enabled: bool = False
    local_data_dir: str = "/app/data"
    chroma_persist_dir: str = "/app/data/chroma"
    chroma_collection: str = "dstrmaysam-healthcare-knowledge-agent"
    local_app_secret_file: str = "/app/data/local_app_secret.json"
    local_test_admin_enabled: bool = False
    local_test_admin_username: str = "admin"
    local_test_admin_password: str = "admin123"
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "healthcare_agent"
    postgres_user: str = "healthcare_agent"
    postgres_password: str = "healthcare_agent_dev"
    postgres_sslmode: str = "disable"
    deterministic_lookup_enabled: bool = True

    @classmethod
    def from_env(cls) -> "AppSettings":
        stage = _env("SECRETS_STAGE", "dev")
        default_prefix = f"/dstrmaysam-healthcare-knowledge-agent/{stage}"
        origins = tuple(
            origin.strip()
            for origin in _env("CORS_ORIGINS", "http://localhost:8501").split(",")
            if origin.strip()
        )
        return cls(
            app_env=_env("APP_ENV", "local"),
            aws_region=_env("AWS_REGION", "eu-west-2"),
            secrets_stage=stage,
            app_secret_name=_env("APP_SECRET_NAME", f"{default_prefix}/app"),
            azure_openai_secret_name=_env(
                "AZURE_OPENAI_SECRET_NAME", f"{default_prefix}/azure-openai"
            ),
            azure_openai_deployment=_env("AZURE_OPENAI_DEPLOYMENT", ""),
            langfuse_secret_name=_env("LANGFUSE_SECRET_NAME", f"{default_prefix}/langfuse"),
            s3_bucket=_env("S3_BUCKET"),
            s3_raw_prefix=_env("S3_RAW_PREFIX", "raw/"),
            s3_manifest_key=_env("S3_MANIFEST_KEY", "manifests/documents.json"),
            opensearch_endpoint=_env("OPENSEARCH_ENDPOINT"),
            opensearch_index=_env("OPENSEARCH_INDEX", "dstrmaysam-healthcare-knowledge-agent-dev"),
            dynamodb_chat_table=_env(
                "DYNAMODB_CHAT_TABLE",
                "dstrmaysam-healthcare-knowledge-agent-dev",
            ),
            chat_history_backend=_env("CHAT_HISTORY_BACKEND", "memory"),
            cors_origins=origins,
            prompt_label=_env("PROMPT_LABEL", "dev"),
            max_history_chars=int(_env("MAX_HISTORY_CHARS", "8000")),
            document_manifest_cache_ttl_seconds=int(
                _env("DOCUMENT_MANIFEST_CACHE_TTL_SECONDS", "300")
            ),
            langfuse_prompt_cache_ttl_seconds=int(
                _env("LANGFUSE_PROMPT_CACHE_TTL_SECONDS", "300")
            ),
            chat_fast_rag_enabled=_env_bool("CHAT_FAST_RAG_ENABLED", True),
            chat_fast_rag_min_query_terms=int(_env("CHAT_FAST_RAG_MIN_QUERY_TERMS", "3")),
            max_graph_llm_calls=int(_env("MAX_GRAPH_LLM_CALLS", "2")),
            rag_top_k=int(_env("RAG_TOP_K", "5")),
            rag_neighbor_chunks=int(_env("RAG_NEIGHBOR_CHUNKS", "1")),
            ingestion_chunk_size=int(_env("INGESTION_CHUNK_SIZE", "1500")),
            ingestion_chunk_overlap=int(_env("INGESTION_CHUNK_OVERLAP", "250")),
            rag_parallel_search_enabled=_env_bool("RAG_PARALLEL_SEARCH_ENABLED", True),
            chat_background_history_save_enabled=_env_bool("CHAT_BACKGROUND_HISTORY_SAVE_ENABLED", True),
            chat_response_guardrail_enabled=_env_bool("CHAT_RESPONSE_GUARDRAIL_ENABLED", False),
            local_data_dir=_env("LOCAL_DATA_DIR", "/app/data"),
            chroma_persist_dir=_env("CHROMA_PERSIST_DIR", "/app/data/chroma"),
            chroma_collection=_env("CHROMA_COLLECTION", "dstrmaysam-healthcare-knowledge-agent"),
            local_app_secret_file=_env("LOCAL_APP_SECRET_FILE", "/app/data/local_app_secret.json"),
            local_test_admin_enabled=_env_bool("LOCAL_TEST_ADMIN_ENABLED", False),
            local_test_admin_username=_env("LOCAL_TEST_ADMIN_USERNAME", "admin"),
            local_test_admin_password=_env("LOCAL_TEST_ADMIN_PASSWORD", "admin123"),
            postgres_host=_env("POSTGRES_HOST", "postgres"),
            postgres_port=int(_env("POSTGRES_PORT", "5432")),
            postgres_db=_env("POSTGRES_DB", "healthcare_agent"),
            postgres_user=_env("POSTGRES_USER", "healthcare_agent"),
            postgres_password=_env("POSTGRES_PASSWORD", "healthcare_agent_dev"),
            postgres_sslmode=_env("POSTGRES_SSLMODE", "disable"),
            deterministic_lookup_enabled=_env_bool("DETERMINISTIC_LOOKUP_ENABLED", True),
        )

    def public_summary(self) -> dict[str, str | int]:
        return {
            "app_env": self.app_env,
            "aws_region": self.aws_region,
            "secrets_stage": self.secrets_stage,
            "s3_bucket": self.s3_bucket,
            "s3_raw_prefix": self.s3_raw_prefix,
            "s3_manifest_key": self.s3_manifest_key,
            "opensearch_configured": str(bool(self.opensearch_endpoint)),
            "opensearch_index": self.opensearch_index,
            "azure_openai_deployment": self.azure_openai_deployment,
            "chat_history_backend": self.chat_history_backend,
            "prompt_label": self.prompt_label,
            "max_history_chars": self.max_history_chars,
            "document_manifest_cache_ttl_seconds": self.document_manifest_cache_ttl_seconds,
            "langfuse_prompt_cache_ttl_seconds": self.langfuse_prompt_cache_ttl_seconds,
            "chat_fast_rag_enabled": str(self.chat_fast_rag_enabled),
            "chat_fast_rag_min_query_terms": self.chat_fast_rag_min_query_terms,
            "max_graph_llm_calls": self.max_graph_llm_calls,
            "rag_top_k": self.rag_top_k,
            "rag_neighbor_chunks": self.rag_neighbor_chunks,
            "ingestion_chunk_size": self.ingestion_chunk_size,
            "ingestion_chunk_overlap": self.ingestion_chunk_overlap,
            "rag_parallel_search_enabled": str(self.rag_parallel_search_enabled),
            "chat_background_history_save_enabled": str(self.chat_background_history_save_enabled),
            "chat_response_guardrail_enabled": str(self.chat_response_guardrail_enabled),
            "local_data_dir": self.local_data_dir,
            "chroma_persist_dir": self.chroma_persist_dir,
            "chroma_collection": self.chroma_collection,
            "local_app_secret_file": self.local_app_secret_file,
            "local_test_admin_enabled": str(self.local_test_admin_enabled),
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_db": self.postgres_db,
            "deterministic_lookup_enabled": str(self.deterministic_lookup_enabled),
        }

    def use_local_resources(self) -> bool:
        return self.local_test_admin_enabled
