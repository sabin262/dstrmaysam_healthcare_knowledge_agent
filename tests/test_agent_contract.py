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
            DocumentRecord(
                title="doctor_rota.csv",
                uri="postgres://uploaded_lookup_rows/doctor_rota.csv",
                key="postgres://uploaded_lookup_rows/doctor_rota.csv",
                content_type="text/csv",
                metadata={
                    "asset_source": "postgres_uploaded_lookup",
                    "columns": ["date", "doctor", "status"],
                    "row_count": 1,
                    "allowed_roles": ["staff", "admin", "doctor"],
                },
            ),
            DocumentRecord(
                title="formulary_upload.csv",
                uri="postgres://uploaded_lookup_rows/formulary_upload.csv",
                key="postgres://uploaded_lookup_rows/formulary_upload.csv",
                content_type="text/csv",
                metadata={
                    "asset_source": "postgres_uploaded_lookup",
                    "columns": ["medicine_name", "category", "notes"],
                    "row_count": 3,
                    "semantic_terms": ["morphine", "opioid", "analgesic"],
                    "categorical_values": {"medicine_name": ["Morphine"]},
                    "allowed_roles": ["staff", "admin", "doctor", "pharmacy"],
                },
            ),
            DocumentRecord(
                title="equipment_assets.csv",
                uri="postgres://uploaded_lookup_rows/equipment_assets.csv",
                key="postgres://uploaded_lookup_rows/equipment_assets.csv",
                content_type="text/csv",
                metadata={
                    "asset_source": "postgres_uploaded_lookup",
                    "columns": ["asset_id", "equipment_type", "location", "status"],
                    "row_count": 30,
                    "semantic_terms": ["asset", "equipment", "ventilator", "infusion", "pump"],
                    "categorical_values": {"equipment_type": ["Ventilator", "Infusion Pump"]},
                    "allowed_roles": ["staff", "admin", "doctor"],
                },
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
    def __init__(self, payload=None):
        self.payload = payload or {
            "category": "doctor",
            "message": "Found 1 matching row(s).",
            "rows": [{"doctor": "Dr Smith", "status": "on call"}],
        }

    def to_json(self):
        import json

        return json.dumps(self.payload)


class FakeDeterministicLookup:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or FakeLookupResult()

    def lookup(self, query, user, limit=10, csv_assets=None):
        self.calls.append({"query": query, "user": user.user_id, "limit": limit, "csv_assets": list(csv_assets or [])})
        return self.result


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
        self.assertEqual(status["document_count"], 6)
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

    def test_deterministic_lookup_is_chosen_by_llm_not_fast_planned(self):
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {
                            "name": "postgres_deterministic_lookup",
                            "args": {"query": "Which doctor is on call today?"},
                            "id": "call-1",
                        }
                    ]
                ),
                fake_ai_message("Dr Smith is on call."),
                fake_ai_message("Dr Smith is on call."),
            ]
        )
        lookup = FakeDeterministicLookup()
        agent = make_agent(
            fake_llm,
            app_settings=settings(chat_fast_planned_execution_enabled=True),
        )
        agent.deterministic_lookup = lookup

        result = agent.answer("user", "Which doctor is on call today?", session_id="session")

        self.assertEqual(result.answer, "Dr Smith is on call.")
        self.assertEqual(result.tools_used, ["postgres_deterministic_lookup"])
        self.assertEqual(len(fake_llm.messages), 2)
        self.assertEqual(len(lookup.calls), 1)
        self.assertEqual(result.metadata["performance"]["agent_mode"], "langgraph")
        self.assertEqual(lookup.calls[0]["csv_assets"][0]["filename"], "doctor_rota.csv")

    def test_short_entity_info_query_uses_deterministic_preflight(self):
        retrieval = FakeRetrieval()
        fake_llm = FakeLLM([fake_ai_message("Morphine is listed as an opioid analgesic.")])
        lookup = FakeDeterministicLookup(
            FakeLookupResult(
                {
                    "category": "directory",
                    "message": "Found 1 matching row(s).",
                    "lookup_plan": {
                        "row_value_search_used": True,
                        "matched_csv_sources": ["formulary_upload.csv"],
                    },
                    "rows": [
                        {
                            "source_table": "uploaded_lookup_rows",
                            "source_filename": "formulary_upload.csv",
                            "row": {
                                "medicine_name": "Morphine",
                                "category": "Opioid analgesic",
                                "restricted": "No",
                                "approval_required": "No special approval",
                                "max_adult_dose": "Dose by renal function",
                                "monitoring_required": "INR (International Normalized Ratio)",
                                "access_level": "all_staff",
                                "notes": "Use according to approved formulary guidance.",
                            },
                        }
                    ],
                }
            )
        )
        agent = make_agent(
            fake_llm,
            retrieval=retrieval,
            app_settings=settings(chat_fast_planned_execution_enabled=True, chat_fast_rag_enabled=True),
        )
        agent.deterministic_lookup = lookup

        result = agent.answer("user", "info on Morphine", session_id="session")

        self.assertEqual(
            result.answer,
            "Morphine details are as follows:\n\n"
            "- Category: Opioid analgesic\n"
            "- Restricted: No\n"
            "- Approval required: No special approval\n"
            "- Maximum adult dose: Dose by renal function\n"
            "- Monitoring required: INR (International Normalized Ratio)\n"
            "- Access level: All staff\n"
            "- Notes: Use according to approved formulary guidance.",
        )
        self.assertEqual(result.tools_used, ["postgres_deterministic_lookup"])
        self.assertEqual(result.metadata["performance"]["agent_mode"], "deterministic_preflight")
        self.assertEqual(len(lookup.calls), 1)
        self.assertEqual(lookup.calls[0]["query"], "info on Morphine")
        self.assertEqual(retrieval.calls, [])
        self.assertEqual(fake_llm.messages, [])
        self.assertIn(
            "formulary_upload.csv",
            [asset["filename"] for asset in lookup.calls[0]["csv_assets"]],
        )

    def test_list_all_medicines_uses_full_limit_and_formats_multiple_rows(self):
        retrieval = FakeRetrieval()
        lookup = FakeDeterministicLookup(
            FakeLookupResult(
                {
                    "category": "formulary",
                    "message": "Found 3 matching row(s).",
                    "rows": [
                        {
                            "Medicine Name": "Morphine",
                            "category": "Antibiotic",
                            "restricted": "No",
                            "approval_required": "No special approval",
                            "max_adult_dose": "Dose by renal function",
                            "monitoring_required": "INR",
                            "access_level": "all_staff",
                        },
                        {
                            "medicine_name": "Diazepam",
                            "category": "Cardiac",
                            "restricted": "No",
                            "approval_required": "No special approval",
                            "max_adult_dose": "Per protocol",
                            "monitoring_required": "INR",
                            "access_level": "all_staff",
                        },
                        {
                            "medicine_name": "Paracetamol",
                            "category": "Oncology",
                            "restricted": "No",
                            "approval_required": "No special approval",
                            "max_adult_dose": "See formulary notes",
                            "monitoring_required": "INR",
                            "access_level": "all_staff",
                        },
                    ],
                }
            )
        )
        agent = make_agent(
            FakeLLM([fake_ai_message("Unused LLM response")]),
            retrieval=retrieval,
            app_settings=settings(chat_fast_planned_execution_enabled=True, chat_fast_rag_enabled=True),
        )
        agent.deterministic_lookup = lookup

        result = agent.answer("user", "list all medicine", session_id="session")

        self.assertEqual(result.tools_used, ["postgres_deterministic_lookup"])
        self.assertEqual(result.metadata["performance"]["agent_mode"], "deterministic_preflight")
        self.assertEqual(lookup.calls[0]["limit"], 100)
        self.assertEqual(retrieval.calls, [])
        self.assertIn("Medicines returned by deterministic lookup:", result.answer)
        self.assertIn("1. Morphine", result.answer)
        self.assertIn("2. Diazepam", result.answer)
        self.assertIn("3. Paracetamol", result.answer)
        self.assertNotIn("Category:", result.answer)
        self.assertNotIn("Approval required:", result.answer)
        self.assertNotIn("Access level:", result.answer)

    def test_list_all_equipment_types_formats_unique_type_names(self):
        retrieval = FakeRetrieval()
        lookup = FakeDeterministicLookup(
            FakeLookupResult(
                {
                    "category": "directory",
                    "message": "Found 2 matching row(s).",
                    "lookup_plan": {
                        "row_value_search_used": True,
                        "distinct_field": "equipment_type",
                        "matched_csv_sources": ["equipment_assets.csv"],
                    },
                    "rows": [
                        {
                            "source_table": "uploaded_lookup_rows",
                            "source_filename": "equipment_assets.csv",
                            "row": {"equipment_type": "Ventilator"},
                        },
                        {
                            "source_table": "uploaded_lookup_rows",
                            "source_filename": "equipment_assets.csv",
                            "row": {"equipment_type": "Infusion Pump"},
                        },
                    ],
                }
            )
        )
        agent = make_agent(
            FakeLLM([fake_ai_message("Unused LLM response")]),
            retrieval=retrieval,
            app_settings=settings(chat_fast_planned_execution_enabled=True, chat_fast_rag_enabled=True),
        )
        agent.deterministic_lookup = lookup

        result = agent.answer("user", "list all equipment types in assets", session_id="session")

        self.assertEqual(result.tools_used, ["postgres_deterministic_lookup"])
        self.assertEqual(lookup.calls[0]["limit"], 100)
        self.assertIn("equipment_assets.csv", [asset["filename"] for asset in lookup.calls[0]["csv_assets"]])
        self.assertIn("Equipment types returned by deterministic lookup:", result.answer)
        self.assertIn("1. Ventilator", result.answer)
        self.assertIn("2. Infusion Pump", result.answer)
        self.assertNotIn("Equipment fault Desk", result.answer)
        self.assertNotIn("Category:", result.answer)

    def test_equipment_count_formats_total_location_and_status(self):
        retrieval = FakeRetrieval()
        lookup = FakeDeterministicLookup(
            FakeLookupResult(
                {
                    "category": "directory",
                    "message": "Found 2 matching row(s).",
                    "lookup_plan": {
                        "aggregate_intent": "count",
                        "aggregate_result": {
                            "type": "count",
                            "matching_rows": 2,
                            "counts_by_source": {"equipment_assets.csv": 2},
                            "source_filenames": ["equipment_assets.csv"],
                        },
                        "row_value_search_used": True,
                        "matched_csv_sources": ["equipment_assets.csv"],
                    },
                    "rows": [
                        {
                            "source_table": "uploaded_lookup_rows",
                            "source_filename": "equipment_assets.csv",
                            "row": {
                                "equipment_type": "Ventilator",
                                "location": "Respiratory Ward",
                                "status": "Fault logged",
                            },
                        },
                        {
                            "source_table": "uploaded_lookup_rows",
                            "source_filename": "equipment_assets.csv",
                            "row": {
                                "equipment_type": "Ventilator",
                                "location": "Mental Health Ward",
                                "status": "Available",
                            },
                        },
                    ],
                }
            )
        )
        agent = make_agent(
            FakeLLM([fake_ai_message("Unused LLM response")]),
            retrieval=retrieval,
            app_settings=settings(chat_fast_planned_execution_enabled=True, chat_fast_rag_enabled=True),
        )
        agent.deterministic_lookup = lookup

        result = agent.answer("user", "how many ventilators do we have", session_id="session")

        self.assertEqual(result.tools_used, ["postgres_deterministic_lookup"])
        self.assertEqual(lookup.calls[0]["limit"], 100)
        self.assertIn("Total: 2 matching row(s) in equipment_assets.csv.", result.answer)
        self.assertIn("- Ventilator; Location: Respiratory Ward; Status: Fault logged", result.answer)
        self.assertIn("- Ventilator; Location: Mental Health Ward; Status: Available", result.answer)
        self.assertNotIn("Source: Postgres deterministic lookup.", result.answer)

    def test_multipart_lookup_and_rag_are_chosen_by_llm(self):
        retrieval = FakeRetrieval()
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {
                            "name": "rag_search",
                            "args": {"query": "leave policy"},
                            "id": "call-1",
                        },
                        {
                            "name": "postgres_deterministic_lookup",
                            "args": {"query": "which doctor is on call today"},
                            "id": "call-2",
                        },
                    ]
                ),
                fake_ai_message("Policy context plus Dr Smith on call."),
                fake_ai_message("Policy context plus Dr Smith on call."),
            ]
        )
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
        self.assertEqual(len(fake_llm.messages), 2)
        self.assertEqual(len(retrieval.calls), 1)
        self.assertEqual(len(lookup.calls), 1)
        self.assertEqual(result.metadata["performance"]["agent_mode"], "langgraph")
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

    def test_ipd_patient_location_question_uses_deterministic_lookup(self):
        self.assertEqual(
            _planned_tool_names("where in IPD is Leo Bennett"),
            ["postgres_deterministic_lookup"],
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
