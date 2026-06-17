from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    expires_in: int


class ChatRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = None


class Source(BaseModel):
    title: str
    uri: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sources: list[Source]
    tools_used: list[str]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    trace_id: str
    safety: dict[str, Any] = Field(default_factory=dict)
    audit_event: dict[str, Any] = Field(default_factory=dict)


class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    updated_at: str


class ChatSessionDetail(BaseModel):
    session_id: str
    messages: list[dict[str, Any]]
