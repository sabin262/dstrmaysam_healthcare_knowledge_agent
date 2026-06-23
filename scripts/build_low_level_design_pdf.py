from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs"
PDF_PATH = OUT_DIR / "low_level_design_tech_stack.pdf"

BLUE = colors.HexColor("#2563EB")
DARK_BLUE = colors.HexColor("#1E3A8A")
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#4B5563")
HEADER_FILL = colors.HexColor("#EAF2FF")
LIGHT_FILL = colors.HexColor("#F8FAFC")
GREEN_FILL = colors.HexColor("#ECFDF5")
ORANGE_FILL = colors.HexColor("#FFF7ED")
BORDER = colors.HexColor("#CBD5E1")
WHITE = colors.white


def make_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=23,
            leading=28,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            textColor=MUTED,
            spaceAfter=14,
        ),
        "h1": ParagraphStyle(
            "Heading1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            textColor=BLUE,
            spaceBefore=14,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "Heading2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12.2,
            leading=15,
            textColor=DARK_BLUE,
            spaceBefore=10,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.4,
            leading=12.3,
            textColor=INK,
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10.2,
            textColor=INK,
            spaceAfter=0,
        ),
        "table": ParagraphStyle(
            "TableText",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10.2,
            textColor=INK,
            spaceAfter=0,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.1,
            leading=10.3,
            textColor=DARK_BLUE,
            spaceAfter=0,
        ),
        "flow_title": ParagraphStyle(
            "FlowTitle",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            textColor=DARK_BLUE,
            spaceAfter=0,
        ),
        "code": ParagraphStyle(
            "Code",
            parent=base["Code"],
            fontName="Courier",
            fontSize=7.4,
            leading=9.2,
            textColor=colors.HexColor("#1F2937"),
            backColor=colors.HexColor("#F9FAFB"),
            borderColor=BORDER,
            borderWidth=0.3,
            borderPadding=5,
            spaceAfter=8,
        ),
    }


STYLES = make_styles()


def esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(esc(text), STYLES[style])


def header_footer(canvas, doc):
    canvas.saveState()
    width, height = LETTER
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(inch, height - 0.55 * inch, "Healthcare Knowledge Agent - Low-Level Design")
    canvas.setStrokeColor(colors.HexColor("#E5E7EB"))
    canvas.setLineWidth(0.5)
    canvas.line(inch, height - 0.64 * inch, width - inch, height - 0.64 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(width - inch, 0.55 * inch, f"Page {doc.page}")
    canvas.restoreState()


def section(title: str):
    return [p(title, "h1")]


def subsection(title: str):
    return [p(title, "h2")]


def bullets(items: list[str]):
    return ListFlowable(
        [ListItem(p(item), leftIndent=12) for item in items],
        bulletType="bullet",
        leftIndent=18,
        bulletFontSize=5,
        bulletColor=BLUE,
        spaceAfter=6,
    )


def table(data: list[list[str]], widths: list[float], header: bool = True, fill=HEADER_FILL):
    rows = []
    for row_idx, row in enumerate(data):
        rows.append(
            [
                Paragraph(
                    esc(cell),
                    STYLES["table_header" if header and row_idx == 0 else "table"],
                )
                for cell in row
            ]
        )
    t = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1 if header else 0)
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), fill))
    t.setStyle(TableStyle(commands))
    return [t, Spacer(1, 7)]


def label_detail(rows: list[tuple[str, str]]):
    converted = [[label, detail] for label, detail in rows]
    return table(converted, [1.55 * inch, 4.95 * inch], header=False, fill=HEADER_FILL)


def flow_diagram(title: str, steps: list[str], fill=LIGHT_FILL):
    rows: list[list[Paragraph]] = [[Paragraph(esc(title), STYLES["flow_title"])]]
    for index, step in enumerate(steps, start=1):
        rows.append([Paragraph(esc(f"{index}. {step}"), STYLES["table"])])
        if index < len(steps):
            rows.append([Paragraph("v", STYLES["table_header"])])
    t = Table(rows, colWidths=[6.5 * inch], hAlign="LEFT")
    commands = [
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_FILL),
        ("BACKGROUND", (0, 1), (-1, -1), fill),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 2), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row in range(1, len(rows)):
        if row % 2 == 1:
            commands.append(("BOX", (0, row), (0, row), 0.35, BORDER))
    t.setStyle(TableStyle(commands))
    return [t, Spacer(1, 9)]


