from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs"
DRAWIO_PATH = OUT_DIR / "dstrmaysam_healthcare_knowledge_agent_lld_diagrams.drawio"

BLUE = "#1D4ED8"
NAVY = "#172554"
CYAN = "#E0F2FE"
GREEN = "#DCFCE7"
AMBER = "#FEF3C7"
ROSE = "#FFE4E6"
LIGHT = "#F8FAFC"
BORDER = "#CBD5E1"
INK = "#111827"


def make_model(width: int = 1200, height: int = 850) -> ET.Element:
    model = ET.Element(
        "mxGraphModel",
        {
            "dx": "1200",
            "dy": "850",
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": str(width),
            "pageHeight": str(height),
            "math": "0",
            "shadow": "0",
        },
    )
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    return model


def root(model: ET.Element) -> ET.Element:
    return model.find("root")  # type: ignore[return-value]


def html(label: str, detail: str | None = None) -> str:
    if detail:
        return f"<b>{label}</b><br><font style=\"font-size: 11px\">{detail}</font>"
    return f"<b>{label}</b>"


def node(
    model: ET.Element,
    cell_id: str,
    label: str,
    x: int,
    y: int,
    w: int = 150,
    h: int = 62,
    *,
    detail: str | None = None,
    fill: str = LIGHT,
    stroke: str = BORDER,
    shape: str = "rounded=1;whiteSpace=wrap;html=1;",
) -> None:
    style = (
        f"{shape}arcSize=8;fillColor={fill};strokeColor={stroke};fontColor={INK};"
        "fontFamily=Helvetica;fontSize=12;align=center;verticalAlign=middle;spacing=6;"
    )
    cell = ET.SubElement(
        root(model),
        "mxCell",
        {
            "id": cell_id,
            "value": html(label, detail),
            "style": style,
            "vertex": "1",
            "parent": "1",
        },
    )
    ET.SubElement(cell, "mxGeometry", {"x": str(x), "y": str(y), "width": str(w), "height": str(h), "as": "geometry"})


def group_label(model: ET.Element, cell_id: str, label: str, x: int, y: int, w: int, h: int, fill: str) -> None:
    style = (
        f"rounded=1;whiteSpace=wrap;html=1;arcSize=6;fillColor={fill};strokeColor={BORDER};"
        "fontColor=#475569;fontFamily=Helvetica;fontSize=13;fontStyle=1;align=left;verticalAlign=top;spacing=8;"
    )
    cell = ET.SubElement(
        root(model),
        "mxCell",
        {"id": cell_id, "value": label, "style": style, "vertex": "1", "parent": "1"},
    )
    ET.SubElement(cell, "mxGeometry", {"x": str(x), "y": str(y), "width": str(w), "height": str(h), "as": "geometry"})


def edge(model: ET.Element, cell_id: str, source: str, target: str, label: str = "") -> None:
    style = (
        f"edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
        f"endArrow=block;endFill=1;strokeColor={BLUE};fontColor={NAVY};fontSize=10;"
    )
    attrs = {"id": cell_id, "value": label, "style": style, "edge": "1", "parent": "1", "source": source, "target": target}
    cell = ET.SubElement(root(model), "mxCell", attrs)
    ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})


def title(model: ET.Element, text: str, subtitle: str = "") -> None:
    node(
        model,
        "title",
        text,
        40,
        24,
        900,
        58,
        detail=subtitle,
        fill="#FFFFFF",
        stroke="#FFFFFF",
        shape="text;whiteSpace=wrap;html=1;",
    )


