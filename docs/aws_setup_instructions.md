# AWS Setup Instructions: Dstrmaysam Healthcare Knowledge Agent

Version: 1.0  
Last updated: 2026-06-19  
Target stage: `dev`  
Default AWS region: `eu-west-2`

This runbook lists the AWS resources to create, the config values to use, and the values that must be replaced during deployment.

## 1. Naming And Base Values

Use these names consistently across AWS resources and ECS environment variables.

| Item | Value |
|---|---|
| Project slug | `dstrmaysam-healthcare-knowledge-agent` |
| AWS region | `eu-west-2` |
| AWS dev stage | `dev` |
| S3 bucket | `dstrmaysam-healthcare-knowledge-agent-dev` |
| S3 raw document prefix | `raw/` |
| S3 manifest key | `manifests/documents.json` |
| OpenSearch index | `dstrmaysam-healthcare-knowledge-agent` |
| DynamoDB table | `dstrmaysam-healthcare-knowledge-agent-dev` |
| Backend ECR repo | `dstrmaysam-healthcare-knowledge-agent-backend` |
| Frontend ECR repo | `dstrmaysam-healthcare-knowledge-agent-frontend` |
| Backend ECS task family | `dstrmaysam-healthcare-knowledge-agent-backend` |
| Frontend ECS task family | `dstrmaysam-healthcare-knowledge-agent-frontend` |
| ECS execution role | `dstrmaysam-healthcare-knowledge-agent-ecs-execution-role` |
| Backend task role | `dstrmaysam-healthcare-knowledge-agent-backend-task-role` |
| Frontend task role | `dstrmaysam-healthcare-knowledge-agent-frontend-task-role` |
| Backend log group | `/ecs/dstrmaysam-healthcare-knowledge-agent/backend` |
| Frontend log group | `/ecs/dstrmaysam-healthcare-knowledge-agent/frontend` |

Replace these placeholders before deploying:

| Placeholder | Replace With |
|---|---|
| `<account-id>` | Your AWS account ID |
| `<region>` | `eu-west-2`, unless deploying elsewhere |
| `<collection-id>` | OpenSearch Serverless collection ID |
| `<backend-service-discovery-name>` | ECS Cloud Map or Service Connect DNS name for the backend service |
| `<frontend-url>` | Final HTTPS URL for the Streamlit frontend |
| `<backend-url>` | Internal backend URL, or public API URL if you expose the backend |

## 2. Network And Security Baseline

Create or reuse a VPC with:

- At least two Availability Zones.
- Public subnets for the Application Load Balancer.
- Private subnets for ECS Fargate tasks.
- NAT Gateway or equivalent outbound route so backend tasks can reach Azure OpenAI and Langfuse.
- Security group for the ALB:
  - inbound `443` from allowed users or corporate network
  - optional inbound `80` only for redirect to HTTPS
- Security group for the frontend ECS service:
  - inbound `8501` from the ALB security group
  - outbound to the backend service on `8000`
- Security group for the backend ECS service:
  - inbound `8000` from the frontend service security group or internal ALB
  - outbound HTTPS to AWS APIs, Azure OpenAI, and Langfuse

Recommended private networking additions:

- S3 Gateway Endpoint.
- DynamoDB Gateway Endpoint.
- Interface endpoints for Secrets Manager, ECR API, ECR Docker, CloudWatch Logs, and STS.
- NAT or controlled egress for Azure OpenAI and Langfuse, unless those services are reachable through private networking in your environment.

## 3. S3 Bucket

Create the AWS dev document bucket:

```bash
aws s3api create-bucket \
  --bucket dstrmaysam-healthcare-knowledge-agent-dev \
  --region eu-west-2 \
  --create-bucket-configuration LocationConstraint=eu-west-2
```

Recommended bucket controls:

```bash
aws s3api put-public-access-block \
  --bucket dstrmaysam-healthcare-knowledge-agent-dev \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-encryption \
  --bucket dstrmaysam-healthcare-knowledge-agent-dev \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

Expected object layout:

```text
s3://dstrmaysam-healthcare-knowledge-agent-dev/raw/
s3://dstrmaysam-healthcare-knowledge-agent-dev/manifests/documents.json
```

Upload source documents into `raw/`. The ingestion job writes `manifests/documents.json`.

## 4. DynamoDB Chat History Table

Create the table using the shape in `infra/dynamodb-chat-history-table.json`.

Config values:

| Field | Value |
|---|---|
| Table name | `dstrmaysam-healthcare-knowledge-agent-dev` |
| Billing mode | `PAY_PER_REQUEST` |
| Partition key | `user_id` string |
| Sort key | `sort_key` string |

CLI example:

```bash
aws dynamodb create-table \
  --region eu-west-2 \
  --table-name dstrmaysam-healthcare-knowledge-agent-dev \
  --billing-mode PAY_PER_REQUEST \
  --attribute-definitions AttributeName=user_id,AttributeType=S AttributeName=sort_key,AttributeType=S \
  --key-schema AttributeName=user_id,KeyType=HASH AttributeName=sort_key,KeyType=RANGE