def code_block(text: str):
    return p(text, "code")


def build_story():
    story = []
    story.extend(
        [
            p("Low-Level Design: Healthcare Knowledge Agent Tech Stack", "title"),
            p(
                "Detailed implementation-level design covering runtime stack, AWS deployment, external services, agent orchestration, and tool workflows. Generated from the current repository implementation.",
                "subtitle",
            ),
        ]
    )
    story.extend(
        label_detail(
            [
                ("Version", "1.0"),
                ("Date", "2026-06-19"),
                ("Primary source", "Current repo plus docs/three_agent_capabilities_explained.md"),
                ("System shape", "One FastAPI backend, one Streamlit frontend, one LangGraph-orchestrated KnowledgeAgent, multiple registered tools."),
            ]
        )
    )

    story.extend(section("1. Executive Design Summary"))
    story.append(
        p(
            "The system is a containerized internal healthcare knowledge assistant. Users log in through Streamlit, ask questions in chat, and receive source-backed answers from a FastAPI backend. The backend handles authentication, LangGraph agent orchestration, RAG retrieval, document catalog lookup, structured table lookup, healthcare safety checks, chat history persistence, tracing, and evaluation metadata."
        )
    )
    story.append(
        bullets(
            [
                "LangGraph orchestrates the agent workflow; LangChain remains the Azure OpenAI integration wrapper.",
                "The three conceptual agent capabilities are RAG Search, Document Catalog, and CSV/Table Lookup.",
                "Healthcare extensions add role-aware retrieval, policy search, catalogue search, rota/formulary lookup, PHI redaction, safety assessment, and audit event metadata.",
                "AWS provides S3, OpenSearch Serverless, DynamoDB, Secrets Manager, ECS Fargate, ECR, ALB, IAM, and CloudWatch.",
                "Langfuse provides prompt management, traces, and published RAGAS scores.",
            ]
        )
    )

    story.extend(section("2. Overall Deployment Environment"))
    story.extend(
        flow_diagram(
            "Deployment Architecture",
            [
                "Employees and clinicians access the Streamlit UI through an Application Load Balancer.",
                "Streamlit calls the FastAPI backend with bearer tokens.",
                "FastAPI validates auth, loads secrets, and invokes the KnowledgeAgent.",
                "KnowledgeAgent uses tools backed by S3, OpenSearch Serverless, DynamoDB, Azure OpenAI, and Langfuse.",
                "CloudWatch captures ECS logs; Langfuse captures LLM traces and evaluation scores.",
            ],
        )
    )
    story.extend(
        table(
            [
                ["Layer", "Technology", "Responsibility"],
                ["Frontend", "Streamlit on ECS Fargate", "Login, chat UI, previous sessions, response details."],
                ["Backend", "FastAPI on ECS Fargate", "Auth, chat API, agent execution, persistence, observability."],
                ["Agent", "LangGraph + LangChain Azure wrappers", "Model/tool loop, tool selection, Azure OpenAI calls."],
                ["Knowledge", "S3 + OpenSearch Serverless", "Raw docs, manifest, vector/keyword retrieval."],
                ["State", "DynamoDB", "Chat session and message history."],
                ["Security", "Secrets Manager + IAM", "Secret values and least-privilege runtime permissions."],
                ["Observability", "CloudWatch + Langfuse", "Logs, traces, prompt versions, RAGAS scores."],
            ],
            [1.25 * inch, 2.05 * inch, 3.2 * inch],
        )
    )

    story.extend(section("3. AWS Services And External Services"))
    story.extend(
        table(
            [
                ["Service", "Use"],
                ["S3", "Raw documents, CSV files, and manifests/documents.json."],
                ["OpenSearch Serverless", "Vector and keyword retrieval over ingested chunks."],
                ["DynamoDB", "Persistent chat sessions and messages."],
                ["Secrets Manager", "App auth secret, Azure OpenAI secret, Langfuse secret."],
                ["ECS Fargate", "Runs backend and frontend containers."],
                ["ECR", "Stores container images."],
                ["ALB", "Exposes browser entrypoint and routes traffic."],
                ["CloudWatch", "ECS task logs and operational visibility."],
                ["Azure OpenAI", "Chat completions and embeddings via langchain-openai."],
                ["Langfuse", "Traces, prompt versions, and RAGAS score publishing."],
                ["RAGAS", "Golden-data faithfulness, relevancy, precision, and recall metrics."],
            ],
            [1.95 * inch, 4.55 * inch],
        )
    )

    story.extend(section("4. Backend API And Data Contracts"))
    story.extend(
        table(
            [
                ["Route", "Purpose"],
                ["/health", "Status, public settings, registered tools."],
                ["/auth/login", "Validate username/password and issue bearer token."],
                ["/chat", "Run KnowledgeAgent and return answer, sources, tools, tokens, latency, trace, safety."],
                ["/chat/sessions", "List prior sessions for the current user."],
                ["/chat/sessions/{session_id}", "Load messages for a selected session."],
                ["/documents", "Return role-filtered document manifest entries."],
            ],
            [2.05 * inch, 4.45 * inch],
        )
    )
    story.append(code_block("ChatResponse includes: session_id, answer, sources[title, uri, score, metadata, snippet], tools_used, input_tokens, output_tokens, latency_ms, trace_id, safety, audit_event."))

    story.extend(section("5. Agent Runtime Design"))
    story.extend(
        flow_diagram(
            "POST /chat Agent Workflow",
            [
                "Validate bearer token and create HealthcareUserContext.",
                "Redact PHI from query before prompt/model use.",
                "Load chat history and build bounded context.",
                "Open Langfuse chat trace, or fallback to local UUID.",
                "Register base tools plus healthcare tools for the user context.",
                "Load system prompt from Langfuse or use default prompt.",
                "Run initial safety assessment and build graph prompt.",
                "Run LangGraph-backed model/tool loop with AzureChatOpenAI.",
                "Capture actual tools_used and retrieval source snippets.",
                "Run final safety assessment, audit event, persistence, and response.",
            ],
            fill=GREEN_FILL,
        )
    )
    story.extend(
        table(
            [
                ["Rule", "Behavior"],
                ["Tool selection", "LLM selects tools in model-backed runs."],
                ["Offline fallback", "Runs rag_search deterministically if LLM is unavailable."],
                ["Loop guard", "Maximum 5 LLM/tool turns before final no-tool answer request."],
                ["Source capture", "Retrieval-backed tools produce Source entries with snippets."],
                ["Catalog-guided RAG", "Retrieval-backed tools use document catalog matching to narrow OpenSearch by candidate document keys before broad fallback."],
                ["Safety", "PHI redaction and safety checks stay outside the graph."],
            ],
            [1.75 * inch, 4.75 * inch],
        )
    )

    story.extend(section("6. Tool Workflow Diagrams"))
    story.extend(subsection("6.1 RAG Search Tool"))
    story.extend(
        flow_diagram(
            "rag_search",
            [
                "Receive natural-language query from model tool call.",
                "If OpenSearch endpoint is missing, return no hits.",
                "Load S3 manifest and match catalog terms against title, key, content type, and metadata.",
                "Use up to 8 matching document keys as an OpenSearch key filter; fall back to broad search if none match.",
                "Try Azure OpenAI query embedding.",
                "If embedding succeeds, run OpenSearch knn vector query with optional key filter.",
                "If embedding fails, run OpenSearch multi_match keyword query with optional key filter.",
                "Map results to RetrievalHit objects.",
                "Filter by healthcare allowed_roles metadata.",
                "Return formatted context and Source objects with snippets.",
            ],
            fill=GREEN_FILL,
        )
    )
    story.extend(subsection("6.2 Document Catalog Tool"))
    story.extend(
        flow_diagram(
            "document_catalog",
            [
                "Receive query from model tool call.",
                "Extract terms with length >= 3.",
                "Load S3 manifest from S3_MANIFEST_KEY.",
                "Convert entries to DocumentRecord values.",
                "Match terms against title, key, content type, and metadata JSON.",
                "For explicit calls, limit output to first 20 matching records.",
                "For RAG helper mode, dedupe candidate keys and limit to 8.",
                "Return JSON document metadata or pass candidate keys to retrieval tools.",
            ],
            fill=LIGHT_FILL,
        )
    )
    story.extend(subsection("6.3 Deterministic Fact Lookup Tool"))
    story.extend(
        flow_diagram(
            "table_lookup",
            [
                "Receive query from model tool call.",
                "Extract terms with length >= 3.",
                "Load document records from S3 manifest.",
                "Keep only records whose key ends with .csv.",
                "Read CSV object from S3 and parse with csv.DictReader.",
                "Join row values as searchable text.",
                "Return exact JSON rows where any query term appears, up to limit.",
            ],
            fill=ORANGE_FILL,
        )
    )
    story.extend(subsection("6.4 Healthcare Extension Tools"))
    story.extend(
        table(
            [
                ["Tool", "Workflow summary"],
                ["document_search", "Catalog-guided OpenSearch retrieval, then role-based hit filtering."],
                ["policy_search", "Catalog-guided OpenSearch retrieval with policy/SOP/pathway candidate preference and role filtering."],
                ["catalogue_search", "Manifest records filtered to catalogue/directory/service/systems domains."],
                ["calendar_rota_lookup", "CSV schedule records filtered by rota/calendar metadata and query terms."],
                ["formulary_table_lookup", "CSV rows filtered toward medicine/formulary/restricted/approval terms."],
                ["safety_guard", "Checks urgent terms, patient-specific terms, PHI, missing sources, escalation behavior."],
            ],
            [1.7 * inch, 4.8 * inch],
        )
    )

    story.extend(section("7. Ingestion Workflow"))
    story.extend(
        flow_diagram(
            "Document Ingestion",
            [
                "List S3 objects under S3_RAW_PREFIX.",
                "Keep supported files: pdf, docx, txt, md, csv.",
                "Parse text and infer healthcare metadata.",
                "Chunk text with LangChain splitter or fallback splitter.",
                "Generate Azure OpenAI embeddings for each chunk.",
                "Index chunk body into OpenSearch Serverless.",
                "Write S3 manifest containing documents and chunk counts.",
            ],
            fill=GREEN_FILL,
        )
    )

    story.extend(section("8. Observability And Evaluation Workflow"))
    story.extend(
        flow_diagram(
            "RAGAS Scores To Langfuse",
            [
                "Eval runner reads golden dataset CSV.",
                "For each question, call POST /chat and capture trace_id.",
                "Build contexts from source.snippet, fallback to source.uri.",
                "Run RAGAS metrics and attach row-level metrics.",
                "Load Langfuse secret from AWS Secrets Manager.",
                "Publish per-question scores to matching chat trace.",
                "Create evaluation-run trace and publish aggregate scores.",
                "Write local JSON report regardless of publish failures.",
            ],
            fill=GREEN_FILL,
        )
    )
    story.extend(
        table(
            [
                ["Score", "Meaning"],
                ["ragas_faithfulness", "Whether answer is grounded in provided contexts."],
                ["ragas_answer_relevancy", "How relevant answer is to the question."],
                ["ragas_context_precision", "Whether retrieved contexts are useful."],
                ["ragas_context_recall", "Whether contexts cover expected answer."],
                ["simple_expected_overlap", "Fallback lexical overlap with expected answer."],
            ],
            [2.2 * inch, 4.3 * inch],
        )
    )

    story.extend(section("9. Security And IAM"))
    story.extend(
        table(
            [
                ["Boundary", "Design"],
                ["Secrets", "Only AWS Secrets Manager stores secret values."],
                ["Frontend", "No direct access to AWS knowledge stores or model secrets."],
                ["Backend task role", "Read required secrets, read/write S3 manifest/docs, DynamoDB chat table, OpenSearch collection."],
                ["AWS dev auth", "Use real app secret hashes; disable LOCAL_TEST_ADMIN_ENABLED."],
                ["Healthcare access", "Role-based post-retrieval filtering through allowed_roles metadata."],
            ],
            [1.8 * inch, 4.7 * inch],
        )
    )

    story.extend(section("10. Environment Variables"))
    story.extend(
        table(
            [
                ["Variable", "AWS dev expectation"],
                ["APP_ENV", "dev"],
                ["SECRETS_STAGE", "dev"],
                ["CHAT_HISTORY_BACKEND", "dynamodb"],
                ["LOCAL_TEST_ADMIN_ENABLED", "false"],
                ["APP_SECRET_NAME", "/dstrmaysam-healthcare-knowledge-agent/dev/app"],
                ["AZURE_OPENAI_SECRET_NAME", "/dstrmaysam-healthcare-knowledge-agent/dev/azure-openai"],
                ["LANGFUSE_SECRET_NAME", "/dstrmaysam-healthcare-knowledge-agent/dev/langfuse"],
                ["S3_BUCKET", "dstrmaysam-healthcare-knowledge-agent-dev"],
                ["OPENSEARCH_ENDPOINT", "OpenSearch Serverless collection endpoint"],
                ["BACKEND_URL", "Service discovery name or ALB/API URL"],
            ],
            [2.4 * inch, 4.1 * inch],
        )
    )

    story.extend(section("11. Failure Handling"))
    story.extend(
        table(
            [
                ["Failure", "Behavior"],
                ["Langfuse unavailable", "Default prompt and local UUID trace ID."],
                ["Azure chat unavailable", "Direct fallback, then offline RAG fallback."],
                ["Azure embeddings unavailable", "OpenSearch keyword fallback if endpoint configured."],
                ["OpenSearch endpoint missing", "Retrieval returns empty hits."],
                ["S3 manifest missing", "Catalog returns empty documents list."],
                ["Too many tool calls", "Final answer requested after 5 LLM/tool calls."],
            ],
            [2.2 * inch, 4.3 * inch],
        )
    )

    story.extend(section("12. Testing And Verification"))
    story.append(
        p(
            "The current test suite covers authentication, local admin fallback, chat history, healthcare safety/access, agent tool selection, source snippets, tool-loop limit, model contract compatibility, and RAGAS-to-Langfuse publishing with mocks."
        )
    )
    story.append(code_block('python -m unittest discover -s tests -v\npython -m compileall backend evals tests -q\ndocker compose run --rm -v "${PWD}:/workspace" -w /workspace backend python -m pytest tests -q'))

    story.extend(section("13. Deployment Sequence"))
    story.extend(
        flow_diagram(
            "AWS Dev Deployment",
            [
                "Build backend and frontend Docker images.",
                "Push images to ECR.",
                "Provision S3, DynamoDB, OpenSearch, Secrets Manager, ECS, ALB, CloudWatch.",
                "Write dev secrets to Secrets Manager.",
                "Deploy ECS services with AWS dev env vars.",
                "Upload approved documents to S3 raw prefix.",
                "Run ingestion job.",
                "Smoke test /health, /auth/login, /chat.",
                "Run RAGAS and stress tests; review Langfuse traces and scores.",
            ],
            fill=ORANGE_FILL,
        )
    )

    story.extend(section("14. Hardening Backlog"))
    story.extend(
        table(
            [
                ["Backlog item", "Reason"],
                ["SSO/Cognito", "Enterprise identity and MFA."],
                ["Terraform/CDK", "Repeatable infrastructure."],
                ["OpenSearch auth filters", "Avoid over-retrieval before role filtering."],
                ["Durable audit sink", "Healthcare governance retention."],
                ["Source governance registry", "Reviewed owner/version/effective/review metadata."],
                ["OCR and hybrid reranking", "Better retrieval coverage and precision."],
                ["Feedback UI", "Human quality data for future evals."],
            ],
            [2.1 * inch, 4.4 * inch],
        )
    )
    return story


def build_pdf() -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    doc = BaseDocTemplate(
        str(PDF_PATH),
        pagesize=LETTER,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=0.82 * inch,
        bottomMargin=0.78 * inch,
        title="Low-Level Design: Healthcare Knowledge Agent Tech Stack",
        author="Codex",
        subject="Low-level design and workflow diagrams",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=header_footer)])
    doc.build(build_story())
    return PDF_PATH


if __name__ == "__main__":
    print(build_pdf())
