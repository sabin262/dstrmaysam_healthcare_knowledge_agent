import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from backend.app import main
from backend.app.config import AppSettings
from backend.app.local_chroma import LocalChromaIngestionJob, LocalChromaRetrievalService
from backend.app.secrets import EnvSecretProvider, SecretProvider, StaticSecretProvider
from backend.app.storage import LocalDocumentStore


def settings(**overrides):
    app_settings = AppSettings(
        app_env="local",
        aws_region="eu-west-2",
        secrets_stage="dev",
        app_secret_name="/local/app",
        azure_openai_secret_name="/local/azure",
        langfuse_secret_name="/local/langfuse",
        s3_bucket="bucket",
        s3_raw_prefix="raw/",
        s3_manifest_key="manifests/documents.json",
        opensearch_endpoint="https://collection.example",
        opensearch_index="idx",
        dynamodb_chat_table="table",
        chat_history_backend="dynamodb",
        cors_origins=(),
        prompt_label="dev",
        max_history_chars=1000,
        local_test_admin_enabled=True,
    )
    return replace(app_settings, **overrides)


class FakeCollection:
    def __init__(self):
        self.items = {}
        self.upserts = []
        self.deleted = []

    def upsert(self, ids, documents, embeddings, metadatas):
        self.upserts.append(
            {"ids": ids, "documents": documents, "embeddings": embeddings, "metadatas": metadatas}
        )
        for item_id, document, embedding, metadata in zip(ids, documents, embeddings, metadatas):
            self.items[item_id] = {
                "document": document,
                "embedding": embedding,
                "metadata": metadata,
            }

    def get(self, where=None):
        ids = []
        documents = []
        metadatas = []
        for item_id, item in self.items.items():
            metadata = item["metadata"]
            if where and not self._matches(metadata, where):
                continue
            ids.append(item_id)
            documents.append(item["document"])
            metadatas.append(metadata)
        return {"ids": ids, "documents": documents, "metadatas": metadatas}

    def delete(self, ids):
        self.deleted.extend(ids)
        for item_id in ids:
            self.items.pop(item_id, None)

    def query(self, query_embeddings, n_results, where=None, include=None):
        values = self.get(where=where)
        return {
            "documents": [values["documents"][:n_results]],
            "metadatas": [values["metadatas"][:n_results]],
            "distances": [[0.2 for _ in values["documents"][:n_results]]],
        }

    def _matches(self, metadata, where):
        for key, expected in where.items():
            value = metadata.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if value not in expected["$in"]:
                    return False
            elif value != expected:
                return False
        return True


