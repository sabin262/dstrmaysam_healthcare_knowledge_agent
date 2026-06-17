from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .retrieval import RetrievalHit
from .storage import DocumentRecord


LOGGER = logging.getLogger("healthcare_audit")


@dataclass(frozen=True)
class HealthcareUserContext:
    user_id: str
    roles: tuple[str, ...] = ("staff",)
    departments: tuple[str, ...] = ()

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> "HealthcareUserContext":
        roles = claims.get("roles") or ["staff"]
        departments = claims.get("departments") or []
        return cls(
            user_id=str(claims["sub"]),
            roles=tuple(str(role).lower() for role in roles),
            departments=tuple(str(department).lower() for department in departments),
        )

    def has_role(self, role: str) -> bool:
        return role.lower() in self.roles


@dataclass(frozen=True)
class SourceGovernance:
    owner: str = "unknown"
    version: str = "unknown"
    effective_date: str = "unknown"
    review_date: str = "unknown"
    approval_status: str = "unknown"
    sensitivity: str = "internal"
    domain: str = "general"
    document_type: str = "document"
    allowed_roles: tuple[str, ...] = ("staff",)

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> "SourceGovernance":
        allowed_roles = metadata.get("allowed_roles") or metadata.get("roles") or ["staff"]
        if isinstance(allowed_roles, str):
            allowed_roles = [allowed_roles]
        return cls(
            owner=str(metadata.get("owner", "unknown")),
            version=str(metadata.get("version", "unknown")),
            effective_date=str(metadata.get("effective_date", "unknown")),
            review_date=str(metadata.get("review_date", "unknown")),
            approval_status=str(metadata.get("approval_status", "unknown")),
            sensitivity=str(metadata.get("sensitivity", "internal")),
            domain=str(metadata.get("domain", "general")),
            document_type=str(metadata.get("document_type", "document")),
            allowed_roles=tuple(str(role).lower() for role in allowed_roles),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "version": self.version,
            "effective_date": self.effective_date,
            "review_date": self.review_date,
            "approval_status": self.approval_status,
            "sensitivity": self.sensitivity,
            "domain": self.domain,
            "document_type": self.document_type,
            "allowed_roles": list(self.allowed_roles),
        }


class HealthcareAccessControl:
    """Role-aware filtering scaffold for healthcare source access."""

    def can_access_metadata(self, user: HealthcareUserContext, metadata: dict[str, Any]) -> bool:
        governance = SourceGovernance.from_metadata(metadata)
        if "admin" in user.roles:
            return True
        return bool(set(user.roles) & set(governance.allowed_roles))

    def filter_documents(
        self, user: HealthcareUserContext, documents: list[DocumentRecord]
    ) -> list[DocumentRecord]:
        return [document for document in documents if self.can_access_metadata(user, document.metadata)]

    def filter_hits(self, user: HealthcareUserContext, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        return [hit for hit in hits if self.can_access_metadata(user, hit.metadata)]


@dataclass(frozen=True)
class PHIRedactionResult:
    redacted_text: str
    findings: dict[str, int]

    @property
    def has_phi(self) -> bool:
        return any(count > 0 for count in self.findings.values())


class PHIRedactor:
    """Lightweight PHI redaction hook for prompts, traces, logs, and eval payloads."""

    PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
        ("phone", re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b")),
        ("nhs_number", re.compile(r"\b\d{3}[\s-]?\d{3}[\s-]?\d{4}\b")),
        ("medical_record_number", re.compile(r"\b(?:MRN|NHS|Patient ID)[:#\s-]*[A-Z0-9-]{5,}\b", re.IGNORECASE)),
        ("date_of_birth", re.compile(r"\b(?:DOB|date of birth)[:#\s-]*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.IGNORECASE)),
    )

    def redact(self, text: str) -> PHIRedactionResult:
        findings: dict[str, int] = {}
        redacted = text
        for label, pattern in self.PATTERNS:
            redacted, count = pattern.subn(f"[REDACTED_{label.upper()}]", redacted)
            findings[label] = count
        return PHIRedactionResult(redacted_text=redacted, findings=findings)


@dataclass(frozen=True)
class SafetyAssessment:
    risk_level: str
    flags: tuple[str, ...] = ()
    escalation_required: bool = False
    allow_answer: bool = True
    message: str = "No safety issues detected."

    def as_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "flags": list(self.flags),
            "escalation_required": self.escalation_required,
            "allow_answer": self.allow_answer,
            "message": self.message,
        }


class HealthcareSafetyGuard:
    """Clinical-risk and citation-sufficiency scaffold for Phase One."""

    URGENT_TERMS = (
        "chest pain",
        "stroke",
        "sepsis",
        "suicide",
        "self harm",
        "anaphylaxis",
        "cardiac arrest",
        "unconscious",
        "not breathing",
        "overdose",
        "safeguarding",
    )
    PATIENT_SPECIFIC_TERMS = (
        "patient",
        "diagnose",
        "treat",
        "prescribe",
        "dosage",
        "symptoms",
        "lab result",
        "blood pressure",
    )

    def __init__(self, redactor: PHIRedactor | None = None):
        self.redactor = redactor or PHIRedactor()

    def assess(self, query: str, sources: list[dict[str, Any]] | None = None) -> SafetyAssessment:
        normalized = query.lower()
        flags: list[str] = []
        escalation_required = False
        allow_answer = True

        if any(term in normalized for term in self.URGENT_TERMS):
            flags.append("urgent_or_high_risk_clinical_term")
            escalation_required = True

        if any(term in normalized for term in self.PATIENT_SPECIFIC_TERMS):
            flags.append("patient_specific_or_clinical_advice")

        redaction = self.redactor.redact(query)
        if redaction.has_phi:
            flags.append("possible_phi_detected")

        if not sources:
            flags.append("missing_cited_sources")
            if any(flag in flags for flag in ["patient_specific_or_clinical_advice", "urgent_or_high_risk_clinical_term"]):
                allow_answer = False

        if escalation_required:
            message = (
                "Potential urgent or high-risk healthcare request. Provide approved policy "
                "citations only and direct the user to local escalation pathways."
            )
        elif not allow_answer:
            message = "Clinical or patient-specific request lacks cited approved sources."
        elif flags:
            message = "Safety guard detected issues that should be reflected in the final answer."
        else:
            message = "No safety issues detected."

        risk_level = "high" if escalation_required else "medium" if flags else "low"
        return SafetyAssessment(
            risk_level=risk_level,
            flags=tuple(flags),
            escalation_required=escalation_required,
            allow_answer=allow_answer,
            message=message,
        )


class HealthcareAuditLogger:
    """JSON audit event scaffold for healthcare governance."""

    def log_chat_event(
        self,
        *,
        user: HealthcareUserContext,
        session_id: str,
        query: str,
        tools_used: list[str],
        sources: list[dict[str, Any]],
        trace_id: str,
        safety: SafetyAssessment,
        token_usage: dict[str, int],
    ) -> dict[str, Any]:
        event = {
            "event_type": "healthcare_chat_answer",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user.user_id,
            "roles": list(user.roles),
            "departments": list(user.departments),
            "session_id": session_id,
            "query_redacted": PHIRedactor().redact(query).redacted_text,
            "tools_used": tools_used,
            "source_uris": [source.get("uri") for source in sources],
            "trace_id": trace_id,
            "safety": safety.as_dict(),
            "token_usage": token_usage,
        }
        LOGGER.info(json.dumps(event, sort_keys=True))
        return event

