import json
import unittest

from backend.app.deterministic_lookup import DeterministicLookupService, LookupResult, _best_search_term, _terms
from backend.app.healthcare import HealthcareAccessControl, HealthcareSafetyGuard, HealthcareUserContext
from backend.app.healthcare_tools import build_healthcare_agent_tools


class FakeRetrieval:
    def search(self, query, *args, **kwargs):
        return []


class FakeDocuments:
    def list_documents(self):
        return []

    def lookup_table(self, query):
        return []


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

    def test_patient_ward_question_classifies_as_patient_lookup(self):
        service = DeterministicLookupService(settings=None)

        self.assertEqual(service._classify("Leo Bennett is in which ward?"), "patients")
        self.assertEqual(service._classify("where in IPD is Leo Bennett"), "patients")
        self.assertEqual(_best_search_term(_terms("Leo Bennett is in which ward?")), "bennett")

    def test_ward_directory_question_still_classifies_as_wards(self):
        service = DeterministicLookupService(settings=None)

        self.assertEqual(service._classify("which ward is W07?"), "wards")


if __name__ == "__main__":
    unittest.main()
