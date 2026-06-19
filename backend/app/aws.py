from __future__ import annotations

import os
from typing import Any

from .config import AppSettings


LOCAL_AWS_CREDENTIAL_ENVS = {"local", "test"}


def boto3_session(settings: AppSettings) -> Any:
    import boto3

    if settings.app_env.lower() in LOCAL_AWS_CREDENTIAL_ENVS:
        access_key = os.getenv("AWS_ACCESS_KEY_ID") or None
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or None
        session_token = os.getenv("AWS_SESSION_TOKEN") or None
        if access_key and secret_key:
            return boto3.Session(
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=session_token,
                region_name=settings.aws_region,
            )

    return boto3.Session(region_name=settings.aws_region)


def boto3_client(settings: AppSettings, service_name: str) -> Any:
    return boto3_session(settings).client(service_name, region_name=settings.aws_region)


def boto3_resource(settings: AppSettings, service_name: str) -> Any:
    return boto3_session(settings).resource(service_name, region_name=settings.aws_region)
