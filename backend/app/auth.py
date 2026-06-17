from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
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
                    session_secret="local-test-session-secret-not-for-production",
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

    def login(self, username: str, password: str) -> str:
        app_secrets = self._secrets()
        stored_hash = app_secrets.auth_users.get(username)
        if not stored_hash or not verify_password(password, stored_hash):
            raise AuthenticationError("Invalid username or password")
        profile = app_secrets.user_profiles.get(username, {})
        claims = {
            "roles": profile.get("roles", ["staff"]),
            "departments": profile.get("departments", []),
        }
        return create_access_token(username, app_secrets.session_secret, self.token_ttl_seconds, claims)

    def verify_token(self, token: str) -> str:
        payload = decode_access_token(token, self._secrets().session_secret)
        return str(payload["sub"])

    def verify_token_claims(self, token: str) -> dict[str, Any]:
        return decode_access_token(token, self._secrets().session_secret)


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
