from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .retrieval import RetrievalHit, RetrievalService
from .storage import DocumentRecord, DocumentStore


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    run: Callable[[str], str]


def format_retrieval_hits(hits: list[RetrievalHit]) -> str:
    if not hits:
        return "No relevant document chunks found."
    lines: list[str] = []
    for index, hit in enumerate(hits, start=1):
        facts = hit.metadata.get("facts") if isinstance(hit.metadata, dict) else None
        details = {
            key: value
            for key, value in {
                "chunk_index": hit.metadata.get("_chunk_index"),
                "domain": hit.metadata.get("domain"),
                "document_type": hit.metadata.get("document_type"),
                "facts": facts,
            }.items()
            if value not in (None, "", {})
        }
        detail_text = f"\nMetadata: {json.dumps(details, sort_keys=True)}" if details else ""
        lines.append(
            f"[{index}] {hit.title} ({hit.uri}, score={hit.score}){detail_text}\n{hit.text[:1200]}"
        )
    return "\n\n".join(lines)


def catalog_query_terms(query: str) -> list[str]:
    return [term.lower() for term in query.split() if len(term) >= 3]


def document_matches_catalog_query(record: DocumentRecord, query: str) -> bool:
    terms = catalog_query_terms(query)
    haystack = " ".join(
        [record.title, record.key, record.content_type, json.dumps(record.metadata)]
    ).lower()
    return not terms or any(term in haystack for term in terms)


def document_catalog_payload(record: DocumentRecord) -> dict[str, object]:
    return {
        "title": record.title,
        "uri": record.uri,
        "content_type": record.content_type,
        "metadata": record.metadata,
    }


def build_agent_tools(retrieval: RetrievalService, documents: DocumentStore) -> list[AgentTool]:
    def rag_search(query: str) -> str:
        """Search indexed knowledge documents using retrieval augmented generation context."""
        return format_retrieval_hits(retrieval.search(query))

    def document_catalog(query: str) -> str:
        """List or filter indexed knowledge documents from the S3 manifest."""
        records = []
        for record in documents.list_documents():
            if document_matches_catalog_query(record, query):
                records.append(document_catalog_payload(record))
        return json.dumps(records[:20], indent=2)

    def table_lookup(query: str) -> str:
        """Look up exact answers in CSV or table-like files stored in S3."""
        return json.dumps(documents.lookup_table(query), indent=2)

    return [
        AgentTool(
            name="rag_search",
            description="Semantic RAG search over indexed knowledge documents with citations.",
            run=rag_search,
        ),
        AgentTool(
            name="document_catalog",
            description="List and filter available S3 knowledge documents by metadata.",
            run=document_catalog,
        ),
        AgentTool(
            name="table_lookup",
            description="Find exact values from CSV files stored in S3.",
            run=table_lookup,
        ),
    ]