```

## 5. AWS Secrets Manager

Create three AWS dev secrets. Secret values must be JSON strings.

### App Secret

Secret name:

```text
/dstrmaysam-healthcare-knowledge-agent/dev/app
```

JSON shape:

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

Generate password hashes before creating the secret:

```bash
python -m backend.app.auth hash-password
```

Inside the backend container, use:

```bash
python -m app.auth hash-password
```

Do not use the local testing password in AWS dev. AWS dev ECS sets `LOCAL_TEST_ADMIN_ENABLED=false`.

### Azure OpenAI Secret

Secret name:

```text
/dstrmaysam-healthcare-knowledge-agent/dev/azure-openai
```

JSON shape:

```json
{
  "endpoint": "https://YOUR-RESOURCE.openai.azure.com/",
  "api_key": "replace-with-azure-openai-key",
  "api_version": "2025-04-01-preview",
  "chat_deployment": "replace-with-chat-deployment-name",
  "embedding_deployment": "replace-with-embedding-deployment-name"
}
```

The OpenSearch vector dimension must match the Azure embedding deployment. The current index template uses `1536`.

### Langfuse Secret

Secret name:

```text
/dstrmaysam-healthcare-knowledge-agent/dev/langfuse
```

JSON shape:

```json
{
  "public_key": "pk-lf-...",
  "secret_key": "sk-lf-...",
  "base_url": "https://cloud.langfuse.com"
}
```

## 6. OpenSearch Serverless

Create an OpenSearch Serverless collection for vector search.

Recommended values:

| Item | Value |
|---|---|
| Collection name | `dstrmaysam-healthcare-knowledge-agent` |
| Collection type | Vector search |
| Index name | `dstrmaysam-healthcare-knowledge-agent` |
| Vector field | `embedding` |
| Vector dimension | `1536` |
| Similarity | `cosinesimil` |

Create the index using `infra/opensearch-index.json` after the collection endpoint exists.

The backend ECS environment must receive:

```env
OPENSEARCH_ENDPOINT=https://<collection-id>.eu-west-2.aoss.amazonaws.com
OPENSEARCH_INDEX=dstrmaysam-healthcare-knowledge-agent
```

Access requirements:

- IAM policy on the backend task role must allow `aoss:APIAccessAll` for the collection ARN.
- OpenSearch Serverless data access policy must include the backend task role principal.
- The collection network policy must allow access from the backend task networking path.

## 7. ECR Repositories

Create repositories:

```bash
aws ecr create-repository \
  --region eu-west-2 \
  --repository-name dstrmaysam-healthcare-knowledge-agent-backend

aws ecr create-repository \
  --region eu-west-2 \
  --repository-name dstrmaysam-healthcare-knowledge-agent-frontend
```

Images expected by the ECS templates:

```text
<account-id>.dkr.ecr.eu-west-2.amazonaws.com/dstrmaysam-healthcare-knowledge-agent-backend:latest
<account-id>.dkr.ecr.eu-west-2.amazonaws.com/dstrmaysam-healthcare-knowledge-agent-frontend:latest
```

Build and push from the repo root:

```bash
aws ecr get-login-password --region eu-west-2 \
  | docker login --username AWS --password-stdin <account-id>.dkr.ecr.eu-west-2.amazonaws.com

docker build -t dstrmaysam-healthcare-knowledge-agent-backend:latest backend
docker tag dstrmaysam-healthcare-knowledge-agent-backend:latest <account-id>.dkr.ecr.eu-west-2.amazonaws.com/dstrmaysam-healthcare-knowledge-agent-backend:latest
docker push <account-id>.dkr.ecr.eu-west-2.amazonaws.com/dstrmaysam-healthcare-knowledge-agent-backend:latest

