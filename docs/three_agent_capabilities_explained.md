# Three Agent Capabilities Explained

## Important Clarification

This project does **not** currently run three separate deployed agent services.

The actual implementation has one main orchestrator:

- `KnowledgeAgent` in `backend/app/agent.py`

That `KnowledgeAgent` uses three registered tools from `backend/app/tools.py`:

1. `rag_search`
2. `document_catalog`
3. `table_lookup`

For project presentation, these three tools can be explained as three conceptual agent capabilities:

1. **RAG Search Agent**
2. **Document Catalog Agent**
3. **CSV/Table Lookup Agent**

This is a useful way to explain the system because each capability has a different responsibility, data source, and ideal use case. Technically, however, they are implemented as tools under one LangChain-backed `KnowledgeAgent`, not as three independent microservices.

## How The KnowledgeAgent Orchestrates The Three Capabilities

The `KnowledgeAgent` is the brain of the assistant. It receives a user question from the FastAPI `/chat` endpoint, gathers context, runs the three knowledge tools, calls the LLM, and stores the final answer with metadata.

The high-level flow is:

1. The user asks a question in the Streamlit chat UI.
2. Streamlit sends the question to FastAPI through `POST /chat`.
3. FastAPI validates the bearer token and identifies the user.
4. `KnowledgeAgent.answer()` receives:
   - `user_id`
   - `query`
   - optional `session_id`
5. The agent loads previous messages from chat history.
6. The agent builds a bounded conversation-history context.
7. The agent runs the knowledge tools:
   - `rag_search`
   - `document_catalog`
   - `table_lookup`
8. The agent combines tool outputs into one tool-context block.
9. The agent loads the system prompt from Langfuse when available.
10. The agent calls Azure OpenAI through the LangChain wrapper.
11. The answer is returned with:
    - answer text
    - sources
    - tools used
    - input token estimate
    - output token estimate
    - latency
    - Langfuse trace ID
12. The user message and assistant response are stored in chat history.

In simple terms:

```text
User question
  -> FastAPI
  -> KnowledgeAgent
  -> Chat history
  -> RAG Search Tool
  -> Document Catalog Tool
  -> CSV/Table Lookup Tool
  -> Azure OpenAI via LangChain
  -> Final answer with citations and metadata
```

The three tools are always listed as used in the current implementation:

```python
tools_used = ["rag_search", "document_catalog", "table_lookup"]
```

The agent then builds a combined tool context:

```text
RAG search results:
...

Document catalog results:
...

CSV/table lookup results:
...
```

That combined context is passed to the LLM along with the conversation history and the user question.

## 1. RAG Search Agent

### Actual Tool Name

`rag_search`

### Conceptual Presentation Name

RAG Search Agent

### Main Purpose

The RAG Search Agent finds the most relevant chunks of company documents for a user's question. It is the main grounding mechanism for answers.

RAG stands for **Retrieval Augmented Generation**. The idea is simple:

1. Retrieve relevant company knowledge first.
2. Give that retrieved knowledge to the LLM.
3. Ask the LLM to answer using that context.

This reduces hallucination because the model is not expected to answer only from its general training knowledge. Instead, it receives specific company document excerpts.

### What It Searches

The RAG Search Agent searches document chunks that were previously ingested from S3 and indexed into OpenSearch Serverless.

The ingestion pipeline is handled in `backend/app/ingest.py`.

Supported source files include:

- PDF files
- Markdown files
- Plain text files
- CSV files

During ingestion:

1. Documents are read from S3.
2. Text is extracted.
3. Text is split into chunks.
4. Embeddings are created with Azure OpenAI.
5. Chunks are indexed into OpenSearch Serverless.
6. Metadata and source URIs are stored with each chunk.

### How It Works In Code

The tool is defined in `backend/app/tools.py`:

```python
def rag_search(query: str) -> str:
    """Search indexed company documents using retrieval augmented generation context."""
    return format_retrieval_hits(retrieval.search(query))
```

The real retrieval happens in `backend/app/retrieval.py`.

`RetrievalService.search()` does this:

