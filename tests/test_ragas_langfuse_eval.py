import argparse
import json
import sys
import unittest

import evals.run_ragas_eval as ragas_eval
from evals.run_ragas_eval import (
    attach_ragas_scores,
    build_report,
    load_langfuse_secret_from_aws,
    publish_langfuse_scores,
    source_contexts,
)


class FakeObservation:
    def __init__(self):
        self.output = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def update(self, output=None, metadata=None):
        self.output = output


class FakeLangfuseClient:
    def __init__(self):
        self.scores = []
        self.flushed = False

    def create_trace_id(self):
        return "e" * 32

    def start_as_current_observation(self, **kwargs):
        return FakeObservation()

    def create_score(self, **kwargs):
        self.scores.append(kwargs)

    def flush(self):
        self.flushed = True


class EvalLangfuseTests(unittest.TestCase):
    def test_source_contexts_prefer_snippets_and_fallback_to_uris(self):
        self.assertEqual(
            source_contexts([{"uri": "s3://bucket/doc.md", "snippet": "Important context"}]),
            ["Important context"],
        )
        self.assertEqual(
            source_contexts([{"uri": "s3://bucket/doc.md"}]),
            ["s3://bucket/doc.md"],
        )

    def test_attach_ragas_scores_adds_row_and_summary_metrics(self):
        report = {
            "rows": [{"question": "Q1"}, {"question": "Q2"}],
            "ragas": [
                {
                    "faithfulness": 0.9,
                    "answer_relevancy": 0.8,
                    "context_precision": 0.7,
                    "context_recall": 0.6,
                },
                {
                    "faithfulness": 0.5,
                    "answer_relevancy": 0.4,
                    "context_precision": 0.3,
                    "context_recall": 0.2,
                },
            ],
            "summary": {},
        }

        attach_ragas_scores(report)

        self.assertEqual(report["rows"][0]["ragas"]["faithfulness"], 0.9)
        self.assertAlmostEqual(report["summary"]["avg_faithfulness"], 0.7)
        self.assertAlmostEqual(report["summary"]["avg_context_recall"], 0.4)

    def test_load_langfuse_secret_from_aws_uses_secret_manager_shape(self):
        class FakeSecretsClient:
            def get_secret_value(self, SecretId):
                return {
                    "SecretString": json.dumps(
                        {
                            "public_key": "pk",
                            "secret_key": "sk",
                            "base_url": "https://cloud.langfuse.com",
                        }
                    )
                }

        class FakeBoto3:
            def client(self, service_name, region_name=None):
                self.service_name = service_name
                self.region_name = region_name
                return FakeSecretsClient()

        original = sys.modules.get("boto3")
        sys.modules["boto3"] = FakeBoto3()
        try:
            secret = load_langfuse_secret_from_aws("/secret", "eu-west-2")
        finally:
            if original is None:
                sys.modules.pop("boto3", None)
            else:
                sys.modules["boto3"] = original

        self.assertEqual(secret["public_key"], "pk")
        self.assertEqual(secret["secret_key"], "sk")
        self.assertEqual(secret["base_url"], "https://cloud.langfuse.com")

    def test_publish_langfuse_scores_writes_row_and_summary_scores(self):
        report = {
            "generated_at": "2026-06-18T00:00:00Z",
            "rows": [
                {
                    "question": "Q1",
                    "trace_id": "a" * 32,
                    "simple_expected_overlap": 0.5,
                    "ragas": {
                        "faithfulness": 0.9,
                        "answer_relevancy": 0.8,
                        "context_precision": 0.7,
                        "context_recall": 0.6,
                    },
                }
            ],
            "summary": {
                "total": 1,
                "failed": 0,
                "avg_simple_expected_overlap": 0.5,
                "avg_faithfulness": 0.9,
                "avg_answer_relevancy": 0.8,
                "avg_context_precision": 0.7,
                "avg_context_recall": 0.6,
            },
        }
        args = argparse.Namespace(
            eval_run_name="unit-eval",
            langfuse_secret_name="/unused",
            aws_region="eu-west-2",
        )
        client = FakeLangfuseClient()

        status = publish_langfuse_scores(report, args, client=client)

        self.assertTrue(status["langfuse_published"])
        self.assertEqual(status["langfuse_eval_trace_id"], "e" * 32)
        self.assertEqual(report["rows"][0]["langfuse_publish_status"], "published")
        score_names = {score["name"] for score in client.scores}
        self.assertIn("simple_expected_overlap", score_names)
        self.assertIn("ragas_faithfulness", score_names)
        self.assertIn("avg_faithfulness", score_names)
        self.assertIn("total_questions", score_names)
        self.assertTrue(client.flushed)

    def test_build_report_uses_mocked_api_and_ragas_output(self):
        original_load_dataset = ragas_eval.load_dataset
        original_post_chat = ragas_eval.post_chat
        original_maybe_run_ragas = ragas_eval.maybe_run_ragas

        def fake_load_dataset(path):
            return [
                {
                    "question": "What is leave?",
                    "expected_answer": "Annual leave policy",
                    "expected_sources": "leave-policy",
                }
            ]

        def fake_post_chat(api_url, token, question):
            return {
                "answer": "Annual leave policy",
                "trace_id": "b" * 32,
                "sources": [
                    {
                        "uri": "s3://bucket/leave.md",
                        "snippet": "Annual leave policy source text",
                    }
                ],
            }

        def fake_maybe_run_ragas(rows):
            return {
                "ragas_available": True,
                "ragas": [
                    {
                        "faithfulness": 0.91,
                        "answer_relevancy": 0.82,
                        "context_precision": 0.73,
                        "context_recall": 0.64,
                    }
                ],
            }

        ragas_eval.load_dataset = fake_load_dataset
        ragas_eval.post_chat = fake_post_chat
        ragas_eval.maybe_run_ragas = fake_maybe_run_ragas
        try:
            report = build_report(
                argparse.Namespace(
                    api_url="http://api",
                    token="token",
                    dataset="dataset.csv",
                )
            )
        finally:
            ragas_eval.load_dataset = original_load_dataset
            ragas_eval.post_chat = original_post_chat
            ragas_eval.maybe_run_ragas = original_maybe_run_ragas

        self.assertEqual(report["rows"][0]["trace_id"], "b" * 32)
        self.assertEqual(report["rows"][0]["contexts"], ["Annual leave policy source text"])
        self.assertEqual(report["rows"][0]["ragas"]["faithfulness"], 0.91)
        self.assertAlmostEqual(report["summary"]["avg_context_precision"], 0.73)


if __name__ == "__main__":
    unittest.main()
