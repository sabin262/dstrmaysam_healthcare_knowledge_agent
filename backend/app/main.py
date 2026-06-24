from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import PurePath
import re
import threading

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .agent import KnowledgeAgent
from .auth import (
    AuthService,
    AuthenticationError,
    AuthorizationError,
    KNOWN_USER_ROLES,
    PasswordChangeRequiredError,
    UserManagementError,
)
from .config import AppSettings
from .deterministic_lookup import DeterministicLookupService, build_csv_semantic_metadata
from .healthcare import HealthcareUserContext
from .history import PostgresChatHistoryRepository, create_chat_history_repository
from .ingest import IngestionJob, checksum_bytes
from .local_chroma import LocalChromaIngestionJob, LocalChromaRetrievalService
from .models import (
    AdminDocumentUploadResponse,
    AdminDocumentMetadataUpdateRequest,
    AdminIngestionResponse,
    AdminDeleteIndexesRequest,
    AdminDeleteIndexesResponse,
    AdminPasswordResetRequest,
    AdminUserCreateRequest,
    AdminUserSummary,
    AdminUserUpdateRequest,
    AuthUserResponse,
    ChangePasswordRequest,
    ChatRequest,
    ChatResponse,
    ChatSessionDetail,
    ChatSessionSummary,
    GuardianNewsResponse,
    LoginRequest,
    LoginResponse,
    Source,
)
from .news import GuardianNewsService
from .observability import ObservabilityClient
from .retrieval import RetrievalService
from .secrets import EnvSecretProvider, SecretProvider
from .storage import DocumentStore, LocalDocumentStore


SUPPORTED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv"}
DOCUMENT_METADATA_VALUE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")
DASHBOARD_RANGE_WINDOWS = {
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "1d": timedelta(days=1),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
}
DASHBOARD_RANGE_LABELS = {
    "30m": "30mins",
    "1h": "1hr",
    "3h": "3hr",
    "1d": "1 day",
    "3d": "3 days",
    "7d": "7 days",
    "all": "all time",
}


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings.from_env()


@lru_cache
def get_secret_provider() -> SecretProvider:
    settings = get_settings()
    if settings.use_local_resources():
        return EnvSecretProvider(settings)
    return SecretProvider(settings)


@lru_cache
def get_auth_service() -> AuthService:
    return AuthService(get_secret_provider())


@lru_cache
def get_history_repository():
    settings = get_settings()
    if settings.use_local_resources():
        return PostgresChatHistoryRepository(settings)
    return create_chat_history_repository(settings)


@lru_cache
def get_document_store() -> DocumentStore:
    settings = get_settings()
    if settings.use_local_resources():
        return LocalDocumentStore(settings)
    return DocumentStore(settings)


@lru_cache
def get_retrieval_service() -> RetrievalService:
    settings = get_settings()
    if settings.use_local_resources():
        return LocalChromaRetrievalService(settings, get_secret_provider())
    return RetrievalService(settings, get_secret_provider())


@lru_cache
def get_deterministic_lookup_service() -> DeterministicLookupService:
    return DeterministicLookupService(get_settings())


@lru_cache
def get_observability() -> ObservabilityClient:
    return ObservabilityClient(get_settings(), get_secret_provider())


@lru_cache
def get_news_service() -> GuardianNewsService:
    return GuardianNewsService(get_settings())


def create_ingestion_job():
    settings = get_settings()
    if settings.use_local_resources():
        return LocalChromaIngestionJob(settings, get_secret_provider())
    return IngestionJob(settings, get_secret_provider())


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


def _run_backend_warmup() -> None:
    try:
        get_agent().warm_up()
    except Exception:
        return


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not get_settings().chat_warmup_enabled:
        yield
        return
    threading.Thread(target=_run_backend_warmup, daemon=True).start()
    yield


app = FastAPI(
    title="Healthcare Knowledge Agent",
    version="0.1.0",
    lifespan=lifespan,
)
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


