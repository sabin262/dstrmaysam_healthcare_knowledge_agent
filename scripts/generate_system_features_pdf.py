from __future__ import annotations

from pathlib import Path
import textwrap


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "dstrmaysam_healthcare_knowledge_agent_system_features.pdf"

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
MARGIN_X = 54
TOP_Y = 730
BOTTOM_Y = 58
LINE_HEIGHT = 13
BODY_WIDTH = 86


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap(text: str, width: int = BODY_WIDTH) -> list[str]:
    if not text:
        return [""]
    if text.startswith("    "):
        return [text]
    return textwrap.wrap(text, width=width, break_long_words=False, replace_whitespace=False) or [""]


class SimplePdf:
    def __init__(self, title: str):
        self.title = title
        self.pages: list[list[tuple[str, int, int, str]]] = []
        self.current: list[tuple[str, int, int, str]] = []
        self.y = TOP_Y
        self.page_number = 0
        self.new_page()

    def new_page(self) -> None:
        if self.current:
            self.pages.append(self.current)
        self.current = []
        self.page_number += 1
        self.y = TOP_Y
        if self.page_number > 1:
            self.text(self.title, size=9, font="F2", y=760)
            self.line()

    def ensure_space(self, lines: int = 1) -> None:
        if self.y - (lines * LINE_HEIGHT) < BOTTOM_Y:
            self.new_page()

    def text(self, text: str, *, size: int = 10, font: str = "F1", x: int = MARGIN_X, y: int | None = None) -> None:
        if y is None:
            self.ensure_space()
            y = self.y
            self.y -= LINE_HEIGHT
        self.current.append((font, size, x, f"1 0 0 1 {x} {y} Tm ({esc(text)}) Tj"))

    def paragraph(self, text: str, *, size: int = 10, font: str = "F1", indent: int = 0) -> None:
        for line in wrap(text, BODY_WIDTH - (indent // 5)):
            self.text(line, size=size, font=font, x=MARGIN_X + indent)

    def bullet(self, text: str, *, size: int = 10, indent: int = 12) -> None:
        lines = wrap(text, BODY_WIDTH - 5)
        for index, line in enumerate(lines):
            prefix = "- " if index == 0 else "  "
            self.text(prefix + line, size=size, x=MARGIN_X + indent)

    def h1(self, text: str) -> None:
        self.ensure_space(4)
        self.y -= 6
        self.text(text, size=16, font="F2")
        self.line()

    def h2(self, text: str) -> None:
        self.ensure_space(3)
        self.y -= 4
        self.text(text, size=13, font="F2")

    def h3(self, text: str) -> None:
        self.ensure_space(2)
        self.text(text, size=11, font="F2")

    def line(self) -> None:
        self.ensure_space()
        self.current.append(("F1", 1, MARGIN_X, f"{MARGIN_X} {self.y + 5} m {PAGE_WIDTH - MARGIN_X} {self.y + 5} l S"))
        self.y -= 8

    def spacer(self, lines: int = 1) -> None:
        self.y -= LINE_HEIGHT * lines

    def code_block(self, lines: list[str]) -> None:
        for line in lines:
            self.text(line[:96], size=8, font="F3", x=MARGIN_X + 12)

    def finish(self) -> None:
        if self.current:
            self.pages.append(self.current)
            self.current = []

    def save(self, path: Path) -> None:
        self.finish()
        objects: list[bytes] = []
        catalog_id = 1
        pages_id = 2
        font1_id = 3
        font2_id = 4
        font3_id = 5
        next_id = 6
        page_ids: list[int] = []
        content_ids: list[int] = []
        for _ in self.pages:
            page_ids.append(next_id)
            content_ids.append(next_id + 1)
            next_id += 2

        def obj(body: str | bytes) -> None:
            if isinstance(body, str):
                body = body.encode("latin-1")
            objects.append(body)

        obj(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
        kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
        obj(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>")
        obj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        obj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
        obj("<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

        for index, page in enumerate(self.pages):
            content_parts = ["q", "0 0 0 rg", "BT"]
            current_font = None
            for font, size, _x, command in page:
                if command.endswith(" S"):
                    content_parts.append("ET")
                    content_parts.append(command)
                    content_parts.append("BT")
                    current_font = None
                    continue
                font_ref = f"/{font} {size} Tf"
                if current_font != font_ref:
                    content_parts.append(font_ref)
                    current_font = font_ref
                content_parts.append(command)
            footer = f"1 0 0 1 {PAGE_WIDTH - 90} 32 Tm (Page {index + 1}) Tj"
            content_parts.append("/F1 8 Tf")
            content_parts.append(footer)
            content_parts.append("ET")
            content_parts.append("Q")
            stream = "\n".join(content_parts).encode("latin-1")
            obj(
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font1_id} 0 R /F2 {font2_id} 0 R /F3 {font3_id} 0 R >> >> "
                f"/Contents {content_ids[index]} 0 R >>"
            )
            obj(b"<< /Length " + str(len(stream)).encode("latin-1") + b" >>\nstream\n" + stream + b"\nendstream")

        offsets: list[int] = []
        output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        for obj_id, body in enumerate(objects, start=1):
            offsets.append(len(output))
            output.extend(f"{obj_id} 0 obj\n".encode("latin-1"))
            output.extend(body)
            output.extend(b"\nendobj\n")
        xref_at = len(output)
        output.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
        output.extend(b"0000000000 65535 f \n")
        for offset in offsets:
            output.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
        output.extend(
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode(
                "latin-1"
            )
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(output))


def add_title(pdf: SimplePdf) -> None:
    pdf.text("Healthcare Knowledge Agent", size=20, font="F2", y=720)
    pdf.text("System Feature Guide", size=18, font="F2", y=694)
    pdf.text("Generated for stakeholder and delivery review", size=11, y=670)
    pdf.text("Current implementation snapshot: 2026-06-23", size=10, y=650)
    pdf.spacer(3)
    pdf.paragraph(
        "This document details the major features, design choices, operational behavior, and agent workflow of the "
        "healthcare knowledge-agent system. It is written for product, technical, and delivery stakeholders who need "
        "a clear view of what the platform does and why the current architecture supports those capabilities."
    )


def add_section_overview(pdf: SimplePdf) -> None:
    pdf.h1("1. Product Overview")
    pdf.paragraph(
        "The system is an authenticated healthcare knowledge assistant. It combines document question answering, "
        "structured deterministic lookup, administrator-controlled document ingestion, patient-detail querying, "
        "observability, evaluation metrics, and role-aware governance into one Streamlit and FastAPI application."
    )
    for item in [
        "Primary interface: Streamlit multipage frontend for chat, dashboard, patient details, users, and documents.",
        "Backend API: FastAPI service with authentication, chat orchestration, document APIs, admin APIs, and dashboards.",
        "Agent runtime: LangGraph-based execution with fast planned paths for common RAG and deterministic questions.",
        "Model provider: Azure OpenAI through LangChain OpenAI integrations, allowing Azure deployment configuration.",
        "Retrieval: OpenSearch Serverless in AWS mode and persistent ChromaDB in local mode.",
        "Observability: Langfuse traces, tool flow metadata, latency breakdowns, token counts, and RAGAS scores.",
    ]:
        pdf.bullet(item)

    pdf.h2("Why this feature set exists")
    pdf.paragraph(
        "Healthcare knowledge work often mixes policy interpretation, operational lookup, and document evidence. The "
        "system separates those modes intentionally: RAG is used for narrative document content, deterministic lookup "
        "is used for exact database facts, and dashboards make the quality and latency of each interaction auditable."
    )


def add_deployment(pdf: SimplePdf) -> None:
    pdf.h1("2. Deployment And Runtime Modes")
    pdf.h2("AWS development mode")
    for item in [
        "Secrets come from AWS Secrets Manager so application credentials, Azure OpenAI keys, and Langfuse keys are not hardcoded.",
        "Documents are uploaded to S3 and indexed into AWS OpenSearch Serverless.",
        "OpenSearch index creation is checked before indexing/searching; if the index is absent, the backend creates a compatible knn_vector mapping.",
        "Chat history can use the configured deployed history backend, including DynamoDB or Postgres depending on configuration.",
        "ECS task definitions and IAM policies define the deployable container environment.",
    ]:
        pdf.bullet(item)
    pdf.h2("Local mode")
    for item in [
        "LOCAL_TEST_ADMIN_ENABLED=true switches the backend to local resources.",
        "Local files are stored under the project data folder and indexed into persistent ChromaDB.",
        "Secrets are read from .env/local JSON; admin-created users persist to data/local_app_secret.json.",
        "Chat history is persisted in Postgres for local mode so browser refreshes and restarts keep conversation history.",
    ]:
        pdf.bullet(item)
    pdf.h2("Reasoning")
    pdf.paragraph(
        "The split between AWS mode and local mode lets developers work without depending on cloud services while keeping "
        "the deployed path close to production-like AWS architecture. A single switch reduces environment drift."
    )


def add_security(pdf: SimplePdf) -> None:
    pdf.h1("3. Identity, Roles, And Administration")
    for item in [
        "Authentication uses bearer tokens issued by the backend after username/password login.",
        "The login response includes username, roles, departments, and password_change_required.",
        "First-login or reset-password users must change password before chat, documents, or admin routes are usable.",
        "Admin APIs create users, update roles/departments, reset passwords, and protect the final admin from losing admin role.",
        "Known roles include admin, staff, doctor, nurse, pharmacy, clinical_governance, and manager.",
        "Departments are normalized for consistent authorization and filtering.",
    ]:
        pdf.bullet(item)
    pdf.h2("Reasoning")
    pdf.paragraph(
        "Role and department claims are kept close to the auth token so the agent, document filtering, deterministic "
        "lookup, and admin APIs can make consistent access decisions. Forced password change protects temporary-password "
        "workflows without needing a full identity provider integration."
    )


def add_documents(pdf: SimplePdf) -> None:
    pdf.h1("4. Document Management And Indexing")
    for item in [
        "Admins can upload PDF, DOCX, TXT, Markdown, and CSV files.",
        "Non-CSV documents are stored as raw documents and parsed into chunks for RAG.",
        "CSV uploads are ingested into Postgres deterministic lookup tables and added to the document manifest as metadata-only assets.",
        "The documents page always shows indexed document table data: file name, key, chunks, category, type, status, and URI.",
        "Admins can trigger ingestion and indexing from the frontend.",
        "Admins can delete all search/vector indexes after confirming with their admin password; uploaded source files and deterministic lookup rows are preserved.",
        "Incremental indexing skips unchanged files, reindexes changed files, deletes removed files, and forces reindex when the configured search index changes.",
    ]:
        pdf.bullet(item)
    pdf.h2("Reasoning")
    pdf.paragraph(
        "The ingestion workflow favors repeatability and operational safety. It avoids reprocessing unchanged content, keeps "
        "CSV deterministic data separate from vector retrieval, and gives administrators a controlled reset path for search indexes."
    )


def add_retrieval(pdf: SimplePdf) -> None:
    pdf.h1("5. Retrieval And Knowledge Search")
    pdf.h2("Catalog-guided RAG")
    for item in [
        "The document catalog loads manifest metadata and matches query terms against title, key, content type, and metadata JSON.",
        "For rag_search, document_search, and policy_search, the catalog narrows candidates before vector/keyword search.",
        "Candidate keys are limited to reduce search scope and latency.",
        "Policy search prefers clinical policy, admin policy, compliance, policy, SOP, pathway, and guideline documents.",
        "If no catalog candidates match, the system falls back to broad retrieval to avoid false negatives.",
    ]:
        pdf.bullet(item)
    pdf.h2("OpenSearch and ChromaDB")
    for item in [
        "AWS mode uses OpenSearch Serverless with vector plus keyword search.",
        "Local mode uses persistent ChromaDB with equivalent retrieval contract.",
        "OpenSearch search uses multi-search where available for vector and keyword search.",
        "Neighbor chunk expansion fetches adjacent chunks to improve answer completeness.",
        "Query embedding and retrieval result caches reduce repeated-query latency.",
    ]:
        pdf.bullet(item)
    pdf.h2("Reasoning")
    pdf.paragraph(
        "Pure vector search can miss metadata-specific questions, while pure keyword search misses semantic matches. Combining "
        "catalog narrowing, vector search, keyword search, access filtering, and neighbor expansion improves precision and context coverage."
    )


def add_deterministic(pdf: SimplePdf) -> None:
    pdf.h1("6. Deterministic Lookup And Patient Data")
    for item in [
        "The Postgres deterministic lookup tool handles exact facts such as doctors on call, patients, appointments, wards, contacts, departments, formularies, and schedule rows.",
        "Uploaded CSV lookup files are stored in Postgres as the source of truth.",
        "CSV files are represented in the document manifest as metadata-only assets so administrators can see that they are available.",
        "The patient details dashboard filters and queries configured Postgres tables, including patient and appointment-style data.",
        "Deterministic answers preserve exact values and return structured rows instead of relying on vector similarity.",
    ]:
        pdf.bullet(item)
    pdf.h2("Reasoning")
    pdf.paragraph(
        "Structured healthcare data should not be approximated through embeddings. Postgres lookup provides precise results, "
        "supports filters, and keeps table data queryable without inflating the vector index."
    )


def add_chat(pdf: SimplePdf) -> None:
    pdf.h1("7. Chat Experience")
    for item in [
        "The Streamlit app uses native multipage navigation for smooth page switching.",
        "Chat has a scrollable message window with a fixed query form below the chat area.",
        "The user query appears in the chat window immediately while the answer is processing.",
        "Chat sessions are persisted and available from the Chat page sidebar only, reducing non-chat page overhead.",
        "Responses include citations/sources from retrieval-backed tools and optional snippets.",
        "The frontend hides operational response details from the normal chat view; those details are available in dashboards and traces.",
    ]:
        pdf.bullet(item)
    pdf.h2("Reasoning")
    pdf.paragraph(
        "The chat page is optimized for day-to-day use. Heavy admin pages and history loading are isolated so typing and "
        "chat interaction remain responsive."
    )


def add_observability(pdf: SimplePdf) -> None:
    pdf.h1("8. Observability, Evaluation, And Dashboarding")
    for item in [
        "Each chat request receives a trace ID; Langfuse traces are used when configured and local IDs are used as fallback.",
        "Trace metadata includes user/session, model, prompt label, tools used, tool flow, sources, safety, token counts, and latency.",
        "The admin dashboard shows per-query and aggregate views for latency, tokens, users, tools, models, sources, guardrails, and RAGAS.",
        "Latency breakdown includes history, prompt, trace setup, LLM setup, LLM calls, retrieval, catalog, index checks, guardrail, safety, and history save timings.",
        "RAGAS scoring runs in the background and publishes faithfulness, answer relevancy, context precision, and context recall to Langfuse.",
        "The eval script can publish RAGAS scores to response traces and create an evaluation-run summary trace.",
    ]:
        pdf.bullet(item)
    pdf.h2("Reasoning")
    pdf.paragraph(
        "Healthcare-facing knowledge systems need auditability. The dashboard and Langfuse traces make it possible to diagnose "
        "latency, tool choice, retrieval quality, prompt behavior, and answer quality after the fact."
    )


def add_guardrails(pdf: SimplePdf) -> None:
    pdf.h1("9. Safety And Guardrails")
    for item in [
        "PHI redaction is applied before agent input.",
        "Initial safety context is included before answer generation.",
        "Final safety assessment runs after answer generation using actual sources.",
        "Response-style guardrail rewrites are triggered when the query or draft answer contains risky tone/style markers.",
        "Guardrails require professional, neutral, concise responses and remove jokes, sarcasm, roleplay, slang, emojis, and theatrical wording.",
        "The guardrail rewrite uses the fast Azure OpenAI deployment when available.",
    ]:
        pdf.bullet(item)
    pdf.h2("Reasoning")
    pdf.paragraph(
        "The system balances latency and safety by avoiding a guardrail call for every answer. It uses deterministic risk detection "
        "to decide when an LLM rewrite is needed, then records the outcome in trace metadata."
    )


def add_agent_workflow(pdf: SimplePdf) -> None:
    pdf.h1("10. Agent Workflow")
    pdf.paragraph(
        "The agent uses LangGraph-compatible execution with optimized planned paths. LangChain remains useful for Azure OpenAI "
        "model integrations, embeddings, message compatibility, and callback plumbing."
    )
    pdf.h2("End-to-end chat workflow")
    steps = [
        "1. Request enters /chat with bearer token and optional session_id.",
        "2. Auth dependency validates token and blocks access if password_change_required is true.",
        "3. Query is redacted for PHI and conversation history is loaded.",
        "4. Langfuse trace is opened when configured; otherwise a local trace ID is used.",
        "5. System prompt is loaded from Langfuse or fallback prompt, then response-style baseline is appended.",
        "6. Initial safety assessment is generated and included in agent context.",
        "7. The planner decides whether the query is RAG-only, deterministic-only, multipart, or ambiguous.",
        "8. Fast planned paths execute direct tool calls for clear RAG/deterministic questions.",
        "9. Ambiguous questions fall back to LangGraph tool-calling loop with a maximum LLM-call limit.",
        "10. Tool outputs are bounded into context and one final answer LLM call synthesizes the response.",
        "11. Response guardrail rewrite runs only when tone/style risk is detected.",
        "12. Final safety assessment, audit metadata, token estimates, latency breakdown, sources, and tool flow are stored.",
        "13. Chat history is persisted and RAGAS/Langfuse enrichment runs in the background.",
    ]
    for step in steps:
        pdf.bullet(step)

    pdf.h2("Workflow diagram")
    pdf.code_block(
        [
            "User -> FastAPI /chat -> Auth + password gate",
            "     -> PHI redaction -> history load -> Langfuse trace/prompt",
            "     -> safety context -> planner",
            "     -> [fast RAG | fast deterministic | multipart | LangGraph fallback]",
            "     -> tools -> bounded context -> Azure OpenAI answer",
            "     -> optional guardrail rewrite -> final safety",
            "     -> response + sources -> history + Langfuse + background RAGAS",
        ]
    )

    pdf.h2("RAG document search tool workflow")
    for item in [
        "Catalog candidate selection: manifest metadata is filtered by role and matched against query terms.",
        "Search narrowing: candidate document keys are passed into OpenSearch/Chroma retrieval.",
        "Retrieval: vector and keyword search run against selected documents; broad fallback is used when catalog matching fails.",
        "Neighbor expansion: adjacent chunks are retrieved to improve context continuity.",
        "Source packaging: snippets, title, URI, score, metadata, and document keys are returned for citations and RAGAS.",
    ]:
        pdf.bullet(item)

    pdf.h2("Deterministic lookup workflow")
    for item in [
        "The planner detects structured-fact intent such as rota, patient, appointment, department, contact, or formulary queries.",
        "The Postgres lookup service applies user access context and queries matching tables.",
        "Rows are returned as exact structured JSON.",
        "The answer synthesizer preserves exact values and does not reinterpret deterministic facts.",
    ]:
        pdf.bullet(item)

    pdf.h2("Document catalog workflow")
    for item in [
        "The document catalog tool exposes governed document metadata to the agent when explicitly called.",
        "Internally, the same catalog logic acts as a helper for RAG narrowing.",
        "Internal catalog assistance is shown in tool_flow but is not added to tools_used unless the LLM explicitly selected document_catalog.",
        "This keeps user-visible tool usage honest while still making helper behavior auditable.",
    ]:
        pdf.bullet(item)

    pdf.h2("Loop limit and fallback")
    pdf.paragraph(
        "The LangGraph fallback loop caps LLM tool-calling iterations. If the limit is reached, the agent produces a final "
        "no-tool answer from accumulated context instead of hanging."
    )


def add_performance(pdf: SimplePdf) -> None:
    pdf.h1("11. Performance And Latency Controls")
    for item in [
        "Fast planned execution bypasses tool-selection LLM calls for clear query types.",
        "Fast model routing can use AZURE_OPENAI_FAST_DEPLOYMENT for answer synthesis and guardrail rewrites.",
        "OpenSearch multi-search combines vector and keyword queries when available.",
        "Embedding cache and retrieval cache reduce repeated work.",
        "Catalog candidate cache reduces repeated manifest matching.",
        "Context budgets keep answer prompts bounded.",
        "Startup warmup primes LLM clients, Langfuse prompt cache, document manifest, and retrieval path.",
        "Background history save, trace enrichment, and RAGAS scoring avoid adding synchronous chat latency.",
    ]:
        pdf.bullet(item)
    pdf.h2("Reasoning")
    pdf.paragraph(
        "The system optimizes full-answer completion time without removing functionality. It keeps traceability and evaluation "
        "while moving nonessential enrichment off the critical path."
    )


def add_api(pdf: SimplePdf) -> None:
    pdf.h1("12. Main API And UI Features")
    pdf.h2("Backend endpoints")
    for item in [
        "GET /health: service status, settings summary, registered tools, and warmup status.",
        "POST /auth/login, GET /auth/me, POST /auth/change-password: authentication and password management.",
        "POST /chat: main conversational API with sources, tools, trace ID, tokens, latency, safety, and performance metadata.",
        "GET /chat/sessions and GET /chat/sessions/{session_id}: persisted chat history.",
        "GET /documents: role-filtered indexed document list.",
        "Admin users: GET/POST/PATCH /admin/users and POST /admin/users/{username}/reset-password.",
        "Admin documents: upload, ingest, delete indexes.",
        "Admin dashboard: per-query and aggregate operational metrics.",
        "Admin warmup: manual warmup trigger.",
        "Admin patient details: filtered Postgres patient/appointment data access.",
    ]:
        pdf.bullet(item)

    pdf.h2("Frontend pages")
    for item in [
        "Chat: conversational Q&A with session history.",
        "Dashboard: traces, tool flow, latency, tokens, RAGAS, guardrails, and per-query drilldowns.",
        "Patient Details: filterable table view over configured Postgres patient data.",
        "Users: admin user creation, role assignment, department assignment, and password reset.",
        "Documents: upload, ingest/index, index reset, and indexed-document table.",
    ]:
        pdf.bullet(item)


def add_conclusion(pdf: SimplePdf) -> None:
    pdf.h1("13. Summary")
    pdf.paragraph(
        "The system is a role-aware healthcare knowledge platform with a practical split between semantic document retrieval "
        "and exact deterministic lookup. LangGraph handles agent workflow, Azure OpenAI provides model and embedding services, "
        "OpenSearch/Chroma provide vector search depending on runtime mode, Postgres stores structured facts and local history, "
        "and Langfuse/RAGAS make behavior measurable. The design favors auditability, local-developer productivity, and operational "
        "control while keeping the chat experience focused and responsive."
    )


def build() -> None:
    pdf = SimplePdf("Healthcare Knowledge Agent - System Feature Guide")
    add_title(pdf)
    add_section_overview(pdf)
    add_deployment(pdf)
    add_security(pdf)
    add_documents(pdf)
    add_retrieval(pdf)
    add_deterministic(pdf)
    add_chat(pdf)
    add_observability(pdf)
    add_guardrails(pdf)
    add_agent_workflow(pdf)
    add_performance(pdf)
    add_api(pdf)
    add_conclusion(pdf)
    pdf.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    build()
