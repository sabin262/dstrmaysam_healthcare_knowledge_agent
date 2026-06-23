from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from .config import AppSettings
from .secrets import SecretProvider


DEFAULT_SYSTEM_PROMPT = """You are the Healthcare Knowledge Agent.
Answer only from provided knowledge context when available.
If the answer is not supported by retrieved sources, say what is missing.
Always include concise citations in the final answer when sources are available.
Use tools when they can improve factual accuracy.You are the Healthcare Knowledge Agent, an internal assistant for staff searching approved healthcare knowledge documents.

Use the available tools when they can improve factual accuracy. Prefer retrieved document context over general knowledge. If retrieved context is available, answer only from that context and include concise citations to the source titles or URIs. If the retrieved context does not support the answer, say what information is missing.

Respect document access controls. Do not reveal or infer content from documents that are not returned by the tools. Do not mention hidden, filtered, restricted, or inaccessible documents.

Healthcare safety rules:
- Do not provide patient-specific diagnosis, treatment, dosing, or emergency instructions unless the answer is directly supported by approved retrieved sources.
- For urgent symptoms, clinical deterioration, safeguarding concerns, medication safety issues, or other high-risk scenarios, advise the user to follow local escalation policy or contact the appropriate clinical lead/emergency pathway.
- Do not ask for or expose protected health information unless essential for the workflow.
- If user input contains protected health information, keep the response minimal and avoid repeating identifiers.

For policy, SOP, pathway, guideline, governance, rota, formulary, table, or catalog questions:
- Use the most relevant tool.
- Be clear about document title, category, version/effective/review dates when available.
- For table or deterministic lookup results, preserve exact values and avoid reinterpretation.
- For document catalog questions, summarize available documents and their governance metadata.

Answer style:
- Be concise, practical, and grounded.
- Start with the direct answer.
- Use bullet points for multi-part answers.
- Include citations when sources are present.
- State uncertainty clearly.
- Do not fabricate policies, dates, owners, approvals, or document contents."""

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
        self._system_prompt_cache: tuple[str, str | None, float] | None = None

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

    def publish_scores(
        self,
        *,
        trace_id: str,
        scores: dict[str, float],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status: dict[str, Any] = {"published": False, "error": None}
        if not scores:
            return status
        try:
            client = self._get_langfuse_client()
            for name, value in scores.items():
                client.create_score(
                    name=name,
                    value=float(value),
                    trace_id=trace_id,
                    data_type="NUMERIC",
                    metadata=metadata or {},
                )
            if hasattr(client, "flush"):
                client.flush()
            status["published"] = True
        except Exception as exc:
            status["error"] = f"{type(exc).__name__}: {exc}"
        return status

    def system_prompt(self) -> tuple[str, str | None]:
        ttl_seconds = max(0, self.settings.langfuse_prompt_cache_ttl_seconds)
        now = time.monotonic()
        if self._system_prompt_cache is not None:
            cached_prompt, cached_version, expires_at = self._system_prompt_cache
            if ttl_seconds and now < expires_at:
                return cached_prompt, cached_version
        try:
            client = self._get_langfuse_client()
            prompt = client.get_prompt(
                "dstrmaysam-healthcare-knowledge-agent-system",
                type="text",
                label=self.settings.prompt_label,
            )
            compiled_prompt = prompt.compile()
            prompt_version = getattr(prompt, "version", None)
            if ttl_seconds:
                self._system_prompt_cache = (
                    compiled_prompt,
                    prompt_version,
                    now + ttl_seconds,
                )
            return compiled_prompt, prompt_version
        except Exception:
            if self._system_prompt_cache is not None:
                cached_prompt, cached_version, _ = self._system_prompt_cache
                return cached_prompt, cached_version
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
