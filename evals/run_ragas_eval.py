from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def post_chat(api_url: str, token: str, question: str) -> dict[str, Any]:
    body = json.dumps({"query": question}).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/chat",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def load_dataset(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def simple_score(expected: str, answer: str) -> float:
    expected_terms = {term.lower() for term in expected.split() if len(term) > 3}
    answer_terms = {term.lower() for term in answer.split() if len(term) > 3}
    if not expected_terms:
        return 0.0
    return len(expected_terms & answer_terms) / len(expected_terms)


def maybe_run_ragas(rows: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

        dataset = Dataset.from_dict(
            {
                "question": [row["question"] for row in rows],
                "answer": [row["answer"] for row in rows],
                "contexts": [row["contexts"] for row in rows],
                "ground_truth": [row["expected_answer"] for row in rows],
            }
        )
        result = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        )
        return {"ragas_available": True, "ragas": result.to_pandas().to_dict(orient="records")}
    except Exception as exc:
        return {"ragas_available": False, "ragas_error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run golden-data RAGAS evaluation")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--dataset", default=str(Path(__file__).with_name("golden_dataset.csv")))
    parser.add_argument("--output", default="ragas_report.json")
    args = parser.parse_args()

    records = load_dataset(Path(args.dataset))
    rows: list[dict[str, Any]] = []
    for record in records:
        started = time.perf_counter()
        try:
            response = post_chat(args.api_url, args.token, record["question"])
            answer = response.get("answer", "")
            sources = response.get("sources", [])
            contexts = [source.get("uri", "") for source in sources] or ["No retrieved context returned"]
            error = None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            answer = ""
            sources = []
            contexts = ["Request failed"]
            error = str(exc)
        rows.append(
            {
                "question": record["question"],
                "expected_answer": record["expected_answer"],
                "expected_sources": record["expected_sources"],
                "answer": answer,
                "sources": sources,
                "contexts": contexts,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "simple_expected_overlap": simple_score(record["expected_answer"], answer),
                "error": error,
            }
        )

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rows": rows,
        "summary": {
            "total": len(rows),
            "failed": sum(1 for row in rows if row["error"]),
            "avg_simple_expected_overlap": (
                sum(row["simple_expected_overlap"] for row in rows) / len(rows) if rows else 0
            ),
        },
    }
    report.update(maybe_run_ragas(rows))
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

