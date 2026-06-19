# AWS Dev Deployment Notes

Use the JSON templates in this folder as starting points for ECS Fargate task definitions and IAM policies.

Required AWS resources:

- S3 bucket for raw documents and document manifests
- DynamoDB table for chat history
- OpenSearch Serverless vector collection and index
- ECR repository for backend image
- ECR repository for frontend image
- ECS cluster with two Fargate services
- Application Load Balancer with routes for Streamlit and FastAPI
- CloudWatch log groups
- Secrets Manager secrets:
- `/dstrmaysam-healthcare-knowledge-agent/dev/app`
- `/dstrmaysam-healthcare-knowledge-agent/dev/azure-openai`
- `/dstrmaysam-healthcare-knowledge-agent/dev/langfuse`

The ECS task execution role pulls images and writes logs. The ECS task role reads only the required secret ARNs and application resources.

Use `dynamodb-chat-history-table.json` as the DynamoDB table shape. Use `opensearch-index.json` as the expected OpenSearch index mapping; adjust `embedding.dimension` if your Azure embedding deployment uses a different vector dimension.
