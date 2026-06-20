from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .config import AppSettings
from .healthcare import HealthcareUserContext


def _terms(query: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[A-Za-z0-9@._+-]+", query) if len(term) >= 2]


def _like(term: str) -> str:
    return f"%{term.lower()}%"


STOPWORDS = {
    "show",
    "what",
    "which",
    "who",
    "where",
    "when",
    "for",
    "the",
    "and",
    "with",
    "details",
    "detail",
    "information",
    "info",
    "contact",
    "phone",
    "email",
    "number",
    "patient",
    "doctor",
    "physician",
    "department",
    "ward",
    "appointment",
    "clinic",
    "medicine",
    "drug",
    "restricted",
}


def _best_search_term(terms: list[str]) -> str:
    for term in terms:
        if re.fullmatch(r"(mrn)?\d{4,}|mrn\d+", term.lower()):
            return term
    useful = [term for term in terms if term.lower() not in STOPWORDS]
    return useful[-1] if useful else (terms[-1] if terms else "")


def _access_scopes(user: HealthcareUserContext) -> tuple[str, ...]:
    roles = set(user.roles)
    if "admin" in roles or "director" in roles:
        return ("all_staff", "clinical", "pharmacy", "manager", "hr_manager", "ig_manager", "director")
    scopes = {"all_staff"}
    if roles & {"doctor", "physician", "nurse", "clinical", "clinician"}:
        scopes.add("clinical")
    if roles & {"pharmacist", "pharmacy"}:
        scopes.update({"clinical", "pharmacy"})
    if roles & {"manager", "department_manager"}:
        scopes.update({"clinical", "manager"})
    if roles & {"hr", "hr_manager"}:
        scopes.add("hr_manager")
    if roles & {"ig_manager", "information_governance"}:
        scopes.add("ig_manager")
    return tuple(sorted(scopes))


@dataclass(frozen=True)
class LookupResult:
    category: str
    rows: list[dict[str, Any]]
    access_scopes: tuple[str, ...]
    message: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "category": self.category,
                "message": self.message,
                "access_scopes_applied": list(self.access_scopes),
                "rows": self.rows,
            },
            indent=2,
            default=str,
        )


