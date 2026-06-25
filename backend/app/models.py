from __future__ import annotations

from typing import Any, Literal

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


class AuthUserResponse(BaseModel):
    username: str
    roles: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    password_change_required: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class ChatRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = None
    execution_mode: Literal["deterministic_agent", "agent_only"] = "agent_only"


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
    performance: dict[str, Any] = Field(default_factory=dict)
    latency_breakdown: dict[str, Any] = Field(default_factory=dict)


class ChatSessionSummary(BaseModel):
    session_id: str
    title: str
    updated_at: str


class ChatSessionDetail(BaseModel):
    session_id: str
    messages: list[dict[str, Any]]


class GuardianNewsArticle(BaseModel):
    id: str
    title: str
    section: str = ""
    published_at: str = ""
    summary: str = ""
    url: str
    thumbnail: str = ""


class GuardianNewsResponse(BaseModel):
    articles: list[GuardianNewsArticle] = Field(default_factory=list)
    last_updated: str | None = None
    refresh_seconds: int = 300
    error: str | None = None


class AdminUserSummary(BaseModel):
    username: str
    roles: list[str]
    departments: list[str]
    password_change_required: bool = False


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=1)
    temporary_password: str = Field(min_length=1)
    roles: list[str]
    departments: list[str] = Field(default_factory=list)


class AdminUserUpdateRequest(BaseModel):
    roles: list[str] | None = None
    departments: list[str] | None = None


class AdminPasswordResetRequest(BaseModel):
    temporary_password: str = Field(min_length=1)


class AdminDeleteIndexesRequest(BaseModel):
    admin_password: str = Field(min_length=1)


class AdminDeleteIndexesResponse(BaseModel):
    deleted_chunks: int = 0
    deleted_lookup_rows: int = 0
    manifest_cleared: bool = False
    backend: str
    raw_documents_preserved: bool = True
    deterministic_lookup_preserved: bool = False


class AdminDocumentUploadResponse(BaseModel):
    key: str
    uri: str
    content_type: str
    size_bytes: int


class AdminDocumentMetadataUpdateRequest(BaseModel):
    key: str = Field(min_length=1)
    category: str = Field(min_length=1)
    document_type: str = Field(min_length=1)
    allowed_roles: list[str] = Field(min_length=1)


class AdminIngestionResponse(BaseModel):
    opensearch_index: str | None = None
    previous_opensearch_index: str | None = None
    force_reindex: bool = False
    documents: list[dict[str, Any]] = Field(default_factory=list)
    indexed_chunks: int = 0
    total_chunks: int = 0
    indexed_documents: int = 0
    skipped_documents: int = 0
    deleted_documents: int = 0
    deleted_chunks: int = 0
