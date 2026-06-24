import io
import json
import unittest
from dataclasses import replace

from backend.app.config import AppSettings
from backend.app.ingest import IngestionJob, checksum_bytes, chunk_text, parse_document


def settings():
    return AppSettings(
        app_env="test",
        aws_region="eu-west-2",
        secrets_stage="test",
        app_secret_name="/test/app",
        azure_openai_secret_name="/test/azure",
        langfuse_secret_name="/test/langfuse",
        s3_bucket="bucket",
        s3_raw_prefix="raw/",
        s3_manifest_key="manifests/documents.json",
        opensearch_endpoint="https://collection.eu-west-2.aoss.amazonaws.com",
        opensearch_index="idx",
        dynamodb_chat_table="table",
        chat_history_backend="memory",
        cors_origins=(),
        prompt_label="dev",
        max_history_chars=1000,
    )


class FakePaginator:
    def __init__(self, keys):
        self.keys = keys

    def paginate(self, Bucket, Prefix):
        return {"Contents": [{"Key": key} for key in self.keys]},


class FakeS3:
    def __init__(self, objects, manifest=None):
        self.objects = dict(objects)
        self.manifest = manifest
        self.puts = []

    def get_paginator(self, name):
        return FakePaginator(sorted(self.objects))

    def get_object(self, Bucket, Key):
        if Key == "manifests/documents.json":
            if self.manifest is None:
                raise KeyError(Key)
            return {"Body": io.BytesIO(json.dumps(self.manifest).encode("utf-8"))}
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": Body, "ContentType": ContentType})


class FakeOpenSearch:
    def __init__(self, index_exists=True):
        self.indexes = []
        self.deletes = []
        self.indices = FakeIndices(index_exists)

    def index(self, index, id, body, refresh=False):
        self.indexes.append({"index": index, "id": id, "body": body, "refresh": refresh})

    def delete_by_query(self, index, body, refresh=True, conflicts="proceed"):
        self.deletes.append(
            {"index": index, "body": body, "refresh": refresh, "conflicts": conflicts}
        )
        return {"deleted": 2}


class FakeIndices:
    def __init__(self, exists=True, fail_first_create=False):
        self._exists = exists
        self.fail_first_create = fail_first_create
        self.exists_calls = []
        self.create_calls = []

    def exists(self, index):
        self.exists_calls.append(index)
        return self._exists

    def create(self, index, body):
        self.create_calls.append({"index": index, "body": body})
        if self.fail_first_create and len(self.create_calls) == 1:
            raise ValueError("unsupported mapping")
        self._exists = True
        return {"acknowledged": True}


def make_job(s3, opensearch=None, app_settings=None):
    job = IngestionJob.__new__(IngestionJob)
    job.settings = app_settings or settings()
    job.secret_provider = None
    job.s3 = s3
    job._embeddings = None
    job._opensearch = opensearch or FakeOpenSearch()
    job._embed = lambda text: None
    return job


