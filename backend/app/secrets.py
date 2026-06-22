from __future__ import annotations

import json
import os
import secrets as py_secrets
from dataclasses import dataclass
from pathlib import Path
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
    fast_chat_deployment: str
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
        chat_deployment = self.settings.azure_openai_deployment or str(data.get("chat_deployment", ""))
        fast_chat_deployment = (
            self.settings.azure_openai_fast_deployment
            or str(data.get("fast_chat_deployment", ""))
            or chat_deployment
        )
        required = ["endpoint", "api_key", "embedding_deployment"]
        missing = [key for key in required if not data.get(key)]
        if not chat_deployment:
            missing.append("chat_deployment or AZURE_OPENAI_DEPLOYMENT")
        if missing:
            raise SecretProviderError(
                f"Azure OpenAI secret is missing required keys: {', '.join(missing)}"
            )
        return AzureOpenAISecrets(
            endpoint=str(data["endpoint"]),
            api_key=str(data["api_key"]),
            api_version=str(data.get("api_version", "2025-04-01-preview")),
            chat_deployment=chat_deployment,
            fast_chat_deployment=fast_chat_deployment,
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


class EnvSecretProvider(SecretProvider):
    """Local-mode provider backed by environment variables and a JSON app secret file."""

    def get_json(self, secret_name: str) -> dict[str, Any]:
        if secret_name == self.settings.app_secret_name:
            return self._load_local_app_secret()
        if secret_name == self.settings.azure_openai_secret_name:
            return self._azure_secret_from_env()
        if secret_name == self.settings.langfuse_secret_name:
            return self._langfuse_secret_from_env()
        raise SecretProviderError(f"Local secret {secret_name!r} is not configured")

    def put_json(self, secret_name: str, value: dict[str, Any]) -> None:
        if secret_name != self.settings.app_secret_name:
            raise SecretProviderError("Only the local app secret can be updated in local mode")
        path = Path(self.settings.local_app_secret_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        self._cache[secret_name] = dict(value)

    def _load_local_app_secret(self) -> dict[str, Any]:
        if self.settings.app_secret_name in self._cache:
            return self._cache[self.settings.app_secret_name]
        path = Path(self.settings.local_app_secret_file)
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SecretProviderError(f"Local app secret {str(path)!r} is not valid JSON") from exc
            if not isinstance(value, dict):
                raise SecretProviderError(f"Local app secret {str(path)!r} must contain a JSON object")
        else:
            value = self._default_local_app_secret()
            self.put_json(self.settings.app_secret_name, value)
        self._cache[self.settings.app_secret_name] = value
        return value

    def _default_local_app_secret(self) -> dict[str, Any]:
        from .auth import hash_password

        username = self.settings.local_test_admin_username.strip() or "admin"
        password = self.settings.local_test_admin_password or "admin123"
        session_secret = os.getenv("LOCAL_SESSION_SECRET") or py_secrets.token_urlsafe(32)
        return {
            "session_secret": session_secret,
            "auth_users": {username: hash_password(password, iterations=1000)},
            "user_profiles": {
                username: {
                    "roles": [
                        "admin",
                        "doctor",
                        "nurse",
                        "pharmacy",
                        "clinical_governance",
                        "manager",
                        "staff",
                    ],
                    "departments": ["clinical_governance", "operations", "it", "hr", "finance"],
                    "password_change_required": False,
                }
            },
        }

    def _azure_secret_from_env(self) -> dict[str, Any]:
        chat_deployment = self.settings.azure_openai_deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
        fast_chat_deployment = (
            self.settings.azure_openai_fast_deployment
            or os.getenv("AZURE_OPENAI_FAST_DEPLOYMENT", "")
            or chat_deployment
        )
        return {
            "endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            "api_key": os.getenv("AZURE_OPENAI_API_KEY", ""),
            "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            "chat_deployment": chat_deployment,
            "fast_chat_deployment": fast_chat_deployment,
            "embedding_deployment": os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", ""),
        }

    def _langfuse_secret_from_env(self) -> dict[str, Any]:
        return {
            "public_key": os.getenv("LANGFUSE_PUBLIC_KEY", ""),
            "secret_key": os.getenv("LANGFUSE_SECRET_KEY", ""),
            "base_url": os.getenv("LANGFUSE_BASE_URL", ""),
        }