def _auth_user_response(user: HealthcareUserContext) -> AuthUserResponse:
    return AuthUserResponse(
        username=user.user_id,
        roles=list(user.roles),
        departments=list(user.departments),
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


def _normalize_document_metadata_value(value: str, field_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9_:-]+", "_", str(value).strip().lower()).strip("_:-")
    if not normalized or not DOCUMENT_METADATA_VALUE_PATTERN.match(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document {field_name}",
        )
    return normalized


def _normalize_document_roles(roles: list[str]) -> list[str]:
    normalized = sorted({str(role).strip().lower() for role in roles if str(role).strip()})
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one access role is required")
    unknown_roles = [role for role in normalized if role not in KNOWN_USER_ROLES]
    if unknown_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown access role(s): {', '.join(unknown_roles)}",
        )
    return normalized


def _document_record_payload(document) -> dict[str, object]:
    return {
        "title": document.title,
        "key": document.key,
        "uri": document.uri,
        "content_type": document.content_type,
        "metadata": dict(document.metadata or {}),
        "chunk_count": int(document.chunk_count or 0),
        "ingestion_status": document.ingestion_status or "",
    }


def _csv_manifest_record(filename: str, data: bytes, rows_inserted: int, content_type: str) -> dict[str, object]:
    semantic_metadata = build_csv_semantic_metadata(filename, data)
    columns = [str(column).strip() for column in semantic_metadata.get("columns") or [] if str(column).strip()]
    key = f"postgres://uploaded_lookup_rows/{filename}"
    return {
        "key": key,
        "title": filename,
        "uri": key,
        "content_type": content_type,
        "checksum": checksum_bytes(data),
        "metadata": {
            "key": key,
            "checksum": checksum_bytes(data),
            "owner": "uploaded",
            "version": "uploaded",
            "effective_date": "unknown",
            "review_date": "unknown",
            "approval_status": "uploaded",
            "sensitivity": "internal",
            "domain": "deterministic_lookup",
            "document_type": "csv_table",
            "allowed_roles": ["staff", "admin", "manager", "doctor", "nurse", "pharmacy", "clinical_governance"],
            "asset_source": "postgres_uploaded_lookup",
            "source_table": "uploaded_lookup_rows",
            "row_count": rows_inserted,
            "columns": columns,
            "semantic_terms": semantic_metadata.get("semantic_terms") or [],
            "categorical_values": semantic_metadata.get("categorical_values") or {},
            "sample_values": semantic_metadata.get("sample_values") or [],
            "search_backend": "postgres",
            "rag_indexed": False,
        },
        "chunk_count": 0,
        "ingestion_status": "metadata_only",
    }


def _empty_index_manifest() -> dict[str, object]:
    settings = get_settings()
    base: dict[str, object] = {
        "documents": [],
        "indexed_chunks": 0,
        "total_chunks": 0,
        "indexed_documents": 0,
        "skipped_documents": 0,
        "deleted_documents": 0,
        "deleted_chunks": 0,
    }
    if settings.use_local_resources():
        base.update(
            {
                "vector_backend": "chroma",
                "chroma_collection": settings.chroma_collection,
                "force_reindex": False,
            }
        )
    else:
        base.update(
            {
                "opensearch_index": settings.opensearch_index,
                "force_reindex": False,
            }
        )
    return base


