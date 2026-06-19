from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs"
PDF_PATH = OUT_DIR / "dstrmaysam_healthcare_knowledge_agent_lld_sdlc.pdf"

BLUE = colors.HexColor("#1D4ED8")
NAVY = colors.HexColor("#172554")
CYAN = colors.HexColor("#E0F2FE")
GREEN = colors.HexColor("#DCFCE7")
AMBER = colors.HexColor("#FEF3C7")
ROSE = colors.HexColor("#FFE4E6")
SLATE = colors.HexColor("#334155")
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#475569")
BORDER = colors.HexColor("#CBD5E1")
LIGHT = colors.HexColor("#F8FAFC")
WHITE = colors.white


def make_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=25,
            leading=31,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=11,
            leading=15,
            textColor=MUTED,
            spaceAfter=14,
        ),
        "h1": ParagraphStyle(
            "Heading1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=19,
            textColor=BLUE,
            spaceBefore=12,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "Heading2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11.5,
            leading=14,
            textColor=NAVY,
            spaceBefore=9,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=11.7,
            textColor=INK,
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.6,
            leading=9.3,
            textColor=INK,
            spaceAfter=0,
        ),
        "tiny": ParagraphStyle(
            "Tiny",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=6.8,
            leading=8.1,
            textColor=INK,
            spaceAfter=0,
        ),
        "table": ParagraphStyle(
            "Table",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=7.3,
            leading=9,
            textColor=INK,
            spaceAfter=0,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.3,
            leading=9,
            textColor=NAVY,
            spaceAfter=0,
        ),
        "box": ParagraphStyle(
            "Box",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=7.6,
            leading=9.3,
            textColor=NAVY,
            alignment=TA_CENTER,
            spaceAfter=0,
        ),
        "box_small": ParagraphStyle(
            "BoxSmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=6.7,
            leading=8.3,
            textColor=SLATE,
            alignment=TA_CENTER,
            spaceAfter=0,
        ),
        "code": ParagraphStyle(
            "Code",
            parent=base["Code"],
            fontName="Courier",
            fontSize=7.2,
            leading=8.7,
            textColor=colors.HexColor("#1F2937"),
            backColor=colors.HexColor("#F9FAFB"),
            borderColor=BORDER,
            borderWidth=0.25,
            borderPadding=5,
            spaceAfter=7,
        ),
        "callout": ParagraphStyle(
            "Callout",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.4,
            leading=10.6,
            textColor=INK,
            backColor=colors.HexColor("#EFF6FF"),
            borderColor=colors.HexColor("#93C5FD"),
            borderWidth=0.5,
            borderPadding=7,
            spaceAfter=8,
        ),
    }


STYLES = make_styles()


def esc(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def p(text: object, style: str = "body") -> Paragraph:
    return Paragraph(esc(text), STYLES[style])


def header_footer(canvas, doc):
    canvas.saveState()
    width, height = LETTER
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica-Bold", 7.4)
    canvas.drawString(
        inch,
        height - 0.55 * inch,
        "Dstrmaysam Healthcare Knowledge Agent - SDLC Low-Level Design",
    )
    canvas.setStrokeColor(colors.HexColor("#E2E8F0"))
    canvas.setLineWidth(0.5)
    canvas.line(inch, height - 0.64 * inch, width - inch, height - 0.64 * inch)
    canvas.setFont("Helvetica", 7.2)
    canvas.drawRightString(width - inch, 0.55 * inch, f"Page {doc.page}")
    canvas.restoreState()


def cover_page(canvas, doc):
    canvas.saveState()
    width, height = LETTER
    canvas.setFillColor(colors.HexColor("#F8FAFC"))
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#DBEAFE"))
    canvas.rect(0, height - 1.75 * inch, width, 1.75 * inch, fill=1, stroke=0)
    canvas.setFillColor(BLUE)
    canvas.rect(0, height - 1.75 * inch, 0.18 * inch, 1.75 * inch, fill=1, stroke=0)
    canvas.restoreState()


def section(title: str):
    return [p(title, "h1")]


def subsection(title: str):
    return [p(title, "h2")]


def bullets(items: list[str], style: str = "body"):
    return ListFlowable(
        [ListItem(p(item, style), leftIndent=11) for item in items],
        bulletType="bullet",
        leftIndent=16,
        bulletFontSize=5,
        bulletColor=BLUE,
        spaceAfter=5,
    )


def numbered(items: list[str]):
    return ListFlowable(
        [ListItem(p(item), leftIndent=14) for item in items],
        bulletType="1",
        leftIndent=18,
        bulletFontName="Helvetica-Bold",
        bulletFontSize=8,
        bulletColor=BLUE,
        spaceAfter=5,
    )


def table(data: list[list[str]], widths: list[float], header: bool = True, fill=CYAN):
    rows = []
    for row_idx, row in enumerate(data):
        rows.append(
            [
                Paragraph(esc(cell), STYLES["table_header" if header and row_idx == 0 else "table"])
                for cell in row
            ]
        )
    tbl = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1 if header else 0)
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), fill),
                ("LINEBELOW", (0, 0), (-1, 0), 0.65, BLUE),
            ]
        )
    tbl.setStyle(TableStyle(commands))
    return [tbl, Spacer(1, 7)]


