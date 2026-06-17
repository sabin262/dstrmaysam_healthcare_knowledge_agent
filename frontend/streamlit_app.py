import os
from typing import Any

import requests
import streamlit as st


BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")


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


def get_json(path: str) -> dict[str, Any] | list[dict[str, Any]]:
    response = requests.get(f"{BACKEND_URL}{path}", headers=api_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


st.set_page_config(page_title="Company Knowledge Assistant", page_icon=None, layout="wide")
st.title("Internal Company Knowledge Assistant")

if "access_token" not in st.session_state:
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")

    if submitted:
        try:
            data = post_json("/auth/login", {"username": username, "password": password})
            st.session_state.access_token = data["access_token"]
            st.session_state.session_id = None
            st.session_state.messages = []
            st.rerun()
        except Exception as exc:
            st.error(f"Login failed: {exc}")
    st.stop()

with st.sidebar:
    st.caption("Signed in")
    if st.button("New chat"):
        st.session_state.session_id = None
        st.session_state.messages = []
        st.rerun()
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()

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

for message in st.session_state.get("messages", []):
    role = "assistant" if message.get("role") == "assistant" else "user"
    with st.chat_message(role):
        st.markdown(message.get("content", ""))
        metadata = message.get("metadata") or {}
        if role == "assistant" and metadata:
            with st.expander("Response details"):
                st.json(metadata)

query = st.chat_input("Ask a question about company knowledge")
if query:
    st.session_state.setdefault("messages", []).append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        with st.spinner("Thinking with company context..."):
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