class LocalModeTests(unittest.TestCase):
    def test_env_secret_provider_loads_env_and_persists_local_app_secret(self):
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            "os.environ",
            {
                "AZURE_OPENAI_ENDPOINT": "https://azure.example",
                "AZURE_OPENAI_API_KEY": "key",
                "AZURE_OPENAI_API_VERSION": "2025-04-01-preview",
                "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-mini",
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-small",
                "LANGFUSE_PUBLIC_KEY": "pk",
                "LANGFUSE_SECRET_KEY": "sk",
                "LANGFUSE_BASE_URL": "https://langfuse.example",
            },
            clear=False,
        ):
            app_settings = settings(local_app_secret_file=str(Path(tmpdir) / "local_app_secret.json"))
            provider = EnvSecretProvider(app_settings)

            app_secret = provider.get_json(app_settings.app_secret_name)
            azure = provider.load_azure_openai()
            langfuse = provider.load_langfuse()
            app_secret["auth_users"]["doctor"] = "hash"
            provider.put_json(app_settings.app_secret_name, app_secret)

            self.assertIn("admin", app_secret["auth_users"])
            self.assertEqual(azure.endpoint, "https://azure.example")
            self.assertEqual(azure.embedding_deployment, "text-embedding-3-small")
            self.assertEqual(langfuse.public_key, "pk")
            self.assertIn("doctor", json.loads(Path(app_settings.local_app_secret_file).read_text())["auth_users"])

    def test_factories_use_local_resources_when_local_test_admin_enabled(self):
        app_settings = settings()
        for factory in (
            main.get_secret_provider,
            main.get_history_repository,
            main.get_document_store,
            main.get_retrieval_service,
            main.get_observability,
            main.get_agent,
        ):
            factory.cache_clear()
        with mock.patch.object(main, "get_settings", lambda: app_settings):
            self.assertIsInstance(main.get_secret_provider(), EnvSecretProvider)
            with mock.patch.object(main, "PostgresChatHistoryRepository") as history_class:
                main.get_history_repository.cache_clear()
                main.get_history_repository()
                history_class.assert_called_once_with(app_settings)
            self.assertIsInstance(main.get_document_store(), LocalDocumentStore)
            self.assertIsInstance(main.get_retrieval_service(), LocalChromaRetrievalService)
            self.assertIsInstance(main.create_ingestion_job(), LocalChromaIngestionJob)

    def test_factories_keep_aws_resources_when_local_test_admin_disabled(self):
        app_settings = settings(local_test_admin_enabled=False)
        for factory in (
            main.get_secret_provider,
            main.get_history_repository,
            main.get_document_store,
            main.get_retrieval_service,
            main.get_observability,
            main.get_agent,
        ):
            factory.cache_clear()
        with mock.patch.object(main, "get_settings", lambda: app_settings):
            self.assertIsInstance(main.get_secret_provider(), SecretProvider)
            self.assertNotIsInstance(main.get_secret_provider(), EnvSecretProvider)
            self.assertNotIsInstance(main.get_document_store(), LocalDocumentStore)
            self.assertNotIsInstance(main.get_retrieval_service(), LocalChromaRetrievalService)

    def test_local_document_store_uploads_to_data_raw_and_lists_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            app_settings = settings(local_data_dir=tmpdir)
            store = LocalDocumentStore(app_settings)

            store.upload_document("raw/policy.md", b"# Policy", "text/markdown")
            manifest_path = Path(tmpdir) / "manifests" / "documents.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "key": "raw/policy.md",
                                "title": "policy.md",
                                "content_type": "text/markdown",
                                "metadata": {"domain": "general"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            documents = store.list_documents()

            self.assertEqual((Path(tmpdir) / "raw" / "policy.md").read_bytes(), b"# Policy")
            self.assertEqual(documents[0].uri, "local://raw/policy.md")

    def test_local_chroma_ingestion_indexes_and_skips_unchanged_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            app_settings = settings(local_data_dir=tmpdir)
            raw_path = Path(tmpdir) / "raw" / "policy.txt"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text("Staff policy requires annual review.", encoding="utf-8")
            collection = FakeCollection()
            job = LocalChromaIngestionJob(app_settings, StaticSecretProvider(app_settings, {}))
            job._collection = collection
            job._embed = lambda text: [0.1, 0.2, 0.3]

            first = job.run()
            second = job.run()

            self.assertEqual(first["indexed_documents"], 1)
            self.assertEqual(first["indexed_chunks"], 1)
            self.assertEqual(second["skipped_documents"], 1)
            self.assertEqual(len(collection.upserts), 1)
            self.assertTrue((Path(tmpdir) / "manifests" / "documents.json").exists())

    def test_local_chroma_ingestion_preserves_metadata_only_csv_manifest_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            app_settings = settings(local_data_dir=tmpdir)
            manifest_path = Path(tmpdir) / "manifests" / "documents.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "key": "postgres://uploaded_lookup_rows/doctor_rota.csv",
                                "title": "doctor_rota.csv",
                                "uri": "postgres://uploaded_lookup_rows/doctor_rota.csv",
                                "content_type": "text/csv",
                                "metadata": {"asset_source": "postgres_uploaded_lookup"},
                                "chunk_count": 0,
                                "ingestion_status": "metadata_only",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            raw_path = Path(tmpdir) / "raw" / "policy.txt"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text("Staff policy requires annual review.", encoding="utf-8")
            job = LocalChromaIngestionJob(app_settings, StaticSecretProvider(app_settings, {}))
            job._collection = FakeCollection()
            job._embed = lambda text: [0.1, 0.2, 0.3]

            result = job.run()

            self.assertEqual(result["documents"][0]["key"], "postgres://uploaded_lookup_rows/doctor_rota.csv")
            self.assertEqual(result["documents"][0]["ingestion_status"], "metadata_only")
            self.assertEqual(result["documents"][1]["key"], "raw/policy.txt")
            self.assertEqual(result["indexed_documents"], 1)

    def test_local_chroma_retrieval_returns_hits_and_neighbors(self):
        app_settings = settings(rag_top_k=1, rag_neighbor_chunks=1)
        collection = FakeCollection()
        collection.upsert(
            ids=["policy:0", "policy:1"],
            documents=["Policy heading.", "Staff policy requires annual review."],
            embeddings=[[0.1], [0.2]],
            metadatas=[
                {
                    "key": "raw/policy.txt",
                    "title": "policy.txt",
                    "uri": "local://raw/policy.txt",
                    "chunk_index": 0,
                    "content_type": "text/plain",
                    "checksum": "abc",
                    "metadata_json": json.dumps({"domain": "general"}),
                },
                {
                    "key": "raw/policy.txt",
                    "title": "policy.txt",
                    "uri": "local://raw/policy.txt",
                    "chunk_index": 1,
                    "content_type": "text/plain",
                    "checksum": "abc",
                    "metadata_json": json.dumps({"domain": "admin_policy", "document_type": "policy"}),
                },
            ],
        )
        service = LocalChromaRetrievalService(app_settings, StaticSecretProvider(app_settings, {}))
        service._collection = collection
        service._embed = lambda text: [0.1]

        hits = service.search("policy review", document_keys=["raw/policy.txt"])

        self.assertEqual(hits[0].uri, "local://raw/policy.txt")
        self.assertTrue(any(hit.metadata.get("document_type") == "policy" for hit in hits))
        self.assertTrue(any(hit.metadata.get("_retrieval_strategy") == "neighbor" for hit in hits))

    def test_local_chroma_delete_all_indexes_clears_collection_ids(self):
        app_settings = settings()
        collection = FakeCollection()
        collection.upsert(
            ids=["policy:0", "policy:1"],
            documents=["Policy heading.", "Policy body."],
            embeddings=[[0.1], [0.2]],
            metadatas=[
                {"key": "raw/policy.txt", "title": "policy.txt"},
                {"key": "raw/policy.txt", "title": "policy.txt"},
            ],
        )
        service = LocalChromaRetrievalService(app_settings, StaticSecretProvider(app_settings, {}))
        service._collection = collection

        deleted = service.delete_all_indexes()

        self.assertEqual(deleted, 2)
        self.assertEqual(collection.items, {})
        self.assertEqual(collection.deleted, ["policy:0", "policy:1"])


if __name__ == "__main__":
    unittest.main()