1. Checks whether an OpenSearch endpoint is configured.
2. Creates an OpenSearch Serverless client.
3. Tries to create an embedding for the user query with Azure OpenAI.
4. If an embedding is available, it runs vector search using `knn`.
5. If embedding fails, it falls back to keyword-style search using `multi_match`.
6. Converts OpenSearch hits into `RetrievalHit` objects.

### Input

The input is the user's natural-language query.

Example:

```text
What is the company's annual leave policy?
```

### Output

The output is formatted retrieved context.

Each retrieved hit contains:

- document title
- S3 URI
- retrieval score
- chunk text
- metadata

Example output shape:

```text
[1] Leave Policy (s3://company-assistant-dev/raw/hr/leave-policy.md, score=0.91)
Employees are entitled to annual leave according to...

[2] Employee Handbook (s3://company-assistant-dev/raw/hr/employee-handbook.pdf, score=0.84)
Leave requests should be submitted through...
```

In the final API response, sources are returned separately as structured data:

```json
{
  "title": "Leave Policy",
  "uri": "s3://company-assistant-dev/raw/hr/leave-policy.md",
  "score": 0.91,
  "metadata": {
    "department": "HR"
  }
}
```

### Best Use Cases

Use the RAG Search Agent for questions like:

- "What is our leave policy?"
- "How do I request access to internal systems?"
- "What does the employee handbook say about remote work?"
- "What is the escalation process for incidents?"
- "Summarize the onboarding process for new hires."

This capability is strongest when the answer is written in unstructured or semi-structured documents.

### Example Query

```text
What is the company's remote work policy?
```

### How The Answer Is Produced

1. The query is sent to `RetrievalService.search()`.
2. Azure OpenAI embeddings are generated for the query.
3. OpenSearch Serverless finds similar document chunks.
4. The chunks are formatted as RAG context.
5. The `KnowledgeAgent` passes the context to the LLM.
6. The LLM writes an answer using the retrieved company text.
7. The response includes citations from the S3-backed documents.

### Why This Capability Matters

This is the most important capability for a company knowledge assistant because most internal knowledge is stored in documents:

- policies
- handbooks
- runbooks
- onboarding guides
- standard operating procedures
- internal FAQs
- compliance documents

Without RAG, the LLM might produce fluent but unsupported answers. With RAG, answers can be tied back to company sources.

### Limitations

The RAG Search Agent depends on the quality of ingestion and indexing.

Important limitations:

- If documents have not been ingested, it cannot retrieve useful context.
- If the OpenSearch endpoint is not configured, it returns no results.
- If embeddings fail, retrieval falls back to keyword search.
- Poor chunking can reduce answer quality.
- Scanned PDFs without OCR may produce weak or empty text.
- Retrieval may miss answers if the query uses very different wording from the source documents.

### Future Improvements

Recommended improvements:

- Add OCR for scanned PDFs.
- Add hybrid search combining vector similarity and keyword relevance.
- Add metadata filters such as department, document type, region, and date.
- Add reranking to improve top results.
- Add source snippet previews in the UI.
- Add confidence scoring based on retrieval quality.
- Add document freshness checks.

## 2. Document Catalog Agent

### Actual Tool Name

`document_catalog`

### Conceptual Presentation Name

Document Catalog Agent

### Main Purpose

The Document Catalog Agent helps the assistant understand what documents exist in the knowledge base.

It does not search inside document chunks like the RAG Search Agent. Instead, it reads the document manifest and returns document-level metadata.

This is useful when the user asks discovery-style questions, such as:

- "Which HR documents are indexed?"
- "Do we have any onboarding documents?"
- "What finance policies are available?"
- "Which documents are related to access requests?"

### What It Searches

The Document Catalog Agent searches the S3 document manifest.

The manifest is written by the ingestion job after documents are processed.

The configured manifest key is usually:

```text
manifests/documents.json
```

The manifest contains document-level information such as:

- S3 key
- title
- content type
- checksum
- metadata
- chunk count

### How It Works In Code

