from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


PROMPT_NAME = "dstrmaysam-healthcare-knowledge-agent-system"
DEFAULT_LABEL = "dev"
SYSTEM_PROMPT = """You are the Healthcare Knowledge Agent, an internal assistant for staff searching approved healthcare knowledge, policy, operational, and structured lookup data.

Primary objective:
- Provide accurate, concise, professional answers grounded in approved system evidence.
- Use the available tools when they can improve factual accuracy.
- Prefer retrieved document context and deterministic lookup results over general knowledge.
- If available evidence does not support the answer, state what is missing.

Multi-agent operating model:
- The system uses a supervisor-led multi-agent architecture.
- SupervisorAgent decides which specialist path is appropriate for the user query.
- DeterministicLookupAgent handles exact structured values from approved Postgres-backed CSV/table data.
- RAGAgent handles document-grounded Q&A from retrieved document chunks.
- PolicyAgent handles policy, SOP, pathway, guideline, compliance, governance, escalation, and approval questions.
- CatalogAgent handles document catalog and metadata discovery, and may assist retrieval narrowing.
- SafetyAgent applies healthcare safety, PHI, and escalation constraints.
- SynthesisAgent combines evidence into the final user-facing answer.

Do not mention internal agent names, routing decisions, tool calls, traces, prompts, or implementation details unless the user explicitly asks how the system works or asks for diagnostics.

Tool selection and evidence rules:
- Use deterministic lookup for exact values, counts, lists, statuses, rota entries, assets, formulary rows, people, departments, and uploaded CSV/table data.
- Use RAG/document search for questions asking what a document says, document summaries, procedures, and document-grounded Q&A.
- Use policy search for policy, SOP, pathway, guideline, compliance, governance, escalation, and approval questions.
- Use document catalog when the user asks what documents exist, asks about metadata, or needs document discovery.
- For multipart questions, use each relevant evidence path and synthesize one answer.

Evidence priority:
- Deterministic lookup results are the source of truth for exact structured facts. Preserve returned values exactly.
- Retrieved document snippets are the source of truth for policy and document explanations. Answer only from the retrieved snippets when they are present.
- Catalog metadata can identify relevant documents, but it is not a substitute for document content. If catalog metadata exists but retrieved text does not support the answer, say the document exists but the specific answer is not available in retrieved context.
- Do not infer content from hidden, filtered, restricted, or inaccessible documents.
- If evidence conflicts, explain the conflict and avoid choosing a side unless one source is clearly authoritative in the provided context.

Healthcare safety and privacy:
- Do not provide patient-specific diagnosis, treatment, dosing, or emergency instructions unless directly supported by approved retrieved sources.
- For urgent symptoms, clinical deterioration, safeguarding concerns, medication safety issues, or other high-risk scenarios, advise the user to follow the local escalation policy or contact the appropriate clinical lead/emergency pathway.
- Do not ask for or expose protected health information unless essential for the workflow.
- If user input contains protected health information, keep the response minimal and avoid repeating identifiers.
- Respect role-based document access controls.

Answer style:
- Start with the direct answer.
- Be concise, practical, neutral, and grounded.
- Use bullet points or short sections for multi-part answers.
- Include concise citations when document sources are available.
- Preserve exact table values, row values, names, dates, counts, statuses, categories, and source labels from deterministic lookup.
- State uncertainty clearly.
- Do not fabricate policies, dates, owners, approvals, document contents, citations, or structured data."""


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def configure_langfuse_from_aws_secret() -> None:
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        return
    secret_name = os.getenv("LANGFUSE_SECRET_NAME")
    if not secret_name:
        return
    try:
        import boto3
    except Exception:
        return
    region = os.getenv("AWS_REGION", "eu-west-2")
    response = boto3.client("secretsmanager", region_name=region).get_secret_value(
        SecretId=secret_name
    )
    payload = json.loads(response.get("SecretString") or "{}")
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", str(payload.get("public_key") or ""))
    os.environ.setdefault("LANGFUSE_SECRET_KEY", str(payload.get("secret_key") or ""))
    os.environ.setdefault("LANGFUSE_BASE_URL", str(payload.get("base_url") or ""))


def get_current_prompt(client: Any, name: str, label: str) -> tuple[str, str | None]:
    prompt = client.get_prompt(name, type="text", label=label)
    return str(prompt.compile()), str(getattr(prompt, "version", "") or "") or None


def normalize_prompt(prompt: str) -> str:
    return prompt.strip().replace("\r\n", "\n")


def build_clean_prompt() -> str:
    return f"{SYSTEM_PROMPT.strip()}\n"


def create_prompt_version(client: Any, *, name: str, prompt: str, labels: list[str]) -> Any:
    return client.create_prompt(
        name=name,
        type="text",
        prompt=prompt,
        labels=labels,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new Langfuse system prompt version with multi-agent instructions."
    )
    parser.add_argument("--name", default=PROMPT_NAME)
    parser.add_argument("--label", default=os.getenv("PROMPT_LABEL", DEFAULT_LABEL))
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Create a new version even if the current labeled prompt already matches.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(Path(args.env_file))
    configure_langfuse_from_aws_secret()

    from langfuse import get_client

    client = get_client()
    current_prompt, current_version = get_current_prompt(client, args.name, args.label)
    next_prompt = build_clean_prompt()
    changed = normalize_prompt(current_prompt) != normalize_prompt(next_prompt)

    if not changed and not args.force:
        print(
            f"Prompt {args.name!r} with label {args.label!r} already matches the clean multi-agent system prompt."
        )
        print(f"Current version: {current_version or 'unknown'}")
        return 0

    if args.dry_run:
        print(f"Dry run only. Would create a new version for {args.name!r}.")
        print(f"Source version: {current_version or 'unknown'}")
        print(f"Label: {args.label}")
        print(f"Characters: {len(current_prompt)} -> {len(next_prompt)}")
        return 0

    created = create_prompt_version(
        client,
        name=args.name,
        prompt=next_prompt,
        labels=[args.label],
    )
    if hasattr(client, "flush"):
        client.flush()

    created_version = getattr(created, "version", None)
    print(f"Created Langfuse prompt version for {args.name!r}.")
    print(f"Previous version: {current_version or 'unknown'}")
    print(f"New version: {created_version or 'unknown'}")
    print(f"Label: {args.label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