def _tool_flow_from_metadata(metadata: dict[str, object], tools_used: list[str]) -> list[dict[str, object]]:
    existing = metadata.get("tool_flow")
    if isinstance(existing, list):
        return [dict(item) for item in existing if isinstance(item, dict)]

    guidance_items = metadata.get("catalog_guidance")
    remaining_guidance = [dict(item) for item in guidance_items if isinstance(item, dict)] if isinstance(guidance_items, list) else []
    flow: list[dict[str, object]] = []
    for tool in tools_used:
        guidance_index = next(
            (
                index
                for index, guidance in enumerate(remaining_guidance)
                if str(guidance.get("tool") or "") == tool
            ),
            None,
        )
        if guidance_index is None:
            flow.append({"tool": tool, "kind": "agent_tool", "selected_by_agent": True})
            continue
        guidance = remaining_guidance.pop(guidance_index)
        timing = guidance.get("timing_ms") if isinstance(guidance.get("timing_ms"), dict) else {}
        flow.append(
            {
                "tool": "document_catalog",
                "kind": "helper_tool",
                "helper_for": tool,
                "selected_by_agent": False,
                "query": guidance.get("query"),
                "candidate_count": guidance.get("candidate_count", 0),
                "candidate_keys": guidance.get("candidate_keys", []),
                "fallback_to_broad_search": guidance.get("fallback_to_broad_search", False),
                "latency_ms": int(timing.get("catalog_ms", 0)),
            }
        )
        flow.append(
            {
                "tool": tool,
                "kind": "agent_tool",
                "selected_by_agent": True,
                "query": guidance.get("query"),
                "source": "catalog_filtered_retrieval" if guidance.get("catalog_filter_applied") else "broad_retrieval",
                "candidate_count": guidance.get("candidate_count", 0),
                "returned_hits": int(timing.get("returned_hits", 0)),
                "latency_ms": int(timing.get("retrieval_search_ms", 0)),
            }
        )
    return flow


def _tool_flow_summary(tool_flow: list[dict[str, object]]) -> str:
    names = [str(item.get("tool") or "") for item in tool_flow if isinstance(item, dict) and item.get("tool")]
    return " -> ".join(names)


def _metric_ms(performance: dict[str, object], key: str) -> int:
    try:
        return int(performance.get(key) or 0)
    except Exception:
        return 0


def _tool_timing_totals(tool_timings: list[dict[str, object]]) -> dict[str, int]:
    totals: dict[str, int] = {
        "tool_count": len(tool_timings),
        "index_check_ms": 0,
        "index_created": 0,
        "catalog_ms": 0,
        "retrieval_search_ms": 0,
        "embedding_ms": 0,
        "opensearch_ms": 0,
        "neighbor_ms": 0,
        "access_filter_ms": 0,
        "total_ms": 0,
        "vector_hits": 0,
        "keyword_hits": 0,
        "neighbor_hits": 0,
        "returned_hits": 0,
    }
    for item in tool_timings:
        for key in totals:
            if key == "tool_count":
                continue
            try:
                totals[key] += int(item.get(key) or 0)
            except Exception:
                pass
    return totals


def _raw_timing_metrics(performance: dict[str, object]) -> dict[str, int]:
    timings: dict[str, int] = {}
    for key, value in performance.items():
        if key == "latency_breakdown":
            continue
        if key.endswith("_ms") or key == "total_ms":
            try:
                timings[key] = int(value or 0)
            except Exception:
                pass
    return dict(sorted(timings.items()))


