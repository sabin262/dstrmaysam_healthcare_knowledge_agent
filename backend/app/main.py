from __future__ import annotations

from functools import lru_cache
from pathlib import PurePath
import re

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .agent import KnowledgeAgent
from .auth import (
    AuthService,
    AuthenticationError,
    AuthorizationError,
    PasswordChangeRequiredError,
    UserManagementError,
)
from .config import AppSettings
from .healthcare import HealthcareUserContext
from .history import create_chat_history_repository
from .ingest import IngestionJob
from .models import (
    AdminDocumentUploadResponse,
    AdminIngestionResponse,
    AdminPasswordResetRequest,
    AdminUserCreateRequest,
    AdminUserSummary,
    AdminUserUpdateRequest,
    ChangePasswordRequest,
    ChatRequest,
    ChatResponse,
    ChatSessionDetail,
    ChatSessionSummary,
    LoginRequest,
    LoginResponse,
    Source,
)
from .observability import ObservabilityClient
from .retrieval import RetrievalService
from .secrets import SecretProvider
from .storage import DocumentStore


SUPPORTED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv"}


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings.from_env()


@lru_cache
def get_secret_provider() -> SecretProvider:
    return SecretProvider(get_settings())


@lru_cache
def get_auth_service() -> AuthService:
    return AuthService(get_secret_provider())


@lru_cache
def get_history_repository():
    return create_chat_history_repository(get_settings())


@lru_cache
def get_document_store() -> DocumentStore:
    return DocumentStore(get_settings())


@lru_cache
def get_retrieval_service() -> RetrievalService:
    return RetrievalService(get_settings(), get_secret_provider())


@lru_cache
def get_observability() -> ObservabilityClient:
    return ObservabilityClient(get_settings(), get_secret_provider())


@lru_cache
def get_agent() -> KnowledgeAgent:
    return KnowledgeAgent(
        settings=get_settings(),
        secret_provider=get_secret_provider(),
        history=get_history_repository(),
        retrieval=get_retrieval_service(),
        documents=get_document_store(),
        observability=get_observability(),
    )


app = FastAPI(title="Dstrmaysam Healthcare Knowledge Agent", version="0.1.0")
settings = get_settings()
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

security = HTTPBearer(auto_error=False)


def _login_response(result) -> LoginResponse:
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        username=result.username,
        roles=result.roles,
        departments=result.departments,
        password_change_required=result.password_change_required,
    )


def _admin_user_response(user) -> AdminUserSummary:
    return AdminUserSummary(
        username=user.username,
        roles=user.roles,
        departments=user.departments,
        password_change_required=user.password_change_required,
    )


def _safe_upload_filename(filename: str | None) -> str:
    raw_name = PurePath(filename or "").name.strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name).strip("._-")
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file must have a filename")
    suffix = PurePath(name).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supported file types are pdf, docx, txt, md, and csv",
        )
    return name


def _raw_document_key(filename: str) -> str:
    prefix = get_settings().s3_raw_prefix.strip("/")
    return f"{prefix}/{filename}" if prefix else filename


def current_user_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> HealthcareUserContext:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        claims = get_auth_service().verify_token_claims(credentials.credentials)
        return HealthcareUserContext.from_claims(claims)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


def active_user_context(
    user: HealthcareUserContext = Depends(current_user_context),
) -> HealthcareUserContext:
    try:
        get_auth_service().ensure_password_change_not_required(
            {
                "password_change_required": user.password_change_required,
            }
        )
        return user
    except PasswordChangeRequiredError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def admin_user_context(
    user: HealthcareUserContext = Depends(active_user_context),
) -> HealthcareUserContext:
    try:
        get_auth_service().ensure_admin(
            {
                "roles": list(user.roles),
                "password_change_required": user.password_change_required,
            }
        )
        return user
    except AuthorizationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def current_user(user: HealthcareUserContext = Depends(active_user_context)) -> str:
    return user.user_id


@app.get("/health")
def health() -> dict[str, object]:
    agent = get_agent()
    return {
        "status": "ok",
        "settings": get_settings().public_summary(),
        "registered_tools": agent.registered_tool_names(),
    }


