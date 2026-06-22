from __future__ import annotations

import time
import uuid
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .aws import boto3_resource
from .config import AppSettings


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChatMessage:
    role: str
    content: str
    created_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_api(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class ChatSession:
    session_id: str
    title: str
    updated_at: str


@dataclass
class ChatInteraction:
    user_id: str
    session_id: str
    question: str
    answer: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ChatHistoryRepository(Protocol):
    def save_message(self, user_id: str, session_id: str, message: ChatMessage) -> None:
        ...

    def load_messages(self, user_id: str, session_id: str, limit: int = 50) -> list[ChatMessage]:
        ...

    def list_sessions(self, user_id: str, limit: int = 25) -> list[ChatSession]:
        ...

    def list_recent_interactions(self, limit: int = 100) -> list[ChatInteraction]:
        ...

    def update_message_metadata_by_trace_id(self, trace_id: str, metadata: dict[str, Any]) -> bool:
        ...


class InMemoryChatHistoryRepository:
    def __init__(self) -> None:
        self._messages: dict[tuple[str, str], list[ChatMessage]] = {}
        self._sessions: dict[str, dict[str, ChatSession]] = {}

    def save_message(self, user_id: str, session_id: str, message: ChatMessage) -> None:
        self._messages.setdefault((user_id, session_id), []).append(message)
        title = self._derive_title(user_id, session_id)
        self._sessions.setdefault(user_id, {})[session_id] = ChatSession(
            session_id=session_id,
            title=title,
            updated_at=message.created_at,
        )

    def load_messages(self, user_id: str, session_id: str, limit: int = 50) -> list[ChatMessage]:
        return list(self._messages.get((user_id, session_id), []))[-limit:]

    def list_sessions(self, user_id: str, limit: int = 25) -> list[ChatSession]:
        sessions = list(self._sessions.get(user_id, {}).values())
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions[:limit]

    def list_recent_interactions(self, limit: int = 100) -> list[ChatInteraction]:
        interactions: list[ChatInteraction] = []
        for (user_id, session_id), messages in self._messages.items():
            last_question = ""
            for message in messages:
                if message.role == "user":
                    last_question = message.content
                elif message.role == "assistant":
                    interactions.append(
                        ChatInteraction(
                            user_id=user_id,
                            session_id=session_id,
                            question=last_question,
                            answer=message.content,
                            created_at=message.created_at,
                            metadata=dict(message.metadata),
                        )
                    )
        interactions.sort(key=lambda item: item.created_at, reverse=True)
        return interactions[:limit]

    def update_message_metadata_by_trace_id(self, trace_id: str, metadata: dict[str, Any]) -> bool:
        for messages in self._messages.values():
            for message in messages:
                if message.role == "assistant" and message.metadata.get("trace_id") == trace_id:
                    message.metadata.update(metadata)
                    return True
        return False

    def _derive_title(self, user_id: str, session_id: str) -> str:
        for message in self._messages.get((user_id, session_id), []):
            if message.role == "user" and message.content.strip():
                return message.content.strip()[:80]
        return "New chat"


class DynamoDBChatHistoryRepository:
    def __init__(self, settings: AppSettings):
        if not settings.dynamodb_chat_table:
            raise ValueError("DYNAMODB_CHAT_TABLE must be configured")
        try:
            from boto3.dynamodb.conditions import Key
        except ImportError as exc:
            raise RuntimeError("boto3 is required for DynamoDB chat history") from exc
        self._key = Key
        self._table = boto3_resource(settings, "dynamodb").Table(settings.dynamodb_chat_table)

    def save_message(self, user_id: str, session_id: str, message: ChatMessage) -> None:
        timestamp_ms = int(time.time() * 1000)
        message_sort_key = f"MESSAGE#{session_id}#{timestamp_ms}#{uuid.uuid4().hex}"
        self._table.put_item(
            Item={
                "user_id": user_id,
                "sort_key": message_sort_key,
                "session_id": session_id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
                "metadata": message.metadata,
            }
        )

        title = message.content[:80] if message.role == "user" else "Chat"
        self._table.update_item(
            Key={"user_id": user_id, "sort_key": f"SESSION#{session_id}"},
            UpdateExpression=(
                "SET session_id = :sid, updated_at = :updated, "
                "title = if_not_exists(title, :title)"
            ),
            ExpressionAttributeValues={
                ":sid": session_id,
                ":updated": message.created_at,
                ":title": title,
            },
        )

    def load_messages(self, user_id: str, session_id: str, limit: int = 50) -> list[ChatMessage]:
        response = self._table.query(
            KeyConditionExpression=self._key("user_id").eq(user_id)
            & self._key("sort_key").begins_with(f"MESSAGE#{session_id}#"),
            Limit=limit,
            ScanIndexForward=True,
        )
        messages = [
            ChatMessage(
                role=str(item.get("role", "user")),
                content=str(item.get("content", "")),
                created_at=str(item.get("created_at", "")),
                metadata=dict(item.get("metadata", {})),
            )
            for item in response.get("Items", [])
        ]
        return messages[-limit:]

    def list_sessions(self, user_id: str, limit: int = 25) -> list[ChatSession]:
        response = self._table.query(
            KeyConditionExpression=self._key("user_id").eq(user_id)
            & self._key("sort_key").begins_with("SESSION#"),
            Limit=limit,
            ScanIndexForward=False,
        )
        sessions = [
            ChatSession(
                session_id=str(item.get("session_id", "")).strip(),
                title=str(item.get("title", "Chat")),
                updated_at=str(item.get("updated_at", "")),
            )
            for item in response.get("Items", [])
            if item.get("session_id")
        ]
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions[:limit]

    def list_recent_interactions(self, limit: int = 100) -> list[ChatInteraction]:
        items: list[dict[str, Any]] = []
        scan_kwargs: dict[str, Any] = {"Limit": max(limit * 4, 100)}
        while True:
            response = self._table.scan(**scan_kwargs)
            items.extend(
                item
                for item in response.get("Items", [])
                if str(item.get("sort_key", "")).startswith("MESSAGE#")
            )
            if len(items) >= limit * 4 or "LastEvaluatedKey" not in response:
                break
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

        grouped: dict[tuple[str, str], list[ChatMessage]] = {}
        for item in items:
            user_id = str(item.get("user_id", ""))
            session_id = str(item.get("session_id", ""))
            if not user_id or not session_id:
                continue
            grouped.setdefault((user_id, session_id), []).append(
                ChatMessage(
                    role=str(item.get("role", "user")),
                    content=str(item.get("content", "")),
                    created_at=str(item.get("created_at", "")),
                    metadata=dict(item.get("metadata", {})),
                )
            )

        interactions: list[ChatInteraction] = []
        for (user_id, session_id), messages in grouped.items():
            messages.sort(key=lambda message: message.created_at)
            last_question = ""
            for message in messages:
                if message.role == "user":
                    last_question = message.content
                elif message.role == "assistant":
                    interactions.append(
                        ChatInteraction(
                            user_id=user_id,
                            session_id=session_id,
                            question=last_question,
                            answer=message.content,
                            created_at=message.created_at,
                            metadata=dict(message.metadata),
                        )
                    )
        interactions.sort(key=lambda item: item.created_at, reverse=True)
        return interactions[:limit]

    def update_message_metadata_by_trace_id(self, trace_id: str, metadata: dict[str, Any]) -> bool:
        scan_kwargs: dict[str, Any] = {"Limit": 100}
        while True:
            response = self._table.scan(**scan_kwargs)
            for item in response.get("Items", []):
                if str(item.get("sort_key", "")).startswith("MESSAGE#") and item.get("metadata", {}).get("trace_id") == trace_id:
                    merged = dict(item.get("metadata", {}))
                    merged.update(metadata)
                    self._table.update_item(
                        Key={"user_id": item["user_id"], "sort_key": item["sort_key"]},
                        UpdateExpression="SET metadata = :metadata",
                        ExpressionAttributeValues={":metadata": merged},
                    )
                    return True
            if "LastEvaluatedKey" not in response:
                break
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        return False


class PostgresChatHistoryRepository:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self._ensure_schema()

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except Exception as exc:  # pragma: no cover - exercised when dependency missing
            raise RuntimeError("psycopg is required for Postgres chat history") from exc

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

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        user_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (user_id, session_id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        message_id BIGSERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_chat_messages_user_session
                    ON chat_messages (user_id, session_id, message_id)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_chat_messages_trace_id
                    ON chat_messages ((metadata->>'trace_id'))
                    """
                )
            conn.commit()

    def save_message(self, user_id: str, session_id: str, message: ChatMessage) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_messages (user_id, session_id, role, content, created_at, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        user_id,
                        session_id,
                        message.role,
                        message.content,
                        message.created_at,
                        _json_dumps(message.metadata),
                    ),
                )
                title = self._derive_title(cur, user_id, session_id, message)
                cur.execute(
                    """
                    INSERT INTO chat_sessions (user_id, session_id, title, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, session_id) DO UPDATE
                    SET updated_at = EXCLUDED.updated_at,
                        title = CASE
                            WHEN chat_sessions.title = 'Chat' OR chat_sessions.title = 'New chat'
                            THEN EXCLUDED.title
                            ELSE chat_sessions.title
                        END
                    """,
                    (user_id, session_id, title, message.created_at),
                )
            conn.commit()

    def _derive_title(self, cur, user_id: str, session_id: str, message: ChatMessage) -> str:
        if message.role == "user" and message.content.strip():
            return message.content.strip()[:80]
        cur.execute(
            """
            SELECT content FROM chat_messages
            WHERE user_id = %s AND session_id = %s AND role = 'user'
            ORDER BY message_id ASC
            LIMIT 1
            """,
            (user_id, session_id),
        )
        row = cur.fetchone()
        if row and str(row.get("content", "")).strip():
            return str(row["content"]).strip()[:80]
        return "Chat"

    def load_messages(self, user_id: str, session_id: str, limit: int = 50) -> list[ChatMessage]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT role, content, created_at, metadata
                    FROM chat_messages
                    WHERE user_id = %s AND session_id = %s
                    ORDER BY message_id DESC
                    LIMIT %s
                    """,
                    (user_id, session_id, limit),
                )
                rows = list(cur.fetchall())
        rows.reverse()
        return [
            ChatMessage(
                role=str(row.get("role", "user")),
                content=str(row.get("content", "")),
                created_at=str(row.get("created_at", "")),
                metadata=dict(row.get("metadata") or {}),
            )
            for row in rows
        ]

    def list_sessions(self, user_id: str, limit: int = 25) -> list[ChatSession]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, title, updated_at
                    FROM chat_sessions
                    WHERE user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = list(cur.fetchall())
        return [
            ChatSession(
                session_id=str(row.get("session_id", "")),
                title=str(row.get("title", "Chat")),
                updated_at=str(row.get("updated_at", "")),
            )
            for row in rows
        ]

    def list_recent_interactions(self, limit: int = 100) -> list[ChatInteraction]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, session_id, role, content, created_at, metadata
                    FROM chat_messages
                    ORDER BY message_id DESC
                    LIMIT %s
                    """,
                    (max(limit * 4, 100),),
                )
                rows = list(cur.fetchall())
        rows.reverse()
        grouped: dict[tuple[str, str], list[ChatMessage]] = {}
        for row in rows:
            grouped.setdefault((str(row["user_id"]), str(row["session_id"])), []).append(
                ChatMessage(
                    role=str(row.get("role", "user")),
                    content=str(row.get("content", "")),
                    created_at=str(row.get("created_at", "")),
                    metadata=dict(row.get("metadata") or {}),
                )
            )
        interactions: list[ChatInteraction] = []
        for (user_id, session_id), messages in grouped.items():
            last_question = ""
            for message in messages:
                if message.role == "user":
                    last_question = message.content
                elif message.role == "assistant":
                    interactions.append(
                        ChatInteraction(
                            user_id=user_id,
                            session_id=session_id,
                            question=last_question,
                            answer=message.content,
                            created_at=message.created_at,
                            metadata=dict(message.metadata),
                        )
                    )
        interactions.sort(key=lambda item: item.created_at, reverse=True)
        return interactions[:limit]

    def update_message_metadata_by_trace_id(self, trace_id: str, metadata: dict[str, Any]) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE chat_messages
                    SET metadata = metadata || %s::jsonb
                    WHERE role = 'assistant' AND metadata->>'trace_id' = %s
                    """,
                    (_json_dumps(metadata), trace_id),
                )
                updated = cur.rowcount > 0
            conn.commit()
        return updated


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str)


def create_chat_history_repository(settings: AppSettings) -> ChatHistoryRepository:
    if settings.chat_history_backend.lower() == "postgres":
        return PostgresChatHistoryRepository(settings)
    if settings.chat_history_backend.lower() == "dynamodb":
        return DynamoDBChatHistoryRepository(settings)
    return InMemoryChatHistoryRepository()


def build_history_context(messages: list[ChatMessage], max_chars: int) -> str:
    if not messages:
        return "No previous chat history."

    retained: list[str] = []
    used_chars = 0
    for message in reversed(messages):
        line = f"{message.role}: {message.content}"
        line_length = len(line)
        if retained and used_chars + line_length > max_chars:
            break
        retained.append(line)
        used_chars += line_length

    retained.reverse()
    omitted = len(messages) - len(retained)
    if omitted > 0:
        return f"Earlier conversation summarized: {omitted} older messages omitted.\n" + "\n".join(retained)
    return "\n".join(retained)