def _dashboard_latency_breakdown(
    metadata: dict[str, object],
    performance: dict[str, object],
    latency_ms: int,
) -> dict[str, object]:
    existing = metadata.get("latency_breakdown")
    if isinstance(existing, dict):
        if isinstance(existing.get("sections"), dict):
            return existing
    existing = performance.get("latency_breakdown")
    if isinstance(existing, dict):
        if isinstance(existing.get("sections"), dict):
            return existing

    tool_timings = [dict(item) for item in performance.get("tool_timings") or [] if isinstance(item, dict)]
    tool_totals = _tool_timing_totals(tool_timings)
    trace_setup_ms = _metric_ms(performance, "langfuse_trace_create_ms") + _metric_ms(
        performance,
        "langfuse_trace_enter_ms",
    )
    top_level = {
        "history_load_ms": _metric_ms(performance, "history_load_ms"),
        "trace_setup_ms": trace_setup_ms,
        "prompt_load_ms": _metric_ms(performance, "langfuse_prompt_ms"),
        "initial_safety_ms": _metric_ms(performance, "initial_safety_ms"),
        "agent_execution_ms": _metric_ms(performance, "agent_execution_ms"),
        "response_guardrail_ms": _metric_ms(performance, "response_guardrail_llm_ms"),
        "final_safety_ms": _metric_ms(performance, "final_safety_ms"),
        "history_save_ms": _metric_ms(performance, "history_save_ms"),
    }
    top_level["unattributed_ms"] = max(0, int(latency_ms) - sum(top_level.values()))
    agent_detail = {
        "llm_setup_ms": _metric_ms(performance, "llm_setup_ms"),
        "fast_llm_setup_ms": _metric_ms(performance, "fast_llm_setup_ms"),
        "langfuse_callbacks_ms": _metric_ms(performance, "langfuse_callbacks_ms"),
        "llm_tool_choice_ms": _metric_ms(performance, "llm_tool_choice_ms"),
        "llm_final_ms": _metric_ms(performance, "llm_final_ms"),
        "llm_direct_answer_ms": _metric_ms(performance, "llm_direct_answer_ms"),
        "llm_total_ms": _metric_ms(performance, "llm_tool_choice_ms")
        + _metric_ms(performance, "llm_final_ms")
        + _metric_ms(performance, "llm_direct_answer_ms"),
        "catalog_ms": _metric_ms(performance, "catalog_ms"),
        "index_check_ms": _metric_ms(performance, "index_check_ms"),
        "retrieval_search_ms": _metric_ms(performance, "retrieval_search_ms"),
        "embedding_ms": _metric_ms(performance, "embedding_ms"),
        "opensearch_ms": _metric_ms(performance, "opensearch_ms"),
        "neighbor_ms": _metric_ms(performance, "neighbor_ms"),
        "access_filter_ms": _metric_ms(performance, "access_filter_ms"),
    }
    sections = {
        "history": {
            "load_ms": _metric_ms(performance, "history_load_ms"),
            "save_ms": _metric_ms(performance, "history_save_ms"),
            "save_background": bool(performance.get("history_save_background")),
        },
        "observability": {
            "langfuse_trace_create_ms": _metric_ms(performance, "langfuse_trace_create_ms"),
            "langfuse_trace_enter_ms": _metric_ms(performance, "langfuse_trace_enter_ms"),
            "trace_setup_ms": trace_setup_ms,
            "langfuse_prompt_ms": _metric_ms(performance, "langfuse_prompt_ms"),
            "langfuse_callbacks_ms": _metric_ms(performance, "langfuse_callbacks_ms"),
        },
        "safety_and_guardrail": {
            "initial_safety_ms": _metric_ms(performance, "initial_safety_ms"),
            "response_guardrail_llm_ms": _metric_ms(performance, "response_guardrail_llm_ms"),
            "response_guardrail_applied": bool(performance.get("response_guardrail_applied")),
            "response_guardrail_changed": bool(performance.get("response_guardrail_changed")),
            "response_guardrail_reason": str(performance.get("response_guardrail_reason") or ""),
            "final_safety_ms": _metric_ms(performance, "final_safety_ms"),
        },
        "agent_orchestration": {
            "agent_execution_ms": _metric_ms(performance, "agent_execution_ms"),
            "agent_mode": str(performance.get("agent_mode") or ""),
            "planned_tools": list(performance.get("planned_tools") or []),
            "llm_call_count": int(performance.get("llm_call_count") or 0),
        },
        "llm": {
            "llm_setup_ms": _metric_ms(performance, "llm_setup_ms"),
            "llm_cache_hit": bool(performance.get("llm_cache_hit")),
            "llm_setup_cold_start": bool(performance.get("llm_setup_cold_start")),
            "fast_llm_setup_ms": _metric_ms(performance, "fast_llm_setup_ms"),
            "fast_llm_cache_hit": bool(performance.get("fast_llm_cache_hit")),
            "fast_llm_setup_cold_start": bool(performance.get("fast_llm_setup_cold_start")),
            "llm_tool_choice_ms": _metric_ms(performance, "llm_tool_choice_ms"),
            "llm_final_ms": _metric_ms(performance, "llm_final_ms"),
            "llm_direct_answer_ms": _metric_ms(performance, "llm_direct_answer_ms"),
            "llm_total_ms": agent_detail["llm_total_ms"],
        },
        "retrieval_and_catalog": {
            "catalog_ms": _metric_ms(performance, "catalog_ms"),
            "index_check_ms": _metric_ms(performance, "index_check_ms"),
            "index_created": tool_totals["index_created"],
            "retrieval_search_ms": _metric_ms(performance, "retrieval_search_ms"),
            "embedding_ms": _metric_ms(performance, "embedding_ms"),
            "opensearch_ms": _metric_ms(performance, "opensearch_ms"),
            "neighbor_ms": _metric_ms(performance, "neighbor_ms"),
            "access_filter_ms": _metric_ms(performance, "access_filter_ms"),
            "tool_total_ms": tool_totals["total_ms"],
            "vector_hits": tool_totals["vector_hits"],
            "keyword_hits": tool_totals["keyword_hits"],
            "neighbor_hits": tool_totals["neighbor_hits"],
            "returned_hits": tool_totals["returned_hits"],
        },
    }
    return {
        "total_ms": int(latency_ms),
        "top_level": top_level,
        "agent_detail": agent_detail,
        "sections": sections,
        "raw_timing_metrics": _raw_timing_metrics(performance),
        "tool_timing_totals": tool_totals,
        "tool_timings": tool_timings,
    }


