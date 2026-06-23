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
from .deterministic_lookup import DeterministicLookupService, _is_staff_rota_query
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


def _deterministic_csv_assets(
    *,
    documents: DocumentStore,
    user: HealthcareUserContext,
    access: HealthcareAccessControl,
) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    try:
        records = access.filter_documents(user, documents.list_documents())
    except Exception:
        return assets
    for record in records:
        metadata = record.metadata
        if str(metadata.get("asset_source") or "") != "postgres_uploaded_lookup":
            continue
        columns = [str(column) for column in metadata.get("columns") or [] if str(column).strip()]
        assets.append(
            {
                "filename": record.title or record.key.rsplit("/", 1)[-1],
                "title": record.title,
                "columns": columns,
                "row_count": int(metadata.get("row_count") or 0),
            }
        )
    return assets[:20]


def _deterministic_tool_description(csv_assets: list[dict[str, Any]]) -> str:
    base = (
        "Exact Postgres lookup for patient details, contact information, doctor information, "
        "department directory data, appointments, wards, formulary facts, staff rota availability, "
        "and uploaded CSV lookup rows including all csv. files "
        "Use this when the user asks for exact structured values, multiple known values to look up, "
        "or table-like data that can answer the question without document interpretation."
    )
    if not csv_assets:
        return base
    asset_lines = []
    for asset in csv_assets[:8]:
        columns = ", ".join(asset.get("columns") or [])
        asset_lines.append(
            f"{asset.get('filename')} ({asset.get('row_count', 0)} rows; columns: {columns or 'unknown'})"
        )
    return base + " Available uploaded CSV lookup assets: " + " | ".join(asset_lines)


def build_healthcare_agent_tools(
    *,
    retrieval: RetrievalService,
    documents: DocumentStore,
    user: HealthcareUserContext,
    access: HealthcareAccessControl,
    safety: HealthcareSafetyGuard,
    deterministic_lookup: DeterministicLookupService | None = None,
) -> list[AgentTool]:
    deterministic_csv_assets = _deterministic_csv_assets(
        documents=documents,
        user=user,
        access=access,
    )

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
        if deterministic_lookup is not None and _is_staff_rota_query(query):
            return deterministic_lookup.lookup(query, user, csv_assets=deterministic_csv_assets).to_json()
        terms = _terms(query)
        matches: list[dict[str, Any]] = []
        records = [
            record
            for record in access.filter_documents(user, documents.list_documents())
            if str(record.metadata.get("domain", "")).lower() in {"calendar", "rota"}
            or any(marker in record.key.lower() for marker in ["calendar", "rota", "on-call", "oncall"])
        ]
        for record in records:
            if (
                record.key.startswith("postgres://")
                or str(record.metadata.get("asset_source")) == "postgres_uploaded_lookup"
            ):
                continue
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
        """Exact Postgres lookup for patients, doctors, departments, contacts, appointments, wards, and formulary data."""
        if deterministic_lookup is None:
            return json.dumps(
                {
                    "category": "unavailable",
                    "message": "Postgres deterministic lookup is not configured.",
                    "rows": [],
                },
                indent=2,
            )
        return deterministic_lookup.lookup(query, user, csv_assets=deterministic_csv_assets).to_json()

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
            description=(
                "Lookup clinics, training, and general rota schedules from approved structured sources. "
                "For staff availability, doctors, nurses, or staff_rota.csv questions, prefer postgres_deterministic_lookup."
            ),
            run=calendar_rota_lookup,
        ),
        AgentTool(
            name="formulary_table_lookup",
            description="Lookup restricted medicines, formulary rows, approval rules, codes, and structured facts.",
            run=formulary_table_lookup,
        ),
        AgentTool(
            name="postgres_deterministic_lookup",
            description=_deterministic_tool_description(deterministic_csv_assets),
            run=postgres_deterministic_lookup,
        ),
        AgentTool(
            name="safety_guard",
            description="Detect clinical risk, missing sources, PHI exposure, or escalation needs.",
            run=safety_guard,
        ),
    ]
