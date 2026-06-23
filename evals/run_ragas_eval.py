from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


RAGAS_SCORE_NAMES = {
    "faithfulness": "ragas_faithfulness",
    "answer_relevancy": "ragas_answer_relevancy",
    "context_precision": "ragas_context_precision",
    "context_recall": "ragas_context_recall",
}


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
    with urllib.request.urlopen(request, timeout=500) as response:
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
    ] or ["No retrieved context returned"]


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def metric_value(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        if number != number:
            return None
        return number
    except Exception:
        return None


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
        return {
            "ragas_available": True,
            "ragas": jsonable(result.to_pandas().to_dict(orient="records")),
        }
    except Exception as exc:
        return {"ragas_available": False, "ragas_error": str(exc), "ragas": []}


def attach_ragas_scores(report: dict[str, Any]) -> None:
    ragas_rows = report.get("ragas") or []
    for row, ragas_row in zip(report.get("rows", []), ragas_rows):
        row["ragas"] = {
            metric: metric_value(ragas_row.get(metric))
            for metric in RAGAS_SCORE_NAMES
            if metric in ragas_row
        }

    summary = report.setdefault("summary", {})
    for metric in RAGAS_SCORE_NAMES:
        values = [
            row.get("ragas", {}).get(metric)
            for row in report.get("rows", [])
            if row.get("ragas", {}).get(metric) is not None
        ]
        if values:
            summary[f"avg_{metric}"] = sum(values) / len(values)


def load_langfuse_secret_from_aws(secret_name: str, aws_region: str) -> dict[str, str]:
    import boto3

    client = boto3.client("secretsmanager", region_name=aws_region)
    response = client.get_secret_value(SecretId=secret_name)
    raw = response.get("SecretString")
    if not raw:
        raise RuntimeError(f"Secret {secret_name!r} does not contain SecretString JSON")
    data = json.loads(raw)
    required = ["public_key", "secret_key", "base_url"]
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise RuntimeError(f"Langfuse secret is missing required keys: {', '.join(missing)}")
    return {key: str(data[key]) for key in required}


def create_langfuse_client(secret: dict[str, str]) -> Any:
    from langfuse import Langfuse

    return Langfuse(
        public_key=secret["public_key"],
        secret_key=secret["secret_key"],
        base_url=secret["base_url"],
    )


def create_eval_trace(client: Any, run_name: str, report: dict[str, Any]) -> str:
    if hasattr(client, "create_trace_id"):
        trace_id = client.create_trace_id()
    else:
        trace_id = "0" * 32
    payload = {
        "total": report.get("summary", {}).get("total"),
        "failed": report.get("summary", {}).get("failed"),
        "generated_at": report.get("generated_at"),
    }
    try:
        manager = client.start_as_current_observation(
            name=run_name,
            as_type="evaluator",
            trace_context={"trace_id": trace_id},
            input={"dataset_size": report.get("summary", {}).get("total")},
            metadata={"report_generated_at": report.get("generated_at")},
        )
        with manager as span:
            if hasattr(span, "update"):
                span.update(output=payload)
    except TypeError:
        try:
            manager = client.start_as_current_observation(
                name=run_name,
                trace_context={"trace_id": trace_id},
                input={"dataset_size": report.get("summary", {}).get("total")},
                metadata={"report_generated_at": report.get("generated_at")},
            )
            with manager as span:
                if hasattr(span, "update"):
                    span.update(output=payload)
        except Exception:
            pass
    except Exception:
        try:
            client.create_event(
                trace_context={"trace_id": trace_id},
                name=run_name,
                input={"dataset_size": report.get("summary", {}).get("total")},
                output=payload,
                metadata={"report_generated_at": report.get("generated_at")},
            )
        except Exception:
            pass
    return trace_id


def create_numeric_score(
    client: Any,
    *,
    trace_id: str,
    name: str,
    value: float,
    comment: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    client.create_score(
        name=name,
        value=value,
        trace_id=trace_id,
        data_type="NUMERIC",
        comment=comment,
        metadata=metadata,
    )


def publish_langfuse_scores(report: dict[str, Any], args: argparse.Namespace, client: Any | None = None) -> dict[str, Any]:
    publish_status = {
        "langfuse_published": False,
        "langfuse_eval_trace_id": None,
        "langfuse_publish_error": None,
    }
    try:
        langfuse_client = client
        if langfuse_client is None:
            secret = load_langfuse_secret_from_aws(args.langfuse_secret_name, args.aws_region)
            langfuse_client = create_langfuse_client(secret)

        eval_trace_id = create_eval_trace(langfuse_client, args.eval_run_name, report)
        publish_status["langfuse_eval_trace_id"] = eval_trace_id

        for row in report.get("rows", []):
            row["langfuse_publish_status"] = "skipped"
            row["langfuse_publish_error"] = None
            trace_id = row.get("trace_id")
            if not trace_id:
                row["langfuse_publish_error"] = "Missing trace_id"
                continue
            try:
                create_numeric_score(
                    langfuse_client,
                    trace_id=trace_id,
                    name="simple_expected_overlap",
                    value=float(row.get("simple_expected_overlap", 0.0)),
                    metadata={"question": row.get("question")},
                )
                for metric, score_name in RAGAS_SCORE_NAMES.items():
                    value = row.get("ragas", {}).get(metric)
                    if value is not None:
                        create_numeric_score(
                            langfuse_client,
                            trace_id=trace_id,
                            name=score_name,
                            value=float(value),
                            metadata={"question": row.get("question")},
                        )
                row["langfuse_publish_status"] = "published"
            except Exception as exc:
                row["langfuse_publish_status"] = "failed"
                row["langfuse_publish_error"] = str(exc)

        summary = report.get("summary", {})
        summary_scores = {
            "avg_simple_expected_overlap": summary.get("avg_simple_expected_overlap"),
            "total_questions": summary.get("total"),
            "failed_questions": summary.get("failed"),
        }
        for metric in RAGAS_SCORE_NAMES:
            summary_scores[f"avg_{metric}"] = summary.get(f"avg_{metric}")

        for name, value in summary_scores.items():
            numeric = metric_value(value)
            if numeric is not None:
                create_numeric_score(
                    langfuse_client,
                    trace_id=eval_trace_id,
                    name=name,
                    value=numeric,
                    metadata={"eval_run_name": args.eval_run_name},
                )

        if hasattr(langfuse_client, "flush"):
            langfuse_client.flush()
        publish_status["langfuse_published"] = True
    except Exception as exc:
        publish_status["langfuse_publish_error"] = str(exc)
        for row in report.get("rows", []):
            row.setdefault("langfuse_publish_status", "failed")
            row.setdefault("langfuse_publish_error", str(exc))
    return publish_status


def default_langfuse_secret_name(stage: str) -> str:
    return f"/dstrmaysam-healthcare-knowledge-agent/{stage}/langfuse"


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    records = load_dataset(Path(args.dataset))
    rows: list[dict[str, Any]] = []
    for record in records:
        started = time.perf_counter()
        try:
            response = post_chat(args.api_url, args.token, record["question"])
            answer = response.get("answer", "")
            sources = response.get("sources", [])
            contexts = source_contexts(sources)
            trace_id = response.get("trace_id")
            error = None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            answer = ""
            sources = []
            contexts = ["Request failed"]
            trace_id = None
            error = str(exc)
        rows.append(
            {
                "question": record["question"],
                "expected_answer": record["expected_answer"],
                "expected_sources": record["expected_sources"],
                "answer": answer,
                "sources": sources,
                "contexts": contexts,
                "trace_id": trace_id,
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
    attach_ragas_scores(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run golden-data RAGAS evaluation")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--dataset", default=str(Path(__file__).with_name("golden_dataset.csv")))
    parser.add_argument("--output", default="ragas_report.json")
    parser.add_argument("--publish-langfuse", action="store_true")
    parser.add_argument("--aws-region", default="eu-west-2")
    parser.add_argument("--secrets-stage", default="dev")
    parser.add_argument("--langfuse-secret-name", default=None)
    parser.add_argument("--eval-run-name", default="dstrmaysam-healthcare-knowledge-agent-ragas-eval")
    args = parser.parse_args()
    if not args.langfuse_secret_name:
        args.langfuse_secret_name = default_langfuse_secret_name(args.secrets_stage)

    report = build_report(args)
    if args.publish_langfuse:
        report.update(publish_langfuse_scores(report, args))
    else:
        report.update(
            {
                "langfuse_published": False,
                "langfuse_eval_trace_id": None,
                "langfuse_publish_error": None,
            }
        )

    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
