from __future__ import annotations

import json
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

from .config import AppSettings
from .healthcare import HealthcareUserContext


def _terms(query: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[A-Za-z0-9@._+-]+", query) if len(term) >= 2]


def _like(term: str) -> str:
    return f"%{term.lower()}%"


POSTGRES_LOOKUP_TABLES = {
    "patients",
    "doctors",
    "departments",
    "organization_contacts",
    "appointments",
    "wards",
    "formulary",
    "uploaded_lookup_rows",
}

SCHEMA_SYNONYMS = {
    "appointment": {"appointments", "clinic", "clinics", "slot", "slots", "referral", "referrals"},
    "contact": {"contacts", "phone", "phones", "telephone", "tel", "email", "bleep", "extension", "ext"},
    "department": {"departments", "service", "services", "unit", "units", "specialty", "speciality"},
    "doctor": {"doctors", "physician", "physicians", "consultant", "consultants", "clinician", "clinicians", "dr"},
    "formulary": {"medicine", "medicines", "drug", "drugs", "dose", "doses", "restricted", "approval"},
    "patient": {"patients", "mrn", "nhs", "dob"},
    "ward": {"wards", "bed", "beds", "floor", "ipd", "inpatient"},
}

DOCTOR_ROLE_MARKERS = {
    "doctor",
    "doctors",
    "physician",
    "physicians",
    "consultant",
    "consultants",
    "registrar",
    "registrars",
    "clinician",
    "clinicians",
}

NURSE_ROLE_MARKERS = {"nurse", "nurses", "nursing"}

STAFF_ROTA_QUERY_MARKERS = {
    "available",
    "availability",
    "rota",
    "schedule",
    "scheduled",
    "shift",
    "shifts",
    "oncall",
    "on-call",
    "today",
    "tomorrow",
}

BASE_STOPWORDS = {
    "show",
    "is",
    "are",
    "am",
    "be",
    "being",
    "been",
    "in",
    "on",
    "at",
    "to",
    "from",
    "of",
    "a",
    "an",
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
    "ipd",
    "inpatient",
    "location",
    "located",
}


STOPWORDS = BASE_STOPWORDS


def _word_variants(word: str) -> set[str]:
    cleaned = word.strip().lower()
    if not cleaned:
        return set()
    variants = {cleaned}
    if cleaned.endswith("ies") and len(cleaned) > 3:
        variants.add(cleaned[:-3] + "y")
    if cleaned.endswith("s") and len(cleaned) > 3:
        variants.add(cleaned[:-1])
    else:
        variants.add(cleaned + "s")
    return {variant for variant in variants if len(variant) >= 2}


def _expand_stopwords(words: set[str]) -> set[str]:
    expanded: set[str] = set()
    for word in words:
        parts = [part for part in re.split(r"[^A-Za-z0-9]+", word.lower()) if part]
        for part in parts or [word.lower()]:
            expanded.update(_word_variants(part))
            for synonym in SCHEMA_SYNONYMS.get(part, set()):
                expanded.update(_word_variants(synonym))
    return expanded


def _best_search_term(terms: list[str], stopwords: set[str] | None = None) -> str:
    for term in terms:
        if re.fullmatch(r"(mrn)?\d{4,}|mrn\d+", term.lower()):
            return term
    active_stopwords = STOPWORDS | (stopwords or set())
    useful = [term for term in terms if term.lower() not in active_stopwords]
    return useful[-1] if useful else (terms[-1] if terms else "")


def _has_person_name_hint(terms: list[str]) -> bool:
    useful = [
        term
        for term in terms
        if term.lower() not in STOPWORDS and not re.fullmatch(r"(w\d+|dep-[a-z0-9-]+|\d+)", term.lower())
    ]
    name_like_terms = [term for term in useful if re.fullmatch(r"[a-z][a-z'-]+", term.lower())]
    return len(name_like_terms) >= 2


def _requested_rota_dates(query: str, today: date | None = None) -> list[str]:
    q = query.lower()
    base_date = today or date.today()
    requested: list[date] = []
    if "today" in q:
        requested.append(base_date)
    if "tomorrow" in q:
        requested.append(base_date + timedelta(days=1))
    for match in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", query):
        try:
            requested.append(date.fromisoformat(match))
        except ValueError:
            continue
    if not requested and any(marker in q for marker in ["available", "availability", "rota", "schedule", "scheduled", "shift"]):
        requested.append(base_date)

    unique: list[str] = []
    for value in requested:
        iso_value = value.isoformat()
        if iso_value not in unique:
            unique.append(iso_value)
    return unique


def _requested_rota_role_groups(query: str) -> set[str]:
    terms = set(_terms(query))
    groups: set[str] = set()
    if terms & DOCTOR_ROLE_MARKERS:
        groups.add("doctor")
    if terms & NURSE_ROLE_MARKERS:
        groups.add("nurse")
    return groups


def _is_staff_rota_query(query: str) -> bool:
    q = query.lower()
    terms = set(_terms(query))
    role_requested = bool(terms & (DOCTOR_ROLE_MARKERS | NURSE_ROLE_MARKERS))
    rota_requested = any(marker in q for marker in STAFF_ROTA_QUERY_MARKERS)
    mentions_staff_rota = "staff_rota" in q or "staff rota" in q
    return mentions_staff_rota or (role_requested and rota_requested)


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
    lookup_plan: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "category": self.category,
                "message": self.message,
                "access_scopes_applied": list(self.access_scopes),
                "lookup_plan": self.lookup_plan,
                "rows": self.rows,
            },
            indent=2,
            default=str,
        )


