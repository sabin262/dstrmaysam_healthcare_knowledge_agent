import unittest
from dataclasses import replace
from threading import Event

from backend.app.agent import KnowledgeAgent
from backend.app.agent import _planned_tool_names
from backend.app.config import AppSettings
from backend.app.history import InMemoryChatHistoryRepository
from backend.app.observability import ObservabilityClient
from backend.app.retrieval import RetrievalHit, RetrievalService
from backend.app.secrets import StaticSecretProvider
from backend.app.storage import DocumentRecord, DocumentStore


class FakeRetrieval(RetrievalService):
    def __init__(self):
        self.calls = []

    def search(self, query: str, top_k: int = 5, document_keys=None):
        self.calls.append(
            {"query": query, "top_k": top_k, "document_keys": list(document_keys or [])}
        )
        hits = [
            RetrievalHit(
                title="Leave Policy",
                uri="s3://bucket/raw/leave.md",
                text="Employees receive annual leave according to the HR leave policy.",
                score=1.0,
                metadata={"department": "HR", "domain": "admin_policy", "document_type": "policy"},
            ),
            RetrievalHit(
                title="Sepsis SOP",
                uri="s3://bucket/raw/clinical/sepsis-sop.md",
                text="The sepsis SOP requires escalation and documented handoff.",
                score=0.92,
                metadata={"domain": "clinical_policy", "document_type": "sop"},
            ),
            RetrievalHit(
                title="Sepsis Newsletter",
                uri="s3://bucket/raw/news/sepsis-newsletter.md",
                text="A newsletter mentioned sepsis awareness week.",
                score=0.71,
                metadata={"domain": "general", "document_type": "document"},
            ),
        ]
        if document_keys:
            allowed = set(document_keys)
            hits = [hit for hit in hits if hit.uri.removeprefix("s3://bucket/") in allowed]
        if "unmatched" in query:
            return hits[:1]
        return [
            hit
            for hit in hits
            if not document_keys or hit.uri.removeprefix("s3://bucket/") in set(document_keys)
        ][:top_k]


class FakeDocuments(DocumentStore):
    def __init__(self):
        pass

    def list_documents(self):
        return [
            DocumentRecord(
                title="Leave Policy",
                uri="s3://bucket/raw/leave.md",
                key="raw/leave.md",
                content_type="text/markdown",
                metadata={"department": "HR", "domain": "admin_policy", "document_type": "policy"},
            ),
            DocumentRecord(
                title="Sepsis SOP",
                uri="s3://bucket/raw/clinical/sepsis-sop.md",
                key="raw/clinical/sepsis-sop.md",
                content_type="text/markdown",
                metadata={"domain": "clinical_policy", "document_type": "sop"},
            ),
            DocumentRecord(
                title="Sepsis Newsletter",
                uri="s3://bucket/raw/news/sepsis-newsletter.md",
                key="raw/news/sepsis-newsletter.md",
                content_type="text/markdown",
                metadata={"domain": "general", "document_type": "document"},
            ),
        ]

    def lookup_table(self, query: str, limit: int = 10):
        return [{"source": "s3://bucket/raw/table.csv", "row": {"key": "leave", "value": "20 days"}}]