The tool is defined in `backend/app/tools.py`:

```python
def document_catalog(query: str) -> str:
    """List or filter indexed company documents from the S3 manifest."""
    terms = [term.lower() for term in query.split() if len(term) >= 3]
    records = []
    for record in documents.list_documents():
        haystack = " ".join(
            [record.title, record.key, record.content_type, json.dumps(record.metadata)]
        ).lower()
        if not terms or any(term in haystack for term in terms):
            records.append(
                {
                    "title": record.title,
                    "uri": record.uri,
                    "content_type": record.content_type,
                    "metadata": record.metadata,
                }
            )
    return json.dumps(records[:20], indent=2)
```

It uses `DocumentStore.list_documents()` from `backend/app/storage.py`.

`DocumentStore.list_documents()` does this:

1. Loads the manifest from S3.
2. Reads the `documents` array.
3. Converts each manifest record into a `DocumentRecord`.
4. Builds an S3 URI for each record.
5. Returns document metadata to the tool.

### Input

The input is the user's query.

Example:

```text
Which HR policies are available?
```

The tool extracts query terms with at least three characters:

```text
which, policies, are, available
```

It then compares those terms against:

- document title
- S3 key
- content type
- metadata JSON

### Output

The output is a JSON list of matching documents.

Example:

```json
[
  {
    "title": "Leave Policy",
    "uri": "s3://company-assistant-dev/raw/hr/leave-policy.md",
    "content_type": "text/markdown",
    "metadata": {
      "department": "HR"
    }
  },
  {
    "title": "Employee Handbook",
    "uri": "s3://company-assistant-dev/raw/hr/employee-handbook.pdf",
    "content_type": "application/pdf",
    "metadata": {
      "department": "HR"
    }
  }
]
```

The implementation returns up to 20 matching records:

```python
return json.dumps(records[:20], indent=2)
```

### Best Use Cases

Use the Document Catalog Agent for questions like:

- "What documents are indexed?"
- "Which HR documents do we have?"
- "Are there any finance policies in the knowledge base?"
- "Show me documents related to onboarding."
- "Do we have a policy document about travel?"

This capability is strongest for document discovery, auditability, and transparency.

### Example Query

```text
Which onboarding documents are available?
```

### How The Answer Is Produced

1. The query is passed to `document_catalog`.
2. The tool loads the S3 manifest through `DocumentStore`.
3. It extracts searchable terms from the query.
4. It checks those terms against document title, key, content type, and metadata.
5. It returns matching document records.
6. The `KnowledgeAgent` includes those records in the LLM context.
7. The LLM can answer with document names and source locations.

### Why This Capability Matters

RAG answers are only useful if the system can explain where knowledge came from. The Document Catalog Agent gives the assistant awareness of the available knowledge base.

It helps with:

- transparency
- debugging
- demos
- governance
- document discovery
- checking whether ingestion worked

For example, if a user asks "Do we have a travel policy?", the assistant can inspect the catalog instead of trying to infer from retrieved chunks alone.

### Limitations

Important limitations:

- It only knows about documents present in the manifest.
- If ingestion has not written the manifest, it returns no documents.
- Matching is simple term-based matching, not semantic search.
- It does not inspect full document content.
- It currently limits output to 20 records.
- Metadata quality depends on the ingestion process and source data.

### Future Improvements

Recommended improvements:

- Add richer metadata extraction during ingestion.
- Add department, owner, effective date, expiry date, and sensitivity labels.
- Add semantic search over document titles and metadata.
- Add filters in the Streamlit UI.
- Add document freshness and stale-document warnings.
- Add an admin view showing indexed documents and ingestion status.

## 3. CSV/Table Lookup Agent

### Actual Tool Name

`table_lookup`

### Conceptual Presentation Name

CSV/Table Lookup Agent

### Main Purpose

The CSV/Table Lookup Agent answers questions that require exact or structured data lookup.

RAG is good for paragraphs and policy text. But many company facts live in tables:

- support contacts
- system owners
- escalation paths
- approval limits
- office locations
- cost centers
- department codes
- software request categories
- vendor lists

