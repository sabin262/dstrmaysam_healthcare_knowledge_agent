from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from typing import Any

from .aws import boto3_client
from .config import AppSettings
from .retries import retry_transient


@dataclass
class DocumentRecord:
    title: str
    uri: str
    key: str
    content_type: str
    metadata: dict[str, Any]


class DocumentStore:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._s3_client: Any | None = None
        self._manifest_cache: dict[str, Any] | None = None
        self._manifest_cache_expires_at = 0.0

    @property
    def s3_client(self) -> Any:
        if self._s3_client is None:
            self._s3_client = boto3_client(self.settings, "s3")
        return self._s3_client

    def list_documents(self) -> list[DocumentRecord]:
        manifest = self._load_manifest()
        records = manifest.get("documents", []) if isinstance(manifest, dict) else []
        output: list[DocumentRecord] = []
        for record in records:
            key = str(record.get("key", ""))
            title = str(record.get("title") or key.rsplit("/", 1)[-1] or "Untitled")
            output.append(
                DocumentRecord(
                    title=title,
                    uri=f"s3://{self.settings.s3_bucket}/{key}",
                    key=key,
                    content_type=str(record.get("content_type", "")),
                    metadata=dict(record.get("metadata", {})),
                )
            )
        return output

    def lookup_table(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query_terms = [term.lower() for term in query.split() if len(term) >= 3]
        rows: list[dict[str, Any]] = []
        for document in self.list_documents():
            if not document.key.lower().endswith(".csv"):
                continue
            text = self.read_text(document.key)
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                row_text = " ".join(str(value) for value in row.values()).lower()
                if any(term in row_text for term in query_terms):
                    rows.append(
                        {
                            "source": document.uri,
                            "title": document.title,
                            "row": row,
                        }
                    )
                    if len(rows) >= limit:
                        return rows
        return rows

    @retry_transient
    def read_text(self, key: str) -> str:
        response = self.s3_client.get_object(Bucket=self.settings.s3_bucket, Key=key)
        data = response["Body"].read()
        return data.decode("utf-8", errors="replace")

    @retry_transient
    def upload_document(self, key: str, data: bytes, content_type: str) -> None:
        self.s3_client.put_object(
            Bucket=self.settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        self.invalidate_manifest_cache()

    def invalidate_manifest_cache(self) -> None:
        self._manifest_cache = None
        self._manifest_cache_expires_at = 0.0

    @retry_transient
    def _load_manifest(self) -> dict[str, Any]:
        if not self.settings.s3_bucket:
            return {"documents": []}
        ttl_seconds = max(0, self.settings.document_manifest_cache_ttl_seconds)
        now = time.monotonic()
        if (
            ttl_seconds
            and self._manifest_cache is not None
            and now < self._manifest_cache_expires_at
        ):
            return self._manifest_cache
        try:
            response = self.s3_client.get_object(
                Bucket=self.settings.s3_bucket, Key=self.settings.s3_manifest_key
            )
            manifest = json.loads(response["Body"].read().decode("utf-8"))
        except Exception:
            manifest = {"documents": []}
        if ttl_seconds:
            self._manifest_cache = manifest
            self._manifest_cache_expires_at = now + ttl_seconds
        return manifest
