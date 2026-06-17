from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .retrieval import RetrievalHit, RetrievalService
from .storage import DocumentStore


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
        lines.append(
            f"[{index}] {hit.title} ({hit.uri}, score={hit.score})\n{hit.text[:1200]}"
        )
    return "\n\n".join(lines)


def build_agent_tools(retrieval: RetrievalService, documents: DocumentStore) -> list[AgentTool]:
    def rag_search(query: str) -> str:
        """Search indexed company documents using retrieval augmented generation context."""
        return format_retrieval_hits(retrieval.search(query))

    def document_catalog(query: str) -> str:
        """List or filter indexed company documents from the S3 manifest."""
        terms = [term.lower() for term in query.split() if len(term) >= 3]
        records = []
        for record in documents.list_documents():
            haystack = " ".join(
                [record.title, record.key, record.content_type, json.dumps(record.metadata)]
            ).lower()
            if not terms or any(term in haystack for term in terms):
                records.append(
                    {
                        "title": record.title,
                        "uri": record.uri,
                        "content_type": record.content_type,
                        "metadata": record.metadata,
                    }
                )
        return json.dumps(records[:20], indent=2)

    def table_lookup(query: str) -> str:
        """Look up exact answers in CSV or table-like files stored in S3."""
        return json.dumps(documents.lookup_table(query), indent=2)

    return [
        AgentTool(
            name="rag_search",
            description="Semantic RAG search over indexed company documents with citations.",
            run=rag_search,
        ),
        AgentTool(
            name="document_catalog",
            description="List and filter available S3 company documents by metadata.",
            run=document_catalog,
        ),
        AgentTool(
            name="table_lookup",
            description="Find exact values from CSV files stored in S3.",
            run=table_lookup,
        ),
    ]

