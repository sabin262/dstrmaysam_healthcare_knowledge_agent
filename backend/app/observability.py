from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from .config import AppSettings
from .secrets import SecretProvider


DEFAULT_SYSTEM_PROMPT = """You are the Dstrmaysam Healthcare Knowledge Agent.
Answer only from provided knowledge context when available.
If the answer is not supported by retrieved sources, say what is missing.
Always include concise citations in the final answer when sources are available.
Use tools when they can improve factual accuracy."""


@dataclass
class TraceContext:
    trace_id: str
    _manager: Any | None = None
    _span: Any | None = None

    def __enter__(self) -> "TraceContext":
        if self._manager is not None:
            try:
                self._span = self._manager.__enter__()
            except Exception:
                self._span = None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if self._manager is not None:
            try:
                return bool(self._manager.__exit__(exc_type, exc, traceback))
            except Exception:
                return False
        return False

    def update(self, *, output: Any | None = None, metadata: dict[str, Any] | None = None) -> None:
        if self._span is None:
            return
        try:
            update_payload: dict[str, Any] = {}
            if output is not None:
                update_payload["output"] = output
            if metadata is not None:
                update_payload["metadata"] = metadata
            if update_payload:
                self._span.update(**update_payload)
        except Exception:
            return


class ObservabilityClient:
    def __init__(self, settings: AppSettings, secret_provider: SecretProvider):
        self.settings = settings
        self.secret_provider = secret_provider
        self._callbacks: list[Any] | None = None
        self._langfuse_client: Any | None = None

    def new_trace_id(self) -> str:
        return uuid.uuid4().hex

    def chat_trace(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        metadata: dict[str, Any] | None = None,
    ) -> TraceContext:
        trace_id = self.new_trace_id()
        try:
            client = self._get_langfuse_client()
            if hasattr(client, "create_trace_id"):
                trace_id = client.create_trace_id()
            trace_metadata = {"user_id": user_id, "session_id": session_id}
            if metadata:
                trace_metadata.update(metadata)
            try:
                manager = client.start_as_current_observation(
                    name="dstrmaysam-healthcare-knowledge-agent-chat",
                    as_type="agent",
                    trace_context={"trace_id": trace_id},
                    input={"query": query},
                    metadata=trace_metadata,
                )
            except TypeError:
                manager = client.start_as_current_observation(
                    name="dstrmaysam-healthcare-knowledge-agent-chat",
                    trace_context={"trace_id": trace_id},
                    input={"query": query},
                    metadata=trace_metadata,
                )
            return TraceContext(trace_id=trace_id, _manager=manager)
        except Exception:
            return TraceContext(trace_id=trace_id)

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
                "dstrmaysam-healthcare-knowledge-agent-system",
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
