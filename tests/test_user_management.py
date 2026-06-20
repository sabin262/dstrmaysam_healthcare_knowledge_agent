import unittest
from unittest import mock

from backend.app.auth import AuthService, AuthenticationError, UserManagementError, hash_password
from backend.app.config import AppSettings
from backend.app.history import ChatMessage, InMemoryChatHistoryRepository
from backend.app.secrets import StaticSecretProvider
from backend.app.storage import DocumentRecord

try:
    from fastapi.testclient import TestClient

    from backend.app import main
except ModuleNotFoundError:
    TestClient = None
    main = None


def app_settings() -> AppSettings:
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
    )


def secret_payload() -> dict:
    return {
        "session_secret": "secret",
        "auth_users": {
            "admin": hash_password("adminpass1"),
            "staff": hash_password("staffpass1"),
        },
        "user_profiles": {
            "admin": {"roles": ["admin"], "departments": ["clinical_governance"]},
            "staff": {"roles": ["staff"], "departments": ["operations"]},
        },
    }


def make_auth_service() -> AuthService:
    settings = app_settings()
    return AuthService(StaticSecretProvider(settings, {settings.app_secret_name: secret_payload()}))


class UserManagementServiceTests(unittest.TestCase):
    def test_login_result_includes_profile_and_defaults_password_flag(self):
        service = make_auth_service()

        result = service.login("staff", "staffpass1")

        self.assertEqual(result.username, "staff")
        self.assertEqual(result.roles, ["staff"])
        self.assertEqual(result.departments, ["operations"])
        self.assertFalse(result.password_change_required)

    def test_create_user_sets_password_change_required_and_change_password_clears_it(self):
        service = make_auth_service()

        created = service.create_user("doctor1", "temporary1", ["doctor", "staff"], ["Cardiology"])
        self.assertTrue(created.password_change_required)

        login = service.login("doctor1", "temporary1")
        self.assertTrue(login.password_change_required)
        changed = service.change_password("doctor1", "temporary1", "permanent1")

        self.assertFalse(changed.password_change_required)
        self.assertFalse(service.login("doctor1", "permanent1").password_change_required)
        with self.assertRaises(AuthenticationError):
            service.login("doctor1", "temporary1")

    def test_reset_password_forces_password_change(self):
        service = make_auth_service()

        reset = service.reset_password("staff", "temporary2")

        self.assertTrue(reset.password_change_required)
        self.assertTrue(service.login("staff", "temporary2").password_change_required)

    def test_update_user_validates_known_roles_and_preserves_final_admin(self):
        service = make_auth_service()

        with self.assertRaises(UserManagementError):
            service.update_user("staff", roles=["unknown"])
        with self.assertRaises(UserManagementError):
            service.update_user("admin", roles=["staff"])

        updated = service.update_user("staff", roles=["manager", "staff"], departments=["Ops", "IT"])
        self.assertEqual(updated.roles, ["manager", "staff"])
        self.assertEqual(updated.departments, ["ops", "it"])