The CSV/Table Lookup Agent is designed for those cases.

### What It Searches

The tool searches CSV files that are listed in the S3 manifest.

It does not search every S3 object directly. It first asks `DocumentStore.list_documents()` which documents are indexed. Then it only processes documents whose key ends with `.csv`.

### How It Works In Code

The tool is defined in `backend/app/tools.py`:

```python
def table_lookup(query: str) -> str:
    """Look up exact answers in CSV or table-like files stored in S3."""
    return json.dumps(documents.lookup_table(query), indent=2)
```

The lookup logic is in `DocumentStore.lookup_table()` in `backend/app/storage.py`.

That function does this:

1. Splits the query into terms.
2. Keeps terms with at least three characters.
3. Loads documents from the S3 manifest.
4. Skips non-CSV files.
5. Reads each CSV file from S3.
6. Parses the CSV with `csv.DictReader`.
7. Joins row values into searchable text.
8. Returns rows where any query term appears in the row text.
9. Stops once it reaches the configured limit, currently 10 rows.

### Input

The input is the user query.

Example:

```text
Who owns the payroll system?
```

Possible query terms:

```text
who, owns, the, payroll, system
```

The tool then checks CSV rows for terms such as `payroll` and `system`.

### Output

The output is a JSON list of matching rows.

Example:

```json
[
  {
    "source": "s3://company-assistant-dev/raw/it/system-owners.csv",
    "title": "system-owners.csv",
    "row": {
      "system": "Payroll",
      "owner": "People Operations",
      "support_channel": "hr-payroll@example.com",
      "backup_owner": "Finance Operations"
    }
  }
]
```

### Best Use Cases

Use the CSV/Table Lookup Agent for questions like:

- "Who owns the payroll system?"
- "What is the escalation contact for Salesforce?"
- "Which team approves laptop requests?"
- "What is the cost center for Engineering?"
- "Who is the backup owner for the data warehouse?"
- "What is the support channel for VPN access?"

This capability is strongest when the answer is a specific row or value in a structured file.

### Example Query

```text
What is the support contact for VPN access?
```

### How The Answer Is Produced

1. The query is passed to `table_lookup`.
2. `DocumentStore.lookup_table()` loads the S3 manifest.
3. It filters the manifest to CSV files.
4. It reads matching CSV files from S3.
5. It parses rows with `csv.DictReader`.
6. It searches row values for terms from the query.
7. It returns matched rows as JSON.
8. The `KnowledgeAgent` includes those rows in the LLM context.
9. The LLM converts the structured row into a natural-language answer.

### Why This Capability Matters

Many internal assistant failures happen because the system treats structured data like ordinary text.

For example, a table like this:

```csv
system,owner,support_channel
VPN,IT Infrastructure,it-infra@example.com
Payroll,People Operations,hr-payroll@example.com
```

should be queried as rows and fields, not as a loose document paragraph.

The CSV/Table Lookup Agent makes the assistant better at exact-answer questions.

### Limitations

Important limitations:

- Matching is simple term matching.
- It does not currently understand column intent deeply.
- It does not rank matches by field importance.
- It only reads CSV files listed in the manifest.
- It returns up to 10 matching rows by default.
- Large CSV files may need pagination, indexing, or a database-backed lookup.
- It does not currently support Excel files directly.

### Future Improvements

Recommended improvements:

- Add column-aware matching.
- Add support for Excel files.
- Add schema descriptions for important CSV files.
- Add SQL-like querying through Athena or DynamoDB for larger structured datasets.
- Add exact field extraction, for example "return only the owner column."
- Add confidence scoring based on exact column matches.
- Add validation for duplicate or conflicting rows.

## How The Three Capabilities Work Together

The three capabilities are complementary.

They answer different kinds of questions:

| Capability | Main Data Type | Best For | Example |
|---|---|---|---|
| RAG Search Agent | Unstructured document chunks | Policy/process explanations | "What is the remote work policy?" |
| Document Catalog Agent | Document-level metadata | Knowledge discovery | "Which HR policies are indexed?" |
| CSV/Table Lookup Agent | Structured CSV rows | Exact facts and ownership data | "Who owns the payroll system?" |

