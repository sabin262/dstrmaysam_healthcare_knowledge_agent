import unittest

from backend.app.models import LoginResponse, Source


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


if __name__ == "__main__":
    unittest.main()