class DeterministicLookupService:
    """Safe Postgres lookup service for exact operational healthcare data."""

    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._schema_stopwords_cache: set[str] | None = None

    def lookup(
        self,
        query: str,
        user: HealthcareUserContext,
        limit: int = 10,
        csv_assets: Sequence[dict[str, Any]] | None = None,
    ) -> LookupResult:
        if not self.settings.deterministic_lookup_enabled:
            return LookupResult("disabled", [], _access_scopes(user), "Deterministic lookup is disabled.")

        category = self._classify(query)
        scopes = _access_scopes(user)
        schema_stopwords = self._schema_stopwords()
        search_terms = self._search_terms(query, schema_stopwords)
        selected_assets = self._matching_csv_assets(query, csv_assets or [])
        selected_filenames = [str(asset.get("filename") or "") for asset in selected_assets if asset.get("filename")]
        try:
            if _is_staff_rota_query(query):
                rows = self._query_staff_rota_rows(query, scopes, limit)
                role_groups = _requested_rota_role_groups(query)
                if not rows and role_groups == {"doctor"}:
                    rows.extend(
                        self._lookup_category(
                            "doctors",
                            query,
                            scopes,
                            limit - len(rows),
                            stopwords=schema_stopwords,
                        )
                    )
            elif selected_filenames:
                rows = self._query_uploaded_lookup_rows(
                    query,
                    scopes,
                    limit,
                    source_filenames=selected_filenames,
                    stopwords=schema_stopwords,
                )
                if len(rows) < limit:
                    rows.extend(
                        self._lookup_category(
                            category,
                            query,
                            scopes,
                            limit - len(rows),
                            stopwords=schema_stopwords,
                        )
                    )
            else:
                rows = self._lookup_category(category, query, scopes, limit, stopwords=schema_stopwords)
                uploaded_rows = self._query_uploaded_lookup_rows(
                    query,
                    scopes,
                    max(0, limit - len(rows)),
                    stopwords=schema_stopwords,
                )
                rows = rows + uploaded_rows
        except Exception as exc:
            return LookupResult(
                category,
                [],
                scopes,
                f"Postgres deterministic lookup failed: {type(exc).__name__}: {exc}",
                lookup_plan={
                    "category": category,
                    "search_terms": search_terms,
                    "selected_csv_assets": selected_assets,
                    "source": "postgres",
                },
            )

        if category == "staff_rota":
            message = self._staff_rota_message(query, rows)
        else:
            message = "No matching rows found." if not rows else f"Found {len(rows)} matching row(s)."
        return LookupResult(
            category,
            rows,
            scopes,
            message,
            lookup_plan={
                "category": category,
                "search_terms": search_terms,
                "selected_csv_assets": selected_assets,
                "source": "postgres",
            },
        )

    def _staff_rota_message(self, query: str, rows: Sequence[dict[str, Any]]) -> str:
        requested_dates = _requested_rota_dates(query)
        requested_groups = _requested_rota_role_groups(query)
        if not rows:
            if requested_dates:
                return (
                    "No matching staff_rota.csv rows found for requested date(s): "
                    + ", ".join(requested_dates)
                    + "."
                )
            return "No matching staff_rota.csv rows found."

        found_dates: set[str] = set()
        found_groups: set[str] = set()
        for result_row in rows:
            payload = result_row.get("row") if isinstance(result_row, dict) else {}
            if not isinstance(payload, dict):
                continue
            if payload.get("date"):
                found_dates.add(str(payload["date"]))
            role = str(payload.get("role") or "").lower()
            if any(marker in role for marker in ["consultant", "physician", "registrar", "doctor", "clinician"]):
                found_groups.add("doctor")
            if "nurse" in role:
                found_groups.add("nurse")

        notes = [f"Found {len(rows)} matching staff_rota.csv row(s)."]
        if requested_dates:
            notes.append("Requested dates: " + ", ".join(requested_dates) + ".")
            missing_dates = [value for value in requested_dates if value not in found_dates]
            if missing_dates:
                notes.append("No matching rows found for: " + ", ".join(missing_dates) + ".")
        if requested_groups:
            missing_groups = sorted(requested_groups - found_groups)
            if missing_groups:
                notes.append("No matching " + ", ".join(missing_groups) + " rows found for the requested date range.")
        return " ".join(notes)

    def ingest_uploaded_csv(
        self,
        filename: str,
        data: bytes,
        access_level: str = "all_staff",
    ) -> int:
        decoded = data.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(decoded))
        if not reader.fieldnames:
            return 0

        rows: list[tuple[str, int, str, str, str]] = []
        for row_number, row in enumerate(reader, start=1):
            cleaned = {
                str(key).strip(): str(value).strip()
                for key, value in row.items()
                if key is not None and value is not None and str(value).strip()
            }
            if not cleaned:
                continue
            searchable_text = " ".join([filename, *cleaned.keys(), *cleaned.values()]).lower()
            rows.append(
                (
                    filename,
                    row_number,
                    json.dumps(cleaned, ensure_ascii=False),
                    searchable_text,
                    access_level,
                )
            )

        if not rows:
            return 0

        with self._connect() as conn:
            with conn.cursor() as cur:
                self._ensure_uploaded_lookup_table(cur)
                cur.execute("DELETE FROM uploaded_lookup_rows WHERE source_filename = %s", (filename,))
                cur.executemany(
                    """
                    INSERT INTO uploaded_lookup_rows
                        (source_filename, row_number, row_data, searchable_text, access_level)
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                    """,
                    rows,
                )
            conn.commit()
        return len(rows)

    @staticmethod
    def _ensure_uploaded_lookup_table(cur) -> None:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_lookup_rows (
                id BIGSERIAL PRIMARY KEY,
                source_filename TEXT NOT NULL,
                row_number INTEGER NOT NULL,
                row_data JSONB NOT NULL,
                searchable_text TEXT NOT NULL,
                access_level TEXT NOT NULL DEFAULT 'all_staff',
                uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

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
        self,
        category: str,
        query: str,
        scopes: tuple[str, ...],
        limit: int,
        *,
        stopwords: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        terms = _terms(query)
        primary = terms[0] if terms else ""
        with self._connect() as conn:
            with conn.cursor() as cur:
                if category == "patients":
                    return self._query_patients(cur, terms, scopes, limit, stopwords)
                if category == "doctors":
                    return self._query_doctors(cur, query, terms, scopes, limit, stopwords)
                if category == "departments":
                    return self._query_departments(cur, terms, scopes, limit, stopwords)
                if category == "contacts":
                    return self._query_contacts(cur, terms, scopes, limit, stopwords)
                if category == "appointments":
                    return self._query_appointments(cur, terms, scopes, limit, stopwords)
                if category == "wards":
                    return self._query_wards(cur, terms, scopes, limit, stopwords)
                if category == "formulary":
                    return self._query_formulary(cur, terms, scopes, limit, stopwords)
                return self._query_directory(cur, primary, scopes, limit, stopwords)

    def _query_uploaded_lookup_rows(
        self,
        query: str,
        scopes: tuple[str, ...],
        limit: int,
        *,
        source_filenames: Sequence[str] | None = None,
        stopwords: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        terms = self._search_terms(query, stopwords or set())
        if not terms:
            return []
        patterns = [_like(term) for term in terms[:8]]
        where = " OR ".join(["lower(searchable_text) LIKE %s" for _ in patterns])
        filename_filter = ""
        params: list[Any] = [list(scopes)]
        if source_filenames:
            filename_filter = "AND source_filename = ANY(%s)"
            params.append(list(source_filenames))
        params.extend(patterns)
        fetch_limit = max(limit, min(limit * 5, 100))
        params.append(fetch_limit)
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._ensure_uploaded_lookup_table(cur)
                cur.execute(
                    f"""
                    SELECT source_filename, row_number, row_data, access_level, searchable_text
                    FROM uploaded_lookup_rows
                    WHERE {self._access_sql()}
                      {filename_filter}
                      AND ({where})
                    ORDER BY source_filename, row_number
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = []
                for row in cur.fetchall():
                    row_dict = dict(row)
                    payload = row_dict.get("row_data")
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except json.JSONDecodeError:
                            payload = {"value": payload}
                    rows.append(
                        {
                            "source_table": "uploaded_lookup_rows",
                            "source_filename": row_dict.get("source_filename"),
                            "row_number": row_dict.get("row_number"),
                            "row": payload,
                            "access_level": row_dict.get("access_level"),
                            "_match_score": sum(
                                1
                                for term in terms
                                if term in str(row_dict.get("searchable_text") or "").lower()
                            ),
                        }
                    )
                rows.sort(
                    key=lambda row: (
                        -int(row.get("_match_score") or 0),
                        str(row.get("source_filename") or ""),
                        int(row.get("row_number") or 0),
                    )
                )
                for row in rows:
                    row.pop("_match_score", None)
                return rows[:limit]

    def _schema_stopwords(self) -> set[str]:
        if self._schema_stopwords_cache is not None:
            return set(self._schema_stopwords_cache)
        words = set(STOPWORDS) | set(POSTGRES_LOOKUP_TABLES)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT table_name, column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = ANY(%s)
                        """,
                        (list(POSTGRES_LOOKUP_TABLES),),
                    )
                    for row in cur.fetchall():
                        words.add(str(row.get("table_name") or ""))
                        words.add(str(row.get("column_name") or ""))
        except Exception:
            pass
        self._schema_stopwords_cache = _expand_stopwords(words)
        return set(self._schema_stopwords_cache)

    def _search_terms(self, query: str, stopwords: set[str]) -> list[str]:
        active_stopwords = STOPWORDS | stopwords
        return [term for term in _terms(query) if term.lower() not in active_stopwords]

    def _matching_csv_assets(
        self,
        query: str,
        csv_assets: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        query_terms = set(_terms(query))
        if not query_terms:
            return []
        matches: list[tuple[int, dict[str, Any]]] = []
        for asset in csv_assets:
            filename = str(asset.get("filename") or asset.get("title") or "")
            columns = [str(column) for column in asset.get("columns") or []]
            haystack_text = " ".join([filename, *columns])
            haystack_terms = set(_terms(haystack_text))
            haystack_terms.update(
                term.lower()
                for term in re.split(r"[^A-Za-z0-9]+", haystack_text)
                if len(term) >= 2
            )
            score = sum(1 for term in query_terms if term in haystack_terms)
            if score:
                matches.append(
                    (
                        score,
                        {
                            "filename": filename,
                            "title": str(asset.get("title") or filename),
                            "columns": columns[:20],
                            "row_count": int(asset.get("row_count") or 0),
                        },
                    )
                )
        matches.sort(key=lambda item: (-item[0], item[1]["filename"]))
        return [asset for _, asset in matches[:5]]

    def _classify(self, query: str) -> str:
        q = query.lower()
        terms = _terms(query)
        patient_location_query = any(
            marker in q for marker in ["ward", "bed", "ipd", "inpatient", "location", "located", "where"]
        )
        if patient_location_query and _has_person_name_hint(terms):
            return "patients"
        if any(marker in q for marker in ["patient", "mrn", "nhs", "date of birth", "dob"]):
            return "patients"
        if _is_staff_rota_query(query):
            return "staff_rota"
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
        return self._qualified_access_sql()

    def _qualified_access_sql(self, table_alias: str = "") -> str:
        qualifier = f"{table_alias}." if table_alias else ""
        return f"({qualifier}access_level = ANY(%s) OR {qualifier}access_level IS NULL)"

    def _query_staff_rota_rows(self, query: str, scopes: tuple[str, ...], limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        requested_dates = _requested_rota_dates(query)
        requested_groups = _requested_rota_role_groups(query)
        where_parts = [self._access_sql(), "lower(source_filename) = 'staff_rota.csv'"]
        params: list[Any] = [list(scopes)]
        if requested_dates:
            where_parts.append("row_data->>'date' = ANY(%s)")
            params.append(requested_dates)

        role_filters: list[str] = []
        if "doctor" in requested_groups:
            role_filters.extend(["%consultant%", "%physician%", "%registrar%", "%doctor%", "%clinician%"])
        if "nurse" in requested_groups:
            role_filters.append("%nurse%")
        if role_filters:
            where_parts.append(
                "(" + " OR ".join(["lower(row_data->>'role') LIKE %s" for _ in role_filters]) + ")"
            )
            params.extend(role_filters)

        department_terms = [
            term
            for term in self._search_terms(query, STOPWORDS | STAFF_ROTA_QUERY_MARKERS | DOCTOR_ROLE_MARKERS | NURSE_ROLE_MARKERS)
            if term not in {"list", "me", "available", "availability", "today", "tomorrow", "csv", "file"}
        ]
        if department_terms:
            patterns = [_like(term) for term in department_terms[:4]]
            where_parts.append(
                "("
                + " OR ".join(
                    ["lower(row_data->>'department') LIKE %s OR lower(row_data->>'staff_name') LIKE %s" for _ in patterns]
                )
                + ")"
            )
            for pattern in patterns:
                params.extend([pattern, pattern])

        params.append(limit)
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._ensure_uploaded_lookup_table(cur)
                cur.execute(
                    f"""
                    SELECT source_filename, row_number, row_data, access_level
                    FROM uploaded_lookup_rows
                    WHERE {" AND ".join(where_parts)}
                    ORDER BY row_data->>'date', row_data->>'role', row_data->>'department', row_number
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = []
                for row in cur.fetchall():
                    row_dict = dict(row)
                    payload = row_dict.get("row_data")
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except json.JSONDecodeError:
                            payload = {"value": payload}
                    rows.append(
                        {
                            "source_table": "uploaded_lookup_rows",
                            "source_filename": row_dict.get("source_filename"),
                            "row_number": row_dict.get("row_number"),
                            "row": payload,
                            "access_level": row_dict.get("access_level"),
                        }
                    )
                return rows or self._query_staff_rota_local_csv(
                    query,
                    scopes,
                    limit,
                    requested_dates=requested_dates,
                    requested_groups=requested_groups,
                    department_terms=department_terms,
                )

    def _query_staff_rota_local_csv(
        self,
        query: str,
        scopes: tuple[str, ...],
        limit: int,
        *,
        requested_dates: Sequence[str] | None = None,
        requested_groups: set[str] | None = None,
        department_terms: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.settings:
            return []
        rota_path = Path(self.settings.local_data_dir) / "raw" / "staff_rota.csv"
        if not rota_path.exists():
            return []

        dates = set(requested_dates or _requested_rota_dates(query))
        role_groups = requested_groups if requested_groups is not None else _requested_rota_role_groups(query)
        search_terms = [term.lower() for term in (department_terms or [])]

        rows: list[dict[str, Any]] = []
        with rota_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row_number, payload in enumerate(reader, start=1):
                cleaned = {str(key).strip(): str(value).strip() for key, value in payload.items() if key}
                access_level = cleaned.get("access_level") or "all_staff"
                if access_level not in scopes:
                    continue
                if dates and cleaned.get("date") not in dates:
                    continue
                role = cleaned.get("role", "").lower()
                if role_groups and not (
                    ("doctor" in role_groups and any(marker in role for marker in ["consultant", "physician", "registrar", "doctor", "clinician"]))
                    or ("nurse" in role_groups and "nurse" in role)
                ):
                    continue
                if search_terms:
                    haystack = " ".join(
                        [
                            cleaned.get("department", ""),
                            cleaned.get("staff_name", ""),
                            cleaned.get("role", ""),
                            cleaned.get("contact", ""),
                        ]
                    ).lower()
                    if not any(term in haystack for term in search_terms):
                        continue
                rows.append(
                    {
                        "source_table": "local_csv",
                        "source_filename": "staff_rota.csv",
                        "row_number": row_number,
                        "row": cleaned,
                        "access_level": access_level,
                    }
                )
                if len(rows) >= limit:
                    break
        return rows

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

    def _query_patients(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int, stopwords: set[str] | None = None):
        pattern = _like(_best_search_term(terms, stopwords))
        cur.execute(
            f"""
            SELECT p.patient_id, p.mrn, p.nhs_number, p.full_name, p.date_of_birth, p.ward_code,
                   w.ward_name, w.floor AS ward_floor, w.nurse_in_charge, w.phone AS ward_phone,
                   p.department_name, p.named_consultant, p.care_status, p.risk_flags, p.access_level
            FROM patients p
            LEFT JOIN wards w ON w.ward_code = p.ward_code
            WHERE {self._qualified_access_sql("p")}
              AND (%s = '%%' OR lower(p.full_name) LIKE %s OR lower(p.mrn) LIKE %s OR lower(p.nhs_number) LIKE %s)
            ORDER BY p.full_name
            LIMIT %s
            """,
            (list(scopes), pattern, pattern, pattern, pattern, limit),
        )
        return list(cur.fetchall())

    def _query_doctors(self, cur, query: str, terms: list[str], scopes: tuple[str, ...], limit: int, stopwords: set[str] | None = None):
        pattern = _like(_best_search_term(terms, stopwords))
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

    def _query_departments(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int, stopwords: set[str] | None = None):
        pattern = _like(_best_search_term(terms, stopwords))
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

    def _query_contacts(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int, stopwords: set[str] | None = None):
        pattern = _like(_best_search_term(terms, stopwords))
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

    def _query_appointments(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int, stopwords: set[str] | None = None):
        pattern = _like(_best_search_term(terms, stopwords))
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

    def _query_wards(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int, stopwords: set[str] | None = None):
        pattern = _like(_best_search_term(terms, stopwords))
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

    def _query_formulary(self, cur, terms: list[str], scopes: tuple[str, ...], limit: int, stopwords: set[str] | None = None):
        pattern = _like(_best_search_term(terms, stopwords))
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

    def _query_directory(self, cur, primary: str, scopes: tuple[str, ...], limit: int, stopwords: set[str] | None = None):
        pattern = _like(primary if primary and primary not in (stopwords or set()) else "")
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
