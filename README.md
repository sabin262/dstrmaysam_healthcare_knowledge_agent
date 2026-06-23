# Healthcare Knowledge Agent

This repository implements a containerized internal knowledge assistant MVP with:

- FastAPI backend
- Streamlit chat frontend
- Simple login
- Persistent chat history
- LangGraph agent orchestration with LangChain Azure OpenAI integrations
- RAG over S3 documents and OpenSearch Serverless
- Postgres-backed deterministic lookup for patient, doctor, department, contact, appointment, ward, and formulary data
- Azure OpenAI through `langchain-openai`
- Langfuse tracing and prompt management
- RAGAS golden-data evaluation with optional Langfuse score publishing
- 100-query stress testing
- AWS Secrets Manager in deployed environments, with a local secret-file fallback for development
- ECR/ECS Fargate deployment templates

## Secret Model

Do not put API keys, passwords, token signing secrets, or Langfuse credentials in source code, Docker images, or Streamlit secrets.

In `APP_ENV=local` or `APP_ENV=test`, the backend uses `LOCAL_APP_SECRET_FILE`
for app auth/session secrets and reads Azure OpenAI/Langfuse credentials from
environment variables. If `LOCAL_APP_SECRET_FILE` is missing, the backend creates
one with a generated session secret and the configured local username/password.

In non-local environments, secrets are loaded from AWS Secrets Manager.

Expected secret JSON documents:

`/dstrmaysam-healthcare-knowledge-agent/dev/app`

```json
{
  "session_secret": "replace-with-long-random-value",
  "auth_users": {
    "admin": "pbkdf2_sha256$200000$salt_hex$hash_hex"
  },
  "user_profiles": {
    "admin": {
      "roles": ["admin", "doctor"],
      "departments": ["clinical_governance"]
    }
  }
}
```

`/dstrmaysam-healthcare-knowledge-agent/dev/azure-openai`

```json
{
  "endpoint": "https://YOUR-RESOURCE.openai.azure.com/",
  "api_key": "azure-openai-key",
  "api_version": "2025-04-01-preview",
  "chat_deployment": "your-chat-deployment",
  "embedding_deployment": "your-embedding-deployment"
}
```

`/dstrmaysam-healthcare-knowledge-agent/dev/langfuse`

```json
{
  "public_key": "pk-lf-...",
  "secret_key": "sk-lf-...",
  "base_url": "https://cloud.langfuse.com"
}
```

Generate a password hash without storing the password:

```bash
python -m backend.app.auth hash-password
```

Inside the backend container, use `python -m app.auth hash-password`.

## Local Run

```bash
cp .env.example .env
docker compose up --build
```

Open the chat UI at `http://localhost:8501`.

Docker Compose also starts a local Postgres database. The schema and mock data
are loaded from `database/init/` on first container startup. If you need to
re-run the init scripts from scratch, remove the `postgres_data` Docker volume
and start Compose again.

For local testing only, Docker Compose can inject a test admin account when
`APP_ENV=local`:

- Username: `admin`
- Password: `admin123`

Disable the test-admin overlay by setting `LOCAL_TEST_ADMIN_ENABLED=false`. This
does not disable the local secret file. The app still authenticates users from
`LOCAL_APP_SECRET_FILE`, so update that file if you want different development
users.

## Chat History and Observability Fallbacks

Set `CHAT_HISTORY_BACKEND=dynamodb_postgres` to use DynamoDB first and Postgres
only if DynamoDB operations fail. This keeps production behavior aligned with
DynamoDB while preserving chat history, previous-chat lists, and dashboard query
analytics during local credential/network/table failures.

Supported values:

- `dynamodb_postgres`: DynamoDB primary, Postgres fallback
- `dynamodb`: DynamoDB only
- `postgres`: Postgres only
- `memory`: process memory only, not durable

If Langfuse trace updates fail, the backend writes the trace payload to the
Postgres `langfuse_trace_outbox` table with `status='pending'` so it can be
retried later. Chat message persistence is independent of Langfuse availability.

## Ingest Documents

Upload documents to S3 under the configured `S3_RAW_PREFIX`, then run:

```bash
docker compose run --rm backend python -m app.ingest
```

Supported source formats: PDF, DOCX, markdown, text, and CSV.

## Deterministic Lookup Data

The backend registers `postgres_deterministic_lookup` for exact structured
answers. It should be used for questions about:

- patient details by name, MRN, or NHS number
- doctor or consultant contact details
- department and escalation contacts
- organization directory entries
- appointments and clinic slots
- ward locations, beds, and phone numbers
- formulary and restricted medicine facts

Local mock data lives in Postgres tables created by:

- `database/init/01_schema.sql`
- `database/init/02_seed.sql`

A CSV copy of the organization directory is also available at
`data/organization_directory.csv`.

Example deterministic lookup questions:

- "What is the phone number for ICU outreach?"
- "Which doctor is on call for Cardiology?"
- "Show patient details for MRN10003."
- "Does Leo Bennett have any appointments?"
- "Show me a list of available doctors and nurses for today and tomorrow."
- "Where is ward W05?"
- "Is vancomycin restricted?"

## Evals

Run the golden-data eval after the API is running:

```bash
python evals/run_ragas_eval.py --api-url http://localhost:8000 --token YOUR_TOKEN
```

For the healthcare Phase One scaffold, use:

```bash
python evals/run_ragas_eval.py --dataset evals/healthcare_golden_dataset.csv --api-url http://localhost:8000 --token YOUR_TOKEN
```

Publish per-question and summary RAGAS scores to Langfuse:

```bash
python evals/run_ragas_eval.py --api-url http://localhost:8000 --token YOUR_TOKEN --publish-langfuse --secrets-stage dev
```

The eval runner loads Langfuse credentials from AWS Secrets Manager using
`/dstrmaysam-healthcare-knowledge-agent/<stage>/langfuse` unless `--langfuse-secret-name` is provided.
RAGAS contexts use `/chat` source snippets when available and fall back to source
URIs.

Run the 100-query stress test. The default workload covers patient details,
appointments, rota, formulary, catalogue, policy RAG, and safety-sensitive
questions:

```bash
python evals/stress_test.py --api-url http://localhost:8000 --token YOUR_TOKEN
```

## AWS Deployment

1. Create S3 bucket, DynamoDB table, OpenSearch Serverless collection/index, ECR repositories, and Secrets Manager entries.
2. Build and push backend/frontend images to ECR.
3. Fill in the ECS task definition templates in `infra/`.
4. Create ECS Fargate services behind an Application Load Balancer.
5. Attach IAM task roles with least-privilege permissions for Secrets Manager, S3, DynamoDB, OpenSearch, and CloudWatch.
