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
PDF_PATH = OUT_DIR / "internal_company_knowledge_assistant_explanation.pdf"

BLUE = colors.HexColor("#2E74B5")
DARK_BLUE = colors.HexColor("#1F4D78")
INK = colors.HexColor("#142033")
MUTED = colors.HexColor("#59636E")
HEADER_FILL = colors.HexColor("#E8EEF5")
LIGHT_FILL = colors.HexColor("#F4F6F9")
BORDER = colors.HexColor("#B7C9DD")
WHITE = colors.white


def make_styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=29,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=11.5,
            leading=15,
            textColor=MUTED,
            spaceAfter=14,
        ),
        "h1": ParagraphStyle(
            "Heading1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15.5,
            leading=19,
            textColor=BLUE,
            spaceBefore=16,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "Heading2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12.5,
            leading=16,
            textColor=BLUE,
            spaceBefore=12,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "Heading3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=11.2,
            leading=14,
            textColor=DARK_BLUE,
            spaceBefore=8,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.9,
            leading=12.8,
            textColor=INK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.6,
            leading=11,
            textColor=INK,
            spaceAfter=3,
        ),
        "label": ParagraphStyle(
            "Label",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.8,
            leading=11,
            textColor=DARK_BLUE,
            spaceAfter=0,
        ),
        "table": ParagraphStyle(
            "TableText",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8.3,
            leading=10.6,
            textColor=INK,
            spaceAfter=0,
        ),
        "table_header": ParagraphStyle(
            "TableHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.3,
            leading=10.6,
            textColor=DARK_BLUE,
            spaceAfter=0,
        ),
        "callout_title": ParagraphStyle(
            "CalloutTitle",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=12,
            textColor=DARK_BLUE,
            spaceAfter=3,
        ),
        "code": ParagraphStyle(
            "Code",
            parent=base["Code"],
            fontName="Courier",
            fontSize=8.1,
            leading=10.2,
            textColor=colors.HexColor("#273444"),
            backColor=colors.HexColor("#F7F9FC"),
            borderColor=colors.HexColor("#D9E2EC"),
            borderWidth=0.4,
            borderPadding=6,
            spaceAfter=8,
        ),
        "toc": ParagraphStyle(
            "TOC",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=12.5,
            textColor=INK,
            spaceAfter=2,
        ),
    }
    return styles


def header_footer(canvas, doc):
    canvas.saveState()
    width, height = LETTER
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(inch, height - 0.55 * inch, "Dstrmaysam Healthcare Knowledge Agent")
    canvas.setStrokeColor(colors.HexColor("#D9E2EC"))
    canvas.setLineWidth(0.5)
    canvas.line(inch, height - 0.64 * inch, width - inch, height - 0.64 * inch)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(width - inch, 0.55 * inch, f"Page {doc.page}")
    canvas.restoreState()


def p(text: str, style: str = "body"):
    return Paragraph(text, STYLES[style])


def bullets(items: list[str]):
    return ListFlowable(
        [ListItem(p(item, "body"), leftIndent=14) for item in items],
        bulletType="bullet",
        start="circle",
        leftIndent=18,
        bulletFontSize=5,
        bulletColor=BLUE,
        spaceAfter=6,
    )


def numbers(items: list[str]):
    return ListFlowable(
        [ListItem(p(item, "body"), leftIndent=18) for item in items],
        bulletType="1",
        leftIndent=22,
        bulletFontName="Helvetica-Bold",
        bulletFontSize=8.5,
        bulletColor=BLUE,
        spaceAfter=6,
    )


def table(data: list[list[str]], widths: list[float], header: bool = True):
    converted = []
    for row_idx, row in enumerate(data):
        converted.append(
            [
                Paragraph(
                    str(cell).replace("\n", "<br/>"),
                    STYLES["table_header" if header and row_idx == 0 else "table"],
                )
                for cell in row
            ]
        )
    t = Table(converted, colWidths=widths, hAlign="LEFT", repeatRows=1 if header else 0)
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, BORDER),
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_FILL if header else WHITE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return [t, Spacer(1, 8)]


def label_detail(rows: list[tuple[str, str]]):
    data = [[label, detail] for label, detail in rows]
    converted = []
    for label, detail in data:
        converted.append([Paragraph(label, STYLES["label"]), Paragraph(detail, STYLES["table"])])
    t = Table(converted, colWidths=[1.35 * inch, 5.15 * inch], hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.35, BORDER),
                ("BACKGROUND", (0, 0), (0, -1), HEADER_FILL),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return [t, Spacer(1, 8)]