When a user asks a question, the `KnowledgeAgent` can use all three outputs together.

Example:

```text
Who approves remote work exceptions and where is the policy documented?
```

The tools help in different ways:

- `rag_search` retrieves the remote work policy text.
- `document_catalog` confirms which remote work documents exist.
- `table_lookup` may find an approvals CSV row showing the approving team or contact.

The final answer can then include:

- policy explanation
- approving team or contact
- citation to the policy document
- source metadata

## Example End-To-End Query

### User Question

```text
What is the process for requesting VPN access, and who owns it?
```

### Step 1: Chat History Is Loaded

The assistant first loads previous messages for the same user and session.

This matters if the user previously said:

```text
I am asking about IT access for new joiners.
```

The follow-up question can then be interpreted in that context.

### Step 2: RAG Search Agent Runs

The RAG Search Agent searches indexed documents for VPN access information.

Possible result:

```text
IT Access Guide
s3://company-assistant-dev/raw/it/access-guide.md

New VPN access requests must be submitted through the IT service desk...
```

### Step 3: Document Catalog Agent Runs

The Document Catalog Agent checks what related documents exist.

Possible result:

```json
[
  {
    "title": "IT Access Guide",
    "uri": "s3://company-assistant-dev/raw/it/access-guide.md",
    "content_type": "text/markdown",
    "metadata": {
      "department": "IT"
    }
  }
]
```

### Step 4: CSV/Table Lookup Agent Runs

The CSV/Table Lookup Agent checks CSV rows for structured owner information.

Possible result:

```json
[
  {
    "source": "s3://company-assistant-dev/raw/it/system-owners.csv",
    "title": "system-owners.csv",
    "row": {
      "system": "VPN",
      "owner": "IT Infrastructure",
      "support_channel": "it-infra@example.com"
    }
  }
]
```

### Step 5: The KnowledgeAgent Builds Tool Context

The agent combines:

- conversation history
- RAG search results
- document catalog results
- CSV/table lookup results
- user question
- system prompt

### Step 6: Azure OpenAI Generates The Final Answer

The LLM receives the combined context and writes a user-friendly answer.

Possible final answer:

```text
To request VPN access, submit a request through the IT service desk using the access request process described in the IT Access Guide. The owning team for VPN is IT Infrastructure, and the support channel is it-infra@example.com.

Sources:
- IT Access Guide: s3://company-assistant-dev/raw/it/access-guide.md
- system-owners.csv: s3://company-assistant-dev/raw/it/system-owners.csv
```

### Step 7: Response Metadata Is Stored

The backend stores:

- user question
- assistant answer
- sources
- tools used
- input token estimate
- output token estimate
- latency
- Langfuse trace ID
- prompt version, when available

This makes the assistant auditable and useful for debugging.

## Failure Handling And Fallbacks

### Shared Remote Call Retry

Remote-facing functions use the retry helper in `backend/app/retries.py`.

Retry behavior is applied to:

- Secrets Manager loading
- S3 manifest/object reads
- OpenSearch retrieval
- ingestion job execution

This helps with temporary API or network failures.

### RAG Search Fallbacks

The RAG Search Agent has several fallback behaviors:

- If `OPENSEARCH_ENDPOINT` is not configured, it returns no retrieval hits.
- If Azure OpenAI embedding generation fails, it falls back to OpenSearch `multi_match`.
- If no hits are found, the formatted result says:

```text
No relevant document chunks found.
```

If the LLM is unavailable and no RAG context exists, the offline answer says:

```text
I could not find enough indexed company context to answer this confidently.
Please ingest relevant documents into S3/OpenSearch and try again.
```

### Document Catalog Fallbacks

The Document Catalog Agent depends on the S3 manifest.

If the manifest cannot be loaded, `DocumentStore._load_manifest()` returns:

```json
{
  "documents": []
}
```

This prevents the whole app from crashing, but it means the catalog will appear empty.