@app.post("/auth/login", response_model=LoginResponse)
def login(request: LoginRequest) -> LoginResponse:
    try:
        return _login_response(get_auth_service().login(request.username, request.password))
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@app.post("/auth/change-password", response_model=LoginResponse)
def change_password(
    request: ChangePasswordRequest,
    user: HealthcareUserContext = Depends(current_user_context),
) -> LoginResponse:
    try:
        return _login_response(
            get_auth_service().change_password(
                user.user_id,
                request.current_password,
                request.new_password,
            )
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@app.get("/admin/users", response_model=list[AdminUserSummary])
def list_admin_users(
    user: HealthcareUserContext = Depends(admin_user_context),
) -> list[AdminUserSummary]:
    return [_admin_user_response(admin_user) for admin_user in get_auth_service().list_users()]


@app.post("/admin/users", response_model=AdminUserSummary, status_code=status.HTTP_201_CREATED)
def create_admin_user(
    request: AdminUserCreateRequest,
    user: HealthcareUserContext = Depends(admin_user_context),
) -> AdminUserSummary:
    try:
        created = get_auth_service().create_user(
            request.username,
            request.temporary_password,
            request.roles,
            request.departments,
        )
        return _admin_user_response(created)
    except UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.patch("/admin/users/{username}", response_model=AdminUserSummary)
def update_admin_user(
    username: str,
    request: AdminUserUpdateRequest,
    user: HealthcareUserContext = Depends(admin_user_context),
) -> AdminUserSummary:
    try:
        updated = get_auth_service().update_user(
            username,
            roles=request.roles,
            departments=request.departments,
        )
        return _admin_user_response(updated)
    except UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/admin/users/{username}/reset-password", response_model=AdminUserSummary)
def reset_admin_user_password(
    username: str,
    request: AdminPasswordResetRequest,
    user: HealthcareUserContext = Depends(admin_user_context),
) -> AdminUserSummary:
    try:
        updated = get_auth_service().reset_password(username, request.temporary_password)
        return _admin_user_response(updated)
    except UserManagementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/admin/documents/upload", response_model=AdminDocumentUploadResponse)
async def upload_admin_document(
    file: UploadFile = File(...),
    user: HealthcareUserContext = Depends(admin_user_context),
) -> AdminDocumentUploadResponse:
    filename = _safe_upload_filename(file.filename)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    key = _raw_document_key(filename)
    content_type = file.content_type or "application/octet-stream"
    try:
        get_document_store().upload_document(key, data, content_type)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return AdminDocumentUploadResponse(
        key=key,
        uri=f"s3://{get_settings().s3_bucket}/{key}",
        content_type=content_type,
        size_bytes=len(data),
    )


@app.post("/admin/documents/ingest", response_model=AdminIngestionResponse)
def ingest_admin_documents(
    user: HealthcareUserContext = Depends(admin_user_context),
) -> AdminIngestionResponse:
    try:
        result = IngestionJob(get_settings(), get_secret_provider()).run()
        document_store = get_document_store()
        if hasattr(document_store, "invalidate_manifest_cache"):
            document_store.invalidate_manifest_cache()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return AdminIngestionResponse(
        opensearch_index=result.get("opensearch_index"),
        previous_opensearch_index=result.get("previous_opensearch_index"),
        force_reindex=bool(result.get("force_reindex", False)),
        documents=list(result.get("documents", [])),
        indexed_chunks=int(result.get("indexed_chunks", 0)),
        total_chunks=int(result.get("total_chunks", 0)),
        indexed_documents=int(result.get("indexed_documents", 0)),
        skipped_documents=int(result.get("skipped_documents", 0)),
        deleted_documents=int(result.get("deleted_documents", 0)),
        deleted_chunks=int(result.get("deleted_chunks", 0)),
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, user: HealthcareUserContext = Depends(active_user_context)) -> ChatResponse:
    result = get_agent().answer(
        user_id=user.user_id,
        query=request.query,
        session_id=request.session_id,
        user_context=user,
    )
    return ChatResponse(
        session_id=result.session_id,
        answer=result.answer,
        sources=[Source(**source) for source in result.sources],
        tools_used=result.tools_used,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        trace_id=result.trace_id,
        safety=result.metadata.get("safety", {}),
        audit_event=result.metadata.get("audit_event", {}),
        performance=result.metadata.get("performance", {}),
    )


@app.get("/chat/sessions", response_model=list[ChatSessionSummary])
def list_chat_sessions(user_id: str = Depends(current_user)) -> list[ChatSessionSummary]:
    sessions = get_history_repository().list_sessions(user_id)
    return [
        ChatSessionSummary(
            session_id=session.session_id,
            title=session.title,
            updated_at=session.updated_at,
        )
        for session in sessions
    ]


@app.get("/chat/sessions/{session_id}", response_model=ChatSessionDetail)
def get_chat_session(session_id: str, user_id: str = Depends(current_user)) -> ChatSessionDetail:
    messages = get_history_repository().load_messages(user_id, session_id, limit=100)
    return ChatSessionDetail(session_id=session_id, messages=[message.to_api() for message in messages])


@app.get("/documents")
def documents(user: HealthcareUserContext = Depends(active_user_context)) -> list[dict[str, object]]:
    agent = get_agent()
    return [
        {
            "title": document.title,
            "uri": document.uri,
            "content_type": document.content_type,
            "metadata": document.metadata,
        }
        for document in agent.access.filter_documents(user, get_document_store().list_documents())
    ]