def callout(title: str, body: str):
    t = Table(
        [[Paragraph(title, STYLES["callout_title"]), Paragraph(body, STYLES["table"])]],
        colWidths=[1.45 * inch, 5.05 * inch],
        hAlign="LEFT",
    )
    t.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9E2EC")),
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_FILL),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return [t, Spacer(1, 8)]


def section(title: str):
    return [p(title, "h1")]


def subsection(title: str):
    return [p(title, "h2")]


def code_block(text: str):
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
    return p(safe, "code")


def build_story():
    story = []
    story.extend([p("Dstrmaysam Healthcare Knowledge Agent", "title")])
    story.append(
        p(
            "Detailed project explanation for the FastAPI, Streamlit, LangChain, Azure OpenAI, AWS, Langfuse, and RAGAS MVP.",
            "subtitle",
        )
    )
    story.extend(
        label_detail(
            [
                (
                    "Purpose",
                    "Explain the implemented MVP architecture, component responsibilities, data flows, AWS services, evaluation strategy, and deployment path.",
                ),
                (
                    "Audience",
                    "Engineers, ML/LLM practitioners, cloud reviewers, and project assessors who need to understand how the assistant works.",
                ),
                (
                    "Project state",
                    "Greenfield scaffold implemented in this workspace with backend, frontend, ingestion, evaluation, tests, Docker, and AWS deployment templates.",
                ),
                (
                    "Primary outcome",
                    "A chat-based internal knowledge assistant that retrieves grounded company answers from S3-indexed documents and records observability/evaluation metadata.",
                ),
            ]
        )
    )
    story.extend(
        callout(
            "MVP lens",
            "The project is designed to be credible in five days: containerized services, secure secret loading, RAG, agent tools, persistent history, Langfuse tracing, RAGAS evals, stress testing, and an ECS deployment path.",
        )
    )
    story.append(p("Contents", "h1"))
    for item in [
        "1. Executive overview",
        "2. Architecture and request flow",
        "3. Backend and frontend explanation",
        "4. Agentic AI and RAG design",
        "5. Security, secrets, and chat history",
        "6. Observability, prompt versioning, and evals",
        "7. AWS deployment model",
        "8. Repository map, verification, and next steps",
    ]:
        story.append(p(item, "toc"))
    story.append(PageBreak())

    story.extend(section("1. Executive Overview"))
    story.append(
        p(
            "The Dstrmaysam Healthcare Knowledge Agent is a containerized application that lets employees ask questions against company knowledge. It combines a Streamlit chat interface, a FastAPI backend, a LangChain agent, Azure OpenAI, AWS storage and hosting services, Langfuse observability, and RAGAS evaluation.",
        )
    )
    story.append(
        p(
            "The assistant is intentionally built as an MVP with deployment-shaped boundaries for AWS dev. The frontend stays thin and user-focused. The backend owns authentication, secret loading, retrieval, tool execution, model calls, chat persistence, and tracing. AWS services provide the operational foundation: S3 for documents, OpenSearch Serverless for vector search, DynamoDB for chat history, ECR/ECS for containers, CloudWatch for logs, and Secrets Manager for every secret value.",
        )
    )
    story.append(
        bullets(
            [
                "Users must log in before they can ask questions.",
                "Questions are answered through a chat-only interface.",
                "The agent has at least three tools: RAG search, document catalog lookup, and CSV/table lookup.",
                "Answers return sources, tools used, input/output token estimates, latency, and Langfuse trace IDs.",
                "Chat history persists and is passed back into the agent so follow-up questions have context.",
                "Golden-data evals and a 100-query stress test provide measurable quality checks.",
            ]
        )
    )

    story.extend(section("2. Architecture And Request Flow"))
    story.append(
        p(
            "The application is split into a frontend ECS service and a backend ECS service. The Streamlit service handles user interaction. The FastAPI service handles trusted operations and talks to AWS, Azure OpenAI, Langfuse, and the vector store.",
        )
    )
    story.append(
        code_block(
            "User -> ALB -> Streamlit ECS Service -> FastAPI ECS Service\n"
            "FastAPI -> Auth + AWS Secrets Manager\n"
            "FastAPI -> DynamoDB Chat History\n"
            "FastAPI -> LangChain Agent\n"
            "Agent -> RAG Search Tool -> OpenSearch Serverless\n"
            "Agent -> Document Catalog Tool -> S3 Manifest\n"
            "Agent -> CSV/Table Lookup Tool -> S3 CSV Files\n"
            "Agent -> Azure OpenAI Chat + Embeddings\n"
            "FastAPI -> Langfuse Traces + Prompt Versions"
        )
    )
    story.extend(
        table(
            [
                ["Layer", "Responsibility", "Main services / files"],
                ["User interface", "Login, chat input, response display, session list, metadata panel.", "Streamlit; frontend/streamlit_app.py"],
                ["API layer", "Validates tokens, exposes chat/session/document endpoints, centralizes service wiring.", "FastAPI; backend/app/main.py"],
                ["Agent layer", "Builds context from tools and chat history, calls Azure OpenAI via LangChain.", "backend/app/agent.py"],
                ["Knowledge layer", "Reads S3 manifest, searches vectors, looks up CSV rows.", "S3, OpenSearch Serverless; storage.py, retrieval.py"],
                ["State layer", "Stores sessions and messages for persistent contextual conversations.", "DynamoDB; history.py"],
                ["Observability/evals", "Prompt versions, traces, token metadata, RAGAS reports, stress reports.", "Langfuse, RAGAS; observability.py, evals/"],
            ],
            [1.2 * inch, 3.0 * inch, 2.3 * inch],
        )
    )
    story.extend(subsection("How One Chat Turn Works"))
    story.append(
        numbers(
            [
                "The user signs in through Streamlit, which calls POST /auth/login.",
                "FastAPI validates the credentials against password hashes loaded from AWS Secrets Manager.",
                "Streamlit stores the bearer token in session state and sends it with chat requests.",
                "FastAPI validates the token, identifies the user, and loads prior messages for the session.",
                "The agent runs RAG search, document catalog lookup, and CSV/table lookup.",
                "The agent builds a prompt with system instructions, chat history, tool context, and the user query.",
                "Azure OpenAI generates the response through the LangChain wrapper.",
                "The backend stores the user and assistant turns with sources, tools, token counts, latency, and trace ID.",
                "Streamlit displays the answer and response details.",
            ]
        )
    )

    story.extend(section("3. Backend Explanation"))
    story.append(
        p(
            "The FastAPI backend is the trusted orchestration layer. It keeps secrets and model access away from the browser, enforces authentication, coordinates retrieval and agent calls, and records history and response metadata.",
        )
    )
    story.extend(
        label_detail(
            [
                ("Configuration", "backend/app/config.py reads non-secret runtime values such as region, secret names, bucket, index, table, and prompt label."),
                ("Secrets", "backend/app/secrets.py loads JSON secrets from AWS Secrets Manager. Environment variables contain only secret names and non-sensitive config."),
                ("Authentication", "backend/app/auth.py verifies PBKDF2 password hashes and issues signed HMAC bearer tokens."),
                ("API routes", "backend/app/main.py exposes /health, /auth/login, /chat, /chat/sessions, /chat/sessions/{id}, and /documents."),
                ("Agent", "backend/app/agent.py loads history, runs tools, builds model context, calls Azure OpenAI through LangChain, and returns structured metadata."),
                ("Retries", "backend/app/retries.py adds retry behavior for remote-facing paths such as Secrets Manager, S3, OpenSearch, and ingestion."),
            ]
        )
    )
    story.extend(subsection("API Shape"))
    story.append(code_block('POST /auth/login\n{ "username": "user", "password": "password" }\n\nPOST /chat\n{ "query": "What is our leave policy?", "session_id": "optional-existing-session-id" }\n\nResponse\n{\n  "session_id": "abc123",\n  "answer": "Employees are entitled to...",\n  "sources": [],\n  "tools_used": ["rag_search"],\n  "input_tokens": 1200,\n  "output_tokens": 300,\n  "latency_ms": 2400,\n  "trace_id": "langfuse-trace-id"\n}'))

    story.extend(section("4. Frontend Explanation"))
    story.append(
        p(
            "The frontend is a Streamlit app designed around the chat workflow. It starts with a login screen, then switches to the authenticated chat experience. The sidebar supports new chats, sign out, and previous chat sessions. Assistant messages include a collapsible metadata panel for debugging and demo evidence.",
        )
    )
    story.append(
        bullets(
            [
                "The frontend never stores long-lived secrets.",
                "The bearer token stays in Streamlit session state for the active browser session.",
                "Previous sessions are loaded from FastAPI and can be reopened.",
                "Response metadata makes the system demonstrable: sources, tools, tokens, latency, and trace ID are visible.",
            ]
        )
    )

    story.extend(section("5. Agentic AI And Tool Design"))
    story.append(
        p(
            "The project uses an agentic design rather than a single prompt-only chain. The backend registers tools with clear responsibilities, then the agent decides how to use available context. This creates a more convincing assistant because the system can combine semantic retrieval, document awareness, and exact structured lookup.",
        )
    )
    story.extend(
        table(
            [
                ["Tool", "Purpose", "Best suited for"],
                ["rag_search", "Semantic retrieval over indexed company document chunks with citation metadata.", "Policy, process, handbook, FAQ, and procedural questions."],
                ["document_catalog", "Lists and filters documents from the S3 manifest.", "Questions about what knowledge exists, document type, department, or ownership."],
                ["table_lookup", "Searches CSV-style records in S3 for exact values.", "Structured facts such as contacts, escalation rows, owners, codes, or tabular policy values."],
            ],
            [1.2 * inch, 3.0 * inch, 2.3 * inch],
        )
    )
    story.extend(
        callout(
            "Why this is agentic",
            "The system exposes multiple tools to the model-facing orchestration layer. RAG is one tool, not the whole assistant. The catalog and table tools improve factual coverage and make the system more robust for internal knowledge.",
        )
    )

    story.extend(section("6. RAG And Ingestion Flow"))
    story.append(
        p(
            "Documents live in S3 under a raw prefix. The ingestion job reads supported files, extracts text, chunks content, creates embeddings through Azure OpenAI, indexes chunks into OpenSearch Serverless, and writes a manifest to S3. The manifest powers the document catalog and /documents endpoint.",
        )
    )
    story.append(
        numbers(
            [
                "Upload PDFs, markdown, text files, and CSVs to the configured S3 raw prefix.",
                "Run the ingestion CLI from the backend container.",
                "Parse source files and compute checksums.",
                "Chunk text using LangChain text splitters, with a deterministic fallback splitter.",
                "Generate embeddings with the Azure OpenAI embedding deployment.",
                "Index each chunk into OpenSearch Serverless with metadata and source URI.",
                "Write a JSON manifest back to S3 for document discovery.",
            ]
        )
    )
    story.extend(
        table(
            [
                ["Indexed field", "Why it exists"],
                ["key", "Original S3 object key."],
                ["title", "Human-readable filename/title."],
                ["uri", "Citation path, for example s3://bucket/raw/policy.md."],
                ["text", "Chunk text used by retrieval and answer grounding."],
                ["embedding", "Vector representation for semantic search."],
                ["content_type", "Differentiates PDF, text, markdown, and CSV content."],
                ["chunk_index", "Preserves chunk order within a document."],
                ["checksum", "Supports duplicate/change detection."],
                ["metadata", "Extensible department, owner, document type, or date values."],
            ],
            [1.7 * inch, 4.8 * inch],
        )
    )

    story.extend(section("7. Chat History And Context"))
    story.append(
        p(
            "Chat history is stored per user and session. On every turn, the backend loads prior messages and builds a bounded history context. This lets the assistant answer follow-up questions such as 'What about contractors?' or 'Can you summarize that?' without losing the thread.",
        )
    )
    story.extend(
        label_detail(
            [
                ("Local mode", "Uses an in-memory repository for development and tests."),
                ("AWS dev mode", "Uses DynamoDB with user_id as the partition key and sort_key for session/message records."),
                ("Context control", "MAX_HISTORY_CHARS limits how much history is injected into the agent prompt."),
                ("Long sessions", "Older messages are omitted with a summary marker once the history window becomes too large."),
            ]
        )
    )

    story.extend(section("8. Security And Secrets"))
    story.append(
        p(
            "The project explicitly treats AWS Secrets Manager as the only source for secret values. The code avoids putting secrets into .env files, Docker images, source code, README commands, or Streamlit secrets. Environment variables contain secret names and ordinary configuration only.",
        )
    )
    story.extend(
        table(
            [
                ["Secret name", "Contains", "Used by"],
                ["/dstrmaysam-healthcare-knowledge-agent/{stage}/app", "session_secret and auth_users password-hash map.", "FastAPI authentication service."],
                ["/dstrmaysam-healthcare-knowledge-agent/{stage}/azure-openai", "endpoint, api_key, api_version, chat deployment, embedding deployment.", "LangChain chat and embedding clients."],
                ["/dstrmaysam-healthcare-knowledge-agent/{stage}/langfuse", "public key, secret key, base URL.", "Langfuse tracing and prompt management."],
            ],
            [2.25 * inch, 2.9 * inch, 1.35 * inch],
        )
    )
    story.extend(
        callout(
            "Security note",
            "The MVP uses simple login because it is achievable in the five-day scope. For future production hardening, the natural upgrade is Cognito, SSO/SAML, or the company's existing identity provider.",
        )
    )

    story.extend(section("9. Observability And Prompt Versioning"))
    story.append(
        p(
            "Langfuse is used for LLM observability and prompt governance. CloudWatch captures container logs, while Langfuse captures model traces, tool calls, prompt versions, token usage, latency, and failures. The backend returns trace IDs to the UI so a demo can move from an answer directly into observability evidence.",
        )
    )
    story.extend(
        label_detail(
            [
                ("Tracing", "Records model calls, retrieval/tool behavior, latency, token usage, and trace IDs."),
                ("Prompt versions", "The system prompt can be loaded from Langfuse using the dev label used by AWS dev deployment."),
                ("Fallback prompt", "If Langfuse prompt retrieval fails, the backend uses a safe default system prompt."),
                ("Demo value", "The trace ID links the UI response to backend behavior and model/tool execution details."),
            ]
        )
    )

    story.extend(section("10. Evaluation And Stress Testing"))
    story.append(
        p(
            "Evaluation is designed at two levels. Golden-data evaluation checks answer quality against expected answers and sources. Stress testing checks consistency and reliability across repeated paraphrases.",
        )
    )
    story.extend(
        table(
            [
                ["Evaluation", "Implementation", "Measures"],
                ["Golden dataset", "evals/golden_dataset.csv", "Question, expected answer, expected source, tags."],
                ["RAGAS runner", "evals/run_ragas_eval.py", "Faithfulness, answer relevancy, context precision, context recall, plus fallback overlap scoring."],
                ["Stress test", "evals/stress_test.py", "100 paraphrased queries, latency, failures, source overlap, answer similarity."],
                ["Unit tests", "tests/", "Auth, token validation, history persistence, bounded context, agent tool contract."],
            ],
            [1.45 * inch, 2.45 * inch, 2.6 * inch],
        )
    )

    story.extend(section("11. AWS Dev Deployment Model"))
    story.append(
        p(
            "The target deployment is ECS Fargate. Backend and frontend images are built independently, pushed to ECR, and deployed as separate ECS services. An Application Load Balancer exposes the user interface and can route API traffic to the backend service. IAM task roles grant least-privilege access to the exact AWS resources needed.",
        )
    )
    story.extend(
        table(
            [
                ["AWS service", "Role in the project", "Important configuration"],
                ["S3", "Stores raw company documents and the ingestion manifest.", "Bucket policy, raw prefix, manifest key, encryption."],
                ["ECR", "Stores backend and frontend Docker images.", "Separate repositories and immutable tags for releases."],
                ["ECS Fargate", "Runs FastAPI and Streamlit containers.", "Task roles, service networking, CPU/memory, health checks."],
                ["ALB", "Provides browser access to the app.", "HTTPS listener, security groups, optional IP/VPN restriction."],
                ["Secrets Manager", "Stores all secret values.", "Least-privilege GetSecretValue permissions."],
                ["DynamoDB", "Persists chat sessions and messages.", "PAY_PER_REQUEST table with user_id/sort_key keys."],
                ["OpenSearch Serverless", "Stores the vector index for RAG search.", "AOSS permissions, vector mapping, collection endpoint."],
                ["CloudWatch", "Captures ECS task logs.", "Backend and frontend log groups."],
            ],
            [1.45 * inch, 2.65 * inch, 2.4 * inch],
        )
    )

    story.extend(section("12. Repository Map"))
    story.extend(
        table(
            [
                ["Path", "Explanation"],
                ["backend/app/main.py", "FastAPI entrypoint, dependency wiring, auth enforcement, and API route definitions."],
                ["backend/app/agent.py", "Agent orchestration, context assembly, Azure OpenAI call, token estimates, and response metadata."],
                ["backend/app/auth.py", "Password hashing, verification, and signed bearer-token creation/validation."],
                ["backend/app/secrets.py", "AWS Secrets Manager JSON loading for app, Azure OpenAI, and Langfuse secrets."],
                ["backend/app/history.py", "In-memory and DynamoDB chat history repositories."],
                ["backend/app/ingest.py", "S3 document parsing, chunking, embedding, OpenSearch indexing, and manifest writing."],
                ["frontend/streamlit_app.py", "Login and chat interface, session sidebar, response details panel."],
                ["evals/", "Golden dataset, RAGAS runner, and 100-query stress-test runner."],
                ["infra/", "ECS task definitions, IAM policy, DynamoDB schema, and OpenSearch index mapping."],
                ["tests/", "Unit tests for auth, chat history, and the agent/tool contract."],
            ],
            [2.05 * inch, 4.45 * inch],
        )
    )

    story.extend(section("13. Verification Completed"))
    story.append(
        p(
            "The scaffold was verified locally without live cloud credentials. These checks prove the project structure, core behavior, and deployment templates are internally consistent.",
        )
    )
    story.extend(
        label_detail(
            [
                ("Python syntax", "compileall passed for backend, frontend, eval scripts, and tests."),
                ("Unit tests", "6 tests passed: authentication, token validation, password hashing, history persistence, bounded context, and agent tool contract."),
                ("Docker Compose", "docker compose config produced a valid service configuration."),
                ("Infra JSON", "ECS task definitions, IAM policy, DynamoDB table definition, and OpenSearch index mapping parsed successfully."),
            ]
        )
    )

    story.extend(section("14. What Needs Live AWS/Azure Setup"))
    story.append(
        bullets(
            [
                "Create AWS Secrets Manager entries with real Azure OpenAI and Langfuse credentials.",
                "Create the S3 bucket and upload real company documents.",
                "Create the OpenSearch Serverless collection and index with the correct vector dimension.",
                "Create the DynamoDB chat history table.",
                "Build and push Docker images to ECR.",
                "Deploy ECS Fargate services and configure the Application Load Balancer.",
                "Run ingestion, RAGAS evals, and the 100-query stress test against live infrastructure.",
            ]
        )
    )

    story.extend(section("15. Recommended Next Improvements"))
    story.extend(
        table(
            [
                ["Improvement", "Why it matters", "Priority"],
                ["Cognito or SSO", "Replaces simple login with enterprise identity and lifecycle management.", "High"],
                ["Terraform or CDK", "Makes infrastructure repeatable and reviewable.", "High"],
                ["Admin ingestion UI", "Lets authorized users upload and re-index documents without CLI access.", "Medium"],
                ["Source preview", "Lets users inspect the exact retrieved snippets behind an answer.", "Medium"],
                ["Feedback buttons", "Captures human quality signals for future eval datasets.", "Medium"],
                ["PII redaction", "Reduces risk when indexing sensitive internal documents.", "Medium"],
                ["Cost dashboard", "Tracks token spend, retrieval latency, and query volume.", "Low"],
            ],
            [1.65 * inch, 3.75 * inch, 1.1 * inch],
        )
    )

    story.extend(section("16. Final Summary"))
    story.append(
        p(
            "This project is a strong five-day MVP because it demonstrates the full AWS dev deployment shape of an internal knowledge assistant: containerized services, AWS hosting path, secure secret management, agentic tool use, RAG, persistent context, observability, prompt versioning, evaluation, and stress testing. The scaffold can run locally with development settings, then run against AWS dev once AWS resources and live Azure OpenAI/Langfuse secrets are configured, with future production hardening as a later step.",
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
        bottomMargin=0.8 * inch,
        title="Dstrmaysam Healthcare Knowledge Agent Project Explanation",
        author="Codex",
        subject="Detailed project explanation",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    template = PageTemplate(id="main", frames=[frame], onPage=header_footer)
    doc.addPageTemplates([template])
    doc.build(build_story())
    return PDF_PATH


STYLES = make_styles()


if __name__ == "__main__":
    print(build_pdf())