### CSV/Table Lookup Fallbacks

The CSV/Table Lookup Agent also depends on the manifest.

If no CSV files are listed, or if no rows match the query terms, it returns an empty list:

```json
[]
```

The LLM can then answer that no structured match was found.

### LLM Fallback

If Azure OpenAI is unavailable, the `KnowledgeAgent` can produce an offline fallback response from retrieved tool context.

This is useful for local development and demos where AWS resources may not be fully configured yet.

However, production-quality answers require Azure OpenAI to be configured through AWS Secrets Manager.

## Future Improvements

### Improvements For The RAG Search Agent

- Add OCR for scanned PDFs.
- Add hybrid vector and keyword search.
- Add reranking after OpenSearch retrieval.
- Add metadata filters for department, owner, region, and effective date.
- Add source snippet previews in the frontend.
- Add retrieval confidence scoring.
- Add automated stale-document detection.

### Improvements For The Document Catalog Agent

- Add richer metadata extraction during ingestion.
- Add tags for department, document owner, policy type, sensitivity, and expiry date.
- Add semantic search over document metadata.
- Add a Streamlit admin tab for indexed document visibility.
- Add ingestion status, last indexed time, and checksum display.
- Add warnings for missing, duplicate, or stale documents.

### Improvements For The CSV/Table Lookup Agent

- Add support for Excel files.
- Add column-aware matching.
- Add schema descriptions for important CSV files.
- Add exact field extraction, such as "return only the owner."
- Add Athena or DynamoDB integration for larger structured datasets.
- Add validation for duplicate rows and conflicting ownership data.

### Improvements For The Overall Agent

- Let the LLM decide whether to call all tools or only selected tools.
- Add tool-specific confidence scores.
- Add human feedback buttons in the UI.
- Send more detailed tool traces to Langfuse.
- Add query classification before tool selection.
- Add stricter citation enforcement.
- Add answer quality checks before returning responses.

## Code Reference Map

| Area | File | What To Look For |
|---|---|---|
| Main agent orchestration | `backend/app/agent.py` | `KnowledgeAgent.answer()`, `_build_tool_context()`, `_generate_answer()`, `_call_langchain_agent()` |
| Tool definitions | `backend/app/tools.py` | `build_agent_tools()`, `rag_search`, `document_catalog`, `table_lookup` |
| RAG retrieval | `backend/app/retrieval.py` | `RetrievalService.search()`, Azure OpenAI embeddings, OpenSearch `knn`, OpenSearch `multi_match` fallback |
| S3 document manifest | `backend/app/storage.py` | `DocumentStore.list_documents()`, `_load_manifest()` |
| CSV/table lookup | `backend/app/storage.py` | `DocumentStore.lookup_table()`, `read_text()` |
| Chat history context | `backend/app/history.py` | `build_history_context()`, in-memory and DynamoDB repositories |
| API entrypoint | `backend/app/main.py` | `/chat`, `/documents`, `/chat/sessions`, `/auth/login` |
| Ingestion | `backend/app/ingest.py` | S3 loading, parsing, chunking, embedding, OpenSearch indexing, manifest writing |
| Observability | `backend/app/observability.py` | Langfuse callbacks and prompt loading |
| Eval scripts | `evals/run_ragas_eval.py`, `evals/stress_test.py` | Golden-data evaluation and 100-query stress testing |

## Short Presentation Summary

If you need to explain this in an interview, demo, or project review, use this version:

> The project has one main LangChain-backed `KnowledgeAgent`. It uses three tool-based capabilities that I explain as three conceptual agents. The RAG Search Agent retrieves relevant document chunks from OpenSearch over S3-ingested company documents. The Document Catalog Agent checks what documents exist in the S3 manifest and helps with discovery and governance. The CSV/Table Lookup Agent searches structured CSV rows for exact facts like owners, contacts, and escalation paths. The `KnowledgeAgent` combines these outputs with chat history, sends the context to Azure OpenAI, and returns an answer with citations, tools used, token counts, latency, and Langfuse trace metadata.

