import json
import os
from typing import Any

import requests
import streamlit as st
import streamlit.components.v1 as components


BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
KNOWN_ROLES = ["admin", "staff", "doctor", "nurse", "pharmacy", "clinical_governance", "manager"]
MIN_PASSWORD_LENGTH = 8
AUTH_COOKIE_NAME = "hka_access_token"
AUTH_COOKIE_DEFAULT_MAX_AGE_SECONDS = 3600


def _set_auth_cookie(token: str, max_age_seconds: int) -> None:
    components.html(
        f"""
        <script>
        const cookieName = {json.dumps(AUTH_COOKIE_NAME)};
        const token = {json.dumps(token)};
        const maxAge = {int(max_age_seconds)};
        document.cookie = cookieName + "=" + encodeURIComponent(token)
            + "; Max-Age=" + maxAge + "; Path=/; SameSite=Lax";
        </script>
        """,
        height=0,
    )


def _clear_auth_cookie(*, reload_parent: bool = False) -> None:
    reload_script = "setTimeout(() => window.parent.location.reload(), 50);" if reload_parent else ""
    components.html(
        f"""
        <script>
        const cookieName = {json.dumps(AUTH_COOKIE_NAME)};
        document.cookie = cookieName + "=; Max-Age=0; Path=/; SameSite=Lax";
        {reload_script}
        </script>
        """,
        height=0,
    )


def _read_auth_cookie() -> str | None:
    try:
        value = st.context.cookies.get(AUTH_COOKIE_NAME)
        return str(value) if value else None
    except Exception:
        return None


def sync_auth_cookie() -> None:
    token = st.session_state.get("access_token")
    if not token:
        return
    max_age = int(st.session_state.get("access_token_expires_in") or AUTH_COOKIE_DEFAULT_MAX_AGE_SECONDS)
    _set_auth_cookie(str(token), max_age)


def store_user_context(data: dict[str, Any]) -> None:
    st.session_state.username = data.get("username")
    st.session_state.roles = data.get("roles", [])
    st.session_state.departments = data.get("departments", [])
    st.session_state.password_change_required = data.get("password_change_required", False)


def restore_login_from_cookie() -> None:
    if st.session_state.get("access_token"):
        return
    token = _read_auth_cookie()
    if not token:
        return
    st.session_state.access_token = token
    try:
        data = get_json("/auth/me")
        if isinstance(data, dict):
            store_user_context(data)
        st.session_state.setdefault("session_id", None)
        st.session_state.setdefault("messages", [])
        warm_document_manifest_cache()
    except Exception:
        for key in (
            "access_token",
            "access_token_expires_in",
            "username",
            "roles",
            "departments",
            "password_change_required",
        ):
            st.session_state.pop(key, None)
        _clear_auth_cookie()


def sign_out() -> None:
    _clear_auth_cookie(reload_parent=True)
    st.session_state.clear()
    st.stop()


def api_headers() -> dict[str, str]:
    token = st.session_state.get("access_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def raise_for_api_error(response: requests.Response) -> None:
    if response.ok:
        return
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = response.text
    if isinstance(detail, list):
        detail = "; ".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
    raise RuntimeError(f"{response.status_code}: {detail or response.reason}")


def post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{BACKEND_URL}{path}",
        json=payload,
        headers=api_headers(),
        timeout=120,
    )
    raise_for_api_error(response)
    return response.json()


def post_file(path: str, field_name: str, filename: str, data: bytes, content_type: str) -> dict[str, Any]:
    response = requests.post(
        f"{BACKEND_URL}{path}",
        files={field_name: (filename, data, content_type)},
        headers=api_headers(),
        timeout=120,
    )
    raise_for_api_error(response)
    return response.json()


def patch_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.patch(
        f"{BACKEND_URL}{path}",
        json=payload,
        headers=api_headers(),
        timeout=30,
    )
    raise_for_api_error(response)
    return response.json()


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
    response = requests.get(f"{BACKEND_URL}{path}", params=params, headers=api_headers(), timeout=30)
    raise_for_api_error(response)
    return response.json()


