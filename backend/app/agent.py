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
from .tools import (
    AgentTool,
    build_agent_tools,
    document_matches_catalog_query,
    format_retrieval_hits,
)


HEALTHCARE_TOOL_NAMES = [
    "document_search",
    "policy_search",
    "catalogue_search",
    "calendar_rota_lookup",
    "formulary_table_lookup",
    "safety_guard",
]
RETRIEVAL_SOURCE_TOOLS = {"rag_search", "document_search", "policy_search"}
MAX_GRAPH_LLM_CALLS = 5
CATALOG_RAG_CANDIDATE_LIMIT = 8
POLICY_DOMAINS = {"clinical_policy", "admin_policy", "compliance"}
POLICY_DOCUMENT_TYPES = {"policy", "sop", "pathway", "guideline"}


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
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or content)
    return str(content)


def _message_tool_calls(message: Any) -> list[Any]:
    calls = getattr(message, "tool_calls", None)
    if calls is None and isinstance(message, dict):
        calls = message.get("tool_calls")
    return list(calls or [])


def _tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or "")


def _tool_call_id(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or uuid.uuid4().hex)
    return str(getattr(tool_call, "id", "") or uuid.uuid4().hex)


def _tool_call_args(tool_call: Any) -> Any:
    if isinstance(tool_call, dict):
        return tool_call.get("args") or {}
    return getattr(tool_call, "args", {}) or {}


