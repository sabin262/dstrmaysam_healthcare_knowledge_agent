import unittest
import asyncio
from dataclasses import replace

from backend.app.config import AppSettings
from backend.app import ragas_scoring
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
        opensearch_endpoint="",
        opensearch_index="idx",
        dynamodb_chat_table="table",
        chat_history_backend="memory",
        cors_origins=(),
        prompt_label="dev",
        max_history_chars=1000,
        azure_openai_deployment="primary",
        azure_openai_fast_deployment="fast",
    )
    return replace(app_settings, **overrides)


def secret_provider(app_settings):
    return StaticSecretProvider(
        app_settings,
        {
            "/test/azure": {
                "endpoint": "https://example.openai.azure.com",
                "api_key": "test-key",
                "api_version": "2025-04-01-preview",
                "chat_deployment": "primary",
                "fast_chat_deployment": "fast",
                "embedding_deployment": "embedding",
            }
        },
    )


class LiveRagasScoringTests(unittest.TestCase):
    def test_live_scoring_uses_settings_and_secret_provider(self):
        app_settings = settings()
        provider = secret_provider(app_settings)
        original = ragas_scoring._compute_with_ragas

        def fake_compute(question, answer, contexts, *, settings=None, secret_provider=None):
            self.assertEqual(question, "What is the policy?")
            self.assertEqual(answer, "The policy requires documented handoff.")
            self.assertEqual(contexts, ["The policy requires documented handoff."])
            self.assertIs(settings, app_settings)
            self.assertIs(secret_provider, provider)
            return {
                "ragas_faithfulness": 0.9,
                "ragas_answer_relevancy": 0.8,
                "ragas_context_precision": 0.7,
            }

        ragas_scoring._compute_with_ragas = fake_compute
        try:
            result = ragas_scoring.compute_live_ragas_scores(
                question="What is the policy?",
                answer="The policy requires documented handoff.",
                sources=[{"snippet": "The policy requires documented handoff."}],
                settings=app_settings,
                secret_provider=provider,
            )
        finally:
            ragas_scoring._compute_with_ragas = original

        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["provider"], "ragas_azure_openai")
        self.assertEqual(result["scores"]["ragas_faithfulness"], 0.9)

    def test_live_scoring_falls_back_when_ragas_errors(self):
        original = ragas_scoring._compute_with_ragas

        def failing_compute(*args, **kwargs):
            raise RuntimeError("ragas unavailable")

        ragas_scoring._compute_with_ragas = failing_compute
        try:
            result = ragas_scoring.compute_live_ragas_scores(
                question="What is the handoff policy?",
                answer="The handoff policy requires documentation.",
                sources=[{"snippet": "The handoff policy requires documentation."}],
            )
        finally:
            ragas_scoring._compute_with_ragas = original

        self.assertEqual(result["status"], "fallback_scored")
        self.assertEqual(result["provider"], "lexical_fallback")
        self.assertIn("ragas unavailable", result["error"])
        self.assertIn("ragas_faithfulness", result["scores"])

    def test_ragas_metric_name_maps_reference_free_context_precision(self):
        self.assertEqual(
            ragas_scoring.RAGAS_SCORE_NAMES["llm_context_precision_without_reference"],
            "ragas_context_precision",
        )

    def test_builds_ragas_wrappers_from_azure_secret(self):
        app_settings = settings()
        provider = secret_provider(app_settings)

        ragas_llm, ragas_embeddings = ragas_scoring._build_ragas_azure_clients(
            app_settings,
            provider,
        )

        self.assertEqual(type(ragas_llm).__name__, "LangchainLLMWrapper")
        self.assertEqual(type(ragas_embeddings).__name__, "LangchainEmbeddingsWrapper")

    def test_closes_owned_sync_and_async_clients(self):
        class AsyncClient:
            def __init__(self):
                self.closed = False

            async def close(self):
                await asyncio.sleep(0)
                self.closed = True

        class SyncClient:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class Owner:
            def __init__(self):
                self.root_async_client = AsyncClient()
                self.root_client = SyncClient()
                self.http_async_client = None
                self.http_client = None

        class Wrapper:
            def __init__(self):
                self.langchain_llm = Owner()

        wrapper = Wrapper()

        ragas_scoring._close_ragas_clients(wrapper, None)

        self.assertTrue(wrapper.langchain_llm.root_async_client.closed)
        self.assertTrue(wrapper.langchain_llm.root_client.closed)


if __name__ == "__main__":
    unittest.main()
