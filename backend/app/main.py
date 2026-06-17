from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .agent import KnowledgeAgent
from .auth import AuthService, AuthenticationError
from .config import AppSettings
from .healthcare import HealthcareUserContext
from .history import create_chat_history_repository
from .models import (
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


app = FastAPI(title="Internal Company Knowledge Assistant", version="0.1.0")
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


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> str:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        return get_auth_service().verify_token(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


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
        token = get_auth_service().login(request.username, request.password)
        return LoginResponse(access_token=token, expires_in=get_auth_service().token_ttl_seconds)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, user: HealthcareUserContext = Depends(current_user_context)) -> ChatResponse:
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
def documents(user: HealthcareUserContext = Depends(current_user_context)) -> list[dict[str, object]]:
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