def architecture_page() -> ET.Element:
    model = make_model(1400, 900)
    title(model, "Overall Deployment And Runtime Architecture", "Trusted backend boundary with AWS, Azure OpenAI, Langfuse, and RAG stores")
    group_label(model, "g_user", "User / Ingress", 30, 110, 240, 210, "#F8FAFC")
    group_label(model, "g_app", "Application Runtime", 310, 110, 470, 310, "#EFF6FF")
    group_label(model, "g_agent", "Agent + Knowledge", 820, 110, 520, 430, "#F0FDF4")
    group_label(model, "g_ops", "State, Secrets, Observability", 310, 460, 470, 270, "#FFFBEB")
    group_label(model, "g_batch", "Batch / Evaluation", 820, 580, 520, 150, "#FFF1F2")

    node(model, "user", "Employees / Clinicians", 70, 170, 160, 62, detail="Browser HTTPS", fill=GREEN)
    node(model, "alb", "Application Load Balancer", 70, 250, 160, 62, detail="TLS ingress", fill=CYAN)

    node(model, "frontend", "Streamlit Frontend", 350, 170, 170, 68, detail="ECS Fargate :8501", fill=CYAN)
    node(model, "backend", "FastAPI Backend", 570, 170, 170, 68, detail="ECS Fargate :8000", fill=CYAN)
    node(model, "auth", "AuthService", 350, 285, 160, 58, detail="Bearer token", fill=LIGHT)
    node(model, "api", "API Routes", 570, 285, 160, 58, detail="/health /auth /chat /documents", fill=LIGHT)

    node(model, "agent", "KnowledgeAgent", 860, 170, 170, 68, detail="LangGraph bounded loop", fill=AMBER)
    node(model, "tools", "Agent Tools", 1060, 170, 170, 68, detail="RAG, policy, catalog, table, safety", fill=AMBER)
    node(model, "s3", "AWS S3", 860, 300, 150, 62, detail="raw/ + manifest", fill=GREEN)
    node(model, "os", "OpenSearch Serverless", 1040, 300, 190, 62, detail="Vector index", fill=GREEN)
    node(model, "azure", "Azure OpenAI", 860, 420, 150, 62, detail="Chat + embeddings", fill=ROSE)
    node(model, "access", "Healthcare Controls", 1040, 420, 190, 62, detail="PHI, roles, safety, audit", fill=GREEN)

    node(model, "ddb", "DynamoDB", 350, 525, 170, 62, detail="Chat history table", fill=GREEN)
    node(model, "secrets", "Secrets Manager", 570, 525, 170, 62, detail="App, Azure, Langfuse", fill=GREEN)
    node(model, "cw", "CloudWatch Logs", 350, 635, 170, 62, detail="ECS + audit logs", fill=LIGHT)
    node(model, "lf", "Langfuse", 570, 635, 170, 62, detail="Traces, prompts, scores", fill=LIGHT)

    node(model, "ingest", "Ingestion Job", 860, 635, 160, 62, detail="One-off backend task", fill=ROSE)
    node(model, "evals", "RAGAS / Stress Tests", 1060, 635, 170, 62, detail="Quality reports + scores", fill=ROSE)

    for i, (src, dst, label) in enumerate(
        [
            ("user", "alb", "HTTPS"),
            ("alb", "frontend", ""),
            ("frontend", "backend", "API calls"),
            ("backend", "auth", ""),
            ("backend", "api", ""),
            ("backend", "agent", "answer()"),
            ("agent", "tools", "tool calls"),
            ("tools", "s3", "manifest / CSV"),
            ("tools", "os", "retrieval"),
            ("agent", "azure", "LLM"),
            ("agent", "access", "safety + filters"),
            ("backend", "ddb", "sessions"),
            ("backend", "secrets", "load secret names"),
            ("backend", "cw", "logs"),
            ("backend", "lf", "trace ID"),
            ("ingest", "s3", "read/write"),
            ("ingest", "os", "index chunks"),
            ("ingest", "azure", "embeddings"),
            ("evals", "backend", "/chat"),
            ("evals", "lf", "scores"),
        ],
        start=1,
    ):
        edge(model, f"e{i}", src, dst, label)
    return model


def chat_workflow_page() -> ET.Element:
    model = make_model(1300, 780)
    title(model, "Authenticated Chat Workflow", "Request lifecycle from Streamlit to LangGraph, safety, history, and response metadata")
    steps = [
        ("s1", "Streamlit Chat UI", "POST /chat with bearer token, query, optional session_id"),
        ("s2", "FastAPI Auth", "Verify token and build HealthcareUserContext"),
        ("s3", "PHI Redaction", "Redact identifiers before prompt/trace inputs"),
        ("s4", "History Context", "Load bounded session messages"),
        ("s5", "Langfuse Trace", "Open root trace/span or fallback UUID"),
        ("s6", "LangGraph Agent", "LLM chooses tools; loop is bounded"),
        ("s7", "Source + Safety", "Capture snippets, run final safety, create audit event"),
        ("s8", "Persist + Return", "Save messages and return answer, sources, tools, trace ID"),
    ]
    x, y = 80, 150
    for idx, (cell_id, label, detail) in enumerate(steps):
        node(model, cell_id, label, x + idx * 145, y + (idx % 2) * 170, 130, 72, detail=detail, fill=CYAN if idx < 5 else GREEN)
        if idx:
            edge(model, f"e{idx}", steps[idx - 1][0], cell_id)
    return model


