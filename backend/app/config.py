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
    local_test_admin_enabled: bool = False
    local_test_admin_username: str = "admin"
    local_test_admin_password: str = "admin123"

    @classmethod
    def from_env(cls) -> "AppSettings":
        stage = _env("SECRETS_STAGE", "dev")
        default_prefix = f"/company-assistant/{stage}"
        origins = tuple(
            origin.strip()
            for origin in _env("CORS_ORIGINS", "http://localhost:8501").split(",")
            if origin.strip()
        )
        return cls(
            app_env=_env("APP_ENV", "local"),
            aws_region=_env("AWS_REGION", "us-east-1"),
            secrets_stage=stage,
            app_secret_name=_env("APP_SECRET_NAME", f"{default_prefix}/app"),
            azure_openai_secret_name=_env(
                "AZURE_OPENAI_SECRET_NAME", f"{default_prefix}/azure-openai"
            ),
            langfuse_secret_name=_env("LANGFUSE_SECRET_NAME", f"{default_prefix}/langfuse"),
            s3_bucket=_env("S3_BUCKET"),
            s3_raw_prefix=_env("S3_RAW_PREFIX", "raw/"),
            s3_manifest_key=_env("S3_MANIFEST_KEY", "manifests/documents.json"),
            opensearch_endpoint=_env("OPENSEARCH_ENDPOINT"),
            opensearch_index=_env("OPENSEARCH_INDEX", "company-knowledge"),
            dynamodb_chat_table=_env("DYNAMODB_CHAT_TABLE", "company_assistant_chat_history"),
            chat_history_backend=_env("CHAT_HISTORY_BACKEND", "memory"),
            cors_origins=origins,
            prompt_label=_env("PROMPT_LABEL", "production"),
            max_history_chars=int(_env("MAX_HISTORY_CHARS", "8000")),
            local_test_admin_enabled=_env_bool("LOCAL_TEST_ADMIN_ENABLED", False),
            local_test_admin_username=_env("LOCAL_TEST_ADMIN_USERNAME", "admin"),
            local_test_admin_password=_env("LOCAL_TEST_ADMIN_PASSWORD", "admin123"),
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
            "chat_history_backend": self.chat_history_backend,
            "prompt_label": self.prompt_label,
            "max_history_chars": self.max_history_chars,
            "local_test_admin_enabled": str(self.local_test_admin_enabled),
        }
