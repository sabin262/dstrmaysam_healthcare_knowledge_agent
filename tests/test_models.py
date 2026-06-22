import unittest

from backend.app.models import ChatResponse, LoginResponse, Source


class ModelTests(unittest.TestCase):
    def test_source_snippet_is_optional(self):
        source = Source(title="Policy", uri="s3://bucket/policy.md")

        self.assertIsNone(source.snippet)

    def test_source_accepts_snippet(self):
        source = Source(
            title="Policy",
            uri="s3://bucket/policy.md",
            snippet="Relevant policy text",
        )

        self.assertEqual(source.snippet, "Relevant policy text")

    def test_login_response_profile_fields_default_for_compatibility(self):
        response = LoginResponse(access_token="token", expires_in=3600)

        self.assertEqual(response.roles, [])
        self.assertEqual(response.departments, [])
        self.assertFalse(response.password_change_required)

    def test_chat_response_performance_defaults_for_compatibility(self):
        response = ChatResponse(
            session_id="session",
            answer="Answer",
            sources=[],
            tools_used=[],
            input_tokens=1,
            output_tokens=1,
            latency_ms=10,
            trace_id="trace",
        )

        self.assertEqual(response.performance, {})
        self.assertEqual(response.latency_breakdown, {})


if __name__ == "__main__":
    unittest.main()