def agent_tool_page() -> ET.Element:
    model = make_model(1300, 1020)
    title(model, "LangGraph Agent And Tool Workflow", "Bounded tool-calling workflow with catalog-guided RAG and retrieval-backed source capture")
    node(model, "prompt", "Prompt Assembly", 70, 170, 150, 70, detail="System prompt + history + safety + query", fill=CYAN)
    node(model, "llm", "LLM Node", 280, 170, 150, 70, detail="AzureChatOpenAI.bind_tools()", fill=CYAN)
    node(model, "decision", "Tool Calls?", 490, 162, 135, 86, detail="Continue while calls exist", fill=AMBER, shape="rhombus;whiteSpace=wrap;html=1;")
    node(model, "tool", "Tool Node", 700, 170, 150, 70, detail="Run selected registered tool", fill=GREEN)
    node(model, "sources", "Source Capture", 910, 170, 150, 70, detail="Only RAG/document/policy hits with snippets", fill=GREEN)
    node(model, "answer", "Final Answer", 1120, 170, 140, 70, detail="No tool calls or loop-limit final", fill=CYAN)
    edge(model, "e1", "prompt", "llm")
    edge(model, "e2", "llm", "decision")
    edge(model, "e3", "decision", "tool", "yes")
    edge(model, "e4", "tool", "sources")
    edge(model, "e5", "sources", "llm", "tool output")
    edge(model, "e6", "decision", "answer", "no")

    group_label(model, "retrieval_group", "Retrieval-backed tools", 90, 360, 360, 300, "#F0FDF4")
    group_label(model, "structured_group", "Catalog / structured tools", 500, 360, 360, 300, "#FFFBEB")
    group_label(model, "guard_group", "Healthcare guard", 910, 360, 300, 300, "#FFF1F2")
    node(model, "rag", "rag_search", 130, 430, 130, 58, detail="catalog-guided RAG", fill=GREEN)
    node(model, "doc", "document_search", 280, 430, 130, 58, detail="catalog-guided docs", fill=GREEN)
    node(model, "policy", "policy_search", 205, 530, 130, 58, detail="policy candidate preference", fill=GREEN)
    node(model, "catalog", "document_catalog", 535, 430, 135, 58, detail="manifest + candidate keys", fill=AMBER)
    node(model, "table", "table_lookup", 690, 430, 135, 58, detail="deterministic CSV facts", fill=AMBER)
    node(model, "rota", "calendar_rota_lookup", 535, 535, 135, 58, detail="schedules", fill=AMBER)
    node(model, "formulary", "formulary_table_lookup", 690, 535, 135, 58, detail="medicine facts", fill=AMBER)
    node(model, "safety", "safety_guard", 990, 455, 140, 65, detail="risk, PHI, escalation", fill=ROSE)
    edge(model, "helper1", "catalog", "rag", "helper")
    edge(model, "helper2", "catalog", "doc", "helper")
    edge(model, "helper3", "catalog", "policy", "helper")

    group_label(model, "workflow_group", "Detailed tool workflows", 70, 690, 1160, 230, "#F8FAFC")
    node(
        model,
        "rag_flow",
        "RAG document search",
        110,
        760,
        320,
        92,
        detail="Catalog terms -> up to 8 role-allowed keys -> OpenSearch vector/keyword query with optional key filter -> snippets/citations",
        fill=GREEN,
    )
    node(
        model,
        "fact_flow",
        "Deterministic fact lookup",
        490,
        760,
        320,
        92,
        detail="Manifest CSV sources -> read S3 object -> csv.DictReader -> term match row values -> exact JSON rows",
        fill=AMBER,
    )
    node(
        model,
        "catalog_flow",
        "Document catalog",
        870,
        760,
        320,
        92,
        detail="Load manifest -> match title/key/content type/metadata -> explicit metadata output or internal RAG candidate keys",
        fill=CYAN,
    )
    return model


