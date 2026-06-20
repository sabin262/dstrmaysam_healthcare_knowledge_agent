from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
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
    chunk_count: int = 0
    ingestion_status: str = ""


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
                    chunk_count=int(record.get("chunk_count") or 0),
                    ingestion_status=str(record.get("ingestion_status") or ""),
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


class LocalDocumentStore(DocumentStore):
    def __init__(self, settings: AppSettings):
        super().__init__(settings)
        self.local_data_dir = Path(settings.local_data_dir)

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
                    uri=f"local://{key}",
                    key=key,
                    content_type=str(record.get("content_type", "")),
                    metadata=dict(record.get("metadata", {})),
                    chunk_count=int(record.get("chunk_count") or 0),
                    ingestion_status=str(record.get("ingestion_status") or ""),
                )
            )
        return output

    def read_text(self, key: str) -> str:
        return self._path_for_key(key).read_text(encoding="utf-8", errors="replace")

    def upload_document(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.invalidate_manifest_cache()

    def _load_manifest(self) -> dict[str, Any]:
        ttl_seconds = max(0, self.settings.document_manifest_cache_ttl_seconds)
        now = time.monotonic()
        if (
            ttl_seconds
            and self._manifest_cache is not None
            and now < self._manifest_cache_expires_at
        ):
            return self._manifest_cache
        path = self._path_for_key(self.settings.s3_manifest_key)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {"documents": []}
        if ttl_seconds:
            self._manifest_cache = manifest
            self._manifest_cache_expires_at = now + ttl_seconds
        return manifest

    def _path_for_key(self, key: str) -> Path:
        safe_key = key.replace("\\", "/").lstrip("/")
        path = (self.local_data_dir / safe_key).resolve()
        root = self.local_data_dir.resolve()
        if root != path and root not in path.parents:
            raise ValueError("Local document key escapes LOCAL_DATA_DIR")
        return path
