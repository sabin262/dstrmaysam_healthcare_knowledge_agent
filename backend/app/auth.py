from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import re
import secrets
import sys
import time
from dataclasses import dataclass
from typing import Any

from .secrets import AppSecrets, SecretProvider


PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
DEFAULT_PASSWORD_ITERATIONS = 200_000


class AuthenticationError(RuntimeError):
    """Raised when credentials or tokens are invalid."""


LOCAL_TEST_ADMIN_ENVS = {"local", "test"}
KNOWN_USER_ROLES = (
    "admin",
    "staff",
    "doctor",
    "nurse",
    "pharmacy",
    "clinical_governance",
    "manager",
)
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")


class AuthorizationError(RuntimeError):
    """Raised when the authenticated user lacks a required permission."""


class PasswordChangeRequiredError(AuthorizationError):
    """Raised when a user must change their password before continuing."""


class UserManagementError(RuntimeError):
    """Raised when an admin user-management operation is invalid."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def hash_password(password: str, *, salt_hex: str | None = None, iterations: int = DEFAULT_PASSWORD_ITERATIONS) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PASSWORD_HASH_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_hex, digest_hex = stored_hash.split("$", 3)
        if algorithm != PASSWORD_HASH_ALGORITHM:
            return False
        expected = hash_password(password, salt_hex=salt_hex, iterations=int(iterations_raw))
        return hmac.compare_digest(expected, stored_hash)
    except Exception:
        return False


def create_access_token(
    username: str,
    session_secret: str,
    expires_in_seconds: int = 3600,
    claims: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": username, "iat": now, "exp": now + expires_in_seconds}
    if claims:
        payload.update(claims)
    signing_input = ".".join(
        [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(session_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
    return f"{signing_input}.{_b64url_encode(signature.digest())}"


def decode_access_token(token: str, session_secret: str) -> dict[str, Any]:
    try:
        header_raw, payload_raw, signature_raw = token.split(".", 2)
        signing_input = f"{header_raw}.{payload_raw}"
        expected = hmac.new(session_secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256)
        if not hmac.compare_digest(_b64url_encode(expected.digest()), signature_raw):
            raise AuthenticationError("Invalid token signature")
        payload = json.loads(_b64url_decode(payload_raw))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise AuthenticationError("Token expired")
        if not payload.get("sub"):
            raise AuthenticationError("Token subject missing")
        return payload
    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError("Invalid access token") from exc


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    expires_in: int
    username: str
    roles: list[str]
    departments: list[str]
    password_change_required: bool


@dataclass(frozen=True)
class ManagedUser:
    username: str
    roles: list[str]
    departments: list[str]
    password_change_required: bool


@dataclass
class AuthService:
    secret_provider: SecretProvider
    token_ttl_seconds: int = 3600
    _app_secrets: AppSecrets | None = None

    def _secrets(self) -> AppSecrets:
        if self._app_secrets is None:
            try:
                app_secrets = self.secret_provider.load_app()
            except Exception:
                if not self._local_test_admin_allowed():
                    raise
                app_secrets = AppSecrets(
                    session_secret="local-test-session-secret-local-only",
                    auth_users={},
                    user_profiles={},
                )
            if self._local_test_admin_allowed():
                app_secrets = self._with_local_test_admin(app_secrets)
            self._app_secrets = app_secrets
        return self._app_secrets

    def _local_test_admin_allowed(self) -> bool:
        settings = self.secret_provider.settings
        return (
            settings.local_test_admin_enabled
            and settings.app_env.lower() in LOCAL_TEST_ADMIN_ENVS
        )

    def _with_local_test_admin(self, app_secrets: AppSecrets) -> AppSecrets:
        settings = self.secret_provider.settings
        username = settings.local_test_admin_username.strip() or "admin"
        password = settings.local_test_admin_password or "admin123"
        auth_users = dict(app_secrets.auth_users)
        user_profiles = dict(app_secrets.user_profiles)
        auth_users[username] = hash_password(
            password,
            salt_hex="11" * 16,
            iterations=1000,
        )
        user_profiles[username] = {
            "roles": [
                "admin",
                "doctor",
                "nurse",
                "pharmacy",
                "clinical_governance",
                "manager",
                "staff",
            ],
            "departments": ["clinical_governance", "operations", "it", "hr", "finance"],
        }
        return AppSecrets(
            session_secret=app_secrets.session_secret,
            auth_users=auth_users,
            user_profiles=user_profiles,
        )

    def _app_secret_payload(self) -> dict[str, Any]:
        return dict(self.secret_provider.get_json(self.secret_provider.settings.app_secret_name))

    def _write_app_secret_payload(self, payload: dict[str, Any]) -> None:
        self.secret_provider.put_json(self.secret_provider.settings.app_secret_name, payload)
        self._app_secrets = None

    def _profile_for(self, username: str, app_secrets: AppSecrets | None = None) -> dict[str, Any]:
        secrets_value = app_secrets or self._secrets()
        return dict(secrets_value.user_profiles.get(username, {}))

    def _roles_from_profile(self, profile: dict[str, Any]) -> list[str]:
        roles = profile.get("roles", ["staff"])
        if isinstance(roles, str):
            roles = [roles]
        result = []
        for role in roles or ["staff"]:
            value = str(role).strip().lower()
            if value and value not in result:
                result.append(value)
        return result or ["staff"]

    def _departments_from_profile(self, profile: dict[str, Any]) -> list[str]:
        departments = profile.get("departments", [])
        if isinstance(departments, str):
            departments = [departments]
        result = []
        for department in departments or []:
            value = str(department).strip().lower()
            if value and value not in result:
                result.append(value)
        return result

    def _password_change_required_from_profile(self, profile: dict[str, Any]) -> bool:
        return bool(profile.get("password_change_required", False))

    def _claims_for_username(self, username: str) -> dict[str, Any]:
        app_secrets = self._secrets()
        if username not in app_secrets.auth_users:
            raise AuthenticationError("User no longer exists")
        profile = self._profile_for(username, app_secrets)
        return {
            "sub": username,
            "roles": self._roles_from_profile(profile),
            "departments": self._departments_from_profile(profile),
            "password_change_required": self._password_change_required_from_profile(profile),
        }

    def _login_result_for(self, username: str) -> LoginResult:
        claims = self._claims_for_username(username)
        token_claims = {
            "roles": claims["roles"],
            "departments": claims["departments"],
            "password_change_required": claims["password_change_required"],
        }
        token = create_access_token(
            username,
            self._secrets().session_secret,
            self.token_ttl_seconds,
            token_claims,
        )
        return LoginResult(
            access_token=token,
            expires_in=self.token_ttl_seconds,
            username=username,
            roles=list(claims["roles"]),
            departments=list(claims["departments"]),
            password_change_required=bool(claims["password_change_required"]),
        )

    def login(self, username: str, password: str) -> LoginResult:
        username = username.strip()
        app_secrets = self._secrets()
        stored_hash = app_secrets.auth_users.get(username)
        if not stored_hash or not verify_password(password, stored_hash):
            raise AuthenticationError("Invalid username or password")
        return self._login_result_for(username)

    def verify_user_password(self, username: str, password: str) -> None:
        username = username.strip()
        stored_hash = self._secrets().auth_users.get(username)
        if not stored_hash or not verify_password(password, stored_hash):
            raise AuthenticationError("Invalid username or password")

    def verify_token(self, token: str) -> str:
        return str(self.verify_token_claims(token)["sub"])

    def verify_token_claims(self, token: str) -> dict[str, Any]:
        payload = decode_access_token(token, self._secrets().session_secret)
        return self._claims_for_username(str(payload["sub"]))

    def ensure_password_change_not_required(self, claims: dict[str, Any]) -> None:
        if claims.get("password_change_required"):
            raise PasswordChangeRequiredError("Password change required")

    def ensure_admin(self, claims: dict[str, Any]) -> None:
        self.ensure_password_change_not_required(claims)
        roles = {str(role).lower() for role in claims.get("roles", [])}
        if "admin" not in roles:
            raise AuthorizationError("Admin role required")

    def change_password(self, username: str, current_password: str, new_password: str) -> LoginResult:
        username = username.strip()
        self._validate_password(new_password)
        payload = self._app_secret_payload()
        auth_users = self._auth_users_payload(payload)
        user_profiles = self._user_profiles_payload(payload)
        stored_hash = auth_users.get(username)
        if not stored_hash or not verify_password(current_password, str(stored_hash)):
            raise AuthenticationError("Invalid username or password")
        auth_users[username] = hash_password(new_password)
        profile = dict(user_profiles.get(username, {}))
        profile["password_change_required"] = False
        user_profiles[username] = profile
        payload["auth_users"] = auth_users
        payload["user_profiles"] = user_profiles
        self._write_app_secret_payload(payload)
        return self._login_result_for(username)

    def list_users(self) -> list[ManagedUser]:
        app_secrets = self._secrets()
        users = []
        for username in sorted(app_secrets.auth_users):
            profile = self._profile_for(username, app_secrets)
            users.append(
                ManagedUser(
                    username=username,
                    roles=self._roles_from_profile(profile),
                    departments=self._departments_from_profile(profile),
                    password_change_required=self._password_change_required_from_profile(profile),
                )
            )
        return users

    def create_user(
        self,
        username: str,
        temporary_password: str,
        roles: list[str],
        departments: list[str],
    ) -> ManagedUser:
        username = self._validate_username(username)
        self._validate_password(temporary_password)
        normalized_roles = self._validate_roles(roles)
        normalized_departments = self._normalize_departments(departments)
        payload = self._app_secret_payload()
        auth_users = self._auth_users_payload(payload)
        user_profiles = self._user_profiles_payload(payload)
        if username in auth_users:
            raise UserManagementError("User already exists", status_code=409)
        auth_users[username] = hash_password(temporary_password)
        user_profiles[username] = {
            "roles": normalized_roles,
            "departments": normalized_departments,
            "password_change_required": True,
        }
        payload["auth_users"] = auth_users
        payload["user_profiles"] = user_profiles
        self._write_app_secret_payload(payload)
        return ManagedUser(username, normalized_roles, normalized_departments, True)

    def update_user(
        self,
        username: str,
        roles: list[str] | None = None,
        departments: list[str] | None = None,
    ) -> ManagedUser:
        username = username.strip()
        payload = self._app_secret_payload()
        auth_users = self._auth_users_payload(payload)
        user_profiles = self._user_profiles_payload(payload)
        if username not in auth_users:
            raise UserManagementError("User not found", status_code=404)
        profile = dict(user_profiles.get(username, {}))
        if roles is not None:
            profile["roles"] = self._validate_roles(roles)
        if departments is not None:
            profile["departments"] = self._normalize_departments(departments)
        user_profiles[username] = profile
        self._ensure_admin_remains(auth_users, user_profiles)
        payload["auth_users"] = auth_users
        payload["user_profiles"] = user_profiles
        self._write_app_secret_payload(payload)
        return ManagedUser(
            username=username,
            roles=self._roles_from_profile(profile),
            departments=self._departments_from_profile(profile),
            password_change_required=self._password_change_required_from_profile(profile),
        )

    def reset_password(self, username: str, temporary_password: str) -> ManagedUser:
        username = username.strip()
        self._validate_password(temporary_password)
        payload = self._app_secret_payload()
        auth_users = self._auth_users_payload(payload)
        user_profiles = self._user_profiles_payload(payload)
        if username not in auth_users:
            raise UserManagementError("User not found", status_code=404)
        auth_users[username] = hash_password(temporary_password)
        profile = dict(user_profiles.get(username, {}))
        profile["password_change_required"] = True
        user_profiles[username] = profile
        payload["auth_users"] = auth_users
        payload["user_profiles"] = user_profiles
        self._write_app_secret_payload(payload)
        return ManagedUser(
            username=username,
            roles=self._roles_from_profile(profile),
            departments=self._departments_from_profile(profile),
            password_change_required=True,
        )

    def _auth_users_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        auth_users = payload.get("auth_users", {})
        if not isinstance(auth_users, dict):
            raise UserManagementError("App secret auth_users must be a JSON object")
        return dict(auth_users)

    def _user_profiles_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        profiles = payload.get("user_profiles", {})
        if not isinstance(profiles, dict):
            raise UserManagementError("App secret user_profiles must be a JSON object")
        return {str(username): dict(profile) for username, profile in profiles.items() if isinstance(profile, dict)}

    def _validate_username(self, username: str) -> str:
        normalized = username.strip()
        if not USERNAME_PATTERN.fullmatch(normalized):
            raise UserManagementError(
                "Username must be 3-64 characters using letters, numbers, _, ., @, or -"
            )
        return normalized

    def _validate_password(self, password: str) -> None:
        if len(password) < 8:
            raise UserManagementError("Password must be at least 8 characters")

    def _validate_roles(self, roles: list[str]) -> list[str]:
        normalized = []
        for role in roles:
            value = str(role).strip().lower()
            if value and value not in normalized:
                normalized.append(value)
        if not normalized:
            raise UserManagementError("At least one role is required")
        invalid = [role for role in normalized if role not in KNOWN_USER_ROLES]
        if invalid:
            raise UserManagementError(f"Unknown role: {', '.join(invalid)}")
        return normalized

    def _normalize_departments(self, departments: list[str]) -> list[str]:
        normalized = []
        for department in departments:
            value = str(department).strip().lower()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    def _ensure_admin_remains(
        self,
        auth_users: dict[str, Any],
        user_profiles: dict[str, dict[str, Any]],
    ) -> None:
        for username in auth_users:
            profile = dict(user_profiles.get(username, {}))
            if "admin" in self._roles_from_profile(profile):
                return
        raise UserManagementError("At least one admin user is required")


def _hash_password_cli() -> int:
    password = getpass.getpass("Password to hash: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "hash-password":
        return _hash_password_cli()
    print("Usage: python -m backend.app.auth hash-password")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