class UserManagementApiTests(unittest.TestCase):
    @unittest.skipIf(TestClient is None, "FastAPI test dependencies are not installed")
    def setUp(self):
        self.auth = make_auth_service()
        self.patch = mock.patch.object(main, "get_auth_service", lambda: self.auth)
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()

    def headers_for(self, username: str, password: str) -> dict[str, str]:
        token = self.auth.login(username, password).access_token
        return {"Authorization": f"Bearer {token}"}

    def test_admin_can_manage_users_and_non_admin_cannot(self):
        admin_headers = self.headers_for("admin", "adminpass1")
        staff_headers = self.headers_for("staff", "staffpass1")

        self.assertEqual(self.client.get("/admin/users", headers=staff_headers).status_code, 403)

        create_response = self.client.post(
            "/admin/users",
            headers=admin_headers,
            json={
                "username": "nurse1",
                "temporary_password": "temporary1",
                "roles": ["nurse", "staff"],
                "departments": ["Ward_A"],
            },
        )
        self.assertEqual(create_response.status_code, 201)
        self.assertTrue(create_response.json()["password_change_required"])

        update_response = self.client.patch(
            "/admin/users/nurse1",
            headers=admin_headers,
            json={"roles": ["manager", "staff"], "departments": ["Operations"]},
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["roles"], ["manager", "staff"])

        reset_response = self.client.post(
            "/admin/users/nurse1/reset-password",
            headers=admin_headers,
            json={"temporary_password": "temporary2"},
        )
        self.assertEqual(reset_response.status_code, 200)
        self.assertTrue(reset_response.json()["password_change_required"])

    def test_short_temporary_password_returns_domain_error_not_validation_422(self):
        admin_headers = self.headers_for("admin", "adminpass1")

        response = self.client.post(
            "/admin/users",
            headers=admin_headers,
            json={
                "username": "shortpass",
                "temporary_password": "short",
                "roles": ["staff"],
                "departments": [],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Password must be at least 8 characters")

    def test_password_change_required_user_is_blocked_until_password_change(self):
        self.auth.create_user("doctor1", "temporary1", ["doctor"], ["cardiology"])
        headers = self.headers_for("doctor1", "temporary1")

        self.assertEqual(self.client.post("/chat", headers=headers, json={"query": "hello"}).status_code, 403)
        self.assertEqual(self.client.get("/documents", headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/admin/users", headers=headers).status_code, 403)

        change_response = self.client.post(
            "/auth/change-password",
            headers=headers,
            json={"current_password": "temporary1", "new_password": "permanent1"},
        )
        self.assertEqual(change_response.status_code, 200)
        self.assertFalse(change_response.json()["password_change_required"])


class FakeDocumentStore:
    def __init__(self):
        self.uploads = []
        self.records = [
            DocumentRecord(
                title="policy.md",
                uri="s3://bucket/raw/policy.md",
                key="raw/policy.md",
                content_type="text/markdown",
                metadata={"domain": "admin_policy", "document_type": "policy"},
                chunk_count=4,
                ingestion_status="indexed",
            )
        ]

    def upload_document(self, key: str, data: bytes, content_type: str) -> None:
        self.uploads.append({"key": key, "data": data, "content_type": content_type})

    def list_documents(self):
        return list(self.records)


class FakeAccess:
    def filter_documents(self, user, documents):
        return documents


class FakeAgent:
    access = FakeAccess()


class FakeIngestionJob:
    calls = 0

    def __init__(self, settings, secret_provider):
        self.settings = settings
        self.secret_provider = secret_provider

    def run(self):
        FakeIngestionJob.calls += 1
        return {
            "documents": [{"key": "raw/policy.md", "title": "policy.md"}],
            "indexed_chunks": 3,
        }


class FakePatientLookup:
    def __init__(self):
        self.calls = []

    def patient_dashboard(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "available_tables": ["patients", "appointments"],
            "access_scopes_applied": ["all_staff", "clinical"],
            "filters": {
                "query": kwargs.get("query"),
                "patient_identifier": kwargs.get("patient_identifier"),
                "department": kwargs.get("department"),
                "ward": kwargs.get("ward"),
                "care_status": kwargs.get("care_status"),
                "tables": kwargs.get("tables"),
                "limit": kwargs.get("limit"),
            },
            "summary": {
                "row_count": 1,
                "unique_patients": 1,
                "table_counts": {"patients": 1},
                "message": "Found 1 matching row(s).",
            },
            "rows": [
                {
                    "table": "patients",
                    "patient_id": "PAT-001",
                    "mrn": "MRN10001",
                    "patient_name": "John Spencer",
                    "department_name": "Cardiology",
                    "ward_code": "W02",
                    "care_status": "Inpatient",
                }
            ],
        }


class AdminDocumentApiTests(unittest.TestCase):
    @unittest.skipIf(TestClient is None, "FastAPI test dependencies are not installed")
    def setUp(self):
        self.settings = app_settings()
        self.auth = make_auth_service()
        self.documents = FakeDocumentStore()
        self.history = InMemoryChatHistoryRepository()
        self.patient_lookup = FakePatientLookup()
        FakeIngestionJob.calls = 0
        self.patches = [
            mock.patch.object(main, "get_auth_service", lambda: self.auth),
            mock.patch.object(main, "get_settings", lambda: self.settings),
            mock.patch.object(main, "get_document_store", lambda: self.documents),
            mock.patch.object(main, "get_agent", lambda: FakeAgent()),
            mock.patch.object(main, "get_history_repository", lambda: self.history),
            mock.patch.object(main, "get_deterministic_lookup_service", lambda: self.patient_lookup),
            mock.patch.object(main, "get_secret_provider", lambda: self.auth.secret_provider),
            mock.patch.object(main, "IngestionJob", FakeIngestionJob),
        ]
        for patch in self.patches:
            patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def headers_for(self, username: str, password: str) -> dict[str, str]:
        token = self.auth.login(username, password).access_token
        return {"Authorization": f"Bearer {token}"}

    def test_admin_can_upload_document_to_raw_s3_prefix(self):
        response = self.client.post(
            "/admin/documents/upload",
            headers=self.headers_for("admin", "adminpass1"),
            files={"file": ("Clinical Policy.md", b"# Policy", "text/markdown")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["key"], "raw/Clinical_Policy.md")
        self.assertEqual(self.documents.uploads[0]["key"], "raw/Clinical_Policy.md")
        self.assertEqual(self.documents.uploads[0]["data"], b"# Policy")

    def test_non_admin_cannot_upload_document(self):
        response = self.client.post(
            "/admin/documents/upload",
            headers=self.headers_for("staff", "staffpass1"),
            files={"file": ("policy.md", b"# Policy", "text/markdown")},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.documents.uploads, [])

    def test_unsupported_upload_extension_returns_400(self):
        response = self.client.post(
            "/admin/documents/upload",
            headers=self.headers_for("admin", "adminpass1"),
            files={"file": ("malware.exe", b"nope", "application/octet-stream")},
        )

        self.assertEqual(response.status_code, 400)

    def test_admin_can_run_ingestion(self):
        response = self.client.post(
            "/admin/documents/ingest",
            headers=self.headers_for("admin", "adminpass1"),
            json={},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["indexed_chunks"], 3)
        self.assertEqual(FakeIngestionJob.calls, 1)

    def test_documents_endpoint_returns_chunk_table_fields(self):
        response = self.client.get(
            "/documents",
            headers=self.headers_for("admin", "adminpass1"),
        )

        self.assertEqual(response.status_code, 200)
        document = response.json()[0]
        self.assertEqual(document["title"], "policy.md")
        self.assertEqual(document["key"], "raw/policy.md")
        self.assertEqual(document["chunk_count"], 4)
        self.assertEqual(document["ingestion_status"], "indexed")
        self.assertEqual(document["metadata"]["domain"], "admin_policy")

    def test_admin_dashboard_returns_query_summary(self):
        self.history.save_message(
            "staff",
            "session-1",
            ChatMessage(role="user", content="What is the leave policy?"),
        )
        self.history.save_message(
            "staff",
            "session-1",
            ChatMessage(
                role="assistant",
                content="The leave policy is available.",
                metadata={
                    "trace_id": "trace-123",
                    "tools_used": ["rag_search"],
                    "latency_ms": 1200,
                    "input_tokens": 10,
                    "output_tokens": 6,
                    "model": "gpt-4.1-mini",
                    "sources": [{"uri": "s3://bucket/raw/policy.md"}],
                    "source_document_keys": ["raw/policy.md"],
                    "guardrail_applied": False,
                    "performance": {"agent_mode": "fast_rag"},
                    "safety": {"risk_level": "low"},
                },
            ),
        )

        response = self.client.get(
            "/admin/dashboard",
            headers=self.headers_for("admin", "adminpass1"),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["total_queries"], 1)
        self.assertEqual(payload["summary"]["avg_input_tokens"], 10)
        self.assertEqual(payload["summary"]["avg_output_tokens"], 6)
        self.assertEqual(payload["summary"]["avg_total_tokens"], 16)
        self.assertEqual(payload["summary"]["tool_counts"]["rag_search"], 1)
        self.assertEqual(payload["summary"]["model_counts"]["gpt-4.1-mini"], 1)
        self.assertEqual(payload["queries"][0]["user_id"], "staff")
        self.assertEqual(payload["queries"][0]["trace_id"], "trace-123")
        self.assertEqual(payload["queries"][0]["total_tokens"], 16)
        self.assertEqual(payload["queries"][0]["source_document_keys"], ["raw/policy.md"])

    def test_admin_patient_details_uses_postgres_lookup_filters(self):
        response = self.client.get(
            "/admin/patient-details",
            headers=self.headers_for("admin", "adminpass1"),
            params={
                "q": "john",
                "patient_identifier": "MRN10001",
                "department": "Cardiology",
                "ward": "W02",
                "care_status": "Inpatient",
                "tables": ["patients"],
                "limit": 25,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["row_count"], 1)
        self.assertEqual(payload["rows"][0]["patient_id"], "PAT-001")
        self.assertEqual(self.patient_lookup.calls[0]["query"], "john")
        self.assertEqual(self.patient_lookup.calls[0]["patient_identifier"], "MRN10001")
        self.assertEqual(self.patient_lookup.calls[0]["tables"], ["patients"])
        self.assertEqual(self.patient_lookup.calls[0]["limit"], 25)


if __name__ == "__main__":
    unittest.main()
