from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import AppSettings
from .healthcare import (
    HealthcareAccessControl,
    HealthcareAuditLogger,
    HealthcareSafetyGuard,
    HealthcareUserContext,
    PHIRedactor,
)
from .healthcare_tools import build_healthcare_agent_tools
from .history import ChatHistoryRepository, ChatMessage, build_history_context
from .observability import ObservabilityClient
from .retrieval import RetrievalHit, RetrievalService
from .secrets import SecretProvider
from .storage import DocumentStore
from .tools import build_agent_tools, format_retrieval_hits


HEALTHCARE_TOOL_NAMES = [
    "document_search",
    "policy_search",
    "catalogue_search",
    "calendar_rota_lookup",
    "formulary_table_lookup",
    "safety_guard",
]


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text.split()) * 1.3)) if text else 0


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


@dataclass
class AgentResult:
    session_id: str
    answer: str
    sources: list[dict[str, Any]]
    tools_used: list[str]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    trace_id: str
    prompt_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class KnowledgeAgent:
    def __init__(
        self,
        settings: AppSettings,
        secret_provider: SecretProvider,
        history: ChatHistoryRepository,
        retrieval: RetrievalService,
        documents: DocumentStore,
        observability: ObservabilityClient,
    ):
        self.settings = settings
        self.secret_provider = secret_provider
        self.history = history
        self.retrieval = retrieval
        self.documents = documents
        self.observability = observability
        self.access = HealthcareAccessControl()
        self.redactor = PHIRedactor()
        self.safety = HealthcareSafetyGuard(self.redactor)
        self.audit = HealthcareAuditLogger()
        self.tools = build_agent_tools(retrieval, documents)
        self._llm: Any | None = None

    def answer(
        self,
        user_id: str,
        query: str,
        session_id: str | None = None,
        user_context: HealthcareUserContext | None = None,
    ) -> AgentResult:
        started = time.perf_counter()
        session_id = session_id or uuid.uuid4().hex
        user_context = user_context or HealthcareUserContext(user_id=user_id)
        redaction = self.redactor.redact(query)
        safe_query = redaction.redacted_text
        prior_messages = self.history.load_messages(user_id, session_id)
        history_context = build_history_context(prior_messages, self.settings.max_history_chars)
        trace_id = self.observability.new_trace_id()

        healthcare_tools = build_healthcare_agent_tools(
            retrieval=self.retrieval,
            documents=self.documents,
            user=user_context,
            access=self.access,
            safety=self.safety,
        )
        original_tools = self.tools
        try:
            self.tools = original_tools + healthcare_tools
            rag_hits = self.access.filter_hits(user_context, self.retrieval.search(safe_query))
            catalog_json = self._run_tool("document_catalog", safe_query)
            table_json = self._run_tool("table_lookup", safe_query)
            policy_json = self._run_tool("policy_search", safe_query)
            catalogue_json = self._run_tool("catalogue_search", safe_query)
            rota_json = self._run_tool("calendar_rota_lookup", safe_query)
            formulary_json = self._run_tool("formulary_table_lookup", safe_query)
            safety_json = self._run_tool("safety_guard", safe_query)
            tools_used = [
                "rag_search",
                "document_search",
                "document_catalog",
                "table_lookup",
                "policy_search",
                "catalogue_search",
                "calendar_rota_lookup",
                "formulary_table_lookup",
                "safety_guard",
            ]
            tool_context = self._build_tool_context(
                rag_hits,
                catalog_json,
                table_json,
                policy_json,
                catalogue_json,
                rota_json,
                formulary_json,
                safety_json,
            )
            system_prompt, prompt_version = self.observability.system_prompt()

            answer = self._generate_answer(
                system_prompt=system_prompt,
                history_context=history_context,
                tool_context=tool_context,
                query=safe_query,
            )

            sources = [
                {
                    "title": hit.title,
                    "uri": hit.uri,
                    "score": hit.score,
                    "metadata": hit.metadata,
                }
                for hit in rag_hits
                if hit.uri
            ]
            input_tokens = estimate_tokens("\n".join([system_prompt, history_context, tool_context, query]))
            output_tokens = estimate_tokens(answer)
            latency_ms = int((time.perf_counter() - started) * 1000)
            safety_assessment = self.safety.assess(safe_query, sources)
            audit_event = self.audit.log_chat_event(
                user=user_context,
                session_id=session_id,
                query=query,
                tools_used=tools_used,
                sources=sources,
                trace_id=trace_id,
                safety=safety_assessment,
                token_usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
            )

            self.history.save_message(user_id, session_id, ChatMessage(role="user", content=query))
            self.history.save_message(
                user_id,
                session_id,
                ChatMessage(
                    role="assistant",
                    content=answer,
                    metadata={
                        "sources": sources,
                        "tools_used": tools_used,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "latency_ms": latency_ms,
                        "trace_id": trace_id,
                        "prompt_version": prompt_version,
                        "safety": safety_assessment.as_dict(),
                        "phi_redaction": redaction.findings,
                        "audit_event": audit_event,
                    },
                ),
            )
        finally:
            self.tools = original_tools

        return AgentResult(
            session_id=session_id,
            answer=answer,
            sources=sources,
            tools_used=tools_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            trace_id=trace_id,
            prompt_version=prompt_version,
            metadata={
                "safety": safety_assessment.as_dict(),
                "phi_redaction": redaction.findings,
                "audit_event": audit_event,
            },
        )

    def _run_tool(self, name: str, query: str) -> str:
        for tool in self.tools:
            if tool.name == name:
                try:
                    return tool.run(query)
                except Exception as exc:
                    return f"Tool {name} failed: {exc}"
        return f"Tool {name} is not registered."

    def _build_tool_context(
        self,
        rag_hits: list[RetrievalHit],
        catalog_json: str,
        table_json: str,
        policy_json: str = "",
        catalogue_json: str = "",
        rota_json: str = "",
        formulary_json: str = "",
        safety_json: str = "",
    ) -> str:
        return "\n\n".join(
            [
                "RAG search results:\n" + format_retrieval_hits(rag_hits),
                "Document catalog results:\n" + catalog_json[:3000],
                "CSV/table lookup results:\n" + table_json[:3000],
                "Healthcare policy search results:\n" + policy_json[:3000],
                "Healthcare catalogue search results:\n" + catalogue_json[:3000],
                "Healthcare calendar/rota lookup results:\n" + rota_json[:3000],
                "Healthcare formulary/table lookup results:\n" + formulary_json[:3000],
                "Healthcare safety guard results:\n" + safety_json[:1500],
            ]
        )

    def _generate_answer(
        self,
        *,
        system_prompt: str,
        history_context: str,
        tool_context: str,
        query: str,
    ) -> str:
        llm = self._get_llm()
        user_prompt = (
            "Conversation history:\n"
            f"{history_context}\n\n"
            "Tool context:\n"
            f"{tool_context}\n\n"
            "User question:\n"
            f"{query}\n\n"
            "Answer with citations when sources are present."
        )
        if llm is None:
            return self._offline_answer(query=query, tool_context=tool_context)

        callbacks = self.observability.callbacks()
        config = {"callbacks": callbacks} if callbacks else None
        try:
            return self._call_langchain_agent(llm, system_prompt, user_prompt, config)
        except Exception:
            try:
                response = llm.invoke(
                    [("system", system_prompt), ("human", user_prompt)],
                    config=config,
                )
                return _message_text(response)
            except Exception:
                return self._offline_answer(query=query, tool_context=tool_context)

    def _call_langchain_agent(
        self,
        llm: Any,
        system_prompt: str,
        user_prompt: str,
        config: dict[str, Any] | None,
    ) -> str:
        from langchain.agents import create_agent

        def rag_search(query: str) -> str:
            """Search indexed company documents using RAG and return source-backed context."""
            return self._run_tool("rag_search", query)

        def document_catalog(query: str) -> str:
            """List and filter available company documents from the S3 manifest."""
            return self._run_tool("document_catalog", query)

        def table_lookup(query: str) -> str:
            """Look up exact values from CSV files stored in S3."""
            return self._run_tool("table_lookup", query)

        def policy_search(query: str) -> str:
            """Focused retrieval over approved healthcare policies, SOPs, pathways, and guidelines."""
            return self._run_tool("policy_search", query)

        def catalogue_search(query: str) -> str:
            """Find healthcare services, owners, systems, departments, and approved tools."""
            return self._run_tool("catalogue_search", query)

        def calendar_rota_lookup(query: str) -> str:
            """Lookup clinics, training, rota, and on-call schedules from approved sources."""
            return self._run_tool("calendar_rota_lookup", query)

        def formulary_table_lookup(query: str) -> str:
            """Lookup formulary rows, restricted medicines, approval rules, codes, and structured facts."""
            return self._run_tool("formulary_table_lookup", query)

        def safety_guard(query: str) -> str:
            """Detect clinical risk, missing sources, PHI exposure, or escalation needs."""
            return self._run_tool("safety_guard", query)

        agent = create_agent(
            model=llm,
            tools=[
                rag_search,
                document_catalog,
                table_lookup,
                policy_search,
                catalogue_search,
                calendar_rota_lookup,
                formulary_table_lookup,
                safety_guard,
            ],
            system_prompt=system_prompt,
        )
        result = agent.invoke({"messages": [{"role": "user", "content": user_prompt}]}, config=config)
        messages = result.get("messages", []) if isinstance(result, dict) else []
        if messages:
            return _message_text(messages[-1])
        return _message_text(result)

    def _get_llm(self) -> Any | None:
        if self._llm is not None:
            return self._llm
        try:
            from langchain_openai import AzureChatOpenAI

            secrets = self.secret_provider.load_azure_openai()
            self._llm = AzureChatOpenAI(
                azure_endpoint=secrets.endpoint,
                api_key=secrets.api_key,
                api_version=secrets.api_version,
                azure_deployment=secrets.chat_deployment,
                temperature=0,
                timeout=60,
                max_retries=2,
            )
        except Exception:
            self._llm = None
        return self._llm

    def _offline_answer(self, *, query: str, tool_context: str) -> str:
        if "No relevant document chunks found." in tool_context:
            return (
                "I could not find enough indexed company context to answer this confidently. "
                "Please ingest relevant documents into S3/OpenSearch and try again."
            )
        context_preview = tool_context.split("RAG search results:", 1)[-1].strip()[:1500]
        return (
            "Based on the retrieved company context, here is the best available answer:\n\n"
            f"{context_preview}\n\n"
            "This fallback answer was generated without an LLM call because the Azure OpenAI "
            "configuration was unavailable."
        )

    def registered_tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools] + HEALTHCARE_TOOL_NAMES
