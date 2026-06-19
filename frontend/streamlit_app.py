import os
from typing import Any

import requests
import streamlit as st


BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
KNOWN_ROLES = ["admin", "staff", "doctor", "nurse", "pharmacy", "clinical_governance", "manager"]
MIN_PASSWORD_LENGTH = 8


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


def get_json(path: str) -> dict[str, Any] | list[dict[str, Any]]:
    response = requests.get(f"{BACKEND_URL}{path}", headers=api_headers(), timeout=30)
    raise_for_api_error(response)
    return response.json()


def store_login(data: dict[str, Any]) -> None:
    st.session_state.access_token = data["access_token"]
    st.session_state.username = data.get("username")
    st.session_state.roles = data.get("roles", [])
    st.session_state.departments = data.get("departments", [])
    st.session_state.password_change_required = data.get("password_change_required", False)


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


def render_admin_documents() -> None:
    st.header("Documents")
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

                document_rows = []
                for document in result.get("documents", []):
                    metadata = document.get("metadata") or {}
                    document_rows.append(
                        {
                            "File": document.get("title") or str(document.get("key", "")).rsplit("/", 1)[-1],
                            "S3 key": document.get("key", ""),
                            "Chunks": document.get("chunk_count", 0),
                            "Category": metadata.get("domain", "general"),
                            "Type": metadata.get("document_type", "document"),
                            "Status": document.get("ingestion_status", "indexed"),
                        }
                    )
                if document_rows:
                    st.dataframe(document_rows, hide_index=True, use_container_width=True)
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


def render_response_details(metadata: dict[str, Any]) -> None:
    sources = metadata.get("sources") or []
    if sources:
        source_rows = []
        for source in sources:
            source_metadata = source.get("metadata") or {}
            source_rows.append(
                {
                    "Title": source.get("title", ""),
                    "URI": source.get("uri", ""),
                    "Score": source.get("score"),
                    "Strategy": source_metadata.get("_retrieval_strategy", ""),
                    "Chunk": source_metadata.get("_chunk_index", ""),
                    "Category": source_metadata.get("domain", ""),
                    "Snippet": source.get("snippet", ""),
                }
            )
        st.dataframe(source_rows, hide_index=True, use_container_width=True)
    st.json(metadata)


st.set_page_config(page_title="Dstrmaysam Healthcare Knowledge Agent", page_icon=None, layout="wide")
st.title("Dstrmaysam Healthcare Knowledge Agent")

if "access_token" not in st.session_state:
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        try:
            data = post_json("/auth/login", {"username": username, "password": password})
            store_login(data)
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
            st.session_state.clear()
            st.rerun()
    render_password_change()
    st.stop()

with st.sidebar:
    st.caption(f"Signed in as {st.session_state.get('username') or 'user'}")
    selected_view = "Chat"
    if "admin" in st.session_state.get("roles", []):
        selected_view = st.radio("View", ["Chat", "Users", "Documents"], key="selected-view")
    if selected_view == "Chat" and st.button("New chat"):
        st.session_state.session_id = None
        st.session_state.messages = []
        st.rerun()
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()

    if selected_view == "Chat":
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

if selected_view == "Users":
    render_admin_users()
    st.stop()

if selected_view == "Documents":
    render_admin_documents()
    st.stop()

for message in st.session_state.get("messages", []):
    role = "assistant" if message.get("role") == "assistant" else "user"
    with st.chat_message(role):
        st.markdown(message.get("content", ""))
        metadata = message.get("metadata") or {}
        if role == "assistant" and metadata:
            with st.expander("Response details"):
                render_response_details(metadata)

query = st.chat_input("Ask a question about healthcare knowledge")
if query:
    st.session_state.setdefault("messages", []).append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        with st.spinner("Thinking with knowledge context..."):
            try:
                payload = {"query": query, "session_id": st.session_state.get("session_id")}
                data = post_json("/chat", payload)
                st.session_state.session_id = data["session_id"]
                metadata = {
                    "sources": data.get("sources", []),
                    "tools_used": data.get("tools_used", []),
                    "input_tokens": data.get("input_tokens"),
                    "output_tokens": data.get("output_tokens"),
                    "latency_ms": data.get("latency_ms"),
                    "trace_id": data.get("trace_id"),
                    "safety": data.get("safety", {}),
                    "audit_event": data.get("audit_event", {}),
                    "performance": data.get("performance", {}),
                }
                st.markdown(data["answer"])
                with st.expander("Response details"):
                    render_response_details(metadata)
                st.session_state.messages.append(
                    {"role": "assistant", "content": data["answer"], "metadata": metadata}
                )
            except Exception as exc:
                st.error(f"Chat failed: {exc}")
