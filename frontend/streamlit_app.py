import os
from typing import Any

import requests
import streamlit as st


BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
KNOWN_ROLES = ["admin", "staff", "doctor", "nurse", "pharmacy", "clinical_governance", "manager"]


def api_headers() -> dict[str, str]:
    token = st.session_state.get("access_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{BACKEND_URL}{path}",
        json=payload,
        headers=api_headers(),
        timeout=120,
    )
    response.raise_for_status()
    return response.json()


def patch_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.patch(
        f"{BACKEND_URL}{path}",
        json=payload,
        headers=api_headers(),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def get_json(path: str) -> dict[str, Any] | list[dict[str, Any]]:
    response = requests.get(f"{BACKEND_URL}{path}", headers=api_headers(), timeout=30)
    response.raise_for_status()
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
                try:
                    post_json(
                        f"/admin/users/{username}/reset-password",
                        {"temporary_password": temporary_password},
                    )
                    st.success("Password reset")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Password reset failed: {exc}")


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
        selected_view = st.radio("View", ["Chat", "Users"], key="selected-view")
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

for message in st.session_state.get("messages", []):
    role = "assistant" if message.get("role") == "assistant" else "user"
    with st.chat_message(role):
        st.markdown(message.get("content", ""))
        metadata = message.get("metadata") or {}
        if role == "assistant" and metadata:
            with st.expander("Response details"):
                st.json(metadata)

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
                }
                st.markdown(data["answer"])
                with st.expander("Response details"):
                    st.json(metadata)
                st.session_state.messages.append(
                    {"role": "assistant", "content": data["answer"], "metadata": metadata}
                )
            except Exception as exc:
                st.error(f"Chat failed: {exc}")
