from __future__ import annotations

import os
import uuid
from typing import Any

from .config import AppSettings
from .secrets import SecretProvider


DEFAULT_SYSTEM_PROMPT = """You are an internal company knowledge assistant.
Answer only from provided company context when available.
If the answer is not supported by retrieved sources, say what is missing.
Always include concise citations in the final answer when sources are available.
Use tools when they can improve factual accuracy."""


class ObservabilityClient:
    def __init__(self, settings: AppSettings, secret_provider: SecretProvider):
        self.settings = settings
        self.secret_provider = secret_provider
        self._callbacks: list[Any] | None = None
        self._langfuse_client: Any | None = None

    def new_trace_id(self) -> str:
        return uuid.uuid4().hex

    def callbacks(self) -> list[Any]:
        if self._callbacks is not None:
            return self._callbacks
        try:
            secrets = self.secret_provider.load_langfuse()
            os.environ["LANGFUSE_PUBLIC_KEY"] = secrets.public_key
            os.environ["LANGFUSE_SECRET_KEY"] = secrets.secret_key
            os.environ["LANGFUSE_BASE_URL"] = secrets.base_url
            from langfuse.langchain import CallbackHandler

            self._callbacks = [CallbackHandler()]
        except Exception:
            self._callbacks = []
        return self._callbacks

    def system_prompt(self) -> tuple[str, str | None]:
        try:
            client = self._get_langfuse_client()
            prompt = client.get_prompt(
                "company-assistant-system",
                type="text",
                label=self.settings.prompt_label,
            )
            return prompt.compile(), getattr(prompt, "version", None)
        except Exception:
            return DEFAULT_SYSTEM_PROMPT, None

    def _get_langfuse_client(self) -> Any:
        if self._langfuse_client is not None:
            return self._langfuse_client
        secrets = self.secret_provider.load_langfuse()
        os.environ["LANGFUSE_PUBLIC_KEY"] = secrets.public_key
        os.environ["LANGFUSE_SECRET_KEY"] = secrets.secret_key
        os.environ["LANGFUSE_BASE_URL"] = secrets.base_url
        from langfuse import get_client

        self._langfuse_client = get_client()
        return self._langfuse_client

