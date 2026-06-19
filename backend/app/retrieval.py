from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .aws import boto3_session
from .config import AppSettings
from .retries import retry_transient
from .secrets import SecretProvider


@dataclass
class RetrievalHit:
    title: str
    uri: str
    text: str
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RetrievalService:
    def __init__(self, settings: AppSettings, secret_provider: SecretProvider):
        self.settings = settings
        self.secret_provider = secret_provider
        self._opensearch: Any | None = None
        self._embedding_model: Any | None = None

    @retry_transient
    def search(
        self,
        query: str,
        top_k: int = 5,
        document_keys: Sequence[str] | None = None,
    ) -> list[RetrievalHit]:
        if not self.settings.opensearch_endpoint:
            return []
        client = self._get_opensearch_client()
        vector = self._embed_query(query)
        filtered_keys = list(dict.fromkeys(key for key in (document_keys or []) if key))
        key_filter = {"terms": {"key": filtered_keys}} if filtered_keys else None
        body: dict[str, Any]
        if vector:
            knn_field: dict[str, Any] = {
                "vector": vector,
                "k": top_k,
            }
            if key_filter:
                knn_field["filter"] = key_filter
            body = {
                "size": top_k,
                "query": {
                    "knn": {
                        "embedding": knn_field
                    }
                },
            }
        else:
            keyword_query = {
                "multi_match": {
                    "query": query,
                    "fields": ["text^2", "title", "metadata.*"],
                }
            }
            query_body = (
                {"bool": {"must": [keyword_query], "filter": [key_filter]}}
                if key_filter
                else keyword_query
            )
            body = {
                "size": top_k,
                "query": query_body,
            }

        response = client.search(index=self.settings.opensearch_index, body=body)
        hits: list[RetrievalHit] = []
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            hits.append(
                RetrievalHit(
                    title=str(source.get("title") or source.get("key") or "Untitled"),
                    uri=str(source.get("uri") or source.get("source") or ""),
                    text=str(source.get("text") or ""),
                    score=float(hit.get("_score")) if hit.get("_score") is not None else None,
                    metadata=dict(source.get("metadata", {})),
                )
            )
        return hits

    def _get_opensearch_client(self) -> Any:
        if self._opensearch is not None:
            return self._opensearch
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from opensearchpy import AWSV4SignerAuth

        credentials = boto3_session(self.settings).get_credentials()
        auth = AWSV4SignerAuth(credentials, self.settings.aws_region, "aoss")
        host = self.settings.opensearch_endpoint.replace("https://", "").replace("http://", "")
        self._opensearch = OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )
        return self._opensearch

    def _embed_query(self, query: str) -> list[float] | None:
        try:
            if self._embedding_model is None:
                from langchain_openai import AzureOpenAIEmbeddings

                secrets = self.secret_provider.load_azure_openai()
                self._embedding_model = AzureOpenAIEmbeddings(
                    azure_endpoint=secrets.endpoint,
                    api_key=secrets.api_key,
                    api_version=secrets.api_version,
                    azure_deployment=secrets.embedding_deployment,
                )
            return list(self._embedding_model.embed_query(query))
        except Exception:
            return None