docker build -t dstrmaysam-healthcare-knowledge-agent-frontend:latest frontend
docker tag dstrmaysam-healthcare-knowledge-agent-frontend:latest <account-id>.dkr.ecr.eu-west-2.amazonaws.com/dstrmaysam-healthcare-knowledge-agent-frontend:latest
docker push <account-id>.dkr.ecr.eu-west-2.amazonaws.com/dstrmaysam-healthcare-knowledge-agent-frontend:latest
```

## 8. IAM Roles And Policies

### ECS Execution Role

Create:

```text
dstrmaysam-healthcare-knowledge-agent-ecs-execution-role
```

Attach the AWS managed policy:

```text
AmazonECSTaskExecutionRolePolicy
```

This role lets ECS pull ECR images and write container logs.

### Backend Task Role

Create:

```text
dstrmaysam-healthcare-knowledge-agent-backend-task-role
```

Attach the policy in `infra/iam-backend-task-policy.json` after replacing:

- `<account-id>`
- `<region>` with `eu-west-2`
- `<collection-id>` with the OpenSearch Serverless collection ID

Required permissions:

| Service | Permissions |
|---|---|
| Secrets Manager | `secretsmanager:GetSecretValue` on the three AWS dev secrets |
| S3 | `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` on the document bucket |
| DynamoDB | `GetItem`, `PutItem`, `Query`, `UpdateItem` on the chat history table |
| OpenSearch Serverless | `aoss:APIAccessAll` on the vector collection |

### Frontend Task Role

Create:

```text
dstrmaysam-healthcare-knowledge-agent-frontend-task-role
```

The frontend does not need direct access to Secrets Manager, S3, DynamoDB, OpenSearch, Azure OpenAI, or Langfuse. Keep this role minimal unless you add frontend-side AWS integrations later.

## 9. CloudWatch Logs

Create log groups:

```bash
aws logs create-log-group \
  --region eu-west-2 \
  --log-group-name /ecs/dstrmaysam-healthcare-knowledge-agent/backend

aws logs create-log-group \
  --region eu-west-2 \
  --log-group-name /ecs/dstrmaysam-healthcare-knowledge-agent/frontend
```

Recommended:

```bash
aws logs put-retention-policy \
  --region eu-west-2 \
  --log-group-name /ecs/dstrmaysam-healthcare-knowledge-agent/backend \
  --retention-in-days 30

aws logs put-retention-policy \
  --region eu-west-2 \
  --log-group-name /ecs/dstrmaysam-healthcare-knowledge-agent/frontend \
  --retention-in-days 30
```

## 10. ECS Fargate Task Definitions

Use these templates:

- `infra/ecs-backend-task-definition.json`
- `infra/ecs-frontend-task-definition.json`

### Backend Container

Container:

| Setting | Value |
|---|---|
| Container name | `backend` |
| Port | `8000` |
| CPU | `1024` |
| Memory | `2048` |
| Health check path | `/health` |

Environment variables:

```env
APP_ENV=dev
AWS_REGION=eu-west-2
SECRETS_STAGE=dev
APP_SECRET_NAME=/dstrmaysam-healthcare-knowledge-agent/dev/app
AZURE_OPENAI_SECRET_NAME=/dstrmaysam-healthcare-knowledge-agent/dev/azure-openai
LANGFUSE_SECRET_NAME=/dstrmaysam-healthcare-knowledge-agent/dev/langfuse
S3_BUCKET=dstrmaysam-healthcare-knowledge-agent-dev
S3_RAW_PREFIX=raw/
S3_MANIFEST_KEY=manifests/documents.json
OPENSEARCH_ENDPOINT=https://<collection-id>.eu-west-2.aoss.amazonaws.com
OPENSEARCH_INDEX=dstrmaysam-healthcare-knowledge-agent
DYNAMODB_CHAT_TABLE=dstrmaysam-healthcare-knowledge-agent-dev
CHAT_HISTORY_BACKEND=dynamodb
LOCAL_TEST_ADMIN_ENABLED=false
```

Optional if exposing the backend directly to browsers:

```env
CORS_ORIGINS=https://<frontend-url>
```

### Frontend Container

Container:

| Setting | Value |
|---|---|
| Container name | `frontend` |
| Port | `8501` |
| CPU | `512` |
| Memory | `1024` |
| Health check path | `/_stcore/health` |

Environment variables:

```env
BACKEND_URL=http://<backend-service-discovery-name>:8000
```

Use ECS Service Connect, AWS Cloud Map, or an internal ALB so the frontend can resolve the backend service name privately.

## 11. ECS Cluster And Services

Create one ECS cluster:

```text
dstrmaysam-healthcare-knowledge-agent
```

Create two Fargate services:

| Service | Task Definition | Desired Count | Network |
|---|---|---|---|
| `dstrmaysam-healthcare-knowledge-agent-backend` | backend task definition | `1` or more | private subnets |
| `dstrmaysam-healthcare-knowledge-agent-frontend` | frontend task definition | `1` or more | private subnets |

Recommended service setup:

- Enable ECS Service Connect or Cloud Map for backend discovery.
- Use rolling deployments.
- Enable deployment circuit breaker with rollback.
- Use the backend service discovery name in `BACKEND_URL`.
- Do not expose backend publicly unless you need direct API access.

## 12. Application Load Balancer

Create one public Application Load Balancer.

Recommended listener setup:

| Listener | Target |
|---|---|
| HTTPS `443` | Frontend target group on port `8501` |
| HTTP `80` | Redirect to HTTPS |

Frontend target group:

| Setting | Value |
|---|---|
| Target type | `ip` |
| Protocol | `HTTP` |
| Port | `8501` |
| Health path | `/_stcore/health` |

Optional backend target group, only if exposing API directly:

| Setting | Value |
|---|---|
| Target type | `ip` |
| Protocol | `HTTP` |
| Port | `8000` |
| Health path | `/health` |

If using a custom domain, create an ACM certificate in `eu-west-2`, attach it to the HTTPS listener, and point Route 53 or your DNS provider to the ALB.

## 13. Document Ingestion

After the backend has access to S3, OpenSearch, and Azure OpenAI:

1. Upload approved source documents to:

```text
s3://dstrmaysam-healthcare-knowledge-agent-dev/raw/
```

2. Run ingestion as a one-off ECS task using the backend image, backend task role, and the same backend environment variables.

Command override:

```bash
python -m app.ingest
```

The ingestion job:

- Reads files from `S3_BUCKET` and `S3_RAW_PREFIX`.
- Extracts text from supported documents.
- Creates Azure OpenAI embeddings.
- Writes chunks into the OpenSearch index.
- Writes the S3 manifest to `manifests/documents.json`.

## 14. RAGAS And Langfuse Evaluation

After the API is deployed and you can log in:

1. Get a bearer token by calling `/auth/login`.
2. Run the eval script from a machine with AWS credentials that can read the Langfuse secret.

Example:

```bash
python evals/run_ragas_eval.py \
  --api-url https://<backend-url> \
  --token <bearer-token> \
  --publish-langfuse \
  --aws-region eu-west-2 \
  --secrets-stage dev \
  --eval-run-name dstrmaysam-healthcare-knowledge-agent-ragas-eval
