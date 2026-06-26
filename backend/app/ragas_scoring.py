from __future__ import annotations

import re
import os
import asyncio
import inspect
from typing import TYPE_CHECKING, Any

os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

if TYPE_CHECKING:
    from .config import AppSettings
    from .secrets import SecretProvider


RAGAS_SCORE_NAMES = {
    "faithfulness": "ragas_faithfulness",
    "answer_relevancy": "ragas_answer_relevancy",
    "context_precision": "ragas_context_precision",
    "llm_context_precision_without_reference": "ragas_context_precision",
    "context_recall": "ragas_context_recall",
}


def source_contexts(sources: list[dict[str, Any]]) -> list[str]:
    contexts = [
        str(source.get("snippet") or "").strip()
        for source in sources
        if str(source.get("snippet") or "").strip()
    ]
    if contexts:
        return contexts
    return [
        str(source.get("uri") or "").strip()
        for source in sources
        if str(source.get("uri") or "").strip()
    ]


def compute_live_ragas_scores(
    *,
    question: str,
    answer: str,
    sources: list[dict[str, Any]],
    settings: "AppSettings | None" = None,
    secret_provider: "SecretProvider | None" = None,
) -> dict[str, Any]:
    contexts = source_contexts(sources)
    if not answer.strip() or not contexts:
        return {
            "scores": {},
            "status": "skipped",
            "provider": None,
            "error": "Answer or retrieved context missing.",
        }

    try:
        scores = _compute_with_ragas(
            question=question,
            answer=answer,
            contexts=contexts,
            settings=settings,
            secret_provider=secret_provider,
        )
        if scores:
            return {
                "scores": scores,
                "status": "scored",
                "provider": "ragas_azure_openai" if settings and secret_provider else "ragas",
                "error": None,
            }
    except Exception as exc:
        fallback = _lexical_fallback_scores(question=question, answer=answer, contexts=contexts)
        return {
            "scores": fallback,
            "status": "fallback_scored",
            "provider": "lexical_fallback",
            "error": f"{type(exc).__name__}: {exc}",
        }

    fallback = _lexical_fallback_scores(question=question, answer=answer, contexts=contexts)
    return {
        "scores": fallback,
        "status": "fallback_scored",
        "provider": "lexical_fallback",
        "error": "RAGAS returned no numeric scores.",
    }


def _compute_with_ragas(
    question: str,
    answer: str,
    contexts: list[str],
    *,
    settings: "AppSettings | None" = None,
    secret_provider: "SecretProvider | None" = None,
) -> dict[str, float]:
    os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import LLMContextPrecisionWithoutReference, answer_relevancy, faithfulness

    dataset = Dataset.from_dict(
        {
            "user_input": [question],
            "response": [answer],
            "retrieved_contexts": [contexts],
        }
    )
    ragas_llm = None
    ragas_embeddings = None
    if settings is not None and secret_provider is not None:
        ragas_llm, ragas_embeddings = _build_ragas_azure_clients(settings, secret_provider)

    try:
        result = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy, LLMContextPrecisionWithoutReference()],
            llm=ragas_llm,
            embeddings=ragas_embeddings,
            raise_exceptions=True,
            show_progress=False,
        )
    finally:
        _close_ragas_clients(ragas_llm, ragas_embeddings)
    rows = result.to_pandas().to_dict(orient="records")
    if not rows:
        return {}
    scores: dict[str, float] = {}
    for metric, score_name in RAGAS_SCORE_NAMES.items():
        value = rows[0].get(metric)
        numeric = _metric_value(value)
        if numeric is not None:
            scores[score_name] = numeric
    return scores


def _build_ragas_azure_clients(
    settings: "AppSettings",
    secret_provider: "SecretProvider",
) -> tuple[Any, Any]:
    os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")
    from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
    from ragas.embeddings.base import LangchainEmbeddingsWrapper
    from ragas.llms.base import LangchainLLMWrapper
    from ragas.run_config import RunConfig

    secrets = secret_provider.load_azure_openai()
    run_config = RunConfig(timeout=90, max_retries=2, max_workers=1)
    llm = AzureChatOpenAI(
        azure_endpoint=secrets.endpoint,
        api_key=secrets.api_key,
        api_version=secrets.api_version,
        azure_deployment=secrets.fast_chat_deployment or secrets.chat_deployment,
        temperature=0,
        n=1,
        timeout=60,
        max_retries=2,
    )
    embeddings = AzureOpenAIEmbeddings(
        azure_endpoint=secrets.endpoint,
        api_key=secrets.api_key,
        api_version=secrets.api_version,
        azure_deployment=secrets.embedding_deployment,
        timeout=60,
        max_retries=2,
    )
    return (
        LangchainLLMWrapper(llm, run_config=run_config, bypass_n=True),
        LangchainEmbeddingsWrapper(embeddings, run_config=run_config),
    )


def _close_ragas_clients(ragas_llm: Any, ragas_embeddings: Any) -> None:
    for owner in _ragas_client_owners(ragas_llm, ragas_embeddings):
        for attr in ("root_async_client", "root_client", "http_async_client", "http_client"):
            _close_maybe_async(getattr(owner, attr, None))
        for resource_attr in ("async_client", "client"):
            resource = getattr(owner, resource_attr, None)
            _close_maybe_async(getattr(resource, "_client", None))


def _ragas_client_owners(ragas_llm: Any, ragas_embeddings: Any) -> list[Any]:
    owners: list[Any] = []
    llm = getattr(ragas_llm, "langchain_llm", None)
    if llm is not None:
        owners.append(llm)
    embeddings = getattr(ragas_embeddings, "embeddings", None)
    if embeddings is not None:
        owners.append(embeddings)
    return owners


def _close_maybe_async(client: Any) -> None:
    if client is None:
        return
    try:
        is_closed = getattr(client, "is_closed", None)
        if callable(is_closed) and is_closed():
            return
        close = getattr(client, "close", None)
        if close is None:
            return
        if inspect.iscoroutinefunction(close):
            asyncio.run(close())
            return
        result = close()
        if inspect.isawaitable(result):
            asyncio.run(result)
    except RuntimeError as exc:
        if "Event loop is closed" not in str(exc):
            raise


def _lexical_fallback_scores(question: str, answer: str, contexts: list[str]) -> dict[str, float]:
    question_terms = _terms(question)
    answer_terms = _terms(answer)
    context_text = " ".join(contexts)
    context_terms = _terms(context_text)
    combined_terms = question_terms | answer_terms
    context_precision_values = [
        _overlap(_terms(context), combined_terms)
        for context in contexts
        if context.strip()
    ]
    answer_context_overlap = _overlap(answer_terms, context_terms)
    return {
        "ragas_faithfulness": answer_context_overlap,
        "ragas_answer_relevancy": _overlap(answer_terms, question_terms),
        "ragas_context_precision": (
            sum(context_precision_values) / len(context_precision_values)
            if context_precision_values
            else 0.0
        ),
        "ragas_context_recall": answer_context_overlap,
    }


def _terms(text: str) -> set[str]:
    return {
        term.lower()
        for term in re.findall(r"[A-Za-z0-9]+", text)
        if len(term) > 3
    }


def _overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


def _metric_value(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if number != number:
            return None
        return number
    except Exception:
        return None
