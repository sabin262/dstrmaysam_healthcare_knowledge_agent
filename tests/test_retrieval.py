import unittest

from backend.app.config import AppSettings
from backend.app.retrieval import RetrievalService
from backend.app.secrets import StaticSecretProvider


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
        opensearch_endpoint="https://collection.us-east-1.aoss.amazonaws.com",
        opensearch_index="idx",
        dynamodb_chat_table="table",
        chat_history_backend="memory",
        cors_origins=(),
        prompt_label="dev",
        max_history_chars=1000,
    )


class FakeOpenSearchClient:
    def __init__(self):
        self.search_calls = []

    def search(self, index, body):
        self.search_calls.append({"index": index, "body": body})
        return {"hits": {"hits": []}}


class RetrievalQueryTests(unittest.TestCase):
    def make_service(self, vector):
        app_settings = settings()
        service = RetrievalService(
            app_settings,
            StaticSecretProvider(app_settings, {"/test/app": {"session_secret": "secret", "auth_users": {"u": "h"}}}),
        )
        service._opensearch = FakeOpenSearchClient()
        service._embed_query = lambda query: vector
        return service

    def test_vector_search_adds_document_key_terms_filter(self):
        service = self.make_service([0.1, 0.2, 0.3])

        service.search("leave policy", document_keys=["raw/leave.md", "raw/leave.md"])

        body = service._opensearch.search_calls[-1]["body"]
        self.assertEqual(
            body["query"]["knn"]["embedding"]["filter"],
            {"terms": {"key": ["raw/leave.md"]}},
        )

    def test_keyword_fallback_adds_document_key_terms_filter(self):
        service = self.make_service(None)

        service.search("leave policy", document_keys=["raw/leave.md"])

        body = service._opensearch.search_calls[-1]["body"]
        self.assertEqual(
            body["query"]["bool"]["filter"],
            [{"terms": {"key": ["raw/leave.md"]}}],
        )
        self.assertEqual(body["query"]["bool"]["must"][0]["multi_match"]["query"], "leave policy")

    def test_search_without_document_keys_preserves_broad_query_shape(self):
        service = self.make_service(None)

        service.search("leave policy")

        body = service._opensearch.search_calls[-1]["body"]
        self.assertIn("multi_match", body["query"])
        self.assertNotIn("bool", body["query"])


if __name__ == "__main__":
    unittest.main()
