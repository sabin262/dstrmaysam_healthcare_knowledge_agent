from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .aws import boto3_client
from .config import AppSettings
from .retries import retry_transient


class SecretProviderError(RuntimeError):
    """Raised when a required secret cannot be loaded."""


@dataclass(frozen=True)
class AppSecrets:
    session_secret: str
    auth_users: dict[str, str]
    user_profiles: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class AzureOpenAISecrets:
    endpoint: str
    api_key: str
    api_version: str
    chat_deployment: str
    embedding_deployment: str


@dataclass(frozen=True)
class LangfuseSecrets:
    public_key: str
    secret_key: str
    base_url: str


class SecretProvider:
    """Loads application secrets from AWS Secrets Manager.

    The application intentionally does not read secret values from environment
    variables. Environment variables contain only secret names and non-sensitive
    configuration.
    """

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._cache: dict[str, dict[str, Any]] = {}

    @retry_transient
    def get_json(self, secret_name: str) -> dict[str, Any]:
        if secret_name in self._cache:
            return self._cache[secret_name]

        try:
            import boto3
        except ImportError as exc:
            raise SecretProviderError(
                "boto3 is required to load secrets from AWS Secrets Manager"
            ) from exc

        client = boto3_client(self.settings, "secretsmanager")
        try:
            response = client.get_secret_value(SecretId=secret_name)
        except Exception as exc:  # boto3 raises service-specific exceptions.
            raise SecretProviderError(f"Unable to load secret {secret_name!r}") from exc

        raw = response.get("SecretString")
        if not raw:
            raise SecretProviderError(f"Secret {secret_name!r} does not contain SecretString JSON")

        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SecretProviderError(f"Secret {secret_name!r} is not valid JSON") from exc

        if not isinstance(value, dict):
            raise SecretProviderError(f"Secret {secret_name!r} must contain a JSON object")

        self._cache[secret_name] = value
        return value

    @retry_transient
    def put_json(self, secret_name: str, value: dict[str, Any]) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise SecretProviderError(
                "boto3 is required to write secrets to AWS Secrets Manager"
            ) from exc

        client = boto3_client(self.settings, "secretsmanager")
        try:
            client.put_secret_value(SecretId=secret_name, SecretString=json.dumps(value))
        except Exception as exc:  # boto3 raises service-specific exceptions.
            raise SecretProviderError(f"Unable to update secret {secret_name!r}") from exc
        self._cache[secret_name] = dict(value)

    def load_app(self) -> AppSecrets:
        data = self.get_json(self.settings.app_secret_name)
        session_secret = str(data.get("session_secret", ""))
        auth_users = data.get("auth_users", {})
        user_profiles = data.get("user_profiles", {})
        if not session_secret:
            raise SecretProviderError("App secret must contain session_secret")
        if not isinstance(auth_users, dict) or not auth_users:
            raise SecretProviderError("App secret must contain non-empty auth_users map")
        if not isinstance(user_profiles, dict):
            raise SecretProviderError("App secret user_profiles must be a JSON object when provided")
        return AppSecrets(
            session_secret=session_secret,
            auth_users={str(username): str(password_hash) for username, password_hash in auth_users.items()},
            user_profiles={
                str(username): dict(profile) for username, profile in user_profiles.items() if isinstance(profile, dict)
            },
        )

    def load_azure_openai(self) -> AzureOpenAISecrets:
        data = self.get_json(self.settings.azure_openai_secret_name)
        required = ["endpoint", "api_key", "chat_deployment", "embedding_deployment"]
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise SecretProviderError(
                f"Azure OpenAI secret is missing required keys: {', '.join(missing)}"
            )
        return AzureOpenAISecrets(
            endpoint=str(data["endpoint"]),
            api_key=str(data["api_key"]),
            api_version=str(data.get("api_version", "2025-04-01-preview")),
            chat_deployment=str(data["chat_deployment"]),
            embedding_deployment=str(data["embedding_deployment"]),
        )

    def load_langfuse(self) -> LangfuseSecrets:
        data = self.get_json(self.settings.langfuse_secret_name)
        required = ["public_key", "secret_key", "base_url"]
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise SecretProviderError(f"Langfuse secret is missing required keys: {', '.join(missing)}")
        return LangfuseSecrets(
            public_key=str(data["public_key"]),
            secret_key=str(data["secret_key"]),
            base_url=str(data["base_url"]),
        )


class StaticSecretProvider(SecretProvider):
    """Test-only provider that keeps the deployed app contract intact."""

    def __init__(self, settings: AppSettings, secrets: dict[str, dict[str, Any]]):
        super().__init__(settings)
        self._static_secrets = secrets

    def get_json(self, secret_name: str) -> dict[str, Any]:
        if secret_name not in self._static_secrets:
            raise SecretProviderError(f"Static secret {secret_name!r} not configured")
        return self._static_secrets[secret_name]

    def put_json(self, secret_name: str, value: dict[str, Any]) -> None:
        self._static_secrets[secret_name] = dict(value)
        self._cache[secret_name] = dict(value)
