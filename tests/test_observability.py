import unittest
from dataclasses import replace

from backend.app.config import AppSettings
from backend.app.observability import ObservabilityClient
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
    )
    return replace(app_settings, **overrides)


class FakePrompt:
    version = "v1"

    def compile(self):
        return "cached system prompt"


class FakeLangfuseClient:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def get_prompt(self, *args, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("prompt unavailable")
        return FakePrompt()


class ObservabilityCacheTests(unittest.TestCase):
    def test_system_prompt_uses_cache_and_stale_cache_on_failure(self):
        app_settings = settings(langfuse_prompt_cache_ttl_seconds=300)
        observability = ObservabilityClient(
            app_settings,
            StaticSecretProvider(app_settings, {"/test/app": {"session_secret": "secret"}}),
        )
        client = FakeLangfuseClient()
        observability._langfuse_client = client

        self.assertEqual(observability.system_prompt(), ("cached system prompt", "v1"))
        self.assertEqual(observability.system_prompt(), ("cached system prompt", "v1"))
        self.assertEqual(client.calls, 1)

        observability._system_prompt_cache = ("stale prompt", "v0", 0)
        client.fail = True

        self.assertEqual(observability.system_prompt(), ("stale prompt", "v0"))


if __name__ == "__main__":
    unittest.main()