def _latency_breakdown_summary(latency_breakdown: dict[str, object]) -> str:
    top_level = latency_breakdown.get("top_level")
    if not isinstance(top_level, dict):
        return ""
    labels = {
        "agent_execution_ms": "agent",
        "history_load_ms": "history load",
        "trace_setup_ms": "trace",
        "prompt_load_ms": "prompt",
        "initial_safety_ms": "initial safety",
        "response_guardrail_ms": "guardrail",
        "final_safety_ms": "final safety",
        "history_save_ms": "history save",
        "unattributed_ms": "other",
    }
    values: list[tuple[str, int]] = []
    for key, label in labels.items():
        try:
            value = int(top_level.get(key) or 0)
        except Exception:
            value = 0
        if value:
            values.append((label, value))
    values.sort(key=lambda item: item[1], reverse=True)
    return ", ".join(f"{label} {value} ms" for label, value in values[:3])


def _parse_dashboard_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _dashboard_cutoff(range_key: str) -> datetime | None:
    window = DASHBOARD_RANGE_WINDOWS.get(range_key)
    if window is None:
        return None
    return datetime.now(timezone.utc) - window


def _dashboard_registered_users() -> list[str]:
    try:
        return [managed_user.username for managed_user in get_auth_service().list_users()]
    except Exception:
        return []


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
        "warmup": agent.warmup_status(),
    }


@app.get("/news", response_model=GuardianNewsResponse)
def guardian_news() -> GuardianNewsResponse:
    return GuardianNewsResponse(**get_news_service().get_payload())


