from __future__ import annotations

import argparse
import hashlib
import io
import json
from dataclasses import dataclass
from typing import Any

from .config import AppSettings
from .retries import retry_transient
from .secrets import SecretProvider


@dataclass
class ParsedDocument:
    key: str
    title: str
    text: str
    content_type: str
    checksum: str
    metadata: dict[str, Any]


def checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def infer_healthcare_metadata(key: str, checksum: str) -> dict[str, Any]:
    normalized = key.lower()
    domain = "general"
    document_type = "document"
    allowed_roles = ["staff"]

    if any(marker in normalized for marker in ["clinical", "sop", "pathway", "guideline", "sepsis"]):
        domain = "clinical_policy"
        document_type = "policy"
        allowed_roles = ["doctor", "nurse", "clinical_governance", "admin"]
    elif any(marker in normalized for marker in ["hr", "training", "payroll", "onboarding"]):
        domain = "admin_policy"
        document_type = "policy"
        allowed_roles = ["staff", "admin", "manager"]
    elif any(marker in normalized for marker in ["incident", "breach", "safeguarding", "governance"]):
        domain = "compliance"
        document_type = "policy"
        allowed_roles = ["staff", "manager", "clinical_governance"]
    elif any(marker in normalized for marker in ["catalogue", "directory", "service", "owner", "system"]):
        domain = "catalogue"
        document_type = "directory"
    elif any(marker in normalized for marker in ["calendar", "rota", "on-call", "oncall", "clinic"]):
        domain = "rota"
        document_type = "schedule"
    elif any(marker in normalized for marker in ["formulary", "medicine", "drug", "restricted"]):
        domain = "formulary"
        document_type = "table"
        allowed_roles = ["doctor", "nurse", "pharmacy", "clinical_governance", "admin"]

    return {
        "key": key,
        "checksum": checksum,
        "owner": "unknown",
        "version": "unknown",
        "effective_date": "unknown",
        "review_date": "unknown",
        "approval_status": "unknown",
        "sensitivity": "internal",
        "domain": domain,
        "document_type": document_type,
        "allowed_roles": allowed_roles,
    }


def parse_document(key: str, data: bytes) -> ParsedDocument:
    lower = key.lower()
    title = key.rsplit("/", 1)[-1]
    checksum = checksum_bytes(data)
    metadata = infer_healthcare_metadata(key, checksum)

    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            text = f"PDF parsing failed for {key}: {exc}"
        content_type = "application/pdf"
    elif lower.endswith(".docx"):
        try:
            from docx import Document

            document = Document(io.BytesIO(data))
            paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
            table_rows = []
            for table in document.tables:
                for row in table.rows:
                    table_rows.append(" | ".join(cell.text.strip() for cell in row.cells))
            text = "\n".join(paragraphs + table_rows)
        except Exception as exc:
            text = f"DOCX parsing failed for {key}: {exc}"
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif lower.endswith(".csv"):
        text = data.decode("utf-8", errors="replace")
        content_type = "text/csv"
    elif lower.endswith(".md"):
        text = data.decode("utf-8", errors="replace")
        content_type = "text/markdown"
    else:
        text = data.decode("utf-8", errors="replace")
        content_type = "text/plain"

    return ParsedDocument(
        key=key,
        title=title,
        text=text,
        content_type=content_type,
        checksum=checksum,
        metadata=metadata,
    )


def chunk_text(text: str) -> list[str]:
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=180)
        return splitter.split_text(text)
    except Exception:
        chunks: list[str] = []
        step = 1000
        overlap = 150
        index = 0
        while index < len(text):
            chunks.append(text[index : index + step])
            index += step - overlap
        return chunks


class IngestionJob:
    def __init__(self, settings: AppSettings, secret_provider: SecretProvider):
        self.settings = settings
        self.secret_provider = secret_provider
        import boto3

        self.s3 = boto3.client("s3", region_name=settings.aws_region)
        self._embeddings: Any | None = None
        self._opensearch: Any | None = None

    @retry_transient
    def run(self) -> dict[str, Any]:
        documents = self._load_documents()
        indexed_chunks = 0
        manifest_documents = []
        for document in documents:
            chunks = chunk_text(document.text)
            for chunk_index, chunk in enumerate(chunks):
                self._index_chunk(document, chunk, chunk_index)
                indexed_chunks += 1
            manifest_documents.append(
                {
                    "key": document.key,
                    "title": document.title,
                    "content_type": document.content_type,
                    "checksum": document.checksum,
                    "metadata": document.metadata,
                    "chunk_count": len(chunks),
                }
            )

        manifest = {"documents": manifest_documents, "indexed_chunks": indexed_chunks}
        self.s3.put_object(
            Bucket=self.settings.s3_bucket,
            Key=self.settings.s3_manifest_key,
            Body=json.dumps(manifest, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        return manifest

    def _load_documents(self) -> list[ParsedDocument]:
        paginator = self.s3.get_paginator("list_objects_v2")
        documents: list[ParsedDocument] = []
        for page in paginator.paginate(Bucket=self.settings.s3_bucket, Prefix=self.settings.s3_raw_prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                if key.endswith("/"):
                    continue
                if not key.lower().endswith((".pdf", ".docx", ".txt", ".md", ".csv")):
                    continue
                body = self.s3.get_object(Bucket=self.settings.s3_bucket, Key=key)["Body"].read()
                documents.append(parse_document(key, body))
        return documents

    def _index_chunk(self, document: ParsedDocument, chunk: str, chunk_index: int) -> None:
        client = self._get_opensearch()
        embedding = self._embed(chunk)
        doc_id = hashlib.sha256(f"{document.key}:{chunk_index}:{document.checksum}".encode()).hexdigest()
        body = {
            "key": document.key,
            "title": document.title,
            "uri": f"s3://{self.settings.s3_bucket}/{document.key}",
            "text": chunk,
            "content_type": document.content_type,
            "chunk_index": chunk_index,
            "checksum": document.checksum,
            "metadata": document.metadata,
        }
        if embedding is not None:
            body["embedding"] = embedding
        client.index(index=self.settings.opensearch_index, id=doc_id, body=body, refresh=False)

    def _embed(self, text: str) -> list[float] | None:
        try:
            if self._embeddings is None:
                from langchain_openai import AzureOpenAIEmbeddings

                secrets = self.secret_provider.load_azure_openai()
                self._embeddings = AzureOpenAIEmbeddings(
                    azure_endpoint=secrets.endpoint,
                    api_key=secrets.api_key,
                    api_version=secrets.api_version,
                    azure_deployment=secrets.embedding_deployment,
                )
            return list(self._embeddings.embed_query(text))
        except Exception:
            return None

    def _get_opensearch(self) -> Any:
        if self._opensearch is not None:
            return self._opensearch
        import boto3
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from opensearchpy import AWSV4SignerAuth

        if not self.settings.opensearch_endpoint:
            raise RuntimeError("OPENSEARCH_ENDPOINT is required for ingestion")
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, self.settings.aws_region, "aoss")
        host = self.settings.opensearch_endpoint.replace("https://", "").replace("http://", "")
        self._opensearch = OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )
        return self._opensearch


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest S3 documents into OpenSearch")
    parser.parse_args()
    settings = AppSettings.from_env()
    result = IngestionJob(settings, SecretProvider(settings)).run()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