class IncrementalIngestionTests(unittest.TestCase):
    def test_parse_document_returns_generic_metadata_without_extracted_facts(self):
        document = parse_document(
            "raw/hr_leave_policy.txt",
            b"HR leave policy. Employees should follow the return-to-work review process.",
        )

        self.assertEqual(document.content_type, "text/plain")
        self.assertEqual(document.metadata["domain"], "admin_policy")
        self.assertEqual(document.metadata["document_type"], "policy")
        self.assertNotIn("facts", document.metadata)

    def test_chunk_text_honors_configured_size_and_overlap(self):
        chunks = chunk_text("abcdefghij" * 200, chunk_size=500, chunk_overlap=100)

        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        self.assertGreater(len(chunks), 1)

    def test_unchanged_manifest_file_is_skipped(self):
        body = b"# Leave policy"
        manifest = {
            "opensearch_index": "idx",
            "documents": [
                {
                    "key": "raw/leave.md",
                    "title": "leave.md",
                    "content_type": "text/markdown",
                    "checksum": checksum_bytes(body),
                    "metadata": {"domain": "admin_policy", "document_type": "policy"},
                    "chunk_count": 1,
                }
            ]
        }
        opensearch = FakeOpenSearch()
        job = make_job(FakeS3({"raw/leave.md": body}, manifest), opensearch)

        result = job.run()

        self.assertEqual(result["indexed_chunks"], 0)
        self.assertEqual(result["skipped_documents"], 1)
        self.assertEqual(result["documents"][0]["ingestion_status"], "skipped_unchanged")
        self.assertEqual(opensearch.indexes, [])
        self.assertEqual(opensearch.deletes, [])

    def test_opensearch_index_change_forces_reindex_of_unchanged_file(self):
        body = b"# Leave policy"
        manifest = {
            "opensearch_index": "old-idx",
            "documents": [
                {
                    "key": "raw/leave.md",
                    "title": "leave.md",
                    "content_type": "text/markdown",
                    "checksum": checksum_bytes(body),
                    "metadata": {"domain": "admin_policy", "document_type": "policy"},
                    "chunk_count": 1,
                }
            ],
        }
        opensearch = FakeOpenSearch()
        app_settings = replace(settings(), opensearch_index="new-idx")
        job = make_job(FakeS3({"raw/leave.md": body}, manifest), opensearch, app_settings)

        result = job.run()

        self.assertTrue(result["force_reindex"])
        self.assertEqual(result["previous_opensearch_index"], "old-idx")
        self.assertEqual(result["opensearch_index"], "new-idx")
        self.assertEqual(result["indexed_documents"], 1)
        self.assertEqual(result["skipped_documents"], 0)
        self.assertEqual(opensearch.indexes[0]["index"], "new-idx")
        self.assertEqual(opensearch.deletes, [])

    def test_changed_file_deletes_old_chunks_and_reindexes(self):
        old_body = b"# Old leave policy"
        new_body = b"# New leave policy"
        manifest = {
            "opensearch_index": "idx",
            "documents": [
                {
                    "key": "raw/leave.md",
                    "title": "leave.md",
                    "content_type": "text/markdown",
                    "checksum": checksum_bytes(old_body),
                    "metadata": {"domain": "admin_policy", "document_type": "policy"},
                    "chunk_count": 1,
                }
            ]
        }
        opensearch = FakeOpenSearch()
        job = make_job(FakeS3({"raw/leave.md": new_body}, manifest), opensearch)

        result = job.run()

        self.assertEqual(result["indexed_documents"], 1)
        self.assertEqual(result["indexed_chunks"], 1)
        self.assertEqual(result["total_chunks"], 1)
        self.assertEqual(result["deleted_chunks"], 2)
        self.assertEqual(result["documents"][0]["ingestion_status"], "indexed")
        self.assertEqual(opensearch.deletes[0]["body"], {"query": {"term": {"key": "raw/leave.md"}}})
        self.assertEqual(opensearch.indexes[0]["body"]["key"], "raw/leave.md")

    def test_missing_opensearch_index_is_created_before_indexing(self):
        opensearch = FakeOpenSearch(index_exists=False)
        job = make_job(FakeS3({"raw/leave.md": b"# Leave policy"}), opensearch)

        result = job.run()

        self.assertEqual(result["indexed_documents"], 1)
        self.assertEqual(opensearch.indices.exists_calls, ["idx"])
        self.assertEqual(opensearch.indices.create_calls[0]["index"], "idx")
        self.assertEqual(opensearch.indices.create_calls[0]["body"]["settings"], {"index.knn": True})
        self.assertEqual(
            opensearch.indices.create_calls[0]["body"]["mappings"]["properties"]["embedding"]["type"],
            "knn_vector",
        )
        self.assertEqual(opensearch.indexes[0]["index"], "idx")

    def test_missing_opensearch_index_uses_fallback_mapping_when_rich_mapping_fails(self):
        opensearch = FakeOpenSearch(index_exists=False)
        opensearch.indices.fail_first_create = True
        job = make_job(FakeS3({"raw/leave.md": b"# Leave policy"}), opensearch)

        result = job.run()

        self.assertEqual(result["indexed_documents"], 1)
        self.assertEqual(len(opensearch.indices.create_calls), 2)
        fallback_body = opensearch.indices.create_calls[1]["body"]
        self.assertEqual(fallback_body["settings"], {"index.knn": True})
        self.assertNotIn("method", fallback_body["mappings"]["properties"]["embedding"])
        self.assertEqual(fallback_body["mappings"]["properties"]["metadata"]["type"], "object")

    def test_removed_manifest_file_deletes_stale_chunks(self):
        manifest = {
            "opensearch_index": "idx",
            "documents": [
                {
                    "key": "raw/removed.md",
                    "title": "removed.md",
                    "content_type": "text/markdown",
                    "checksum": "old",
                    "metadata": {"domain": "general", "document_type": "document"},
                    "chunk_count": 1,
                }
            ]
        }
        opensearch = FakeOpenSearch()
        job = make_job(FakeS3({}, manifest), opensearch)

        result = job.run()

        self.assertEqual(result["deleted_documents"], 1)
        self.assertEqual(result["deleted_chunks"], 2)
        self.assertEqual(result["documents"], [])
        self.assertEqual(opensearch.deletes[0]["body"], {"query": {"term": {"key": "raw/removed.md"}}})

    def test_csv_files_are_not_indexed_for_rag(self):
        opensearch = FakeOpenSearch()
        job = make_job(
            FakeS3(
                {
                    "raw/doctor_rota.csv": b"date,doctor\nToday,Dr Aisha Malik\n",
                    "raw/privacy_policy.md": b"# Patient privacy policy",
                }
            ),
            opensearch,
        )

        result = job.run()

        self.assertEqual(result["indexed_documents"], 1)
        self.assertEqual(result["documents"][0]["key"], "raw/privacy_policy.md")
        self.assertEqual(result["documents"][0]["uri"], "s3://bucket/raw/privacy_policy.md")
        self.assertEqual(opensearch.indexes[0]["body"]["key"], "raw/privacy_policy.md")

    def test_metadata_only_csv_manifest_records_are_preserved(self):
        manifest = {
            "opensearch_index": "idx",
            "documents": [
                {
                    "key": "postgres://uploaded_lookup_rows/doctor_rota.csv",
                    "title": "doctor_rota.csv",
                    "uri": "postgres://uploaded_lookup_rows/doctor_rota.csv",
                    "content_type": "text/csv",
                    "checksum": "old",
                    "metadata": {
                        "domain": "deterministic_lookup",
                        "document_type": "csv_table",
                        "asset_source": "postgres_uploaded_lookup",
                    },
                    "chunk_count": 0,
                    "ingestion_status": "metadata_only",
                }
            ],
        }
        opensearch = FakeOpenSearch()
        job = make_job(
            FakeS3({"raw/privacy_policy.md": b"# Patient privacy policy"}, manifest),
            opensearch,
        )

        result = job.run()

        self.assertEqual(result["indexed_documents"], 1)
        self.assertEqual(result["deleted_documents"], 0)
        self.assertEqual(result["deleted_chunks"], 0)
        self.assertEqual(result["documents"][0]["key"], "postgres://uploaded_lookup_rows/doctor_rota.csv")
        self.assertEqual(result["documents"][0]["ingestion_status"], "metadata_only")
        self.assertEqual(result["documents"][1]["key"], "raw/privacy_policy.md")
        self.assertEqual(result["documents"][1]["uri"], "s3://bucket/raw/privacy_policy.md")
        self.assertEqual(result["total_chunks"], 1)


if __name__ == "__main__":
    unittest.main()
