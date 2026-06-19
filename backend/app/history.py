from __future__ import annotations

import time
import uuid
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


class ChatHistoryRepository(Protocol):
    def save_message(self, user_id: str, session_id: str, message: ChatMessage) -> None:
        ...

    def load_messages(self, user_id: str, session_id: str, limit: int = 50) -> list[ChatMessage]:
        ...

    def list_sessions(self, user_id: str, limit: int = 25) -> list[ChatSession]:
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


def create_chat_history_repository(settings: AppSettings) -> ChatHistoryRepository:
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