def ingestion_retrieval_page() -> ET.Element:
    model = make_model(1300, 780)
    title(model, "Document Ingestion And Retrieval", "How source documents become searchable chunks and citations")
    ingestion = [
        ("i1", "S3 raw/ prefix", "PDF, DOCX, MD, TXT, CSV"),
        ("i2", "Parse Document", "extract text + tables"),
        ("i3", "Infer Metadata", "domain, type, roles, checksum"),
        ("i4", "Chunk Text", "1200 chars + overlap"),
        ("i5", "Embed Chunk", "Azure OpenAI embeddings"),
        ("i6", "Index Chunk", "OpenSearch vector index"),
        ("i7", "Write Manifest", "S3 manifests/documents.json"),
    ]
    retrieval = [
        ("r1", "User Query", "redacted safe query"),
        ("r2", "Embed Query", "Azure OpenAI"),
        ("r3", "Search Index", "knn vector or keyword fallback"),
        ("r4", "Normalize Hits", "title, uri, text, score, metadata"),
        ("r5", "Access Filter", "allowed_roles vs user roles"),
        ("r6", "Return Sources", "citations + snippets"),
    ]
    for idx, (cell_id, label, detail) in enumerate(ingestion):
        node(model, cell_id, label, 70 + idx * 165, 170, 130, 62, detail=detail, fill=GREEN)
        if idx:
            edge(model, f"ie{idx}", ingestion[idx - 1][0], cell_id)
    for idx, (cell_id, label, detail) in enumerate(retrieval):
        node(model, cell_id, label, 150 + idx * 175, 430, 135, 62, detail=detail, fill=CYAN)
        if idx:
            edge(model, f"re{idx}", retrieval[idx - 1][0], cell_id)
    node(model, "label1", "Ingestion path", 70, 110, 180, 40, fill="#FFFFFF", stroke="#FFFFFF", shape="text;whiteSpace=wrap;html=1;")
    node(model, "label2", "Retrieval path", 150, 370, 180, 40, fill="#FFFFFF", stroke="#FFFFFF", shape="text;whiteSpace=wrap;html=1;")
    return model


def eval_page() -> ET.Element:
    model = make_model(1300, 760)
    title(model, "RAGAS Evaluation And Langfuse Score Publishing", "Per-question scores attach to chat traces; aggregate scores attach to an eval-run trace")
    steps = [
        ("e1", "Golden Dataset", "questions + expected answers"),
        ("e2", "Call /chat", "store response trace_id"),
        ("e3", "Build Contexts", "prefer source.snippet, fallback URI"),
        ("e4", "Run RAGAS", "faithfulness, relevancy, precision, recall"),
        ("e5", "Publish Row Scores", "Langfuse score on chat trace"),
        ("e6", "Eval-Run Trace", "synthetic summary trace"),
        ("e7", "Summary Scores", "averages, totals, failures"),
        ("e8", "Local JSON Report", "publish status/errors retained"),
    ]
    for idx, (cell_id, label, detail) in enumerate(steps):
        node(model, cell_id, label, 70 + (idx % 4) * 300, 170 + (idx // 4) * 210, 190, 70, detail=detail, fill=ROSE if idx in {4, 5, 6} else CYAN)
        if idx:
            edge(model, f"edge{idx}", steps[idx - 1][0], cell_id)
    return model


def build_drawio() -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    mxfile = ET.Element(
        "mxfile",
        {
            "host": "app.diagrams.net",
            "modified": "2026-06-19T00:00:00.000Z",
            "agent": "OpenAI Codex",
            "version": "24.7.17",
            "type": "device",
        },
    )
    pages = [
        ("Overall Architecture", architecture_page()),
        ("Chat Workflow", chat_workflow_page()),
        ("LangGraph Agent Tools", agent_tool_page()),
        ("Ingestion And Retrieval", ingestion_retrieval_page()),
        ("RAGAS Langfuse Evaluation", eval_page()),
    ]
    for idx, (name, model) in enumerate(pages, start=1):
        diagram = ET.SubElement(mxfile, "diagram", {"id": f"diagram-{idx}", "name": name})
        diagram.append(model)

    tree = ET.ElementTree(mxfile)
    ET.indent(tree, space="  ")
    tree.write(DRAWIO_PATH, encoding="utf-8", xml_declaration=True)
    return DRAWIO_PATH


if __name__ == "__main__":
    print(build_drawio())
