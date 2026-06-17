from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

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

    @property
    def s3_client(self) -> Any:
        if self._s3_client is None:
            import boto3

            self._s3_client = boto3.client("s3", region_name=self.settings.aws_region)
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
    def _load_manifest(self) -> dict[str, Any]:
        if not self.settings.s3_bucket:
            return {"documents": []}
        try:
            response = self.s3_client.get_object(
                Bucket=self.settings.s3_bucket, Key=self.settings.s3_manifest_key
            )
            return json.loads(response["Body"].read().decode("utf-8"))
        except Exception:
            return {"documents": []}
