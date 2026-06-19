import unittest

from backend.app.config import AppSettings
from backend.app.secrets import StaticSecretProvider


def settings(azure_openai_deployment: str = "") -> AppSettings:
    return AppSettings(
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
        azure_openai_deployment=azure_openai_deployment,
    )


class SecretProviderTests(unittest.TestCase):
    def test_azure_openai_deployment_env_setting_overrides_secret_chat_deployment(self):
        app_settings = settings(azure_openai_deployment="gpt-4.1-mini")
        provider = StaticSecretProvider(
            app_settings,
            {
                "/test/azure": {
                    "endpoint": "https://example.openai.azure.com/",
                    "api_key": "secret",
                    "api_version": "2025-04-01-preview",
                    "chat_deployment": "stale-deployment",
                    "embedding_deployment": "text-embedding-3-small",
                }
            },
        )

        secrets = provider.load_azure_openai()

        self.assertEqual(secrets.chat_deployment, "gpt-4.1-mini")
        self.assertEqual(secrets.embedding_deployment, "text-embedding-3-small")


if __name__ == "__main__":
    unittest.main()
