import unittest
from unittest import mock

from backend.app.auth import AuthService, AuthenticationError, UserManagementError, hash_password
from backend.app.config import AppSettings
from backend.app.secrets import StaticSecretProvider

try:
    from fastapi.testclient import TestClient

    from backend.app import main
except ModuleNotFoundError:
    TestClient = None
    main = None


def app_settings() -> AppSettings:
    return AppSettings(
        app_env="test",
        aws_region="us-east-1",
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


if __name__ == "__main__":
    unittest.main()