def warm_document_manifest_cache() -> None:
    if st.session_state.get("password_change_required"):
        return
    try:
        documents = get_json("/documents")
        st.session_state.document_cache = list(documents) if isinstance(documents, list) else []
        st.session_state.document_cache_loaded = True
        st.session_state.document_cache_error = None
    except Exception as exc:
        st.session_state.document_cache = []
        st.session_state.document_cache_loaded = False
        st.session_state.document_cache_error = str(exc)


def store_login(data: dict[str, Any]) -> None:
    st.session_state.access_token = data["access_token"]
    st.session_state.access_token_expires_in = data.get("expires_in", AUTH_COOKIE_DEFAULT_MAX_AGE_SECONDS)
    store_user_context(data)


def parse_departments(raw: str) -> list[str]:
    departments = []
    for item in raw.split(","):
        value = item.strip().lower()
        if value and value not in departments:
            departments.append(value)
    return departments


def render_password_change() -> None:
    st.subheader("Change password")
    with st.form("change-password"):
        current_password = st.text_input("Current password", type="password")
        new_password = st.text_input("New password", type="password")
        confirm_password = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update password")
    if submitted:
        if new_password != confirm_password:
            st.error("New passwords do not match")
            return
        if len(new_password) < MIN_PASSWORD_LENGTH:
            st.error(f"New password must be at least {MIN_PASSWORD_LENGTH} characters")
            return
        try:
            data = post_json(
                "/auth/change-password",
                {"current_password": current_password, "new_password": new_password},
            )
            store_login(data)
            warm_document_manifest_cache()
            st.success("Password updated")
            st.rerun()
        except Exception as exc:
            st.error(f"Password update failed: {exc}")


def render_admin_users() -> None:
    st.header("Users")
    with st.expander("Create user", expanded=True):
        with st.form("create-user"):
            username = st.text_input("Username")
            temporary_password = st.text_input("Temporary password", type="password")
            roles = st.multiselect("Roles", KNOWN_ROLES, default=["staff"])
            departments = st.text_input("Departments", placeholder="clinical_governance, operations")
            submitted = st.form_submit_button("Create user")
        if submitted:
            if len(temporary_password) < MIN_PASSWORD_LENGTH:
                st.error(f"Temporary password must be at least {MIN_PASSWORD_LENGTH} characters")
                return
            if not roles:
                st.error("Select at least one role")
                return
            try:
                post_json(
                    "/admin/users",
                    {
                        "username": username,
                        "temporary_password": temporary_password,
                        "roles": roles,
                        "departments": parse_departments(departments),
                    },
                )
                st.success("User created")
                st.rerun()
            except Exception as exc:
                st.error(f"Create user failed: {exc}")

    try:
        users = get_json("/admin/users")
    except Exception as exc:
        st.error(f"Unable to load users: {exc}")
        return

    for user in users:
        username = user["username"]
        with st.expander(username):
            st.caption("Password change required" if user.get("password_change_required") else "Password current")
            with st.form(f"profile-{username}"):
                selected_roles = st.multiselect(
                    "Roles",
                    KNOWN_ROLES,
                    default=[role for role in user.get("roles", []) if role in KNOWN_ROLES],
                    key=f"roles-{username}",
                )
                departments = st.text_input(
                    "Departments",
                    value=", ".join(user.get("departments", [])),
                    key=f"departments-{username}",
                )
                save_profile = st.form_submit_button("Save profile")
            if save_profile:
                try:
                    patch_json(
                        f"/admin/users/{username}",
                        {
                            "roles": selected_roles,
                            "departments": parse_departments(departments),
                        },
                    )
                    st.success("Profile saved")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Save profile failed: {exc}")

            with st.form(f"reset-{username}"):
                temporary_password = st.text_input(
                    "Temporary password",
                    type="password",
                    key=f"reset-password-{username}",
                )
                reset_password = st.form_submit_button("Reset password")
            if reset_password:
                if len(temporary_password) < MIN_PASSWORD_LENGTH:
                    st.error(f"Temporary password must be at least {MIN_PASSWORD_LENGTH} characters")
                    return
                try:
                    post_json(
                        f"/admin/users/{username}/reset-password",
                        {"temporary_password": temporary_password},
                    )
                    st.success("Password reset")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Password reset failed: {exc}")


