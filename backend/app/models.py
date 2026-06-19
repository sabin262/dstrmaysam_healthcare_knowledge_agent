from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    expires_in: int
    username: str | None = None
    roles: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    password_change_required: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)


class ChatRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = None


class Source(BaseModel):
    title: str
    uri: str
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    snippet: str | None = None


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


class AdminUserSummary(BaseModel):
    username: str
    roles: list[str]
    departments: list[str]
    password_change_required: bool = False


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=1)
    temporary_password: str = Field(min_length=8)
    roles: list[str]
    departments: list[str] = Field(default_factory=list)


class AdminUserUpdateRequest(BaseModel):
    roles: list[str] | None = None
    departments: list[str] | None = None


class AdminPasswordResetRequest(BaseModel):
    temporary_password: str = Field(min_length=8)
