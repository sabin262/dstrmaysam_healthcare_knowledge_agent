from __future__ import annotations

import json
import csv
import io
from typing import Any

from .healthcare import (
    HealthcareAccessControl,
    HealthcareSafetyGuard,
    HealthcareUserContext,
    SourceGovernance,
)
from .deterministic_lookup import DeterministicLookupService
from .retrieval import RetrievalService
from .storage import DocumentRecord, DocumentStore
from .tools import AgentTool, format_retrieval_hits


def _terms(query: str) -> list[str]:
    return [term.lower() for term in query.split() if len(term) >= 3]


def _record_matches(record: DocumentRecord, query: str, domains: set[str] | None = None) -> bool:
    terms = _terms(query)
    metadata = record.metadata
    if domains and str(metadata.get("domain", "")).lower() not in domains:
        return False
    haystack = " ".join(
        [
            record.title,
            record.key,
            record.content_type,
            json.dumps(metadata, sort_keys=True),
        ]
    ).lower()
    return not terms or any(term in haystack for term in terms)


def _document_payload(record: DocumentRecord) -> dict[str, Any]:
    governance = SourceGovernance.from_metadata(record.metadata)
    return {
        "title": record.title,
        "uri": record.uri,
        "content_type": record.content_type,
        "metadata": record.metadata,
        "governance": governance.as_dict(),
    }


def build_healthcare_agent_tools(
    *,
    retrieval: RetrievalService,
    documents: DocumentStore,
    user: HealthcareUserContext,
    access: HealthcareAccessControl,
    safety: HealthcareSafetyGuard,
    deterministic_lookup: DeterministicLookupService | None = None,
) -> list[AgentTool]:
    def document_search(query: str) -> str:
        """Semantic search over approved healthcare documents."""
        hits = access.filter_hits(user, retrieval.search(query))
        return format_retrieval_hits(hits)

    def policy_search(query: str) -> str:
        """Focused search over clinical/admin policies, SOPs, pathways, and guidelines."""
        hits = retrieval.search(query)
        filtered = []
        for hit in hits:
            metadata = hit.metadata
            domain = str(metadata.get("domain", "")).lower()
            document_type = str(metadata.get("document_type", "")).lower()
            if domain in {"clinical_policy", "admin_policy", "compliance"} or document_type in {
                "policy",
                "sop",
                "pathway",
                "guideline",
            }:
                filtered.append(hit)
        return format_retrieval_hits(access.filter_hits(user, filtered or hits))

    def catalogue_search(query: str) -> str:
        """Find departments, services, owners, systems, and approved tools."""
        records = access.filter_documents(user, documents.list_documents())
        matches = [
            _document_payload(record)
            for record in records
            if _record_matches(record, query, {"catalogue", "directory", "service", "systems"})
        ]
        return json.dumps(matches[:20], indent=2)

    def calendar_rota_lookup(query: str) -> str:
        """Lookup calendar, clinic, training, on-call, and rota data from approved CSV sources."""
        terms = _terms(query)
        matches: list[dict[str, Any]] = []
        records = [
            record
            for record in access.filter_documents(user, documents.list_documents())
            if str(record.metadata.get("domain", "")).lower() in {"calendar", "rota"}
            or any(marker in record.key.lower() for marker in ["calendar", "rota", "on-call", "oncall"])
        ]
        for record in records:
            if not record.key.lower().endswith(".csv"):
                continue
            try:
                reader = csv.DictReader(io.StringIO(documents.read_text(record.key)))
                for raw_row in reader:
                    row = {"source": record.uri, "title": record.title, "row": raw_row}
                    row_text = json.dumps(raw_row, sort_keys=True).lower()
                    if not terms or any(term in row_text for term in terms):
                        matches.append(row)
            except Exception as exc:
                matches.append({"source": record.uri, "error": str(exc)})
        return json.dumps(matches[:10], indent=2)

    def formulary_table_lookup(query: str) -> str:
        """Exact lookup over formulary, restricted medicines, codes, approvals, and structured facts."""
        rows = documents.lookup_table(query)
        filtered = []
        for row in rows:
            row_text = json.dumps(row, sort_keys=True).lower()
            if any(marker in row_text for marker in ["medicine", "formulary", "restricted", "drug", "approval"]):
                filtered.append(row)
        return json.dumps((filtered or rows)[:10], indent=2)

    def safety_guard(query: str) -> str:
        """Detect clinical risk, missing sources, PHI exposure, or escalation needs."""
        assessment = safety.assess(query)
        return json.dumps(assessment.as_dict(), indent=2)

    def postgres_deterministic_lookup(query: str) -> str:
        """Catalogue-guided exact CSV lookup, with Postgres fallback for operational healthcare data."""
        if deterministic_lookup is None:
            return json.dumps(
                {
                    "category": "unavailable",
                    "message": "Postgres deterministic lookup is not configured.",
                    "rows": [],
                },
                indent=2,
            )
        return deterministic_lookup.lookup(query, user).to_json()

    return [
        AgentTool(
            name="document_search",
            description="Semantic search over approved healthcare documents.",
            run=document_search,
        ),
        AgentTool(
            name="policy_search",
            description="Focused retrieval over approved clinical/admin policies, SOPs, pathways, and guidelines.",
            run=policy_search,
        ),
        AgentTool(
            name="catalogue_search",
            description="Find healthcare departments, services, owners, systems, and approved tools.",
            run=catalogue_search,
        ),
        AgentTool(
            name="calendar_rota_lookup",
            description="Lookup clinics, training, rota, and on-call schedules from approved structured sources.",
            run=calendar_rota_lookup,
        ),
        AgentTool(
            name="formulary_table_lookup",
            description="Lookup restricted medicines, formulary rows, approval rules, codes, and structured facts.",
            run=formulary_table_lookup,
        ),
        AgentTool(
            name="postgres_deterministic_lookup",
            description=(
                "Catalogue-guided deterministic lookup for patient details, contact information, doctor information, "
                "department directory data, appointments, wards, and formulary facts. It checks tagged CSV lookup "
                "documents first and falls back to Postgres operational tables."
            ),
            run=postgres_deterministic_lookup,
        ),
        AgentTool(
            name="safety_guard",
            description="Detect clinical risk, missing sources, PHI exposure, or escalation needs.",
            run=safety_guard,
        ),
    ]