def count_rows(counts: dict[str, Any], label: str) -> list[dict[str, Any]]:
    return [
        {label: str(name), "Count": int(value)}
        for name, value in sorted(counts.items(), key=lambda item: int(item[1]), reverse=True)
    ]


def latency_rows(values: dict[str, Any], labels: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for key, label in labels.items():
        try:
            value = int(values.get(key) or 0)
        except Exception:
            value = 0
        if value:
            rows.append({"Phase": label, "Latency ms": value})
    rows.sort(key=lambda row: int(row["Latency ms"]), reverse=True)
    return rows


def latency_section_rows(sections: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for section, metrics in sections.items():
        if not isinstance(metrics, dict):
            continue
        for metric, value in metrics.items():
            if isinstance(value, (dict, list)):
                display_value = json.dumps(value)
            else:
                display_value = str(value)
            rows.append(
                {
                    "Section": str(section).replace("_", " ").title(),
                    "Metric": str(metric),
                    "Value": display_value,
                }
            )
    return rows


def raw_latency_rows(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for metric, value in sorted(metrics.items()):
        try:
            latency_ms = int(value or 0)
        except Exception:
            continue
        rows.append({"Metric": str(metric), "Latency ms": latency_ms})
    rows.sort(key=lambda row: int(row["Latency ms"]), reverse=True)
    return rows


def counter_rows(counters: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for metric, value in sorted(counters.items()):
        try:
            count = int(value or 0)
        except Exception:
            continue
        rows.append({"Metric": str(metric), "Value": count})
    return rows


def tool_latency_rows(tool_timings: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for item in tool_timings:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "Tool": item.get("tool", ""),
                "Total ms": int(item.get("total_ms") or 0),
                "Catalog ms": int(item.get("catalog_ms") or 0),
                "Retrieval ms": int(item.get("retrieval_search_ms") or 0),
                "Embedding ms": int(item.get("embedding_ms") or 0),
                "OpenSearch ms": int(item.get("opensearch_ms") or 0),
                "Neighbor ms": int(item.get("neighbor_ms") or 0),
                "Access filter ms": int(item.get("access_filter_ms") or 0),
                "Vector hits": int(item.get("vector_hits") or 0),
                "Keyword hits": int(item.get("keyword_hits") or 0),
                "Neighbor hits": int(item.get("neighbor_hits") or 0),
                "Returned hits": int(item.get("returned_hits") or 0),
            }
        )
    return rows


def render_admin_dashboard() -> None:
    st.header("Dashboard")
    try:
        payload = get_json("/admin/dashboard?limit=200")
    except Exception as exc:
        st.error(f"Unable to load dashboard: {exc}")
        return

    if not isinstance(payload, dict):
        st.error("Unexpected dashboard response")
        return

    summary = payload.get("summary") or {}
    queries = payload.get("queries") or []
    ragas_summary = summary.get("ragas") or {}
    metric_columns = st.columns(8)
    metric_columns[0].metric("Queries", summary.get("total_queries", 0))
    metric_columns[1].metric("Users", summary.get("unique_users", 0))
    metric_columns[2].metric("Avg latency", f"{summary.get('avg_latency_ms', 0)} ms")
    metric_columns[3].metric("Max latency", f"{summary.get('max_latency_ms', 0)} ms")
    metric_columns[4].metric("Avg tokens", summary.get("avg_total_tokens", 0))
    metric_columns[5].metric("Avg sources", f"{float(summary.get('avg_sources_per_query', 0)):.1f}")
    metric_columns[6].metric("Avg faithfulness", format_score(ragas_summary.get("ragas_faithfulness")))
    metric_columns[7].metric("Guardrails", summary.get("guardrail_trigger_count", 0))

    st.divider()
    chart_columns = st.columns(3)
    tool_rows = count_rows(summary.get("tool_flow_counts") or summary.get("tool_counts") or {}, "Tool")
    user_rows = count_rows(summary.get("user_counts") or {}, "User")
    model_rows = count_rows(summary.get("model_counts") or {}, "Model")
    with chart_columns[0]:
        st.subheader("Tool flow")
        if tool_rows:
            st.bar_chart(tool_rows, x="Tool", y="Count")
        else:
            st.caption("No tool calls yet")
    with chart_columns[1]:
        st.subheader("Users")
        if user_rows:
            st.bar_chart(user_rows, x="User", y="Count")
        else:
            st.caption("No user activity yet")
    with chart_columns[2]:
        st.subheader("Models")
        if model_rows:
            st.bar_chart(model_rows, x="Model", y="Count")
        else:
            st.caption("No model activity yet")

    st.divider()
    st.subheader("Per-query details")
    query_rows = []
    for item in queries:
        tools = item.get("tools_used") or []
        query_rows.append(
            {
                "Time": item.get("created_at", ""),
                "User": item.get("user_id", ""),
                "Query": item.get("query", ""),
                "Model": item.get("model", ""),
                "Tools": ", ".join(tools),
                "Flow": item.get("tool_flow_summary") or " -> ".join(tools),
                "Sources": item.get("source_count", 0),
                "Tokens": item.get("total_tokens", 0),
                "Latency ms": item.get("latency_ms", 0),
                "Latency breakdown": item.get("latency_breakdown_summary", ""),
                "Faithfulness": format_score((item.get("ragas") or {}).get("ragas_faithfulness")),
                "Relevancy": format_score((item.get("ragas") or {}).get("ragas_answer_relevancy")),
                "Context precision": format_score((item.get("ragas") or {}).get("ragas_context_precision")),
                "Context recall": format_score((item.get("ragas") or {}).get("ragas_context_recall")),
                "RAGAS status": item.get("ragas_status") or "",
                "Trace": item.get("trace_id", ""),
                "Guardrail": item.get("guardrail_applied", False),
            }
        )
    if query_rows:
        st.dataframe(query_rows, hide_index=True, use_container_width=True)
    else:
        st.info("No chat queries found yet.")

    for index, item in enumerate(queries[:25]):
        label = f"{item.get('user_id', 'user')} - {str(item.get('query', ''))[:80]}"
        with st.expander(label):
            detail_columns = st.columns(5)
            detail_columns[0].metric("Latency", f"{item.get('latency_ms', 0)} ms")
            detail_columns[1].metric("Sources", item.get("source_count", 0))
            detail_columns[2].metric("Input tokens", item.get("input_tokens") or 0)
            detail_columns[3].metric("Output tokens", item.get("output_tokens") or 0)
            detail_columns[4].metric("Total tokens", item.get("total_tokens") or 0)
            st.caption(f"Trace ID: {item.get('trace_id') or 'unavailable'}")
            st.caption(f"Session ID: {item.get('session_id')}")
            st.markdown("**Question**")
            st.write(item.get("query", ""))
            st.markdown("**Answer**")
            st.write(item.get("answer", ""))
            latency_breakdown = item.get("latency_breakdown") if isinstance(item.get("latency_breakdown"), dict) else {}
            if latency_breakdown:
                top_level = latency_breakdown.get("top_level") if isinstance(latency_breakdown.get("top_level"), dict) else {}
                agent_detail = latency_breakdown.get("agent_detail") if isinstance(latency_breakdown.get("agent_detail"), dict) else {}
                top_level_rows = latency_rows(
                    top_level,
                    {
                        "agent_execution_ms": "Agent execution",
                        "history_load_ms": "History load",
                        "trace_setup_ms": "Langfuse trace setup",
                        "prompt_load_ms": "Prompt load",
                        "initial_safety_ms": "Initial safety",
                        "response_guardrail_ms": "Response guardrail",
                        "final_safety_ms": "Final safety",
                        "history_save_ms": "History save",
                        "unattributed_ms": "Other",
                    },
                )
                agent_rows = latency_rows(
                    agent_detail,
                    {
                        "llm_total_ms": "LLM calls",
                        "llm_tool_choice_ms": "LLM tool choice",
                        "llm_final_ms": "LLM final answer",
                        "llm_direct_answer_ms": "LLM direct answer",
                        "llm_setup_ms": "LLM setup",
                        "fast_llm_setup_ms": "Fast LLM setup",
                        "langfuse_callbacks_ms": "Langfuse callbacks",
                        "catalog_ms": "Document catalog",
                        "retrieval_search_ms": "Retrieval search",
                        "embedding_ms": "Embedding",
                        "opensearch_ms": "OpenSearch",
                        "neighbor_ms": "Neighbor chunks",
                        "access_filter_ms": "Access filtering",
                    },
                )
                timing_columns = st.columns(2)
                with timing_columns[0]:
                    st.markdown("**Latency breakdown**")
                    if top_level_rows:
                        st.dataframe(top_level_rows, hide_index=True, use_container_width=True)
                    else:
                        st.caption("No phase timings captured.")
                with timing_columns[1]:
                    st.markdown("**Agent detail**")
                    if agent_rows:
                        st.dataframe(agent_rows, hide_index=True, use_container_width=True)
                    else:
                        st.caption("No agent detail timings captured.")
                tool_rows = tool_latency_rows(latency_breakdown.get("tool_timings") or [])
                if tool_rows:
                    st.markdown("**Tool timings**")
                    st.dataframe(tool_rows, hide_index=True, use_container_width=True)
                section_rows = latency_section_rows(
                    latency_breakdown.get("sections")
                    if isinstance(latency_breakdown.get("sections"), dict)
                    else {}
                )
                if section_rows:
                    st.markdown("**Detailed latency sections**")
                    st.dataframe(section_rows, hide_index=True, use_container_width=True)
                raw_rows = raw_latency_rows(
                    latency_breakdown.get("raw_timing_metrics")
                    if isinstance(latency_breakdown.get("raw_timing_metrics"), dict)
                    else {}
                )
                if raw_rows:
                    st.markdown("**All captured timing metrics**")
                    st.dataframe(raw_rows, hide_index=True, use_container_width=True)
                total_rows = counter_rows(
                    latency_breakdown.get("tool_timing_totals")
                    if isinstance(latency_breakdown.get("tool_timing_totals"), dict)
                    else {}
                )
                if total_rows:
                    st.markdown("**Tool totals and hit counts**")
                    st.dataframe(total_rows, hide_index=True, use_container_width=True)
            st.markdown("**Tools and source keys**")
            st.json(
                {
                    "tools_used": item.get("tools_used", []),
                    "tool_flow": item.get("tool_flow", []),
                    "source_document_keys": item.get("source_document_keys", []),
                    "latency_breakdown": item.get("latency_breakdown", {}),
                    "agent_mode": item.get("agent_mode"),
                    "ragas": item.get("ragas", {}),
                    "ragas_status": item.get("ragas_status"),
                    "ragas_provider": item.get("ragas_provider"),
                    "langfuse_ragas_published": item.get("langfuse_ragas_published"),
                    "guardrail_applied": item.get("guardrail_applied"),
                    "guardrail_reason": item.get("guardrail_reason"),
                    "safety": item.get("safety", {}),
                }
            )


def patient_detail_table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table_rows = []
    for row in rows:
        appointment_parts = [
            str(row.get("appointment_date") or ""),
            str(row.get("appointment_time") or ""),
        ]
        appointment = " ".join(part for part in appointment_parts if part).strip()
        table_rows.append(
            {
                "Table": row.get("table", ""),
                "Patient ID": row.get("patient_id", ""),
                "MRN": row.get("mrn", ""),
                "Name": row.get("patient_name", ""),
                "DOB": row.get("date_of_birth", ""),
                "Department": row.get("department_name", ""),
                "Ward": row.get("ward_code", ""),
                "Care status": row.get("care_status", ""),
                "Risk flags": row.get("risk_flags", ""),
                "Appointment": appointment,
                "Clinic": row.get("clinic_name", ""),
                "Clinician": row.get("clinician_name") or row.get("named_consultant", ""),
                "Status": row.get("status", ""),
            }
        )
    return table_rows


def render_patient_details_dashboard() -> None:
    st.header("Patient Details")
    with st.form("patient-details-filters"):
        first_row = st.columns([2, 1, 1])
        query = first_row[0].text_input("Search", placeholder="Name, MRN, NHS number, consultant, clinic")
        patient_identifier = first_row[1].text_input("Patient ID / MRN / NHS")
        limit = first_row[2].number_input("Limit", min_value=1, max_value=250, value=50, step=10)

        second_row = st.columns([1, 1, 1, 1])
        department = second_row[0].text_input("Department")
        ward = second_row[1].text_input("Ward")
        care_status = second_row[2].text_input("Care status")
        selected_tables = second_row[3].multiselect(
            "Tables",
            ["patients", "appointments"],
            default=["patients", "appointments"],
        )
        submitted = st.form_submit_button("Apply filters")

    if submitted or "patient_details_payload" not in st.session_state:
        params = {
            "q": query,
            "patient_identifier": patient_identifier,
            "department": department,
            "ward": ward,
            "care_status": care_status,
            "tables": selected_tables,
            "limit": int(limit),
        }
        try:
            payload = get_json("/admin/patient-details", params=params)
            st.session_state.patient_details_payload = payload if isinstance(payload, dict) else {}
            st.session_state.patient_details_error = None
        except Exception as exc:
            st.session_state.patient_details_payload = {}
            st.session_state.patient_details_error = str(exc)

    if st.session_state.get("patient_details_error"):
        st.error(f"Unable to load patient details: {st.session_state.patient_details_error}")
        return

    payload = st.session_state.get("patient_details_payload") or {}
    summary = payload.get("summary") or {}
    rows = payload.get("rows") or []
    metric_columns = st.columns(4)
    metric_columns[0].metric("Rows", summary.get("row_count", 0))
    metric_columns[1].metric("Patients", summary.get("unique_patients", 0))
    metric_columns[2].metric("Patient records", (summary.get("table_counts") or {}).get("patients", 0))
    metric_columns[3].metric("Appointments", (summary.get("table_counts") or {}).get("appointments", 0))

    st.caption(f"Access scopes applied: {', '.join(payload.get('access_scopes_applied') or [])}")
    table_rows = patient_detail_table_rows(rows)
    if table_rows:
        st.dataframe(table_rows, hide_index=True, use_container_width=True)
    else:
        st.info(summary.get("message") or "No matching patient rows found.")

    with st.expander("Raw database rows"):
        st.json(rows)


def document_table_rows(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for document in documents:
        metadata = document.get("metadata") or {}
        key = document.get("key") or str(document.get("uri", "")).split("/", 3)[-1]
        rows.append(
            {
                "File": document.get("title") or str(key).rsplit("/", 1)[-1],
                "Key": key,
                "Chunks": document.get("chunk_count", 0),
                "Category": metadata.get("domain", "general"),
                "Type": metadata.get("document_type", "document"),
                "Status": document.get("ingestion_status") or "indexed",
                "URI": document.get("uri", ""),
            }
        )
    return rows


def render_documents_table(documents: list[dict[str, Any]]) -> None:
    rows = document_table_rows(documents)
    st.subheader("Indexed documents")
    if not rows:
        st.info("No indexed documents found. Upload files and run ingestion to create searchable chunks.")
        return
    metric_columns = st.columns(3)
    metric_columns[0].metric("Documents", len(rows))
    metric_columns[1].metric("Total chunks", sum(int(row.get("Chunks") or 0) for row in rows))
    metric_columns[2].metric(
        "Categories",
        len({str(row.get("Category") or "general") for row in rows}),
    )
    st.dataframe(rows, hide_index=True, use_container_width=True)


def render_admin_documents() -> None:
    st.header("Documents")
    current_documents: list[dict[str, Any]] = list(st.session_state.get("document_cache", []))
    if not st.session_state.get("document_cache_loaded"):
        warm_document_manifest_cache()
        current_documents = list(st.session_state.get("document_cache", []))
    if st.session_state.get("document_cache_error"):
        st.error(f"Unable to load indexed documents: {st.session_state.document_cache_error}")

    uploaded_files = st.file_uploader(
        "Upload documents to S3",
        type=["pdf", "docx", "txt", "md", "csv"],
        accept_multiple_files=True,
    )
    if st.button("Upload selected files", disabled=not uploaded_files):
        uploaded_count = 0
        for uploaded_file in uploaded_files or []:
            try:
                result = post_file(
                    "/admin/documents/upload",
                    "file",
                    uploaded_file.name,
                    uploaded_file.getvalue(),
                    uploaded_file.type or "application/octet-stream",
                )
                uploaded_count += 1
                st.caption(f"Uploaded {result['key']}")
            except Exception as exc:
                st.error(f"Upload failed for {uploaded_file.name}: {exc}")
        if uploaded_count:
            st.success(f"Uploaded {uploaded_count} file(s)")

    st.divider()
    if st.button("Refresh indexed documents"):
        warm_document_manifest_cache()
        st.rerun()
    render_documents_table(current_documents)
    st.divider()
    st.subheader("Ingest and index")
    if st.button("Run ingestion and indexing"):
        with st.spinner("Ingesting S3 documents and updating the search index..."):
            try:
                result = post_json("/admin/documents/ingest", {})
                st.success("Ingestion complete")
                metric_columns = st.columns(5)
                metric_columns[0].metric("Documents", len(result.get("documents", [])))
                metric_columns[1].metric("New chunks", result.get("indexed_chunks", 0))
                metric_columns[2].metric("Total chunks", result.get("total_chunks", 0))
                metric_columns[3].metric("Skipped", result.get("skipped_documents", 0))
                metric_columns[4].metric("Removed", result.get("deleted_documents", 0))
                st.session_state.document_cache = list(result.get("documents", []))
                st.session_state.document_cache_loaded = True
                st.session_state.document_cache_error = None
                render_documents_table(st.session_state.document_cache)
                if result.get("force_reindex"):
                    st.caption(
                        "Re-indexed unchanged files because the OpenSearch index changed "
                        f"from {result.get('previous_opensearch_index') or 'unknown'} "
                        f"to {result.get('opensearch_index') or 'current index'}."
                    )
                if result.get("deleted_chunks"):
                    st.caption(f"Deleted {result.get('deleted_chunks')} stale indexed chunk(s)")
            except Exception as exc:
                st.error(f"Ingestion failed: {exc}")

    st.divider()
    with st.expander("Delete all indexes"):
        st.warning(
            "This clears search/vector indexes and the document manifest. "
            "Uploaded source files and deterministic Postgres CSV rows are preserved."
        )
        with st.form("delete-all-indexes"):
            admin_password = st.text_input("Admin password", type="password")
            confirm_delete = st.checkbox("I understand this will clear all indexed document entries")
            delete_submitted = st.form_submit_button("Delete all indexes")
        if delete_submitted:
            if not admin_password:
                st.error("Enter your admin password")
                return
            if not confirm_delete:
                st.error("Confirm that you understand the index entries will be cleared")
                return
            try:
                result = post_json(
                    "/admin/documents/delete-indexes",
                    {"admin_password": admin_password},
                )
                st.success(
                    f"Deleted {result.get('deleted_chunks', 0)} indexed chunk(s) "
                    f"from {result.get('backend', 'search')} and cleared the manifest."
                )
                st.session_state.document_cache = []
                st.session_state.document_cache_loaded = True
                st.session_state.document_cache_error = None
                st.rerun()
            except Exception as exc:
                st.error(f"Delete indexes failed: {exc}")


def format_score(value: Any) -> str:
    try:
        if value is None:
            return "-"
        return f"{float(value):.2f}"
    except Exception:
        return "-"


def submit_chat_query(query: str) -> None:
    payload = {"query": query, "session_id": st.session_state.get("session_id")}
    data = post_json("/chat", payload)
    st.session_state.session_id = data["session_id"]
    st.session_state.messages.append({"role": "assistant", "content": data["answer"]})


def render_chat_messages(show_thinking: bool = False) -> None:
    with st.container(height=620, border=True):
        messages = st.session_state.get("messages", [])
        if not messages:
            st.info("Ask a question about healthcare knowledge.")
        for message in messages:
            role = "assistant" if message.get("role") == "assistant" else "user"
            with st.chat_message(role):
                st.markdown(message.get("content", ""))
        if show_thinking:
            with st.chat_message("assistant"):
                with st.spinner("Thinking with knowledge context..."):
                    st.write("Preparing answer...")


def render_chat_page() -> None:
    chat_window = st.empty()
    with chat_window:
        render_chat_messages()

    with st.form("chat-query-form", clear_on_submit=True):
        input_columns = st.columns([8, 1])
        query = input_columns[0].text_input(
            "Message",
            placeholder="Ask a question about healthcare knowledge",
            label_visibility="collapsed",
        )
        submitted = input_columns[1].form_submit_button("Send", use_container_width=True)

    if submitted:
        cleaned_query = query.strip()
        if not cleaned_query:
            return
        st.session_state.setdefault("messages", []).append({"role": "user", "content": cleaned_query})
        with chat_window:
            render_chat_messages(show_thinking=True)
        try:
            submit_chat_query(cleaned_query)
            st.rerun()
        except Exception as exc:
            st.error(f"Chat failed: {exc}")


st.set_page_config(page_title="Dstrmaysam Healthcare Knowledge Agent", page_icon=None, layout="wide")
st.title("Dstrmaysam Healthcare Knowledge Agent")
restore_login_from_cookie()
sync_auth_cookie()

if "access_token" not in st.session_state:
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        try:
            data = post_json("/auth/login", {"username": username, "password": password})
            store_login(data)
            warm_document_manifest_cache()
            st.session_state.session_id = None
            st.session_state.messages = []
            st.rerun()
        except Exception as exc:
            st.error(f"Login failed: {exc}")
    st.stop()

if st.session_state.get("password_change_required"):
    with st.sidebar:
        st.caption(f"Signed in as {st.session_state.get('username') or 'user'}")
        if st.button("Sign out"):
            sign_out()
    render_password_change()
    st.stop()

with st.sidebar:
    st.caption(f"Signed in as {st.session_state.get('username') or 'user'}")
    if st.button("New chat"):
        st.session_state.session_id = None
        st.session_state.messages = []
        st.rerun()
    if st.button("Sign out"):
        sign_out()

    try:
        sessions = get_json("/chat/sessions")
        if sessions:
            st.divider()
            st.caption("Previous chats")
            for session in sessions[:20]:
                label = session.get("title") or session["session_id"]
                if st.button(label, key=f"session-{session['session_id']}"):
                    detail = get_json(f"/chat/sessions/{session['session_id']}")
                    st.session_state.session_id = session["session_id"]
                    st.session_state.messages = detail.get("messages", [])
                    st.rerun()
    except Exception:
        st.caption("Chat history unavailable")

if "admin" in st.session_state.get("roles", []):
    chat_tab, dashboard_tab, patient_tab, users_tab, documents_tab = st.tabs(
        ["Chat", "Dashboard", "Patient Details", "Users", "Documents"]
    )
    with chat_tab:
        render_chat_page()
    with dashboard_tab:
        render_admin_dashboard()
    with patient_tab:
        render_patient_details_dashboard()
    with users_tab:
        render_admin_users()
    with documents_tab:
        render_admin_documents()
else:
    render_chat_page()
