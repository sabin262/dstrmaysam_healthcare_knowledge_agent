from __future__ import annotations

from typing import Any


DEFAULT_VECTOR_DIMENSION = 1536


def healthcare_chunks_index_body(vector_dimension: int = DEFAULT_VECTOR_DIMENSION) -> dict[str, Any]:
    return {
        "settings": {
            "index.knn": True,
        },
        "mappings": {
            "properties": {
                "key": {"type": "keyword"},
                "title": {"type": "text"},
                "uri": {"type": "keyword"},
                "text": {"type": "text"},
                "content_type": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "checksum": {"type": "keyword"},
                "metadata": {
                    "properties": {
                        "key": {"type": "keyword"},
                        "checksum": {"type": "keyword"},
                        "owner": {"type": "keyword"},
                        "version": {"type": "keyword"},
                        "effective_date": {"type": "keyword"},
                        "review_date": {"type": "keyword"},
                        "approval_status": {"type": "keyword"},
                        "sensitivity": {"type": "keyword"},
                        "domain": {"type": "keyword"},
                        "document_type": {"type": "keyword"},
                        "allowed_roles": {"type": "keyword"},
                    }
                },
                "embedding": {
                    "type": "knn_vector",
                    "dimension": vector_dimension,
                    "method": {
                        "engine": "faiss",
                        "name": "hnsw",
                    },
                },
            }
        },
    }


def fallback_healthcare_chunks_index_body(vector_dimension: int = DEFAULT_VECTOR_DIMENSION) -> dict[str, Any]:
    return {
        "settings": {
            "index.knn": True,
        },
        "mappings": {
            "properties": {
                "embedding": {
                    "type": "knn_vector",
                    "dimension": vector_dimension,
                },
                "key": {"type": "keyword"},
                "title": {"type": "text"},
                "uri": {"type": "keyword"},
                "text": {"type": "text"},
                "content_type": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "checksum": {"type": "keyword"},
                "metadata": {"type": "object", "enabled": True},
            }
        },
    }


def ensure_opensearch_index(client: Any, index_name: str) -> bool:
    indices = getattr(client, "indices", None)
    if indices is None or not hasattr(indices, "exists") or not hasattr(indices, "create"):
        return False
    try:
        if indices.exists(index=index_name):
            return False
        try:
            indices.create(index=index_name, body=healthcare_chunks_index_body())
        except Exception:
            indices.create(index=index_name, body=fallback_healthcare_chunks_index_body())
        return True
    except Exception:
        return False