class DeterministicLookupService:
    """Safe Postgres lookup service for exact operational healthcare data."""

    def __init__(self, settings: AppSettings):
        self.settings = settings

    def lookup(self, query: str, user: HealthcareUserContext, limit: int = 10) -> LookupResult:
        if not self.settings.deterministic_lookup_enabled:
            return LookupResult("disabled", [], _access_scopes(user), "Deterministic lookup is disabled.")

        category = self._classify(query)
        scopes = _access_scopes(user)
        try:
            rows = self._lookup_category(category, query, scopes, limit)
        except Exception as exc:
            return LookupResult(
                category,
                [],
                scopes,
                f"Postgres deterministic lookup failed: {type(exc).__name__}: {exc}",
            )

        message = "No matching rows found." if not rows else f"Found {len(rows)} matching row(s)."
        return LookupResult(category, rows, scopes, message)

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except Exception as exc:  # pragma: no cover - exercised when dependency missing
            raise RuntimeError("psycopg is not installed. Install backend requirements.") from exc

        return psycopg.connect(
            host=self.settings.postgres_host,
            port=self.settings.postgres_port,
            dbname=self.settings.postgres_db,
            user=self.settings.postgres_user,
            password=self.settings.postgres_password,
            sslmode=self.settings.postgres_sslmode,
            row_factory=dict_row,
            connect_timeout=3,
        )

    def _lookup_category(
        self, category: str, query: str, scopes: tuple[str, ...], limit: int
    ) -> list[dict[str, Any]]:
        terms = _terms(query)
        primary = terms[0] if terms else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                if category == "patients":
                    return self._query_patients(cur, terms, scopes, limit)
                if category == "doctors":
                    return self._query_doctors(cur, query, terms, scopes, limit)
                if category == "departments":
                    return self._query_departments(cur, terms, scopes, limit)
                if category == "contacts":
                    return self._query_contacts(cur, terms, scopes, limit)
                if category == "appointments":
                    return self._query_appointments(cur, terms, scopes, limit)
                if category == "wards":
                    return self._query_wards(cur, terms, scopes, limit)
                if category == "formulary":
                    return self._query_formulary(cur, terms, scopes, limit)
                return self._query_directory(cur, primary, scopes, limit)

    def _classify(self, query: str) -> str:
        q = query.lower()
        if any(marker in q for marker in ["patient", "mrn", "nhs", "date of birth", "dob"]):
            return "patients"
        if any(marker in q for marker in ["doctor", "physician", "consultant", "clinician"]):
            return "doctors"
        if any(marker in q for marker in ["department", "service", "unit"]):
            return "departments"
        if any(marker in q for marker in ["contact", "phone", "email", "bleep", "extension"]):
            return "contacts"
        if any(marker in q for marker in ["appointment", "clinic", "slot", "referral"]):
            return "appointments"
        if any(marker in q for marker in ["ward", "bed", "floor"]):
            return "wards"
        if any(marker in q for marker in ["medicine", "drug", "formulary", "restricted", "dose"]):
            return "formulary"
        return "directory"

    def _access_sql(self) -> str:
        return "(access_level = ANY(%s) OR access_level IS NULL)"

    def _query_patients(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int):
        pattern = _like(_best_search_term(terms))
        cur.execute(
            f"""
            SELECT patient_id, mrn, nhs_number, full_name, date_of_birth, ward_code,
                   department_name, named_consultant, care_status, risk_flags, access_level
            FROM patients
            WHERE {self._access_sql()}
              AND (%s = '%%' OR lower(full_name) LIKE %s OR lower(mrn) LIKE %s OR lower(nhs_number) LIKE %s)
            ORDER BY full_name
            LIMIT %s
            """,
            (list(scopes), pattern, pattern, pattern, pattern, limit),
        )
        return list(cur.fetchall())

    def _query_doctors(self, cur, query: str, terms: list[str], scopes: tuple[str, ...], limit: int):
        pattern = _like(_best_search_term(terms))
        on_call_only = "on call" in query.lower() or "on-call" in query.lower()
        cur.execute(
            f"""
            SELECT doctor_id, full_name, grade, specialty, department_name, phone,
                   email, bleep, on_call_today, access_level
            FROM doctors
            WHERE {self._access_sql()}
              AND (%s = false OR on_call_today = true)
              AND (%s = '%%' OR lower(full_name) LIKE %s OR lower(specialty) LIKE %s OR lower(department_name) LIKE %s)
            ORDER BY department_name, full_name
            LIMIT %s
            """,
            (list(scopes), on_call_only, "%" if on_call_only else pattern, pattern, pattern, pattern, limit),
        )
        return list(cur.fetchall())

    def _query_departments(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int):
        pattern = _like(_best_search_term(terms))
        cur.execute(
            f"""
            SELECT department_id, department_name, specialty_group, location, main_phone,
                   email, service_lead, escalation_contact, access_level
            FROM departments
            WHERE {self._access_sql()}
              AND (%s = '%%' OR lower(department_name) LIKE %s OR lower(specialty_group) LIKE %s)
            ORDER BY department_name
            LIMIT %s
            """,
            (list(scopes), pattern, pattern, pattern, limit),
        )
        return list(cur.fetchall())

    def _query_contacts(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int):
        pattern = _like(_best_search_term(terms))
        cur.execute(
            f"""
            SELECT contact_id, contact_type, department_name, contact_name, role,
                   phone, email, available_hours, escalation_level, access_level
            FROM organization_contacts
            WHERE {self._access_sql()}
              AND (%s = '%%' OR lower(contact_name) LIKE %s OR lower(department_name) LIKE %s
                   OR lower(contact_type) LIKE %s OR lower(role) LIKE %s)
            ORDER BY department_name, escalation_level
            LIMIT %s
            """,
            (list(scopes), pattern, pattern, pattern, pattern, pattern, limit),
        )
        return list(cur.fetchall())

    def _query_appointments(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int):
        pattern = _like(_best_search_term(terms))
        cur.execute(
            f"""
            SELECT appointment_id, patient_mrn, patient_name, clinic_name, department_name,
                   appointment_date, appointment_time, clinician_name, status, referral_priority, access_level
            FROM appointments
            WHERE {self._access_sql()}
              AND (%s = '%%' OR lower(patient_name) LIKE %s OR lower(patient_mrn) LIKE %s
                   OR lower(clinic_name) LIKE %s OR lower(department_name) LIKE %s)
            ORDER BY appointment_date, appointment_time
            LIMIT %s
            """,
            (list(scopes), pattern, pattern, pattern, pattern, pattern, limit),
        )
        return list(cur.fetchall())

    def _query_wards(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int):
        pattern = _like(_best_search_term(terms))
        cur.execute(
            f"""
            SELECT ward_code, ward_name, department_name, floor, bed_capacity,
                   beds_available, nurse_in_charge, phone, access_level
            FROM wards
            WHERE {self._access_sql()}
              AND (%s = '%%' OR lower(ward_code) LIKE %s OR lower(ward_name) LIKE %s OR lower(department_name) LIKE %s)
            ORDER BY ward_code
            LIMIT %s
            """,
            (list(scopes), pattern, pattern, pattern, pattern, limit),
        )
        return list(cur.fetchall())

    def _query_formulary(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int):
        pattern = _like(_best_search_term(terms))
        cur.execute(
            f"""
            SELECT medicine_id, medicine_name, category, restricted, approval_required,
                   max_adult_dose, monitoring_required, access_level
            FROM formulary
            WHERE {self._access_sql()}
              AND (%s = '%%' OR lower(medicine_name) LIKE %s OR lower(category) LIKE %s)
            ORDER BY medicine_name
            LIMIT %s
            """,
            (list(scopes), pattern, pattern, pattern, limit),
        )
        return list(cur.fetchall())

    def _query_directory(self, cur, primary: str, scopes: tuple[str, ...], limit: int):
        pattern = _like(primary)
        cur.execute(
            f"""
            SELECT 'department' AS result_type, department_name AS name, service_lead AS role,
                   main_phone AS phone, email, access_level
            FROM departments
            WHERE {self._access_sql()}
              AND (%s = '%%' OR lower(department_name) LIKE %s OR lower(service_lead) LIKE %s)
            UNION ALL
            SELECT 'contact' AS result_type, contact_name AS name, role, phone, email, access_level
            FROM organization_contacts
            WHERE {self._access_sql()}
              AND (%s = '%%' OR lower(contact_name) LIKE %s OR lower(role) LIKE %s OR lower(department_name) LIKE %s)
            ORDER BY result_type, name
            LIMIT %s
            """,
            (
                list(scopes),
                pattern,
                pattern,
                pattern,
                list(scopes),
                pattern,
                pattern,
                pattern,
                pattern,
                limit,
            ),
        )
        return list(cur.fetchall())