@app.post("/auth/login", response_model=LoginResponse)
def login(request: LoginRequest) -> LoginResponse:
    try:
        return _login_response(get_auth_service().login(request.username, request.password))
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@app.get("/auth/me", response_model=AuthUserResponse)
def auth_me(user: HealthcareUserContext = Depends(current_user_context)) -> AuthUserResponse:
    return _auth_user_response(user)


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
    if filename.lower().endswith(".csv"):
        try:
            rows_inserted = get_deterministic_lookup_service().ingest_uploaded_csv(filename, data)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if rows_inserted == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No CSV lookup rows found")
        content_type = file.content_type or "text/csv"
        try:
            document_store = get_document_store()
            if hasattr(document_store, "upsert_manifest_record"):
                document_store.upsert_manifest_record(
                    _csv_manifest_record(filename, data, rows_inserted, content_type)
                )
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return AdminDocumentUploadResponse(
            key=f"postgres://uploaded_lookup_rows/{filename}",
            uri=f"postgres://uploaded_lookup_rows/{filename}",
            content_type=content_type,
            size_bytes=len(data),
        )
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


@app.patch("/admin/documents/metadata")
def update_admin_document_metadata(
    request: AdminDocumentMetadataUpdateRequest,
    user: HealthcareUserContext = Depends(admin_user_context),
) -> dict[str, object]:
    category = _normalize_document_metadata_value(request.category, "category")
    document_type = _normalize_document_metadata_value(request.document_type, "type")
    allowed_roles = _normalize_document_roles(request.allowed_roles)
    try:
        document_store = get_document_store()
        documents = document_store.list_documents()
        target = next(
            (
                document
                for document in documents
                if document.key == request.key or document.uri == request.key or document.title == request.key
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        metadata = dict(target.metadata or {})
        metadata["domain"] = category
        metadata["document_type"] = document_type
        metadata["allowed_roles"] = allowed_roles
        updated_record = {
            "title": target.title,
            "key": target.key,
            "uri": target.uri,
            "content_type": target.content_type,
            "metadata": metadata,
            "chunk_count": int(target.chunk_count or 0),
            "ingestion_status": target.ingestion_status or "",
        }
        if not hasattr(document_store, "upsert_manifest_record"):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Document manifest is read-only")
        document_store.upsert_manifest_record(updated_record)
        if hasattr(document_store, "invalidate_manifest_cache"):
            document_store.invalidate_manifest_cache()
        agent = get_agent()
        if hasattr(agent, "invalidate_caches"):
            agent.invalidate_caches()
        return updated_record
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@app.post("/admin/documents/delete-indexes", response_model=AdminDeleteIndexesResponse)
def delete_admin_document_indexes(
    request: AdminDeleteIndexesRequest,
    user: HealthcareUserContext = Depends(admin_user_context),
) -> AdminDeleteIndexesResponse:
    try:
        get_auth_service().verify_user_password(user.user_id, request.admin_password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    try:
        retrieval_service = get_retrieval_service()
        deleted_chunks = 0
        deleted_lookup_rows = 0
        if hasattr(retrieval_service, "delete_all_indexes"):
            deleted_chunks = int(retrieval_service.delete_all_indexes())
        elif hasattr(retrieval_service, "invalidate_cache"):
            retrieval_service.invalidate_cache()

        deterministic_lookup = get_deterministic_lookup_service()
        if hasattr(deterministic_lookup, "delete_uploaded_lookup_rows"):
            deleted_lookup_rows = int(deterministic_lookup.delete_uploaded_lookup_rows())

        document_store = get_document_store()
        if hasattr(document_store, "replace_manifest"):
            document_store.replace_manifest(_empty_index_manifest())
        if hasattr(document_store, "invalidate_manifest_cache"):
            document_store.invalidate_manifest_cache()

        agent = get_agent()
        if hasattr(agent, "invalidate_caches"):
            agent.invalidate_caches()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    return AdminDeleteIndexesResponse(
        deleted_chunks=deleted_chunks,
        deleted_lookup_rows=deleted_lookup_rows,
        manifest_cleared=True,
        backend="chroma" if get_settings().use_local_resources() else "opensearch",
        raw_documents_preserved=True,
        deterministic_lookup_preserved=False,
    )


@app.post("/admin/documents/ingest", response_model=AdminIngestionResponse)
def ingest_admin_documents(
    user: HealthcareUserContext = Depends(admin_user_context),
) -> AdminIngestionResponse:
    try:
        result = create_ingestion_job().run()
        document_store = get_document_store()
        if hasattr(document_store, "invalidate_manifest_cache"):
            document_store.invalidate_manifest_cache()
        retrieval_service = get_retrieval_service()
        if hasattr(retrieval_service, "invalidate_cache"):
            retrieval_service.invalidate_cache()
        agent = get_agent()
        if hasattr(agent, "invalidate_caches"):
            agent.invalidate_caches()
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


@app.get("/admin/dashboard")
def admin_dashboard(
    limit: int = Query(default=500, ge=1, le=2000),
    range: str = Query(default="all"),
    user_id: str = Query(default="all"),
    user: HealthcareUserContext = Depends(admin_user_context),
) -> dict[str, object]:
    range_key = range if range in DASHBOARD_RANGE_LABELS else "all"
    selected_user = user_id.strip() if user_id else "all"
    registered_users = _dashboard_registered_users()
    if selected_user != "all" and registered_users and selected_user not in registered_users:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown dashboard user filter")
    cutoff = _dashboard_cutoff(range_key)
    interactions = get_history_repository().list_recent_interactions(limit=limit)
    rows: list[dict[str, object]] = []
    tool_counts: dict[str, int] = {}
    tool_flow_counts: dict[str, int] = {}
    user_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    latencies: list[int] = []
    input_tokens: list[int] = []
    output_tokens: list[int] = []
    total_tokens: list[int] = []
    ragas_values: dict[str, list[float]] = {
        "ragas_faithfulness": [],
        "ragas_answer_relevancy": [],
        "ragas_context_precision": [],
        "ragas_context_recall": [],
    }
    guardrail_count = 0
    total_sources = 0

    for interaction in interactions:
        if selected_user != "all" and interaction.user_id != selected_user:
            continue
        if cutoff is not None:
            created_at = _parse_dashboard_datetime(interaction.created_at)
            if created_at is None or created_at < cutoff:
                continue
        metadata = interaction.metadata or {}
        performance = metadata.get("performance") if isinstance(metadata.get("performance"), dict) else {}
        tools_used = [str(tool) for tool in metadata.get("tools_used", [])]
        tool_flow = _tool_flow_from_metadata(metadata, tools_used)
        sources = metadata.get("sources", []) if isinstance(metadata.get("sources"), list) else []
        latency_ms = int(metadata.get("latency_ms") or performance.get("total_ms") or 0)
        latency_breakdown = _dashboard_latency_breakdown(metadata, performance, latency_ms)
        input_token_count = int(metadata.get("input_tokens") or 0)
        output_token_count = int(metadata.get("output_tokens") or 0)
        total_token_count = input_token_count + output_token_count
        model = str(metadata.get("model") or get_settings().azure_openai_deployment or "unknown")
        guardrail_applied = bool(metadata.get("guardrail_applied") or performance.get("response_guardrail_applied"))
        ragas_scores = metadata.get("ragas") if isinstance(metadata.get("ragas"), dict) else {}

        user_counts[interaction.user_id] = user_counts.get(interaction.user_id, 0) + 1
        model_counts[model] = model_counts.get(model, 0) + 1
        total_sources += len(sources)
        if latency_ms:
            latencies.append(latency_ms)
        if input_token_count:
            input_tokens.append(input_token_count)
        if output_token_count:
            output_tokens.append(output_token_count)
        if total_token_count:
            total_tokens.append(total_token_count)
        if guardrail_applied:
            guardrail_count += 1
        for score_name in ragas_values:
            try:
                value = ragas_scores.get(score_name)
                if value is not None:
                    ragas_values[score_name].append(float(value))
            except Exception:
                pass
        for tool in tools_used:
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
        for step in tool_flow:
            tool_name = str(step.get("tool") or "")
            if tool_name:
                tool_flow_counts[tool_name] = tool_flow_counts.get(tool_name, 0) + 1

        rows.append(
            {
                "user_id": interaction.user_id,
                "session_id": interaction.session_id,
                "created_at": interaction.created_at,
                "query": interaction.question,
                "answer": interaction.answer,
                "trace_id": metadata.get("trace_id"),
                "model": model,
                "tools_used": tools_used,
                "tool_flow": tool_flow,
                "tool_flow_summary": _tool_flow_summary(tool_flow),
                "source_count": len(sources),
                "source_document_keys": metadata.get("source_document_keys", []),
                "latency_ms": latency_ms,
                "latency_breakdown": latency_breakdown,
                "latency_breakdown_summary": _latency_breakdown_summary(latency_breakdown),
                "input_tokens": input_token_count,
                "output_tokens": output_token_count,
                "total_tokens": total_token_count,
                "agent_mode": performance.get("agent_mode"),
                "ragas": ragas_scores,
                "ragas_status": metadata.get("ragas_status"),
                "ragas_provider": metadata.get("ragas_provider"),
                "ragas_error": metadata.get("ragas_error"),
                "langfuse_ragas_published": metadata.get("langfuse_ragas_published"),
                "langfuse_ragas_error": metadata.get("langfuse_ragas_error"),
                "guardrail_applied": guardrail_applied,
                "guardrail_reason": metadata.get("guardrail_reason") or performance.get("response_guardrail_reason"),
                "safety": metadata.get("safety", {}),
            }
        )

    summary = {
        "total_queries": len(rows),
        "unique_users": len(user_counts),
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
        "max_latency_ms": max(latencies) if latencies else 0,
        "avg_input_tokens": int(sum(input_tokens) / len(input_tokens)) if input_tokens else 0,
        "avg_output_tokens": int(sum(output_tokens) / len(output_tokens)) if output_tokens else 0,
        "avg_total_tokens": int(sum(total_tokens) / len(total_tokens)) if total_tokens else 0,
        "avg_sources_per_query": (total_sources / len(rows)) if rows else 0,
        "guardrail_trigger_count": guardrail_count,
        "tool_counts": tool_counts,
        "tool_flow_counts": tool_flow_counts,
        "user_counts": user_counts,
        "model_counts": model_counts,
        "ragas": {
            score_name: (sum(values) / len(values) if values else None)
            for score_name, values in ragas_values.items()
        },
    }
    return {
        "summary": summary,
        "queries": rows,
        "filters": {
            "range": range_key,
            "range_label": DASHBOARD_RANGE_LABELS.get(range_key, "all time"),
            "user_id": selected_user,
            "available_ranges": [
                {"value": key, "label": label}
                for key, label in DASHBOARD_RANGE_LABELS.items()
            ],
            "users": registered_users,
        },
    }


@app.post("/admin/warmup")
def admin_warmup(
    user: HealthcareUserContext = Depends(admin_user_context),
) -> dict[str, object]:
    return get_agent().warm_up()


@app.get("/admin/patient-details")
def admin_patient_details(
    q: str = Query(default="", max_length=100),
    patient_identifier: str = Query(default="", max_length=80),
    department: str = Query(default="", max_length=100),
    ward: str = Query(default="", max_length=50),
    care_status: str = Query(default="", max_length=80),
    tables: list[str] = Query(default=[]),
    limit: int = Query(default=50, ge=1, le=250),
    user: HealthcareUserContext = Depends(admin_user_context),
) -> dict[str, object]:
    try:
        return get_deterministic_lookup_service().patient_dashboard(
            user=user,
            query=q,
            patient_identifier=patient_identifier,
            department=department,
            ward=ward,
            care_status=care_status,
            tables=tables,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


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
        latency_breakdown=result.metadata.get("latency_breakdown", {}),
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
        _document_record_payload(document)
        for document in agent.access.filter_documents(user, get_document_store().list_documents())
    ]
