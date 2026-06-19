import unittest

from backend.app.agent import KnowledgeAgent
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


def settings():
    return AppSettings(
        app_env="test",
        aws_region="us-east-1",
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


def make_agent(fake_llm=None, retrieval=None, documents=None):
    app_settings = settings()
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
        history=InMemoryChatHistoryRepository(),
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
                "safety_guard",
            ],
        )
        self.assertEqual(result.tools_used, ["rag_search"])
        self.assertTrue(result.sources)
        self.assertIn("snippet", result.sources[0])
        self.assertIn("safety", result.metadata)
        self.assertIn("audit_event", result.metadata)
        self.assertEqual(len(agent.history.load_messages("user", "session")), 2)

    def test_fake_llm_can_answer_without_tool_calls(self):
        agent = make_agent(FakeLLM([fake_ai_message("Direct answer")]))

        result = agent.answer("user", "Summarise benefits", session_id="session")

        self.assertEqual(result.answer, "Direct answer")
        self.assertEqual(result.tools_used, [])
        self.assertEqual(result.sources, [])

    def test_fake_llm_records_one_selected_tool(self):
        fake_llm = FakeLLM(
            [
                fake_ai_message(
                    tool_calls=[
                        {"name": "table_lookup", "args": {"query": "leave"}, "id": "call-1"}
                    ]
                ),
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
            ]
        )
        agent = make_agent(fake_llm)

        result = agent.answer("user", "What is leave policy?", session_id="session")

        self.assertEqual(result.tools_used, ["rag_search"])
        self.assertNotIn("document_catalog", result.tools_used)

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
            + [fake_ai_message("Final answer after tool limit.")]
        )
        agent = make_agent(fake_llm)

        result = agent.answer("user", "Keep looking up leave", session_id="session")

        self.assertEqual(result.answer, "Final answer after tool limit.")
        self.assertEqual(result.tools_used, ["table_lookup"] * 5)


if __name__ == "__main__":
    unittest.main()
