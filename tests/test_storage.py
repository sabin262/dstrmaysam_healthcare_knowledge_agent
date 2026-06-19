import io
import unittest
from dataclasses import replace

from backend.app.config import AppSettings
from backend.app.storage import DocumentStore


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


class FakeS3Client:
    def __init__(self):
        self.get_calls = 0
        self.put_calls = 0

    def get_object(self, Bucket, Key):
        self.get_calls += 1
        return {
            "Body": io.BytesIO(
                b'{"documents": [{"title": "Policy", "key": "raw/policy.md"}]}'
            )
        }

    def put_object(self, Bucket, Key, Body, ContentType):
        self.put_calls += 1


class DocumentStoreCacheTests(unittest.TestCase):
    def test_manifest_uses_ttl_cache_and_invalidates_after_upload(self):
        store = DocumentStore(settings(document_manifest_cache_ttl_seconds=300))
        store._s3_client = FakeS3Client()

        self.assertEqual(len(store.list_documents()), 1)
        self.assertEqual(len(store.list_documents()), 1)
        self.assertEqual(store._s3_client.get_calls, 1)

        store.upload_document("raw/new.md", b"content", "text/markdown")
        self.assertEqual(len(store.list_documents()), 1)

        self.assertEqual(store._s3_client.put_calls, 1)
        self.assertEqual(store._s3_client.get_calls, 2)


if __name__ == "__main__":
    unittest.main()