def key_value(rows: list[tuple[str, str]]):
    return table([[left, right] for left, right in rows], [2.15 * inch, 4.35 * inch], header=False)


def box_cell(title: str, detail: str = "", fill: colors.Color = LIGHT) -> Table:
    content = [[p(title, "box")]]
    if detail:
        content.append([p(detail, "box_small")])
    inner = Table(content, colWidths=[1.55 * inch], hAlign="CENTER")
    inner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), fill),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return inner


def arrow_cell(label: str = "->") -> Paragraph:
    return Paragraph(esc(label), ParagraphStyle("Arrow", fontName="Helvetica-Bold", fontSize=10, textColor=BLUE, alignment=TA_CENTER))


def horizontal_flow(items: list[tuple[str, str, colors.Color]], box_width: float = 1.55 * inch):
    row = []
    widths = []
    for index, (title, detail, fill) in enumerate(items):
        row.append(box_cell(title, detail, fill))
        widths.append(box_width)
        if index < len(items) - 1:
            row.append(arrow_cell())
            widths.append(0.26 * inch)
    tbl = Table([row], colWidths=widths, hAlign="LEFT")
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return [tbl, Spacer(1, 8)]


def layered_architecture_diagram():
    data = [
        [
            p("Users", "box"),
            box_cell("Employees / Clinicians", "Browser access over HTTPS", GREEN),
            arrow_cell(),
            box_cell("Application Load Balancer", "TLS ingress and routing", CYAN),
            "",
        ],
        [
            p("Application", "box"),
            box_cell("Streamlit Frontend", "Login, chat UI, sessions", CYAN),
            arrow_cell(),
            box_cell("FastAPI Backend", "Auth, APIs, orchestration", CYAN),
            box_cell("CloudWatch Logs", "Container and audit logs", LIGHT),
        ],
        [
            p("Agent Runtime", "box"),
            box_cell("KnowledgeAgent", "LangGraph bounded tool loop", AMBER),
            arrow_cell(),
            box_cell("Agent Tools", "RAG, catalog, table, healthcare tools", AMBER),
            box_cell("Langfuse", "Traces, prompts, RAGAS scores", LIGHT),
        ],
        [
            p("Knowledge", "box"),
            box_cell("S3", "Raw docs + manifest", GREEN),
            arrow_cell(),
            box_cell("OpenSearch Serverless", "Vector + keyword chunks", GREEN),
            box_cell("Azure OpenAI", "Chat + embeddings", ROSE),
        ],
        [
            p("State / Secrets", "box"),
            box_cell("DynamoDB", "Chat sessions and messages", GREEN),
            arrow_cell(),
            box_cell("Secrets Manager", "App, Azure, Langfuse secrets", GREEN),
            box_cell("ECR / ECS Fargate", "Container images and tasks", LIGHT),
        ],
    ]
    tbl = Table(
        data,
        colWidths=[0.95 * inch, 1.62 * inch, 0.25 * inch, 1.62 * inch, 1.62 * inch],
        hAlign="LEFT",
    )
    tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EFF6FF")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return [tbl, Spacer(1, 8)]


