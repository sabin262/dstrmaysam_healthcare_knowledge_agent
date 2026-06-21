from __future__ import annotations

import argparse
import csv
import json
import os
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
RAGAS_OVERALL_SCORE_NAME = "ragas_overall"


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


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def ragas_evaluator_models() -> tuple[Any | None, Any | None]:
    required = [
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
    ]
    if not all(os.getenv(name) for name in required):
        return None, None

    from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    llm = AzureChatOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ["AZURE_OPENAI_API_VERSION"],
        azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        temperature=0,
        timeout=60,
        max_retries=2,
    )
    embeddings = AzureOpenAIEmbeddings(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ["AZURE_OPENAI_API_VERSION"],
        azure_deployment=os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"],
    )
    return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(embeddings)


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
        llm, embeddings = ragas_evaluator_models()
        result = evaluate(
            dataset,
            metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
            llm=llm,
            embeddings=embeddings,
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
        values = [value for value in row["ragas"].values() if value is not None]
        if values:
            row["ragas"][RAGAS_OVERALL_SCORE_NAME] = sum(values) / len(values)

    summary = report.setdefault("summary", {})
    for metric in [*RAGAS_SCORE_NAMES, RAGAS_OVERALL_SCORE_NAME]:
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


def load_langfuse_secret_from_env() -> dict[str, str] | None:
    mapping = {
        "public_key": os.getenv("LANGFUSE_PUBLIC_KEY"),
        "secret_key": os.getenv("LANGFUSE_SECRET_KEY"),
        "base_url": os.getenv("LANGFUSE_BASE_URL"),
    }
    if not any(mapping.values()):
        return None
    missing = [key for key, value in mapping.items() if not value]
    if missing:
        raise RuntimeError(
            "Langfuse environment variables are incomplete. Set "
            "LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_BASE_URL."
        )
    return {key: str(value) for key, value in mapping.items() if value}


def create_langfuse_client(secret: dict[str, str]) -> Any:
    from langfuse import Langfuse

    return Langfuse(
        public_key=secret["public_key"],
        secret_key=secret["secret_key"],
        base_url=secret["base_url"],
    )


def langfuse_preflight(args: argparse.Namespace) -> dict[str, Any]:
    status: dict[str, Any] = {
        "chat_token_present": bool(args.token),
        "langfuse_env_present": bool(load_langfuse_secret_from_env()),
        "langfuse_secret_name": args.langfuse_secret_name,
        "langfuse_client_created": False,
        "langfuse_preflight_error": None,
    }
    try:
        secret = load_langfuse_secret_from_env()
        if secret is None:
            secret = load_langfuse_secret_from_aws(args.langfuse_secret_name, args.aws_region)
        client = create_langfuse_client(secret)
        status["langfuse_client_created"] = True
        if hasattr(client, "flush"):
            client.flush()
    except Exception as exc:
        status["langfuse_preflight_error"] = str(exc)
    return status


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
            secret = load_langfuse_secret_from_env()
            if secret is None:
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
                overall = row.get("ragas", {}).get(RAGAS_OVERALL_SCORE_NAME)
                if overall is not None:
                    create_numeric_score(
                        langfuse_client,
                        trace_id=trace_id,
                        name=RAGAS_OVERALL_SCORE_NAME,
                        value=float(overall),
                        comment="Mean of available RAGAS metrics for this question.",
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
        summary_scores[f"avg_{RAGAS_OVERALL_SCORE_NAME}"] = summary.get(f"avg_{RAGAS_OVERALL_SCORE_NAME}")

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
    parser.add_argument("--token", default=os.getenv("CHAT_API_TOKEN"))
    parser.add_argument("--dataset", default=str(Path(__file__).with_name("golden_dataset.csv")))
    parser.add_argument("--output", default="ragas_report.json")
    parser.add_argument("--publish-langfuse", action="store_true")
    parser.add_argument("--aws-region", default="eu-west-2")
    parser.add_argument("--secrets-stage", default="dev")
    parser.add_argument("--langfuse-secret-name", default=None)
    parser.add_argument("--eval-run-name", default="dstrmaysam-healthcare-knowledge-agent-ragas-eval")
    parser.add_argument("--preflight-langfuse", action="store_true")
    args = parser.parse_args()
    if not args.token:
        parser.error("--token is required unless CHAT_API_TOKEN is set")
    if not args.langfuse_secret_name:
        args.langfuse_secret_name = default_langfuse_secret_name(args.secrets_stage)
    load_env_file()

    if args.preflight_langfuse:
        print(json.dumps(langfuse_preflight(args), indent=2))
        return 0

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
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "ragas_available": report.get("ragas_available"),
                "ragas_error": report.get("ragas_error"),
                "langfuse_published": report.get("langfuse_published"),
                "langfuse_eval_trace_id": report.get("langfuse_eval_trace_id"),
                "langfuse_publish_error": report.get("langfuse_publish_error"),
                "output": args.output,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
