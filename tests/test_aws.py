import os
import types
import unittest
from unittest import mock

from backend.app.aws import boto3_session
from backend.app.config import AppSettings


def settings(app_env: str) -> AppSettings:
    return AppSettings(
        app_env=app_env,
        aws_region="eu-west-2",
        secrets_stage="dev",
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


class AwsSessionTests(unittest.TestCase):
    def test_local_uses_credentials_from_environment(self):
        calls = []
        fake_boto3 = types.SimpleNamespace(Session=lambda **kwargs: calls.append(kwargs) or object())
        with mock.patch.dict(
            os.environ,
            {
                "AWS_ACCESS_KEY_ID": "local-key",
                "AWS_SECRET_ACCESS_KEY": "local-secret",
                "AWS_SESSION_TOKEN": "local-token",
            },
            clear=False,
        ), mock.patch.dict("sys.modules", {"boto3": fake_boto3}):
            boto3_session(settings("local"))

        self.assertEqual(
            calls[-1],
            {
                "aws_access_key_id": "local-key",
                "aws_secret_access_key": "local-secret",
                "aws_session_token": "local-token",
                "region_name": "eu-west-2",
            },
        )

    def test_dev_uses_default_chain_for_ecs_task_role(self):
        calls = []
        fake_boto3 = types.SimpleNamespace(Session=lambda **kwargs: calls.append(kwargs) or object())
        with mock.patch.dict(
            os.environ,
            {
                "AWS_ACCESS_KEY_ID": "ignored-key",
                "AWS_SECRET_ACCESS_KEY": "ignored-secret",
                "AWS_SESSION_TOKEN": "ignored-token",
            },
            clear=False,
        ), mock.patch.dict("sys.modules", {"boto3": fake_boto3}):
            boto3_session(settings("dev"))

        self.assertEqual(calls[-1], {"region_name": "eu-west-2"})


if __name__ == "__main__":
    unittest.main()
