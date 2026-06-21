import json
import unittest
from dataclasses import replace
from datetime import date, timedelta

from backend.app.config import AppSettings
from backend.app.agent import KnowledgeAgent
from backend.app.deterministic_lookup import DeterministicLookupService, LookupResult
from backend.app.healthcare import HealthcareAccessControl, HealthcareSafetyGuard, HealthcareUserContext
from backend.app.healthcare_tools import build_healthcare_agent_tools
from backend.app.storage import DocumentRecord


class FakeRetrieval:
    def search(self, query, *args, **kwargs):
        return []


class FakeDocuments:
    def list_documents(self):
        return []

    def lookup_table(self, query):
        return []

    def read_text(self, key):
        return ""


class FakeRotaDocuments(FakeDocuments):
    def __init__(self):
        today = date.today()
        self.today = today.isoformat()
        self.tomorrow = (today + timedelta(days=1)).isoformat()

    def list_documents(self):
        return [
            DocumentRecord(
                title="Staff rota",
                uri="local://structured_lookup_csv/staff_rota.csv",
                key="structured_lookup_csv/staff_rota.csv",
                content_type="text/csv",
                metadata={
                    "lookup_category": "doctors",
                    "document_type": "schedule",
                    "allowed_roles": ["staff"],
                    "checksum": "test-rota",
                },
            )
        ]

    def read_text(self, key):
        return "\n".join(
            [
                "date,department,role,staff_name,shift_start,shift_end,on_call,contact,access_level",
                f"{self.tomorrow},ICU,Registrar,Future Doctor,09:00,17:00,Yes,icu@example.nhs,clinical",
                f"{self.tomorrow},Respiratory,Senior Nurse,Priya Shah,09:00,17:00,No,respiratory@example.nhs,clinical",
                f"{self.tomorrow},Community Care,Staff Nurse,Emily Turner,08:00,20:00,No,community@example.nhs,clinical",
                f"{self.today},Paediatrics,Registrar,Yusuf Ahmed,08:00,20:00,Yes,paeds@example.nhs,clinical",
            ]
        )


class FakeDeterministicLookup:
    def lookup(self, query, user):
        return LookupResult(
            category="doctors",
            rows=[
                {
                    "doctor_id": "DOC-001",
                    "full_name": "Dr Aisha Malik",
                    "department_name": "Cardiology",
                    "phone": "020-5555-2101",
                    "access_level": "clinical",
                }
            ],
            access_scopes=("all_staff", "clinical"),
            message="Found 1 matching row(s).",
        )


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