def _tool_query(args: Any, default_query: str) -> str:
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        for key in ("query", "question", "input", "text"):
            if key in args and args[key] is not None:
                return str(args[key])
    return default_query


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for source in sources:
        key = (str(source.get("uri", "")), str(source.get("title", "")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(source)
    return unique


def _source_dicts_from_hits(hits: list[RetrievalHit]) -> list[dict[str, Any]]:
    return [
        {
            "title": hit.title,
            "uri": hit.uri,
            "score": hit.score,
            "metadata": hit.metadata,
            "snippet": hit.text[:1200] if hit.text else None,
        }
        for hit in hits
        if hit.uri
    ]


def _make_system_message(content: str) -> Any:
    try:
        from langchain_core.messages import SystemMessage

        return SystemMessage(content=content)
    except Exception:
        return {"role": "system", "content": content}


def _make_human_message(content: str) -> Any:
    try:
        from langchain_core.messages import HumanMessage

        return HumanMessage(content=content)
    except Exception:
        return {"role": "user", "content": content}


def _make_tool_message(content: str, tool_call_id: str) -> Any:
    try:
        from langchain_core.messages import ToolMessage

        return ToolMessage(content=content, tool_call_id=tool_call_id)
    except Exception:
        return {"role": "tool", "content": content, "tool_call_id": tool_call_id}


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


@dataclass
class GraphAgentResult:
    answer: str
    sources: list[dict[str, Any]]
    tools_used: list[str]
    tool_context: str
    catalog_guidance: list[dict[str, Any]] = field(default_factory=list)


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

        with self.observability.chat_trace(
            user_id=user_id,
            session_id=session_id,
            query=safe_query,
            metadata={"roles": list(user_context.roles), "departments": list(user_context.departments)},
        ) as trace:
            trace_id = trace.trace_id
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
                system_prompt, prompt_version = self.observability.system_prompt()
                initial_safety = self.safety.assess(safe_query, sources=[])
                graph_result = self._generate_agent_response(
                    system_prompt=system_prompt,
                    history_context=history_context,
                    query=safe_query,
                    user_context=user_context,
                    initial_safety=initial_safety.as_dict(),
                )

                answer = graph_result.answer
                sources = graph_result.sources
                tools_used = graph_result.tools_used
                catalog_guidance = graph_result.catalog_guidance
                input_tokens = estimate_tokens(
                    "\n".join([system_prompt, history_context, graph_result.tool_context, query])
                )
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
                            "catalog_guidance": catalog_guidance,
                        },
                    ),
                )
                trace.update(
                    output={"answer": answer, "sources": sources},
                    metadata={
                        "tools_used": tools_used,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "latency_ms": latency_ms,
                        "safety": safety_assessment.as_dict(),
                        "prompt_version": prompt_version,
                        "catalog_guidance": catalog_guidance,
                    },
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
                "catalog_guidance": catalog_guidance,
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

    def _retrieval_hits_for_tool(
        self, name: str, query: str, user_context: HealthcareUserContext
    ) -> tuple[list[RetrievalHit], dict[str, Any]]:
        candidate_keys = self._catalog_candidate_keys(name, query, user_context)
        hits = self.retrieval.search(query, document_keys=candidate_keys or None)
        if name == "policy_search":
            filtered: list[RetrievalHit] = []
            for hit in hits:
                domain = str(hit.metadata.get("domain", "")).lower()
                document_type = str(hit.metadata.get("document_type", "")).lower()
                if domain in POLICY_DOMAINS or document_type in POLICY_DOCUMENT_TYPES:
                    filtered.append(hit)
            hits = filtered or hits
        guidance = {
            "tool": name,
            "query": query,
            "candidate_keys": candidate_keys,
            "candidate_count": len(candidate_keys),
            "catalog_filter_applied": bool(candidate_keys),
            "fallback_to_broad_search": not bool(candidate_keys),
        }
        return self.access.filter_hits(user_context, hits), guidance

    def _catalog_candidate_keys(
        self, name: str, query: str, user_context: HealthcareUserContext
    ) -> list[str]:
        try:
            records = self.access.filter_documents(user_context, self.documents.list_documents())
        except Exception:
            return []

        matches = [record for record in records if document_matches_catalog_query(record, query)]
        if name == "policy_search":
            policy_matches = [
                record for record in matches if self._is_policy_catalog_record(record.metadata)
            ]
            if policy_matches:
                matches = policy_matches

        candidate_keys: list[str] = []
        seen: set[str] = set()
        for record in matches:
            if not record.key or record.key in seen:
                continue
            seen.add(record.key)
            candidate_keys.append(record.key)
            if len(candidate_keys) >= CATALOG_RAG_CANDIDATE_LIMIT:
                break
        return candidate_keys

    def _is_policy_catalog_record(self, metadata: dict[str, Any]) -> bool:
        domain = str(metadata.get("domain", "")).lower()
        document_type = str(metadata.get("document_type", "")).lower()
        return domain in POLICY_DOMAINS or document_type in POLICY_DOCUMENT_TYPES

    def _run_graph_tool(
        self, name: str, query: str, user_context: HealthcareUserContext
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        if name in RETRIEVAL_SOURCE_TOOLS:
            hits, guidance = self._retrieval_hits_for_tool(name, query, user_context)
            return format_retrieval_hits(hits), _source_dicts_from_hits(hits), [guidance]
        return self._run_tool(name, query), [], []

    def _tool_callables(self) -> list[Any]:
        callables: list[Any] = []
        for tool in self.tools:
            callables.append(self._make_tool_callable(tool))
        return callables

    def _make_tool_callable(self, tool: AgentTool) -> Any:
        def graph_tool(query: str) -> str:
            """Run an internal assistant tool."""
            return self._run_tool(tool.name, query)

        graph_tool.__name__ = tool.name
        graph_tool.__doc__ = tool.description
        return graph_tool

    def _graph_user_prompt(
        self,
        *,
        history_context: str,
        query: str,
        initial_safety: dict[str, Any],
    ) -> str:
        return (
            "Conversation history:\n"
            f"{history_context}\n\n"
            "Initial safety assessment:\n"
            f"{json.dumps(initial_safety, indent=2)}\n\n"
            "User question:\n"
            f"{query}\n\n"
            "Use tools when they can improve factual accuracy. "
            "Answer with citations when sources are present."
        )

    def _generate_agent_response(
        self,
        *,
        system_prompt: str,
        history_context: str,
        query: str,
        user_context: HealthcareUserContext,
        initial_safety: dict[str, Any],
    ) -> GraphAgentResult:
        user_prompt = self._graph_user_prompt(
            history_context=history_context,
            query=query,
            initial_safety=initial_safety,
        )
        llm = self._get_llm()
        if llm is None:
            return self._offline_graph_answer(query=query, user_context=user_context)

        callbacks = self.observability.callbacks()
        config = {"callbacks": callbacks} if callbacks else None
        try:
            return self._call_langgraph_agent(
                llm=llm,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                original_query=query,
                user_context=user_context,
                config=config,
            )
        except Exception:
            try:
                response = self._invoke_model(
                    llm,
                    [_make_system_message(system_prompt), _make_human_message(user_prompt)],
                    config,
                )
                return GraphAgentResult(
                    answer=_message_text(response),
                    sources=[],
                    tools_used=[],
                    tool_context="No graph tools were executed.",
                )
            except Exception:
                return self._offline_graph_answer(query=query, user_context=user_context)

    def _offline_graph_answer(
        self, *, query: str, user_context: HealthcareUserContext
    ) -> GraphAgentResult:
        tool_output, sources, catalog_guidance = self._run_graph_tool("rag_search", query, user_context)
        tool_context = "RAG search results:\n" + tool_output
        return GraphAgentResult(
            answer=self._offline_answer(query=query, tool_context=tool_context),
            sources=sources,
            tools_used=["rag_search"],
            tool_context=tool_context,
            catalog_guidance=catalog_guidance,
        )

    def _invoke_model(self, model: Any, messages: list[Any], config: dict[str, Any] | None) -> Any:
        if config:
            return model.invoke(messages, config=config)
        return model.invoke(messages)

    def _call_langgraph_agent(
        self,
        *,
        llm: Any,
        system_prompt: str,
        user_prompt: str,
        original_query: str,
        user_context: HealthcareUserContext,
        config: dict[str, Any] | None,
    ) -> GraphAgentResult:
        try:
            from langgraph.graph import END, START, StateGraph
        except Exception:
            return self._call_tool_loop_agent(
                llm=llm,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                original_query=original_query,
                user_context=user_context,
                config=config,
            )

        try:
            from typing_extensions import TypedDict
        except Exception:
            from typing import TypedDict  # type: ignore

        class GraphState(TypedDict, total=False):
            result: GraphAgentResult

        def agent_loop(state: GraphState) -> GraphState:
            return {
                "result": self._call_tool_loop_agent(
                    llm=llm,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    original_query=original_query,
                    user_context=user_context,
                    config=config,
                )
            }

        graph_builder = StateGraph(GraphState)
        graph_builder.add_node("agent_loop", agent_loop)
        graph_builder.add_edge(START, "agent_loop")
        graph_builder.add_edge("agent_loop", END)
        graph = graph_builder.compile()

        final_state = graph.invoke({})
        return final_state["result"]

    def _call_tool_loop_agent(
        self,
        *,
        llm: Any,
        system_prompt: str,
        user_prompt: str,
        original_query: str,
        user_context: HealthcareUserContext,
        config: dict[str, Any] | None,
    ) -> GraphAgentResult:
        tool_names = {tool.name for tool in self.tools}
        bound_llm = llm.bind_tools(self._tool_callables()) if hasattr(llm, "bind_tools") else llm
        messages = [_make_system_message(system_prompt), _make_human_message(user_prompt)]
        tools_used: list[str] = []
        sources: list[dict[str, Any]] = []
        tool_outputs: list[str] = []
        catalog_guidance: list[dict[str, Any]] = []

        for _ in range(MAX_GRAPH_LLM_CALLS):
            response = self._invoke_model(bound_llm, messages, config)
            messages.append(response)
            tool_calls = _message_tool_calls(response)
            if not tool_calls:
                return GraphAgentResult(
                    answer=_message_text(response),
                    sources=_dedupe_sources(sources),
                    tools_used=tools_used,
                    tool_context="\n\n".join(tool_outputs) or "No graph tools were executed.",
                    catalog_guidance=catalog_guidance,
                )
            for tool_call in tool_calls:
                name = _tool_call_name(tool_call)
                query = _tool_query(_tool_call_args(tool_call), original_query)
                tool_call_id = _tool_call_id(tool_call)
                if name not in tool_names:
                    output = f"Tool {name!r} is not registered."
                    new_sources = []
                    new_guidance = []
                else:
                    output, new_sources, new_guidance = self._run_graph_tool(
                        name, query, user_context
                    )
                    tools_used.append(name)
                sources.extend(new_sources)
                catalog_guidance.extend(new_guidance)
                tool_outputs.append(f"{name}({query}):\n{output}")
                messages.append(_make_tool_message(output, tool_call_id))

        messages.append(
            _make_human_message(
                "Tool-call limit reached. Provide the best final answer using the "
                "tool results already available. Do not call more tools."
            )
        )
        response = self._invoke_model(llm, messages, config)
        messages.append(response)
        return GraphAgentResult(
            answer=_message_text(response),
            sources=_dedupe_sources(sources),
            tools_used=tools_used,
            tool_context="\n\n".join(tool_outputs) or "No graph tools were executed.",
            catalog_guidance=catalog_guidance,
        )

    def _graph_state_result(self, state: dict[str, Any]) -> GraphAgentResult:
        messages = state.get("messages", [])
        answer = _message_text(messages[-1]) if messages else ""
        return GraphAgentResult(
            answer=answer,
            sources=_dedupe_sources(list(state.get("sources", []))),
            tools_used=list(state.get("tools_used", [])),
            tool_context="\n\n".join(state.get("tool_outputs", []))
            or "No graph tools were executed.",
            catalog_guidance=list(state.get("catalog_guidance", [])),
        )

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
                "I could not find enough indexed knowledge context to answer this confidently. "
                "Please ingest relevant documents into S3/OpenSearch and try again."
            )
        context_preview = tool_context.split("RAG search results:", 1)[-1].strip()[:1500]
        return (
            "Based on the retrieved knowledge context, here is the best available answer:\n\n"
            f"{context_preview}\n\n"
            "This fallback answer was generated without an LLM call because the Azure OpenAI "
            "configuration was unavailable."
        )

    def registered_tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools] + HEALTHCARE_TOOL_NAMES
