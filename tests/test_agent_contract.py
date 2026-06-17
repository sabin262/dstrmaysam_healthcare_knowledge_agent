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
        pass

    def search(self, query: str, top_k: int = 5):
        return [
            RetrievalHit(
                title="Leave Policy",
                uri="s3://bucket/raw/leave.md",
                text="Employees receive annual leave according to the HR leave policy.",
                score=1.0,
                metadata={"department": "HR"},
            )
        ]


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
                metadata={"department": "HR"},
            )
        ]

    def lookup_table(self, query: str, limit: int = 10):
        return [{"source": "s3://bucket/raw/table.csv", "row": {"key": "leave", "value": "20 days"}}]


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


class AgentContractTests(unittest.TestCase):
    def test_agent_registers_healthcare_tools_and_persists_history(self):
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
        history = InMemoryChatHistoryRepository()
        agent = KnowledgeAgent(
            settings=app_settings,
            secret_provider=secret_provider,
            history=history,
            retrieval=FakeRetrieval(),
            documents=FakeDocuments(),
            observability=ObservabilityClient(app_settings, secret_provider),
        )

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
        self.assertEqual(
            result.tools_used,
            [
                "rag_search",
                "document_search",
                "document_catalog",
                "table_lookup",
                "policy_search",
                "catalogue_search",
                "calendar_rota_lookup",
                "formulary_table_lookup",
                "safety_guard",
            ],
        )
        self.assertTrue(result.sources)
        self.assertIn("safety", result.metadata)
        self.assertIn("audit_event", result.metadata)
        self.assertEqual(len(history.load_messages("user", "session")), 2)


if __name__ == "__main__":
    unittest.main()
