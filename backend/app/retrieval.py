from __future__ import annotations

import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
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
        self._embedding_deployment_name: str = ""
        self._embedding_cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._search_cache: dict[tuple[Any, ...], tuple[float, list[RetrievalHit], dict[str, int]]] = {}
        self.last_timing_ms: dict[str, int] = {}

    @retry_transient
    def search(
        self,
        query: str,
        top_k: int | None = None,
        document_keys: Sequence[str] | None = None,
    ) -> list[RetrievalHit]:
        started = time.perf_counter()
        timing: dict[str, int] = {}
        result_limit = top_k or self.settings.rag_top_k
        filtered_keys = list(dict.fromkeys(key for key in (document_keys or []) if key))
        cache_key = self._search_cache_key(query, result_limit, filtered_keys)
        cached = self._search_cache_get(cache_key)
        if cached is not None:
            hits, cached_timing = cached
            self.last_timing_ms = {**cached_timing, "cache_hit": 1}
            return list(hits)
        if not self.settings.opensearch_endpoint:
            self.last_timing_ms = {"total_ms": int((time.perf_counter() - started) * 1000)}
            return []
        client = self._get_opensearch_client()
        embedding_started = time.perf_counter()
        vector = self._embed_query(query)
        timing["embedding_ms"] = int((time.perf_counter() - embedding_started) * 1000)
        key_filter = {"terms": {"key": filtered_keys}} if filtered_keys else None
        bodies: list[tuple[str, dict[str, Any]]] = []
        if vector:
            knn_field: dict[str, Any] = {
                "vector": vector,
                "k": result_limit,
            }
            if key_filter:
                knn_field["filter"] = key_filter
            bodies.append(
                (
                    "vector",
                    {
                        "size": result_limit,
                        "query": {
                            "knn": {
                                "embedding": knn_field
                            }
                        },
                    },
                )
            )
        keyword_query = {
            "multi_match": {
                "query": query,
                "fields": [
                    "text^2",
                    "title^3",
                    "key^3",
                    "metadata.*",
                ],
            }
        }
        query_body = (
            {"bool": {"must": [keyword_query], "filter": [key_filter]}}
            if key_filter
            else keyword_query
        )
        bodies.append(
            (
                "keyword",
                {
                    "size": result_limit,
                    "query": query_body,
                },
            )
        )

        search_started = time.perf_counter()
        hits: list[RetrievalHit] = []
        search_counts: dict[str, int] = {}
        for search_type, response in self._run_search_bodies(client, bodies):
            typed_hits = self._hits_from_response(response)
            for hit in typed_hits:
                hit.metadata.setdefault("_retrieval_strategy", search_type)
            search_counts[search_type] = len(typed_hits)
            hits.extend(typed_hits)
        timing["opensearch_ms"] = int((time.perf_counter() - search_started) * 1000)
        merged_hits = self._merge_hits(hits)[:result_limit]
        neighbor_started = time.perf_counter()
        neighbor_hits = self._fetch_neighbor_hits(client, merged_hits)
        timing["neighbor_ms"] = int((time.perf_counter() - neighbor_started) * 1000)
        if neighbor_hits:
            for hit in neighbor_hits:
                hit.metadata.setdefault("_retrieval_strategy", "neighbor")
            merged_hits = self._merge_hits(merged_hits + neighbor_hits)
        timing["vector_hits"] = search_counts.get("vector", 0)
        timing["keyword_hits"] = search_counts.get("keyword", 0)
        timing["neighbor_hits"] = len(neighbor_hits)
        timing["returned_hits"] = len(merged_hits)
        timing["cache_hit"] = 0
        timing["total_ms"] = int((time.perf_counter() - started) * 1000)
        self.last_timing_ms = timing
        self._search_cache_set(cache_key, merged_hits, timing)
        return merged_hits

    def _run_search_bodies(
        self,
        client: Any,
        bodies: list[tuple[str, dict[str, Any]]],
    ) -> list[tuple[str, dict[str, Any]]]:
        if len(bodies) < 2 or not self.settings.rag_parallel_search_enabled:
            return [
                (search_type, client.search(index=self.settings.opensearch_index, body=body))
                for search_type, body in bodies
            ]
        if hasattr(client, "msearch"):
            try:
                request_body: list[dict[str, Any]] = []
                for _, body in bodies:
                    request_body.append({"index": self.settings.opensearch_index})
                    request_body.append(body)
                response = client.msearch(body=request_body)
                responses = list(response.get("responses", []))
                if len(responses) == len(bodies):
                    return [
                        (search_type, responses[index])
                        for index, (search_type, _) in enumerate(bodies)
                    ]
            except Exception:
                pass

        def run_one(item: tuple[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
            search_type, body = item
            response = client.search(index=self.settings.opensearch_index, body=body)
            return search_type, response

        with ThreadPoolExecutor(max_workers=len(bodies)) as executor:
            futures = [executor.submit(run_one, item) for item in bodies]
            return [future.result() for future in futures]

    def _hits_from_response(self, response: dict[str, Any]) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            metadata = dict(source.get("metadata", {}))
            metadata.setdefault("_key", source.get("key"))
            metadata.setdefault("_chunk_index", source.get("chunk_index"))
            metadata.setdefault("_content_type", source.get("content_type"))
            metadata.setdefault("_checksum", source.get("checksum"))
            hits.append(
                RetrievalHit(
                    title=str(source.get("title") or source.get("key") or "Untitled"),
                    uri=str(source.get("uri") or source.get("source") or ""),
                    text=str(source.get("text") or ""),
                    score=float(hit.get("_score")) if hit.get("_score") is not None else None,
                    metadata=metadata,
                )
            )
        return hits

    def _merge_hits(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        seen: set[tuple[str, str, str]] = set()
        merged: list[RetrievalHit] = []
        for hit in hits:
            key = str(hit.metadata.get("_key") or hit.uri)
            raw_chunk_index = hit.metadata.get("_chunk_index")
            chunk_index = "" if raw_chunk_index is None else str(raw_chunk_index)
            identity = (key, chunk_index, hit.text[:80])
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(hit)
        return merged

    def _fetch_neighbor_hits(self, client: Any, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        neighbor_count = max(0, self.settings.rag_neighbor_chunks)
        if not neighbor_count:
            return []
        requests: dict[str, set[int]] = {}
        existing: set[tuple[str, int]] = set()
        for hit in hits:
            key = str(hit.metadata.get("_key") or "")
            chunk_index = hit.metadata.get("_chunk_index")
            if not key or chunk_index is None:
                continue
            try:
                index = int(chunk_index)
            except (TypeError, ValueError):
                continue
            existing.add((key, index))
            for neighbor in range(index - neighbor_count, index + neighbor_count + 1):
                if neighbor < 0 or neighbor == index:
                    continue
                requests.setdefault(key, set()).add(neighbor)

        clauses: list[dict[str, Any]] = []
        total_wanted = 0
        for key, indexes in requests.items():
            wanted = sorted(index for index in indexes if (key, index) not in existing)
            if not wanted:
                continue
            total_wanted += len(wanted)
            clauses.append(
                {
                    "bool": {
                        "filter": [
                            {"term": {"key": key}},
                            {"terms": {"chunk_index": wanted}},
                        ]
                    }
                }
            )
        if not clauses:
            return []
        response = client.search(
            index=self.settings.opensearch_index,
            body={
                "size": total_wanted,
                "query": {
                    "bool": {
                        "should": clauses,
                        "minimum_should_match": 1,
                    }
                },
            },
        )
        return self._hits_from_response(response)

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
                self._embedding_deployment_name = secrets.embedding_deployment
                self._embedding_model = AzureOpenAIEmbeddings(
                    azure_endpoint=secrets.endpoint,
                    api_key=secrets.api_key,
                    api_version=secrets.api_version,
                    azure_deployment=secrets.embedding_deployment,
                )
            cache_key = (self._normalize_query(query), self._embedding_deployment_name)
            if self.settings.rag_embedding_cache_size > 0 and cache_key in self._embedding_cache:
                vector = self._embedding_cache.pop(cache_key)
                self._embedding_cache[cache_key] = vector
                return list(vector)
            vector = list(self._embedding_model.embed_query(query))
            if self.settings.rag_embedding_cache_size > 0:
                self._embedding_cache[cache_key] = vector
                while len(self._embedding_cache) > self.settings.rag_embedding_cache_size:
                    self._embedding_cache.popitem(last=False)
            return vector
        except Exception:
            return None

    def _search_cache_key(self, query: str, top_k: int, document_keys: list[str]) -> tuple[Any, ...]:
        return (
            self._normalize_query(query),
            tuple(document_keys),
            self.settings.opensearch_index,
            top_k,
            self.settings.rag_neighbor_chunks,
        )

    def _search_cache_get(
        self,
        key: tuple[Any, ...],
    ) -> tuple[list[RetrievalHit], dict[str, int]] | None:
        ttl = max(0, self.settings.rag_query_cache_ttl_seconds)
        if not ttl:
            return None
        cached = self._search_cache.get(key)
        if cached is None:
            return None
        expires_at, hits, timing = cached
        if time.monotonic() >= expires_at:
            self._search_cache.pop(key, None)
            return None
        return list(hits), dict(timing)

    def _search_cache_set(self, key: tuple[Any, ...], hits: list[RetrievalHit], timing: dict[str, int]) -> None:
        ttl = max(0, self.settings.rag_query_cache_ttl_seconds)
        if not ttl:
            return
        self._search_cache[key] = (time.monotonic() + ttl, list(hits), dict(timing))

    def invalidate_cache(self) -> None:
        self._search_cache.clear()
        self._embedding_cache.clear()

    def delete_all_indexes(self) -> int:
        if not self.settings.opensearch_endpoint:
            self.invalidate_cache()
            return 0
        client = self._get_opensearch_client()
        try:
            response = client.delete_by_query(
                index=self.settings.opensearch_index,
                body={"query": {"match_all": {}}},
                refresh=True,
                conflicts="proceed",
            )
            deleted = int(response.get("deleted") or 0)
        except Exception:
            deleted = 0
        self.invalidate_cache()
        return deleted

    def _normalize_query(self, query: str) -> str:
        return " ".join(query.lower().split())
