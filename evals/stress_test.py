from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from collections import defaultdict
from typing import Any


BASE_QUESTIONS = [
    "Does Leo Bennett have any appointments?",
    "Does patient PAT-006 have any clinic slots?",
    "Leo Bennett is in which ward?",
    "Show me a list of available doctors and nurses for today and tomorrow",
    "Who is on call for Cardiology today?",
    "What is the phone number for ICU outreach?",
    "Is vancomycin restricted locally?",
    "What is the escalation process for suspected sepsis?",
    "Where is the incident reporting form?",
    "What should I do after sending patient data to the wrong recipient?",
    "Patient MRN MRN10006 has chest pain, what should I do?",
    "Which appointments are booked in Cardiology?",
    "Where is ward W07?",
    "Who is the named consultant for Leo Bennett?",
    "Show patient details for MRN10006.",
    "What documents are indexed for clinical policy?",
    "Which medicines need approval locally?",
    "Who can I contact for Radiology escalation?",
    "What is the latest NHS news?",
    "How should I answer if the indexed policy is not enough?",
]

PARAPHRASES = [
    "{q}",
    "Can you answer this using the right healthcare data source: {q}",
    "Give me the current operational answer for: {q}",
    "What source supports the answer to: {q}",
    "Please answer using structured lookup or approved documents: {q}",
]


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


def token_set(text: str) -> set[str]:
    return {term.lower().strip(".,;:!?()[]") for term in text.split() if len(term) > 3}


def jaccard(left: str, right: str) -> float:
    a = token_set(left)
    b = token_set(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 100-query consistency stress test")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--output", default="stress_report.json")
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for base in BASE_QUESTIONS:
        for template in PARAPHRASES:
            query = template.format(q=base)
            started = time.perf_counter()
            try:
                response = post_chat(args.api_url, args.token, query)
                error = None
            except Exception as exc:
                response = {"answer": "", "sources": [], "tools_used": []}
                error = str(exc)
            rows.append(
                {
                    "base_question": base,
                    "query": query,
                    "answer": response.get("answer", ""),
                    "sources": response.get("sources", []),
                    "tools_used": response.get("tools_used", []),
                    "input_tokens": response.get("input_tokens", 0),
                    "output_tokens": response.get("output_tokens", 0),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "error": error,
                }
            )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["base_question"]].append(row)

    consistency = {}
    for question, group in grouped.items():
        anchor = group[0]["answer"]
        similarities = [jaccard(anchor, row["answer"]) for row in group[1:]]
        source_sets = [
            {source.get("uri") for source in row["sources"] if source.get("uri")}
            for row in group
        ]
        source_overlap = 0.0
        if source_sets and source_sets[0]:
            source_overlap = sum(len(source_sets[0] & source_set) / len(source_sets[0]) for source_set in source_sets[1:]) / max(1, len(source_sets) - 1)
        consistency[question] = {
            "avg_answer_similarity": statistics.mean(similarities) if similarities else 1.0,
            "source_overlap": source_overlap,
            "errors": sum(1 for row in group if row["error"]),
        }

    latencies = [row["latency_ms"] for row in rows]
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_queries": len(rows),
        "failed_queries": sum(1 for row in rows if row["error"]),
        "latency_ms": {
            "min": min(latencies) if latencies else 0,
            "max": max(latencies) if latencies else 0,
            "avg": statistics.mean(latencies) if latencies else 0,
        },
        "consistency": consistency,
        "rows": rows,
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({k: report[k] for k in ["total_queries", "failed_queries", "latency_ms"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

