import unittest
from dataclasses import replace

from backend.app.config import AppSettings
from backend.app.retrieval import RetrievalService
from backend.app.secrets import StaticSecretProvider


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
        opensearch_endpoint="https://collection.eu-west-2.aoss.amazonaws.com",
        opensearch_index="idx",
        dynamodb_chat_table="table",
        chat_history_backend="memory",
        cors_origins=(),
        prompt_label="dev",
        max_history_chars=1000,
    )
    return replace(app_settings, **overrides)


class FakeOpenSearchClient:
    def __init__(self, responses=None):
        self.search_calls = []
        self.responses = list(responses or [])

    def search(self, index, body):
        self.search_calls.append({"index": index, "body": body})
        if self.responses:
            return self.responses.pop(0)
        return {"hits": {"hits": []}}


class RetrievalQueryTests(unittest.TestCase):
    def make_service(self, vector):
        app_settings = settings(rag_neighbor_chunks=0)
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

        body = service._opensearch.search_calls[0]["body"]
        self.assertEqual(
            body["query"]["knn"]["embedding"]["filter"],
            {"terms": {"key": ["raw/leave.md"]}},
        )
        self.assertIn("multi_match", service._opensearch.search_calls[1]["body"]["query"]["bool"]["must"][0])

    def test_keyword_fallback_adds_document_key_terms_filter(self):
        service = self.make_service(None)

        service.search("leave policy", document_keys=["raw/leave.md"])

        body = service._opensearch.search_calls[-1]["body"]
        self.assertEqual(
            body["query"]["bool"]["filter"],
            [{"terms": {"key": ["raw/leave.md"]}}],
        )
        self.assertEqual(body["query"]["bool"]["must"][0]["multi_match"]["query"], "leave policy")
        self.assertNotIn("metadata.facts.*^5", body["query"]["bool"]["must"][0]["multi_match"]["fields"])

    def test_search_without_document_keys_preserves_broad_query_shape(self):
        service = self.make_service(None)

        service.search("leave policy")

        body = service._opensearch.search_calls[-1]["body"]
        self.assertIn("multi_match", body["query"])
        self.assertNotIn("bool", body["query"])

    def test_vector_search_merges_keyword_results_and_fetches_neighbors(self):
        responses = [
            {
                "hits": {
                    "hits": [
                        {
                            "_score": 0.9,
                            "_source": {
                                "key": "raw/policy.md",
                                "title": "policy.md",
                                "uri": "s3://bucket/raw/policy.md",
                                "text": "The policy describes staff responsibilities.",
                                "chunk_index": 2,
                                "metadata": {"domain": "general"},
                            },
                        }
                    ]
                }
            },
            {
                "hits": {
                    "hits": [
                        {
                            "_score": 1.1,
                            "_source": {
                                "key": "raw/policy.md",
                                "title": "policy.md",
                                "uri": "s3://bucket/raw/policy.md",
                                "text": "The policy review date is listed in the source.",
                                "chunk_index": 3,
                                "metadata": {"domain": "admin_policy", "document_type": "policy"},
                            },
                        }
                    ]
                }
            },
            {
                "hits": {
                    "hits": [
                        {
                            "_score": 0.1,
                            "_source": {
                                "key": "raw/policy.md",
                                "title": "policy.md",
                                "uri": "s3://bucket/raw/policy.md",
                                "text": "Neighbor context before the policy review.",
                                "chunk_index": 1,
                                "metadata": {"domain": "general"},
                            },
                        }
                    ]
                }
            },
        ]
        app_settings = settings(rag_top_k=10, rag_neighbor_chunks=1)
        service = RetrievalService(
            app_settings,
            StaticSecretProvider(app_settings, {"/test/app": {"session_secret": "secret"}}),
        )
        service._opensearch = FakeOpenSearchClient(responses)
        service._embed_query = lambda query: [0.1, 0.2, 0.3]

        hits = service.search("What does the staff policy say?")

        self.assertEqual(len(service._opensearch.search_calls), 3)
        self.assertEqual([hit.metadata["_chunk_index"] for hit in hits], [2, 3, 1])
        self.assertEqual(
            service._opensearch.search_calls[2]["body"]["query"]["bool"]["filter"],
            [{"term": {"key": "raw/policy.md"}}, {"terms": {"chunk_index": [1, 4]}}],
        )
        self.assertEqual(hits[1].metadata["document_type"], "policy")
        self.assertEqual(service.last_timing_ms["vector_hits"], 1)
        self.assertEqual(service.last_timing_ms["keyword_hits"], 1)
        self.assertEqual(service.last_timing_ms["neighbor_hits"], 1)


if __name__ == "__main__":
    unittest.main()
