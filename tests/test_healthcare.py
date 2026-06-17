import unittest

from backend.app.healthcare import (
    HealthcareAccessControl,
    HealthcareSafetyGuard,
    HealthcareUserContext,
    PHIRedactor,
)
from backend.app.ingest import infer_healthcare_metadata
from backend.app.storage import DocumentRecord


class HealthcareScaffoldTests(unittest.TestCase):
    def test_phi_redactor_masks_common_identifiers(self):
        result = PHIRedactor().redact("Patient MRN: ABC12345 email person@example.com")
        self.assertTrue(result.has_phi)
        self.assertIn("[REDACTED_MEDICAL_RECORD_NUMBER]", result.redacted_text)
        self.assertIn("[REDACTED_EMAIL]", result.redacted_text)

    def test_safety_guard_flags_high_risk_clinical_question_without_sources(self):
        assessment = HealthcareSafetyGuard().assess("Patient has chest pain, what should I do?", sources=[])
        self.assertEqual(assessment.risk_level, "high")
        self.assertTrue(assessment.escalation_required)
        self.assertFalse(assessment.allow_answer)

    def test_role_filtering_respects_allowed_roles(self):
        documents = [
            DocumentRecord(
                title="Clinical SOP",
                uri="s3://bucket/raw/clinical-sop.docx",
                key="raw/clinical-sop.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                metadata={"allowed_roles": ["doctor"]},
            ),
            DocumentRecord(
                title="Staff Policy",
                uri="s3://bucket/raw/staff-policy.md",
                key="raw/staff-policy.md",
                content_type="text/markdown",
                metadata={"allowed_roles": ["staff"]},
            ),
        ]
        user = HealthcareUserContext(user_id="nurse", roles=("staff",))
        visible = HealthcareAccessControl().filter_documents(user, documents)
        self.assertEqual([document.title for document in visible], ["Staff Policy"])

    def test_ingestion_metadata_infers_healthcare_domain(self):
        metadata = infer_healthcare_metadata("raw/clinical/sepsis-sop.docx", "abc")
        self.assertEqual(metadata["domain"], "clinical_policy")
        self.assertEqual(metadata["document_type"], "policy")
        self.assertIn("doctor", metadata["allowed_roles"])


if __name__ == "__main__":
    unittest.main()

