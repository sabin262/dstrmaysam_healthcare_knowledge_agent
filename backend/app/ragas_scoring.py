from __future__ import annotations

import re
import os
from typing import Any


RAGAS_SCORE_NAMES = {
    "faithfulness": "ragas_faithfulness",
    "answer_relevancy": "ragas_answer_relevancy",
    "context_precision": "ragas_context_precision",
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
        scores = _compute_with_ragas(question=question, answer=answer, contexts=contexts)
        if scores:
            return {"scores": scores, "status": "scored", "provider": "ragas", "error": None}
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


def _compute_with_ragas(question: str, answer: str, contexts: list[str]) -> dict[str, float]:
    os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import answer_relevancy, faithfulness

    dataset = Dataset.from_dict(
        {
            "question": [question],
            "answer": [answer],
            "contexts": [contexts],
        }
    )
    result = evaluate(dataset, metrics=[faithfulness, answer_relevancy])
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