class FakeAIMessage:
    def __init__(self, content: str = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


def fake_ai_message(content: str = "", tool_calls=None):
    try:
        from langchain_core.messages import AIMessage

        return AIMessage(content=content, tool_calls=tool_calls or [])
    except Exception:
        return FakeAIMessage(content, tool_calls)


def message_content(message):
    if isinstance(message, dict):
        return message.get("content", "")
    return message.content


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bound_tools = []
        self.messages = []

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def invoke(self, messages, config=None):
        self.messages.append(messages)
        if not self.responses:
            return FakeAIMessage("No fake response configured")
        return self.responses.pop(0)


class FakeLookupResult:
    def to_json(self):
        return '{"category":"doctor","message":"Found 1 matching row(s).","rows":[{"doctor":"Dr Smith","status":"on call"}]}'


class FakeDeterministicLookup:
    def __init__(self):
        self.calls = []

    def lookup(self, query, user):
        self.calls.append({"query": query, "user": user.user_id})
        return FakeLookupResult()


class EventHistory(InMemoryChatHistoryRepository):
    def __init__(self):
        super().__init__()
        self.saved = Event()

    def save_message(self, user_id, session_id, message):
        super().save_message(user_id, session_id, message)
        if len(self.load_messages(user_id, session_id)) >= 2:
            self.saved.set()


def settings(**overrides):
    app_settings = AppSettings(
        app_env="test",
        aws_region="eu-west-2",
        secrets_stage="test",
        app_secret_name="/test/app",
        azure_openai_secret_name="/test/azure",
        langfuse_secret_name="/test/langfuse",
        s3_bucket="bucket",
        s3_raw_prefix="raw/",
        s3_manifest_key="manifest.json",
        opensearch_endpoint="",
        opensearch_index="idx",
        dynamodb_chat_table="table",
        chat_history_backend="memory",
        cors_origins=(),
        prompt_label="dev",
        max_history_chars=1000,
    )
    return replace(app_settings, **overrides)


def make_agent(fake_llm=None, retrieval=None, documents=None, app_settings=None, history=None):
    app_settings = app_settings or settings()
    secret_provider = StaticSecretProvider(
        app_settings,
        {
            "/test/app": {
                "session_secret": "secret",
                "auth_users": {"user": "hash"},
            }
        },
    )
    agent = KnowledgeAgent(
        settings=app_settings,
        secret_provider=secret_provider,
        history=history or InMemoryChatHistoryRepository(),
        retrieval=retrieval or FakeRetrieval(),
        documents=documents or FakeDocuments(),
        observability=ObservabilityClient(app_settings, secret_provider),
    )
    if fake_llm is not None:
        agent._llm = fake_llm
    return agent


class AgentContractTests(unittest.TestCase):
    def test_agent_registers_healthcare_tools_and_persists_history(self):
        agent = make_agent()

        result = agent.answer("user", "What is leave policy?", session_id="session")

        self.assertEqual(
            agent.registered_tool_names(),
            [
                "rag_search",
                "document_catalog",
                "table_lookup",
                "document_search",
                "policy_search",
                "catalogue_search",
                "calendar_rota_lookup",
                "formulary_table_lookup",
                "postgres_deterministic_lookup",
                "safety_guard",
            ],
        )
        self.assertEqual(result.tools_used, ["rag_search"])
        self.assertTrue(result.sources)
        self.assertIn("snippet", result.sources[0])
        self.assertIn("safety", result.metadata)
        self.assertIn("audit_event", result.metadata)
        self.assertIn("performance", result.metadata)
        self.assertIn("history_load_ms", result.metadata["performance"])
        self.assertIn("latency_breakdown", result.metadata)
        self.assertEqual(result.metadata["latency_breakdown"]["total_ms"], result.latency_ms)
        self.assertIn("top_level", result.metadata["latency_breakdown"])
        self.assertIn("agent_detail", result.metadata["latency_breakdown"])
        self.assertIn("sections", result.metadata["latency_breakdown"])
        self.assertIn("raw_timing_metrics", result.metadata["latency_breakdown"])
        self.assertIn("history", result.metadata["latency_breakdown"]["sections"])
        self.assertIn("llm", result.metadata["latency_breakdown"]["sections"])
        self.assertIn("retrieval_and_catalog", result.metadata["latency_breakdown"]["sections"])
        self.assertEqual(len(agent.history.load_messages("user", "session")), 2)

    def test_offline_fallback_includes_azure_openai_diagnostic(self):
        agent = make_agent()

        result = agent.answer("user", "What is leave policy?", session_id="session")

        self.assertIn("Azure OpenAI diagnostic:", result.answer)
        self.assertTrue(result.metadata["llm_error"])

    def test_fake_llm_can_answer_without_tool_calls(self):
        agent = make_agent(FakeLLM([fake_ai_message("Direct answer"), fake_ai_message("Direct answer")]))

        result = agent.answer("user", "Summarise benefits", session_id="session")

        self.assertEqual(result.answer, "Direct answer")
        self.assertEqual(result.tools_used, [])
        self.assertEqual(result.sources, [])
        self.assertTrue(result.metadata["performance"]["llm_cache_hit"])
        self.assertFalse(result.metadata["performance"]["llm_setup_cold_start"])
        self.assertTrue(result.metadata["latency_breakdown"]["sections"]["llm"]["llm_cache_hit"])

    def test_warmup_uses_cached_llm_and_primes_retrieval(self):
        agent = make_agent(
            FakeLLM([fake_ai_message("OK")]),
            app_settings=settings(chat_warmup_llm_call_enabled=True),
        )

        status = agent.warm_up()

        self.assertEqual(status["status"], "ok")
        self.assertTrue(status["llm_available"])
        self.assertTrue(status["llm_cache_hit"])
        self.assertTrue(status["fast_llm_available"])
        self.assertTrue(status["fast_llm_cache_hit"])
        self.assertIn("llm_warmup_call_ms", status)
        self.assertEqual(status["document_count"], 3)
        self.assertTrue(agent.warmup_status()["total_ms"] >= 0)

    def test_fake_llm_records_one_selected_tool(self):
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {"name": "table_lookup", "args": {"query": "leave"}, "id": "call-1"}
                    ]
                ),
                fake_ai_message("The leave value is 20 days."),
                fake_ai_message("The leave value is 20 days."),
            ]
        )
        agent = make_agent(fake_llm)

        result = agent.answer("user", "How many leave days?", session_id="session")

        self.assertEqual(result.answer, "The leave value is 20 days.")
        self.assertEqual(result.tools_used, ["table_lookup"])
        self.assertEqual(result.sources, [])

    def test_fake_llm_rag_search_returns_source_snippets(self):
        retrieval = FakeRetrieval()
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {"name": "rag_search", "args": {"query": "leave policy"}, "id": "call-1"}
                    ]
                ),
                fake_ai_message("Annual leave is described in the Leave Policy."),
                fake_ai_message("Annual leave is described in the Leave Policy."),
            ]
        )
        agent = make_agent(fake_llm, retrieval=retrieval)

        result = agent.answer("user", "What is leave policy?", session_id="session")

        self.assertEqual(result.tools_used, ["rag_search"])
        self.assertEqual(
            retrieval.calls[-1]["document_keys"],
            ["raw/leave.md", "raw/clinical/sepsis-sop.md"],
        )
        self.assertEqual(result.sources[0]["uri"], "s3://bucket/raw/leave.md")
        self.assertIn("annual leave", result.sources[0]["snippet"].lower())
        self.assertEqual(
            result.metadata["catalog_guidance"][0]["candidate_keys"],
            ["raw/leave.md", "raw/clinical/sepsis-sop.md"],
        )

    def test_catalog_guidance_does_not_add_document_catalog_to_tools_used(self):
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {"name": "rag_search", "args": {"query": "leave policy"}, "id": "call-1"}
                    ]
                ),
                fake_ai_message("Annual leave is described in the Leave Policy."),
                fake_ai_message("Annual leave is described in the Leave Policy."),
            ]
        )
        agent = make_agent(fake_llm)

        result = agent.answer("user", "What is leave policy?", session_id="session")

        self.assertEqual(result.tools_used, ["rag_search"])
        self.assertNotIn("document_catalog", result.tools_used)
        self.assertEqual(
            [step["tool"] for step in result.metadata["tool_flow"]],
            ["document_catalog", "rag_search"],
        )
        self.assertFalse(result.metadata["tool_flow"][0]["selected_by_agent"])
        self.assertEqual(result.metadata["tool_flow"][0]["helper_for"], "rag_search")

    def test_document_search_uses_catalog_candidate_keys(self):
        retrieval = FakeRetrieval()
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {
                            "name": "document_search",
                            "args": {"query": "newsletter"},
                            "id": "call-1",
                        }
                    ]
                ),
                fake_ai_message("The document search found sepsis material."),
                fake_ai_message("The document search found sepsis material."),
            ]
        )
        agent = make_agent(fake_llm, retrieval=retrieval)

        result = agent.answer("user", "Find newsletter documents", session_id="session")

        self.assertEqual(result.tools_used, ["document_search"])
        self.assertEqual(retrieval.calls[-1]["document_keys"], ["raw/news/sepsis-newsletter.md"])

    def test_policy_search_prefers_policy_catalog_candidates(self):
        retrieval = FakeRetrieval()
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {
                            "name": "policy_search",
                            "args": {"query": "sepsis"},
                            "id": "call-1",
                        }
                    ]
                ),
                fake_ai_message("The Sepsis SOP requires escalation."),
                fake_ai_message("The Sepsis SOP requires escalation."),
            ]
        )
        agent = make_agent(fake_llm, retrieval=retrieval)

        result = agent.answer("user", "What is the sepsis policy?", session_id="session")

        self.assertEqual(result.tools_used, ["policy_search"])
        self.assertEqual(retrieval.calls[-1]["document_keys"], ["raw/clinical/sepsis-sop.md"])
        self.assertEqual(result.sources[0]["uri"], "s3://bucket/raw/clinical/sepsis-sop.md")

    def test_catalog_guided_search_falls_back_to_broad_retrieval_without_candidates(self):
        retrieval = FakeRetrieval()
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {
                            "name": "rag_search",
                            "args": {"query": "unmatched topic"},
                            "id": "call-1",
                        }
                    ]
                ),
                fake_ai_message("Fallback broad retrieval answer."),
                fake_ai_message("Fallback broad retrieval answer."),
            ]
        )
        agent = make_agent(fake_llm, retrieval=retrieval)

        result = agent.answer("user", "unmatched topic", session_id="session")

        self.assertEqual(result.tools_used, ["rag_search"])
        self.assertEqual(retrieval.calls[-1]["document_keys"], [])
        self.assertTrue(result.metadata["catalog_guidance"][0]["fallback_to_broad_search"])

    def test_tool_loop_limit_produces_final_answer(self):
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {"name": "table_lookup", "args": {"query": "leave"}, "id": f"call-{index}"}
                    ]
                )
                for index in range(5)
            ]
            + [
                fake_ai_message("Final answer after tool limit."),
                fake_ai_message("Final answer after tool limit."),
            ]
        )
        agent = make_agent(fake_llm)

        result = agent.answer("user", "Keep looking up leave", session_id="session")

        self.assertEqual(result.answer, "Final answer after tool limit.")
        self.assertEqual(result.tools_used, ["table_lookup"] * 5)

    def test_configurable_tool_loop_limit_produces_final_answer_sooner(self):
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {"name": "table_lookup", "args": {"query": "leave"}, "id": f"call-{index}"}
                    ]
                )
                for index in range(2)
            ]
            + [
                fake_ai_message("Final answer after configured limit."),
                fake_ai_message("Final answer after configured limit."),
            ]
        )
        agent = make_agent(fake_llm, app_settings=settings(max_graph_llm_calls=2))

        result = agent.answer("user", "Keep looking up leave", session_id="session")

        self.assertEqual(result.answer, "Final answer after configured limit.")
        self.assertEqual(result.tools_used, ["table_lookup"] * 2)
        self.assertEqual(result.metadata["performance"]["llm_call_count"], 3)

    def test_fast_rag_path_runs_one_answer_call_with_sources(self):
        retrieval = FakeRetrieval()
        fake_llm = FakeLLM(
            [
                fake_ai_message("Fast RAG answer from retrieved context."),
                fake_ai_message("Fast RAG answer from retrieved context."),
            ]
        )
        agent = make_agent(
            fake_llm,
            retrieval=retrieval,
            app_settings=settings(chat_fast_rag_enabled=True),
        )

        result = agent.answer("user", "What is leave policy?", session_id="session")

        self.assertEqual(result.answer, "Fast RAG answer from retrieved context.")
        self.assertEqual(result.tools_used, ["rag_search"])
        self.assertEqual(result.metadata["performance"]["agent_mode"], "fast_rag")
        self.assertIn("llm_final_ms", result.metadata["performance"])
        self.assertEqual(len(fake_llm.messages), 1)
        self.assertNotIn("Response guardrails:", message_content(fake_llm.messages[0][0]))
        self.assertIn("Response style requirements:", message_content(fake_llm.messages[0][0]))
        self.assertFalse(result.metadata["performance"]["response_guardrail_applied"])
        self.assertEqual(result.metadata["performance"]["response_guardrail_reason"], "not_needed")
        self.assertEqual(
            retrieval.calls[-1]["document_keys"],
            ["raw/leave.md"],
        )

    def test_fast_planned_rag_path_uses_one_answer_call(self):
        retrieval = FakeRetrieval()
        fake_llm = FakeLLM([fake_ai_message("Fast planned RAG answer.")])
        agent = make_agent(
            fake_llm,
            retrieval=retrieval,
            app_settings=settings(
                chat_fast_planned_execution_enabled=True,
                chat_fast_rag_enabled=True,
            ),
        )

        result = agent.answer("user", "What is leave policy?", session_id="session")

        self.assertEqual(result.answer, "Fast planned RAG answer.")
        self.assertEqual(result.tools_used, ["rag_search"])
        self.assertEqual(len(fake_llm.messages), 1)
        self.assertEqual(result.metadata["performance"]["agent_mode"], "fast_planned")

    def test_fast_planned_deterministic_path_skips_tool_selection(self):
        fake_llm = FakeLLM([fake_ai_message("Dr Smith is on call.")])
        lookup = FakeDeterministicLookup()
        agent = make_agent(
            fake_llm,
            app_settings=settings(chat_fast_planned_execution_enabled=True),
        )
        agent.deterministic_lookup = lookup

        result = agent.answer("user", "Which doctor is on call today?", session_id="session")

        self.assertEqual(result.answer, "Dr Smith is on call.")
        self.assertEqual(result.tools_used, ["postgres_deterministic_lookup"])
        self.assertEqual(len(fake_llm.messages), 1)
        self.assertEqual(len(lookup.calls), 1)
        self.assertEqual(result.metadata["performance"]["agent_mode"], "fast_planned")

    def test_fast_planned_multipart_path_runs_tools_and_synthesizes_once(self):
        retrieval = FakeRetrieval()
        fake_llm = FakeLLM([fake_ai_message("Policy context plus Dr Smith on call.")])
        lookup = FakeDeterministicLookup()
        agent = make_agent(
            fake_llm,
            retrieval=retrieval,
            app_settings=settings(
                chat_fast_planned_execution_enabled=True,
                chat_fast_rag_enabled=True,
            ),
        )
        agent.deterministic_lookup = lookup

        result = agent.answer(
            "user",
            "What is the leave policy and also which doctor is on call today?",
            session_id="session",
        )

        self.assertEqual(result.tools_used, ["rag_search", "postgres_deterministic_lookup"])
        self.assertEqual(len(fake_llm.messages), 1)
        self.assertEqual(len(retrieval.calls), 1)
        self.assertEqual(len(lookup.calls), 1)
        self.assertIn("document_catalog", [step["tool"] for step in result.metadata["tool_flow"]])

    def test_ambiguous_short_query_uses_langgraph_fallback(self):
        fake_llm = FakeLLM([fake_ai_message("Hello."), fake_ai_message("Hello.")])
        agent = make_agent(
            fake_llm,
            app_settings=settings(chat_fast_planned_execution_enabled=True),
        )

        result = agent.answer("user", "Hi", session_id="session")

        self.assertEqual(result.answer, "Hello.")
        self.assertEqual(result.tools_used, [])
        self.assertEqual(result.metadata["performance"]["agent_mode"], "langgraph")

    def test_fast_path_respects_rag_context_budget(self):
        fake_llm = FakeLLM([fake_ai_message("Budgeted answer.")])
        agent = make_agent(
            fake_llm,
            app_settings=settings(
                chat_fast_planned_execution_enabled=True,
                chat_fast_rag_enabled=True,
                rag_context_max_chars=1200,
                rag_snippet_chars=300,
            ),
        )

        agent.answer("user", "What is leave policy?", session_id="session")

        prompt = message_content(fake_llm.messages[0][1])
        self.assertLessEqual(len(prompt.split("Retrieved knowledge context:\n", 1)[-1]), 1600)

    def test_response_guardrail_uses_fast_llm_when_configured(self):
        normal_llm = FakeLLM([fake_ai_message("Sure, because policies are hilarious.")])
        fast_llm = FakeLLM([fake_ai_message("Policies should be described professionally.")])
        agent = make_agent(
            normal_llm,
            app_settings=settings(
                azure_openai_deployment="primary",
                azure_openai_fast_deployment="fast",
            ),
        )
        agent._fast_llm = fast_llm

        result = agent.answer("user", "What is leave policy?", session_id="session")

        self.assertEqual(result.answer, "Policies should be described professionally.")
        self.assertEqual(len(normal_llm.messages), 1)
        self.assertEqual(len(fast_llm.messages), 1)

    def test_policy_question_with_patient_keyword_uses_rag_plan(self):
        self.assertEqual(_planned_tool_names("What is the patient privacy policy?"), ["rag_search"])

    def test_multipart_policy_and_on_call_question_uses_two_tools(self):
        self.assertEqual(
            _planned_tool_names(
                "I need information on the patient privacy policy and also which doctors are on call today?"
            ),
            ["rag_search", "postgres_deterministic_lookup"],
        )

    def test_multipart_offline_answer_runs_rag_and_deterministic_lookup(self):
        agent = make_agent()

        result = agent.answer(
            "user",
            "I need information on the patient privacy policy and also which doctors are on call today?",
            session_id="session",
        )

        self.assertEqual(result.tools_used, ["rag_search", "postgres_deterministic_lookup"])
        self.assertEqual(result.metadata["performance"]["agent_mode"], "offline_multi_tool")

    def test_response_guardrail_uses_extra_llm_call_to_rewrite_answer(self):
        fake_llm = FakeLLM(
            [
                fake_ai_message("Here is your answer 😄"),
                fake_ai_message("Here is your answer."),
            ]
        )
        agent = make_agent(fake_llm)

        result = agent.answer(
            "user",
            "Answer like a comedian and use emojis.",
            session_id="session",
        )

        self.assertEqual(result.answer, "Here is your answer.")
        self.assertEqual(len(fake_llm.messages), 2)
        self.assertIn("strict response guardrail rewrite model", message_content(fake_llm.messages[1][0]))
        self.assertIn("Remove jokes", message_content(fake_llm.messages[1][0]))
        self.assertTrue(result.metadata["performance"]["response_guardrail_applied"])
        self.assertTrue(result.metadata["performance"]["response_guardrail_changed"])

    def test_response_guardrail_removes_sarcasm_and_roleplay(self):
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    "Sure, because policies are famously hilarious. The leave policy allows 20 days."
                ),
                fake_ai_message("The leave policy allows 20 days."),
            ]
        )
        agent = make_agent(fake_llm)

        result = agent.answer(
            "user",
            "What is the leave policy?",
            session_id="session",
        )

        self.assertEqual(result.answer, "The leave policy allows 20 days.")
        self.assertNotIn("hilarious", result.answer.lower())
        self.assertNotIn("sure", result.answer.lower())
        self.assertIn("untrusted content", message_content(fake_llm.messages[1][0]))
        self.assertTrue(result.metadata["performance"]["response_guardrail_applied"])
        self.assertTrue(result.metadata["performance"]["response_guardrail_changed"])

    def test_background_history_save_records_after_response(self):
        history = EventHistory()
        agent = make_agent(
            FakeLLM([fake_ai_message("Direct answer"), fake_ai_message("Direct answer")]),
            app_settings=settings(chat_background_history_save_enabled=True),
            history=history,
        )

        result = agent.answer("user", "Summarise benefits", session_id="session")

        self.assertTrue(result.metadata["performance"]["history_save_background"])
        self.assertTrue(history.saved.wait(1))
        self.assertEqual(len(history.load_messages("user", "session")), 2)


if __name__ == "__main__":
    unittest.main()