class DeterministicLookupToolTests(unittest.TestCase):
    def test_postgres_lookup_tool_returns_structured_rows(self):
        tools = build_healthcare_agent_tools(
            retrieval=FakeRetrieval(),
            documents=FakeDocuments(),
            user=HealthcareUserContext(user_id="doctor", roles=("doctor",)),
            access=HealthcareAccessControl(),
            safety=HealthcareSafetyGuard(),
            deterministic_lookup=FakeDeterministicLookup(),
        )
        tool = {tool.name: tool for tool in tools}["postgres_deterministic_lookup"]

        payload = json.loads(tool.run("doctor contact for cardiology"))

        self.assertEqual(payload["category"], "doctors")
        self.assertEqual(payload["rows"][0]["full_name"], "Dr Aisha Malik")
        self.assertIn("clinical", payload["access_scopes_applied"])

    def test_catalogued_rota_lookup_filters_today(self):
        documents = FakeRotaDocuments()
        service = DeterministicLookupService(settings(), documents)

        result = service.lookup(
            "which doctor is on call today?",
            HealthcareUserContext(user_id="doctor", roles=("doctor",)),
        )

        self.assertEqual(result.category, "catalogued_csv_doctors")
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.rows[0]["date"], documents.today)
        self.assertEqual(result.rows[0]["staff_name"], "Yusuf Ahmed")

    def test_catalogued_rota_lookup_filters_nurses_by_requested_date(self):
        documents = FakeRotaDocuments()
        service = DeterministicLookupService(settings(), documents)

        result = service.lookup(
            "which nurse is available tomorrow?",
            HealthcareUserContext(user_id="nurse", roles=("nurse",)),
        )

        self.assertEqual(result.category, "catalogued_csv_staff_rota")
        self.assertEqual({row["role"] for row in result.rows}, {"Senior Nurse", "Staff Nurse"})
        self.assertTrue(all(row["date"] == documents.tomorrow for row in result.rows))

    def test_catalogued_rota_lookup_returns_empty_staff_rota_for_no_nurse_today(self):
        documents = FakeRotaDocuments()
        service = DeterministicLookupService(settings(), documents)

        result = service.lookup(
            "which nurse is available today?",
            HealthcareUserContext(user_id="nurse", roles=("nurse",)),
        )

        self.assertEqual(result.category, "catalogued_csv_staff_rota")
        self.assertEqual(result.rows, [])
        self.assertIn("No matching deterministic CSV rows", result.message)

    def test_catalogued_rota_answer_is_user_friendly(self):
        documents = FakeRotaDocuments()
        payload = {
            "category": "catalogued_csv_doctors",
            "message": "Found 1 deterministic CSV row(s).",
            "rows": [
                {
                    "date": documents.today,
                    "department": "Paediatrics",
                    "role": "Registrar",
                    "staff_name": "Yusuf Ahmed",
                    "shift_start": "08:00",
                    "shift_end": "20:00",
                    "on_call": "Yes",
                    "contact": "paeds@example.nhs",
                    "access_level": "clinical",
                    "score": 1,
                }
            ],
        }
        answer = object.__new__(KnowledgeAgent)._offline_answer(
            query="which doctor is on call today?",
            tool_context="postgres_deterministic_lookup results:\n" + json.dumps(payload),
        )

        self.assertIn(f"On-call staff for {documents.today}:", answer)
        self.assertIn("Yusuf Ahmed (Registrar, Paediatrics)", answer)
        self.assertIn("Shift: 08:00-20:00", answer)
        self.assertNotIn("access_level", answer)
        self.assertNotIn("score", answer)

    def test_catalogued_rota_answer_formats_available_nurses(self):
        documents = FakeRotaDocuments()
        payload = {
            "category": "catalogued_csv_staff_rota",
            "message": "Found 2 deterministic CSV row(s).",
            "rows": [
                {
                    "date": documents.tomorrow,
                    "department": "Respiratory",
                    "role": "Senior Nurse",
                    "staff_name": "Priya Shah",
                    "shift_start": "09:00",
                    "shift_end": "17:00",
                    "on_call": "No",
                    "contact": "respiratory@example.nhs",
                    "access_level": "clinical",
                    "score": 1,
                },
                {
                    "date": documents.tomorrow,
                    "department": "Community Care",
                    "role": "Staff Nurse",
                    "staff_name": "Emily Turner",
                    "shift_start": "08:00",
                    "shift_end": "20:00",
                    "on_call": "No",
                    "contact": "community@example.nhs",
                    "access_level": "clinical",
                    "score": 1,
                },
            ],
        }
        answer = object.__new__(KnowledgeAgent)._offline_answer(
            query="which nurse is available tomorrow?",
            tool_context="postgres_deterministic_lookup results:\n" + json.dumps(payload),
        )

        self.assertIn(f"Available nursing staff for {documents.tomorrow}:", answer)
        self.assertIn("Priya Shah (Senior Nurse, Respiratory)", answer)
        self.assertIn("Emily Turner (Staff Nurse, Community Care)", answer)
        self.assertNotIn("access_level", answer)
        self.assertNotIn("score", answer)

    def test_catalogued_rota_answer_explains_no_nurse_today(self):
        documents = FakeRotaDocuments()
        payload = {
            "category": "catalogued_csv_staff_rota",
            "message": "No matching deterministic CSV rows found.",
            "rows": [],
        }
        answer = object.__new__(KnowledgeAgent)._offline_answer(
            query="which nurse is available today?",
            tool_context="postgres_deterministic_lookup results:\n" + json.dumps(payload),
        )

        self.assertIn(f"could not find any nurse entries for {documents.today}", answer)


if __name__ == "__main__":
    unittest.main()