def flow_diagram(title: str, steps: list[str], fill: colors.Color = LIGHT):
    rows: list[list[Paragraph]] = [[Paragraph(esc(title), STYLES["table_header"])]]
    for idx, step in enumerate(steps, start=1):
        rows.append([Paragraph(esc(f"{idx}. {step}"), STYLES["table"])])
        if idx < len(steps):
            rows.append([Paragraph("v", STYLES["table_header"])])
    tbl = Table(rows, colWidths=[6.5 * inch], hAlign="LEFT")
    commands = [
        ("BOX", (0, 0), (-1, -1), 0.45, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), CYAN),
        ("BACKGROUND", (0, 1), (-1, -1), fill),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 2), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row in range(1, len(rows)):
        if row % 2 == 1:
            commands.append(("BOX", (0, row), (0, row), 0.3, BORDER))
    tbl.setStyle(TableStyle(commands))
    return [tbl, Spacer(1, 8)]


def two_column(left, right):
    tbl = Table([[left, right]], colWidths=[3.18 * inch, 3.18 * inch], hAlign="LEFT")
    tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [tbl, Spacer(1, 6)]


def story_cover():
    story = [
        Spacer(1, 1.15 * inch),
        p("Dstrmaysam Healthcare Knowledge Agent", "cover_title"),
        p("Low-Level Design Document For SDLC Delivery", "cover_title"),
        p(
            "A developer-oriented design pack covering architecture, APIs, module logic, data design, "
            "agent workflows, operational controls, and non-functional requirements.",
            "cover_subtitle",
        ),
    ]
    story.extend(
        key_value(
            [
                ("Document type", "Low-Level Design / Detailed Design"),
                ("System baseline", "Current repository implementation after LangGraph + RAGAS/Langfuse changes"),
                ("Deployment target", "AWS ECS Fargate, S3, OpenSearch Serverless, DynamoDB, Secrets Manager, CloudWatch"),
                ("Generated", "2026-06-19"),
            ]
        )
    )
    story.extend(
        [
        Spacer(1, 0.15 * inch),
        p(
            "This document describes the implementation-level design for the system: architecture, API contracts, "
            "logging and exception handling, database design, technical specifications, UI flow details, and "
            "non-functional requirements.",
            "callout",
        ),
        NextPageTemplate("body"),
        PageBreak(),
        ]
    )
    return story


