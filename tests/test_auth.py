import time
import unittest

from backend.app.auth import (
    AuthService,
    AuthenticationError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from backend.app.config import AppSettings
from backend.app.secrets import SecretProvider, SecretProviderError


def auth_test_settings(app_env: str = "local", local_test_admin_enabled: bool = True) -> AppSettings:
    return AppSettings(
        app_env=app_env,
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
        local_test_admin_enabled=local_test_admin_enabled,
    )


class FailingSecretProvider(SecretProvider):
    def get_json(self, secret_name: str):
        raise SecretProviderError(f"Secret {secret_name!r} unavailable")


class AuthTests(unittest.TestCase):
    def test_password_hash_verification(self):
        stored = hash_password("correct horse", salt_hex="00" * 16, iterations=1000)
        self.assertTrue(verify_password("correct horse", stored))
        self.assertFalse(verify_password("wrong", stored))

    def test_access_token_round_trip(self):
        token = create_access_token("alice", "secret", expires_in_seconds=60)
        payload = decode_access_token(token, "secret")
        self.assertEqual(payload["sub"], "alice")

    def test_expired_token_fails(self):
        token = create_access_token("alice", "secret", expires_in_seconds=-1)
        time.sleep(0.01)
        with self.assertRaises(AuthenticationError):
            decode_access_token(token, "secret")

    def test_local_test_admin_can_login_without_secret_manager(self):
        service = AuthService(FailingSecretProvider(auth_test_settings()))

        result = service.login("admin", "admin123")
        claims = service.verify_token_claims(result.access_token)

        self.assertEqual(claims["sub"], "admin")
        self.assertIn("admin", claims["roles"])
        self.assertIn("clinical_governance", claims["departments"])
        self.assertFalse(claims["password_change_required"])

    def test_local_test_admin_is_not_allowed_in_dev(self):
        service = AuthService(FailingSecretProvider(auth_test_settings(app_env="dev")))

        with self.assertRaises(SecretProviderError):
            service.login("admin", "admin123")


if __name__ == "__main__":
    unittest.main()
