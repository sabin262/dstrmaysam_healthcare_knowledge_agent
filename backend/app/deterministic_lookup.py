from __future__ import annotations

import json
import re
import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Sequence

from .config import AppSettings
from .healthcare import HealthcareUserContext
from .storage import DocumentRecord, DocumentStore


def _terms(query: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[A-Za-z0-9@._+-]+", query) if len(term) >= 2]


def _like(term: str) -> str:
    return f"%{term.lower()}%"


STOPWORDS = {
    "show",
    "is",
    "are",
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
    "dept",
    "information",
    "info",
    "contact",
    "phone",
    "email",
    "number",
    "patient",
    "doctor",
    "physician",
    "nurse",
    "nurses",
    "department",
    "ward",
    "appointment",
    "clinic",
    "medicine",
    "drug",
    "restricted",
    "on",
    "call",
    "oncall",
    "today",
    "tomorrow",
    "available",
    "duty",
    "rota",
    "shift",
}


def _best_search_term(terms: list[str]) -> str:
    for term in terms:
        if re.fullmatch(r"(mrn)?\d{4,}|mrn\d+", term.lower()):
            return term
    useful = [term for term in terms if term.lower() not in STOPWORDS]
    return useful[-1] if useful else (terms[-1] if terms else "")


def _requested_dates(query: str) -> set[str]:
    lowered = query.lower()
    requested: set[str] = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", query))
    today = date.today()
    if "today" in lowered:
        requested.add(today.isoformat())
    if "tomorrow" in lowered:
        requested.add((today + timedelta(days=1)).isoformat())
    return requested


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
    """Catalogue-guided exact lookup over deterministic CSVs, with Postgres fallback."""

    def __init__(self, settings: AppSettings, documents: DocumentStore | None = None):
        self.settings = settings
        self.documents = documents
        self._csv_cache: dict[str, dict[str, Any]] = {}

    def lookup(self, query: str, user: HealthcareUserContext, limit: int = 10) -> LookupResult:
        if not self.settings.deterministic_lookup_enabled:
            return LookupResult("disabled", [], _access_scopes(user), "Deterministic lookup is disabled.")

        category = self._classify(query)
        scopes = _access_scopes(user)
        csv_result = self._lookup_catalogued_csv(category, query, user, scopes, limit)
        if csv_result.rows or category == "staff_rota":
            return csv_result

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

    def _lookup_catalogued_csv(
        self,
        category: str,
        query: str,
        user: HealthcareUserContext,
        scopes: tuple[str, ...],
        limit: int,
    ) -> LookupResult:
        if self.documents is None:
            return LookupResult(category, [], scopes, "No document store configured for deterministic CSV lookup.")
        records = self._catalogue_candidates(category, query, user)
        requested_dates = _requested_dates(query)
        matches: list[dict[str, Any]] = []
        for record in records[:4]:
            for row in self._csv_rows(record):
                if not self._row_allowed(row, scopes):
                    continue
                if requested_dates and not self._row_matches_requested_date(row, requested_dates):
                    continue
                score = self._row_score(row, query, category)
                if score <= 0:
                    continue
                matches.append(
                    {
                        "source": record.uri,
                        "title": record.title,
                        "lookup_category": record.metadata.get("lookup_category") or category,
                        "score": score,
                        **row,
                    }
                )
        matches.sort(key=lambda row: int(row.get("score") or 0), reverse=True)
        if matches and int(matches[0].get("score") or 0) >= 20:
            top_score = int(matches[0].get("score") or 0)
            matches = [row for row in matches if int(row.get("score") or 0) == top_score]
        rows = matches[:limit]
        message = "No matching deterministic CSV rows found." if not rows else f"Found {len(rows)} deterministic CSV row(s)."
        return LookupResult(f"catalogued_csv_{category}", rows, scopes, message)

    def _row_matches_requested_date(self, row: dict[str, Any], requested_dates: set[str]) -> bool:
        for key in ("date", "shift_date", "appointment_date"):
            value = str(row.get(key) or "").strip()
            if value in requested_dates:
                return True
        return False

    def _catalogue_candidates(
        self,
        category: str,
        query: str,
        user: HealthcareUserContext,
    ) -> list[DocumentRecord]:
        user_roles = {role.lower() for role in user.roles}
        query_terms = set(_terms(query))
        records: list[tuple[int, DocumentRecord]] = []
        for record in self.documents.list_documents():
            if not record.key.lower().endswith(".csv"):
                continue
            if "document_catalogue" in record.key.lower():
                continue
            if not self._document_allowed(record, user_roles):
                continue
            metadata = record.metadata
            lookup_category = str(metadata.get("lookup_category") or "").lower()
            domain = str(metadata.get("domain") or "").lower()
            document_type = str(metadata.get("document_type") or "").lower()
            haystack = " ".join(
                [
                    record.title,
                    record.key,
                    record.content_type,
                    json.dumps(metadata, sort_keys=True),
                ]
            ).lower()
            deterministic = (
                domain in {"deterministic", "catalogue", "rota", "formulary"}
                or document_type in {"lookup_table", "directory", "schedule", "table"}
                or bool(lookup_category)
                or self._category_file_hint(category, record.key)
            )
            if not deterministic:
                continue
            score = 0
            if lookup_category == category:
                score += 20
            elif lookup_category and category in lookup_category:
                score += 12
            if category in haystack:
                score += 8
            score += sum(1 for term in query_terms if term and term in haystack)
            if self._category_file_hint(category, record.key):
                score += 10
            if score:
                records.append((score, record))
        records.sort(key=lambda item: item[0], reverse=True)
        sorted_records = [record for _, record in records]
        exact_category_records = [
            record
            for record in sorted_records
            if str(record.metadata.get("lookup_category") or "").lower() == category
            or self._category_file_hint(category, record.key)
        ]
        return exact_category_records or sorted_records

    def _document_allowed(self, record: DocumentRecord, user_roles: set[str]) -> bool:
        allowed = record.metadata.get("allowed_roles") or []
        if isinstance(allowed, str):
            allowed = [allowed]
        allowed_roles = {str(role).lower() for role in allowed}
        return not allowed_roles or "staff" in allowed_roles or bool(user_roles & allowed_roles)

    def _csv_rows(self, record: DocumentRecord) -> list[dict[str, Any]]:
        checksum = str(record.metadata.get("checksum") or "")
        cached = self._csv_cache.get(record.key)
        if cached and cached.get("checksum") == checksum:
            return list(cached.get("rows") or [])
        try:
            text = self.documents.read_text(record.key) if self.documents else ""
            rows = [dict(row) for row in csv.DictReader(io.StringIO(text))]
        except Exception:
            rows = []
        self._csv_cache[record.key] = {"checksum": checksum, "rows": rows}
        return rows

    def _row_allowed(self, row: dict[str, Any], scopes: tuple[str, ...]) -> bool:
        access_level = str(row.get("access_level") or "").strip().lower()
        return not access_level or access_level in scopes

    def _row_score(self, row: dict[str, Any], query: str, category: str) -> int:
        lowered_query = query.lower()
        if category == "doctors" and any(marker in lowered_query for marker in ["on call", "on-call", "oncall", "duty", "rota"]):
            if str(row.get("on_call") or row.get("on_call_today") or "").strip().lower() not in {"yes", "true", "1"}:
                return 0
        if category == "staff_rota":
            role = str(row.get("role") or "").strip().lower()
            if "senior nurse" in lowered_query and "senior nurse" not in role:
                return 0
            if "staff nurse" in lowered_query and "staff nurse" not in role:
                return 0
            if any(marker in lowered_query for marker in ["nurse", "nurses", "nursing"]) and "nurse" not in role:
                return 0
            if any(marker in lowered_query for marker in ["on call", "on-call", "oncall", "duty"]):
                if str(row.get("on_call") or row.get("on_call_today") or "").strip().lower() not in {"yes", "true", "1"}:
                    return 0
        terms = [term for term in _terms(query) if term not in STOPWORDS]
        if not terms:
            return 1
        haystack = json.dumps(row, sort_keys=True).lower()
        score = 0
        for term in terms:
            if not term:
                continue
            weighted_columns = {
                "contacts": (("department", 30), ("contact_name", 6), ("role", 4), ("escalation_type", 3)),
                "departments": (("department", 30), ("department_name", 30), ("service_lead", 4)),
                "doctors": (("staff_name", 20), ("full_name", 20), ("department", 12), ("role", 6)),
                "staff_rota": (("staff_name", 20), ("full_name", 20), ("role", 18), ("department", 12)),
                "wards": (("ward_name", 20), ("ward_code", 20), ("specialty", 16)),
                "appointments": (("clinic_name", 20), ("department", 12), ("patient_name", 10)),
                "formulary": (("medicine", 20), ("medicine_name", 20), ("category", 8)),
            }.get(category, ())
            matched_weighted_column = False
            for column, weight in weighted_columns:
                value = str(row.get(column) or "").lower()
                if re.search(rf"\b{re.escape(term)}\b", value):
                    score += weight
                    matched_weighted_column = True
                    break
                if term in value:
                    score += max(1, weight // 2)
                    matched_weighted_column = True
                    break
            if matched_weighted_column:
                continue
            if re.search(rf"\b{re.escape(term)}\b", haystack):
                score += 2
            elif term in haystack:
                score += 1
        return score

    def _category_file_hint(self, category: str, key: str) -> bool:
        lowered = key.lower()
        hints = {
            "patients": ("patient",),
            "doctors": ("doctor", "staff_rota", "rota"),
            "staff_rota": ("staff_rota", "rota", "shift"),
            "departments": ("department", "contact", "directory"),
            "contacts": ("contact", "department"),
            "appointments": ("appointment", "clinic"),
            "wards": ("ward", "directory"),
            "formulary": ("formulary", "medication", "medicine"),
        }
        return any(hint in lowered for hint in hints.get(category, ()))

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
        if any(
            marker in q
            for marker in [
                "doctor",
                "physician",
                "consultant",
                "clinician",
            ]
        ):
            return "doctors"
        if any(
            marker in q
            for marker in [
                "nurse",
                "nurses",
                "nursing",
                "staff nurse",
                "senior nurse",
                "on call",
                "on-call",
                "oncall",
                "on duty",
                "duty",
                "rota",
                "shift",
                "available",
            ]
        ):
            return "staff_rota"
        if any(marker in q for marker in ["ward", "bed", "floor"]):
            return "wards"
        if any(marker in q for marker in ["department", "service", "unit"]):
            return "departments"
        if any(marker in q for marker in ["contact", "phone", "email", "bleep", "extension"]):
            return "contacts"
        if any(marker in q for marker in ["appointment", "clinic", "slot", "referral"]):
            return "appointments"
        if any(marker in q for marker in ["medicine", "drug", "formulary", "restricted", "dose"]):
            return "formulary"
        return "directory"

    def _access_sql(self) -> str:
        return self._qualified_access_sql()

    def _qualified_access_sql(self, table_alias: str = "") -> str:
        qualifier = f"{table_alias}." if table_alias else ""
        return f"({qualifier}access_level = ANY(%s) OR {qualifier}access_level IS NULL)"

    def patient_dashboard(
        self,
        user: HealthcareUserContext,
        query: str = "",
        patient_identifier: str = "",
        department: str = "",
        ward: str = "",
        care_status: str = "",
        tables: Sequence[str] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return role-scoped patient detail rows for the admin dashboard."""
        if not self.settings.deterministic_lookup_enabled:
            return {
                "available_tables": ["patients", "appointments"],
                "access_scopes_applied": list(_access_scopes(user)),
                "rows": [],
                "summary": {
                    "row_count": 0,
                    "unique_patients": 0,
                    "table_counts": {},
                    "message": "Deterministic lookup is disabled.",
                },
            }

        selected_tables = [table for table in (tables or ["patients", "appointments"]) if table in {"patients", "appointments"}]
        if not selected_tables:
            selected_tables = ["patients", "appointments"]

        scopes = _access_scopes(user)
        rows: list[dict[str, Any]] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                if "patients" in selected_tables:
                    rows.extend(
                        self._dashboard_patient_rows(
                            cur,
                            scopes=scopes,
                            query=query,
                            patient_identifier=patient_identifier,
                            department=department,
                            ward=ward,
                            care_status=care_status,
                            limit=limit,
                        )
                    )
                if "appointments" in selected_tables:
                    rows.extend(
                        self._dashboard_appointment_rows(
                            cur,
                            scopes=scopes,
                            query=query,
                            patient_identifier=patient_identifier,
                            department=department,
                            ward=ward,
                            care_status=care_status,
                            limit=limit,
                        )
                    )

        rows = rows[:limit]
        table_counts: dict[str, int] = {}
        patient_ids: set[str] = set()
        for row in rows:
            table = str(row.get("table") or "unknown")
            table_counts[table] = table_counts.get(table, 0) + 1
            patient_id = str(row.get("patient_id") or row.get("mrn") or "")
            if patient_id:
                patient_ids.add(patient_id)

        return {
            "available_tables": ["patients", "appointments"],
            "access_scopes_applied": list(scopes),
            "filters": {
                "query": query,
                "patient_identifier": patient_identifier,
                "department": department,
                "ward": ward,
                "care_status": care_status,
                "tables": selected_tables,
                "limit": limit,
            },
            "summary": {
                "row_count": len(rows),
                "unique_patients": len(patient_ids),
                "table_counts": table_counts,
                "message": "No matching rows found." if not rows else f"Found {len(rows)} matching row(s).",
            },
            "rows": rows,
        }

    def _dashboard_patient_rows(
        self,
        cur,
        scopes: tuple[str, ...],
        query: str,
        patient_identifier: str,
        department: str,
        ward: str,
        care_status: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        search = _like(query.strip()) if query.strip() else "%"
        identifier = _like(patient_identifier.strip()) if patient_identifier.strip() else "%"
        department_filter = _like(department.strip()) if department.strip() else "%"
        ward_filter = _like(ward.strip()) if ward.strip() else "%"
        status_filter = _like(care_status.strip()) if care_status.strip() else "%"
        cur.execute(
            f"""
            SELECT 'patients' AS source_table, patient_id, mrn, nhs_number, full_name AS patient_name,
                   date_of_birth, ward_code, department_name, named_consultant, care_status,
                   risk_flags, access_level
            FROM patients
            WHERE {self._access_sql()}
              AND (%s = '%%' OR lower(patient_id) LIKE %s OR lower(mrn) LIKE %s OR lower(nhs_number) LIKE %s)
              AND (%s = '%%' OR lower(department_name) LIKE %s)
              AND (%s = '%%' OR lower(ward_code) LIKE %s)
              AND (%s = '%%' OR lower(care_status) LIKE %s)
              AND (%s = '%%' OR lower(full_name) LIKE %s OR lower(mrn) LIKE %s OR lower(nhs_number) LIKE %s
                   OR lower(department_name) LIKE %s OR lower(ward_code) LIKE %s
                   OR lower(named_consultant) LIKE %s OR lower(care_status) LIKE %s OR lower(risk_flags) LIKE %s)
            ORDER BY full_name
            LIMIT %s
            """,
            (
                list(scopes),
                identifier,
                identifier,
                identifier,
                identifier,
                department_filter,
                department_filter,
                ward_filter,
                ward_filter,
                status_filter,
                status_filter,
                search,
                search,
                search,
                search,
                search,
                search,
                search,
                search,
                search,
                limit,
            ),
        )
        rows = [dict(row) for row in cur.fetchall()]
        for row in rows:
            row["table"] = row.pop("source_table", "patients")
        return rows

    def _dashboard_appointment_rows(
        self,
        cur,
        scopes: tuple[str, ...],
        query: str,
        patient_identifier: str,
        department: str,
        ward: str,
        care_status: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        search = _like(query.strip()) if query.strip() else "%"
        identifier = _like(patient_identifier.strip()) if patient_identifier.strip() else "%"
        department_filter = _like(department.strip()) if department.strip() else "%"
        ward_filter = _like(ward.strip()) if ward.strip() else "%"
        status_filter = _like(care_status.strip()) if care_status.strip() else "%"
        cur.execute(
            f"""
            SELECT 'appointments' AS source_table, p.patient_id, a.patient_mrn AS mrn, p.nhs_number,
                   a.patient_name, p.date_of_birth, p.ward_code, a.department_name,
                   p.named_consultant, p.care_status, p.risk_flags,
                   a.appointment_id, a.clinic_name, a.appointment_date, a.appointment_time,
                   a.clinician_name, a.status, a.referral_priority, a.access_level
            FROM appointments a
            LEFT JOIN patients p ON p.mrn = a.patient_mrn
            WHERE {self._qualified_access_sql("a")}
              AND (%s = '%%' OR lower(COALESCE(p.patient_id, '')) LIKE %s OR lower(a.patient_mrn) LIKE %s
                   OR lower(COALESCE(p.nhs_number, '')) LIKE %s)
              AND (%s = '%%' OR lower(a.department_name) LIKE %s)
              AND (%s = '%%' OR lower(COALESCE(p.ward_code, '')) LIKE %s)
              AND (%s = '%%' OR lower(COALESCE(p.care_status, '')) LIKE %s)
              AND (%s = '%%' OR lower(a.patient_name) LIKE %s OR lower(a.patient_mrn) LIKE %s
                   OR lower(a.clinic_name) LIKE %s OR lower(a.department_name) LIKE %s
                   OR lower(a.clinician_name) LIKE %s OR lower(a.status) LIKE %s
                   OR lower(a.referral_priority) LIKE %s)
            ORDER BY a.appointment_date, a.appointment_time
            LIMIT %s
            """,
            (
                list(scopes),
                identifier,
                identifier,
                identifier,
                identifier,
                department_filter,
                department_filter,
                ward_filter,
                ward_filter,
                status_filter,
                status_filter,
                search,
                search,
                search,
                search,
                search,
                search,
                search,
                search,
                limit,
            ),
        )
        rows = [dict(row) for row in cur.fetchall()]
        for row in rows:
            row["table"] = row.pop("source_table", "appointments")
        return rows

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
        lowered = query.lower()
        on_call_only = any(marker in lowered for marker in ["on call", "on-call", "oncall", "duty", "rota"])
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