```

The eval script reads:

```text
/dstrmaysam-healthcare-knowledge-agent/dev/langfuse
```

It writes local JSON reports and publishes per-question plus summary scores to Langfuse.

## 15. Deployment Checklist

Before first AWS dev deploy:

- Create or confirm VPC, subnets, route tables, NAT or endpoints, and security groups.
- Create S3 bucket `dstrmaysam-healthcare-knowledge-agent-dev`.
- Create DynamoDB table `dstrmaysam-healthcare-knowledge-agent-dev`.
- Create Secrets Manager secrets under `/dstrmaysam-healthcare-knowledge-agent/dev/...`.
- Create OpenSearch Serverless vector collection and index.
- Add backend task role to OpenSearch Serverless data access policy.
- Create ECR repositories and push backend/frontend images.
- Create IAM execution role, backend task role, and frontend task role.
- Create CloudWatch log groups.
- Register ECS task definitions with replaced account, region, collection, and image values.
- Create ECS backend service in private subnets.
- Create ECS frontend service in private subnets.
- Configure frontend `BACKEND_URL` to use private backend service discovery.
- Create public ALB and route HTTPS traffic to frontend.
- Upload documents to S3 `raw/`.
- Run ingestion once.
- Log in through the frontend and test `/chat`.
- Run RAGAS evals and confirm Langfuse traces and scores appear.

## 16. Files To Use In This Repo

| File | Purpose |
|---|---|
| `.env.example` | Local non-secret config defaults |
| `infra/dynamodb-chat-history-table.json` | DynamoDB table definition |
| `infra/opensearch-index.json` | OpenSearch index mapping |
| `infra/iam-backend-task-policy.json` | Backend task role permissions |
| `infra/ecs-backend-task-definition.json` | Backend ECS task template |
| `infra/ecs-frontend-task-definition.json` | Frontend ECS task template |
| `README.md` | Secret model and local run commands |

## 17. AWS Dev Deployment Notes

- Keep secret values only in AWS Secrets Manager.
- Keep `LOCAL_TEST_ADMIN_ENABLED=false` in AWS dev.
- Rotate Azure OpenAI, Langfuse, and app secrets on a regular schedule.
- Use least-privilege IAM policies and scoped OpenSearch Serverless data access policies.
- Review uploaded healthcare documents before ingestion.
- Confirm the embedding dimension in `infra/opensearch-index.json` before indexing AWS dev documents.
- Keep backend tasks private unless direct API access is explicitly required.