def build_story():
    story = []
    story.extend(story_cover())

    story.extend(section("1. LLD Purpose And SDLC Context"))
    story.append(
        p(
            "The LLD explains how the system works at implementation level. It describes module behavior, "
            "class/module responsibilities, database definitions, interfaces, sequence flows, and operational "
            "concerns so developers and reviewers can move from requirements to implementation and deployment "
            "without relying on implicit assumptions."
        )
    )
    story.extend(
        table(
            [
                ["LLD Area", "How This PDF Covers It"],
                ["Architecture diagrams", "Layered AWS and application architecture, plus workflow diagrams."],
                ["API details", "FastAPI routes, request/response models, authentication, and expected behavior."],
                ["Logging and exceptions", "CloudWatch logs, Langfuse traces, audit events, and fallback behavior."],
                ["Database design", "DynamoDB key schema, OpenSearch mapping, S3 manifest, and Secrets Manager schemas."],
                ["Technical specifications", "Module responsibilities, LangGraph loop, tools, ingestion, and eval logic."],
                ["UI detailing", "Streamlit login/chat/session behavior and validation boundaries."],
                ["NFRs", "Security, availability, performance, scalability, maintainability, and evaluation requirements."],
            ],
            [1.7 * inch, 4.8 * inch],
        )
    )

    story.extend(section("2. System Scope"))
    story.append(
        p(
            "The Dstrmaysam Healthcare Knowledge Agent is a containerized internal assistant for grounded healthcare "
            "knowledge retrieval and answer generation. Users authenticate through a simple login UI, ask questions in "
            "Streamlit, and receive answers generated by a FastAPI backend that orchestrates LangGraph, Azure OpenAI, "
            "RAG over S3/OpenSearch, chat history, healthcare access controls, safety assessment, and Langfuse tracing."
        )
    )
    story.extend(
        table(
            [
                ["Capability", "Implementation Detail"],
                ["Authenticated chat", "Bearer tokens from `/auth/login`; token verified on chat/session/document routes."],
                ["Agent execution", "KnowledgeAgent uses a LangGraph-wrapped bounded tool loop with LangChain-compatible Azure OpenAI model calls."],
                ["Grounding", "Retrieval-backed tools capture citations and snippets from OpenSearch hits."],
                ["Healthcare controls", "PHI redaction, role-based document filtering, safety flags, and audit event metadata."],
                ["Observability", "Langfuse trace IDs are returned to `/chat`; RAGAS scores can publish back to Langfuse."],
                ["Persistence", "DynamoDB stores chat sessions/messages in AWS dev; memory store supports local development."],
            ],
            [1.5 * inch, 5.0 * inch],
        )
    )

    story.extend(section("3. Overall Architecture Diagram"))
    story.extend(layered_architecture_diagram())
    story.append(
        p(
            "The backend is the trusted boundary. It owns authentication, secret loading, model access, retrieval, "
            "source filtering, safety checks, history persistence, and observability. The frontend remains thin and "
            "does not connect directly to AWS knowledge stores, Azure OpenAI, Langfuse, or Secrets Manager."
        )
    )

    story.extend(section("4. Deployment Environment"))
    story.extend(
        table(
            [
                ["Layer", "Service", "AWS Dev Value / Responsibility"],
                ["Ingress", "Application Load Balancer", "HTTPS entry point; routes to Streamlit frontend."],
                ["Containers", "ECS Fargate", "Runs backend and frontend task definitions."],
                ["Images", "ECR", "`dstrmaysam-healthcare-knowledge-agent-backend`, `...-frontend`."],
                ["Documents", "S3", "`dstrmaysam-healthcare-knowledge-agent-dev`, raw docs and manifest."],
                ["Vector search", "OpenSearch Serverless", "Index `dstrmaysam-healthcare-knowledge-agent` with 1536-dim vectors."],
                ["History", "DynamoDB", "Table `dstrmaysam-healthcare-knowledge-agent-dev`."],
                ["Secrets", "AWS Secrets Manager", "`/dstrmaysam-healthcare-knowledge-agent/dev/{app,azure-openai,langfuse}`."],
                ["Logs", "CloudWatch Logs", "`/ecs/dstrmaysam-healthcare-knowledge-agent/backend` and `/frontend`."],
                ["LLM", "Azure OpenAI", "Chat and embedding deployments via `langchain-openai`."],
                ["Observability", "Langfuse", "Prompt, trace, span, and score publishing."],
            ],
            [1.0 * inch, 1.55 * inch, 3.95 * inch],
        )
    )
    story.append(
        p(
            "AWS dev ECS configuration sets `APP_ENV=dev`, `SECRETS_STAGE=dev`, "
            "`CHAT_HISTORY_BACKEND=dynamodb`, and `LOCAL_TEST_ADMIN_ENABLED=false`."
        )
    )

    story.extend(section("5. Application Module Design"))
    story.extend(
        table(
            [
                ["Module", "Responsibility", "Key Files"],
                ["API layer", "FastAPI routes, dependency wiring, auth checks, response mapping.", "`backend/app/main.py`, `models.py`"],
                ["Auth service", "PBKDF2 password verification, HMAC token creation/verification.", "`backend/app/auth.py`"],
                ["Agent runtime", "PHI redaction, prompt assembly, LangGraph tool loop, safety/audit/history.", "`backend/app/agent.py`"],
                ["Tool layer", "RAG, catalog, table lookup, healthcare-specific tools.", "`tools.py`, `healthcare_tools.py`"],
                ["Retrieval", "OpenSearch query, Azure embedding query vector, hit normalization.", "`retrieval.py`"],
                ["Storage", "S3 manifest loading, CSV text reads, structured row lookup.", "`storage.py`"],
                ["Ingestion", "S3 scan, parsing, chunking, embeddings, OpenSearch indexing, manifest write.", "`ingest.py`"],
                ["Healthcare controls", "Access control, PHI redaction, safety assessment, audit event.", "`healthcare.py`"],
                ["Observability", "Langfuse trace, callback, prompt loading, fallback trace IDs.", "`observability.py`"],
                ["Evaluation", "RAGAS runs, source snippets, Langfuse score publishing.", "`evals/run_ragas_eval.py`"],
            ],
            [1.15 * inch, 3.15 * inch, 2.2 * inch],
        )
    )

    story.extend(section("6. API Low-Level Design"))
    story.extend(
        table(
            [
                ["Route", "Auth", "Input", "Output / Behavior"],
                ["/health GET", "No", "None", "Status, public settings, registered tools."],
                ["/auth/login POST", "No", "`username`, `password`", "Bearer token and expiry; 401 on invalid credentials."],
                ["/chat POST", "Yes", "`query`, optional `session_id`", "Answer, sources, snippets, tools used, token counts, latency, trace ID, safety, audit event."],
                ["/chat/sessions GET", "Yes", "Bearer token", "Current user's session summaries."],
                ["/chat/sessions/{id} GET", "Yes", "Session ID", "Messages for the selected session."],
                ["/documents GET", "Yes", "Bearer token", "Role-filtered document catalog."],
            ],
            [1.38 * inch, 0.55 * inch, 1.65 * inch, 2.92 * inch],
        )
    )
    story.extend(
        table(
            [
                ["Model", "Fields"],
                ["ChatRequest", "`query: str`, `session_id: str | None`"],
                ["Source", "`title`, `uri`, `score`, `metadata`, optional `snippet`"],
                ["ChatResponse", "`session_id`, `answer`, `sources`, `tools_used`, `input_tokens`, `output_tokens`, `latency_ms`, `trace_id`, `safety`, `audit_event`"],
                ["ChatSessionSummary", "`session_id`, `title`, `updated_at`"],
                ["ChatSessionDetail", "`session_id`, `messages[]`"],
            ],
            [1.55 * inch, 4.95 * inch],
        )
    )

    story.extend(section("7. Chat Workflow"))
    story.extend(
        flow_diagram(
            "Authenticated Chat Request",
            [
                "Streamlit sends `/chat` with bearer token, query, and optional session ID.",
                "FastAPI verifies the token and builds `HealthcareUserContext` from claims.",
                "KnowledgeAgent redacts PHI from prompt/trace inputs and loads bounded chat history.",
                "Langfuse trace context is opened; system prompt and initial safety context are assembled.",
                "LangGraph-backed tool loop lets the LLM choose tools and records only actual tool calls.",
                "Retrieval-backed tools return sources with snippets; role filters remove inaccessible documents.",
                "Final safety assessment and audit event are produced from the actual answer context.",
                "User and assistant messages persist to DynamoDB in AWS dev; response returns trace ID and metadata.",
            ],
            GREEN,
        )
    )
    story.append(
        p(
            "Deterministic healthcare controls stay outside the model loop: PHI redaction happens before model input, "
            "initial safety context is included in the prompt, and the final safety assessment is run after sources "
            "and answer generation are known."
        )
    )

    story.extend(section("8. LangGraph Agent And Tool Workflow"))
    story.extend(
        horizontal_flow(
            [
                ("LLM Node", "AzureChatOpenAI with bound tools", CYAN),
                ("Tool Calls?", "Continue while tool calls exist", AMBER),
                ("Tool Node", "Execute selected registered tool", GREEN),
                ("Sources", "Capture only retrieval-backed hits", GREEN),
                ("Final Answer", "No-tool answer or loop-limit finalization", CYAN),
            ],
            1.18 * inch,
        )
    )
    story.extend(
        table(
            [
                ["Tool", "Purpose", "Source Capture"],
                ["rag_search", "Catalog-guided semantic RAG over indexed knowledge documents.", "Yes"],
                ["document_search", "Catalog-guided semantic search over approved healthcare documents.", "Yes"],
                ["policy_search", "Catalog-guided focused search over policies, SOPs, pathways, guidelines.", "Yes"],
                ["document_catalog", "List/filter S3 manifest documents and provide internal candidate keys for RAG.", "No"],
                ["catalogue_search", "Find services, owners, systems, departments, approved tools.", "No"],
                ["table_lookup", "Exact lookup over CSV/table-like S3 files.", "No"],
                ["calendar_rota_lookup", "Lookup rota, clinic, training, on-call schedule CSV rows.", "No"],
                ["formulary_table_lookup", "Lookup formulary/restricted medicine/approval rows.", "No"],
                ["safety_guard", "Detect clinical risk, missing sources, PHI exposure, escalation needs.", "No"],
            ],
            [1.4 * inch, 3.75 * inch, 1.35 * inch],
        )
    )
    story.append(
        p(
            "The graph is bounded to five LLM calls. If the limit is reached, the agent makes one final no-tool model "
            "call using accumulated tool results, preventing infinite tool-call loops."
        )
    )
    story.extend(
        table(
            [
                ["Workflow", "Low-Level Steps"],
                [
                    "RAG document search",
                    "Load manifest; match catalog terms against title/key/content type/metadata; keep up to 8 role-allowed keys; run OpenSearch vector or keyword query with optional OpenSearch key filter; return snippets and citations. If no candidate keys match, run broad retrieval.",
                ],
                [
                    "Deterministic fact lookup",
                    "Load manifest; keep CSV/table sources; read matching S3 objects; parse with csv.DictReader; match query terms against row values; return exact JSON rows without semantic generation.",
                ],
                [
                    "Document catalog",
                    "Load manifest; create DocumentRecord values; match terms against metadata; return document metadata for explicit tool calls, or candidate keys for internal RAG helper mode.",
                ],
            ],
            [1.45 * inch, 5.05 * inch],
            fill=AMBER,
        )
    )

    story.extend(section("9. Ingestion And Retrieval Design"))
    story.extend(
        two_column(
            flow_diagram(
                "Document Ingestion",
                [
                    "List S3 objects under `raw/`.",
                    "Parse PDF, DOCX, markdown, text, and CSV.",
                    "Infer healthcare metadata from object key.",
                    "Chunk text with overlap.",
                    "Embed chunks with Azure OpenAI.",
                    "Index chunks into OpenSearch.",
                    "Write S3 manifest to `manifests/documents.json`.",
                ],
                AMBER,
            ),
            flow_diagram(
                "Retrieval",
                [
                    "Load manifest and select up to 8 catalog candidate keys.",
                    "Embed user query with Azure OpenAI.",
                    "Search OpenSearch `embedding` vector field with optional OpenSearch key filter.",
                    "Fallback to keyword multi-match with optional OpenSearch key filter when embedding unavailable.",
                    "Normalize hits into title, URI, text, score, metadata.",
                    "Apply role-based access filtering.",
                    "Return citations and snippets to the agent.",
                ],
                GREEN,
            ),
        )
    )
    story.extend(
        table(
            [
                ["Store", "Schema / Contract"],
                ["S3 raw documents", "`raw/` prefix contains PDF, DOCX, TXT, MD, CSV source files."],
                ["S3 manifest", "`documents[]` with key, title, content_type, checksum, metadata, chunk_count."],
                ["OpenSearch index", "`embedding` knn_vector 1536, `key`, `title`, `uri`, `text`, `content_type`, `chunk_index`, `checksum`, `metadata`."],
                ["DynamoDB history", "Partition key `user_id`; sort key `sort_key`; session rows use `SESSION#...`; message rows use `MESSAGE#...`."],
                ["Secrets Manager", "JSON documents for app auth, Azure OpenAI, and Langfuse credentials."],
            ],
            [1.45 * inch, 5.05 * inch],
        )
    )

    story.extend(section("10. Database And Index Low-Level Design"))
    story.extend(
        table(
            [
                ["DynamoDB Attribute", "Role"],
                ["user_id", "Partition key. Every query is scoped to the authenticated user."],
                ["sort_key", "Range key. Prefix distinguishes session summary rows and message rows."],
                ["session_id", "Stable chat session identifier."],
                ["title", "Derived from the first user message for session listing."],
                ["updated_at / created_at", "ISO timestamps for ordering and display."],
                ["role / content / metadata", "Message role, message text, and assistant response metadata."],
            ],
            [1.65 * inch, 4.85 * inch],
        )
    )
    story.extend(
        table(
            [
                ["OpenSearch Field", "Type", "Purpose"],
                ["embedding", "knn_vector dimension 1536", "Semantic vector retrieval."],
                ["text", "text", "Chunk body used as answer context and RAGAS snippet."],
                ["title", "text", "Human-readable citation title."],
                ["uri", "keyword", "Stable S3 citation."],
                ["metadata", "object", "Governance fields, domain, document type, allowed roles."],
                ["checksum", "keyword", "Source version/change detection."],
            ],
            [1.5 * inch, 1.55 * inch, 3.45 * inch],
        )
    )

    story.extend(section("11. UI Low-Level Design"))
    story.extend(
        table(
            [
                ["Screen / State", "UI Elements", "Behavior"],
                ["Login", "Username, password, Sign in button", "Calls `/auth/login`; stores token/session state on success; shows error on failure."],
                ["Chat", "Message history, chat input, assistant response", "Sends `/chat`; displays answer and persists session ID."],
                ["Response details", "Expander with JSON metadata", "Shows sources, tools used, token counts, latency, trace ID, safety, audit event."],
                ["Sidebar", "New chat, Sign out, previous chats", "Clears state or loads `/chat/sessions/{session_id}`."],
                ["Error state", "Streamlit error/caption", "Displays chat/login/history failures without exposing secrets."],
            ],
            [1.2 * inch, 2.0 * inch, 3.3 * inch],
        )
    )

    story.extend(section("12. Logging, Exceptions, And Observability"))
    story.extend(
        table(
            [
                ["Concern", "Design"],
                ["CloudWatch logs", "ECS containers write application logs to backend/frontend log groups."],
                ["Langfuse tracing", "Chat requests open an explicit trace/span when Langfuse is configured; fallback trace IDs are local UUIDs."],
                ["Callbacks", "Langfuse callbacks/config are passed into model calls where supported."],
                ["Audit event", "Healthcare audit JSON includes redacted query, roles, session, tools, source URIs, trace ID, safety, token usage."],
                ["Secrets errors", "SecretProvider raises explicit service errors; local/test admin fallback applies only in local/test environments."],
                ["Retrieval errors", "Search can return empty results; offline/fallback answer tells user when indexed context is missing."],
                ["Tool loop safety", "Unknown tools return an error string; loop limit forces final answer generation instead of hanging."],
                ["Eval publishing", "Langfuse score publishing failures are recorded in local report rows without erasing local RAGAS results."],
            ],
            [1.55 * inch, 4.95 * inch],
        )
    )

    story.extend(section("13. Evaluation And Langfuse Score Workflow"))
    story.extend(
        flow_diagram(
            "RAGAS Evaluation With Langfuse Publishing",
            [
                "Eval runner reads a golden dataset and sends each question to `/chat`.",
                "Each `/chat` response returns answer, source snippets, and real trace ID.",
                "RAGAS contexts prefer `source.snippet`; URI fallback is used when snippets are absent.",
                "RAGAS returns faithfulness, answer relevancy, context precision, and context recall.",
                "Per-question scores publish to the matching Langfuse chat trace.",
                "A synthetic evaluation-run trace receives average metrics, question count, and failure count.",
                "Local JSON report records publish status/error fields.",
            ],
            CYAN,
        )
    )

    story.extend(section("14. Security And Healthcare Controls"))
    story.extend(
        table(
            [
                ["Control", "Implementation"],
                ["Authentication", "Simple username/password login backed by Secrets Manager password hashes."],
                ["Authorization", "HealthcareUserContext roles/departments drive source filtering."],
                ["Secret handling", "Environment variables contain secret names only; secret values remain in AWS Secrets Manager."],
                ["PHI protection", "PHI patterns are redacted before prompts, traces, logs, and audit payloads."],
                ["Source governance", "Metadata includes owner, version, effective/review dates, sensitivity, domain, document type, allowed roles."],
                ["Clinical safety", "Urgent or patient-specific queries are flagged; unsupported clinical advice can be blocked or escalated."],
                ["AWS dev admin", "Local test admin is disabled by `LOCAL_TEST_ADMIN_ENABLED=false`."],
                ["Least privilege", "Backend task role reads only required secrets and app data stores; frontend role is minimal."],
            ],
            [1.55 * inch, 4.95 * inch],
        )
    )

    story.extend(section("15. Non-Functional Requirements"))
    story.extend(
        table(
            [
                ["NFR", "Design Target / Implementation"],
                ["Security", "Private backend, no frontend AWS access, Secrets Manager, IAM least privilege, PHI redaction."],
                ["Reliability", "Retry wrapper for remote calls; bounded agent loop; local report persists if Langfuse publishing fails."],
                ["Availability", "ECS Fargate services behind ALB; recommended multi-AZ subnets and rolling deployments."],
                ["Performance", "OpenSearch top-k retrieval, bounded history context, max five LLM calls, direct S3 manifest for catalog."],
                ["Scalability", "Stateless services scale horizontally; DynamoDB PAY_PER_REQUEST; OpenSearch Serverless collection."],
                ["Maintainability", "Modules split by auth, agent, tools, storage, retrieval, ingestion, observability, evals."],
                ["Auditability", "Trace ID, tools used, sources, safety metadata, token usage, and Langfuse scores retained."],
                ["Usability", "Single chat UI, session list, response details, and clear fallback messages."],
            ],
            [1.35 * inch, 5.15 * inch],
        )
    )

    story.extend(section("16. AWS Dev Configuration Snapshot"))
    story.append(
        p(
            "Use the detailed AWS runbook in `docs/aws_setup_instructions.md` for provisioning. The core runtime "
            "environment expected by the backend task is shown below."
        )
    )
    story.append(
        p(
            "APP_ENV=dev\n"
            "AWS_REGION=eu-west-2\n"
            "SECRETS_STAGE=dev\n"
            "APP_SECRET_NAME=/dstrmaysam-healthcare-knowledge-agent/dev/app\n"
            "AZURE_OPENAI_SECRET_NAME=/dstrmaysam-healthcare-knowledge-agent/dev/azure-openai\n"
            "LANGFUSE_SECRET_NAME=/dstrmaysam-healthcare-knowledge-agent/dev/langfuse\n"
            "S3_BUCKET=dstrmaysam-healthcare-knowledge-agent-dev\n"
            "S3_RAW_PREFIX=raw/\n"
            "S3_MANIFEST_KEY=manifests/documents.json\n"
            "OPENSEARCH_ENDPOINT=https://<collection-id>.eu-west-2.aoss.amazonaws.com\n"
            "OPENSEARCH_INDEX=dstrmaysam-healthcare-knowledge-agent\n"
            "DYNAMODB_CHAT_TABLE=dstrmaysam-healthcare-knowledge-agent-dev\n"
            "CHAT_HISTORY_BACKEND=dynamodb\n"
            "LOCAL_TEST_ADMIN_ENABLED=false",
            "code",
        )
    )

    story.extend(section("17. Developer Implementation Checklist"))
    story.append(
        numbered(
            [
                "Provision AWS resources and secrets using the current project names.",
                "Create OpenSearch index before running ingestion.",
                "Upload approved documents into the S3 `raw/` prefix.",
                "Run ingestion with backend environment and task role.",
                "Deploy backend and frontend ECS services.",
                "Validate login, `/health`, `/documents`, `/chat`, and chat history.",
                "Run RAGAS evaluation and confirm per-trace scores in Langfuse.",
                "Review CloudWatch logs and Langfuse traces for source coverage and safety metadata.",
            ]
        )
    )

    return story


def build_pdf() -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    doc = BaseDocTemplate(
        str(PDF_PATH),
        pagesize=LETTER,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.82 * inch,
        bottomMargin=0.72 * inch,
        title="Dstrmaysam Healthcare Knowledge Agent SDLC Low-Level Design",
        author="OpenAI Codex",
        subject="Low-level design document with architecture, APIs, workflows, data design, and NFRs",
    )
    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="normal",
        showBoundary=0,
    )
    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[frame], onPage=cover_page),
            PageTemplate(id="body", frames=[frame], onPage=header_footer),
        ]
    )
    story = build_story()
    doc.build(story)
    return PDF_PATH


if __name__ == "__main__":
    print(build_pdf())
