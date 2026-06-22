from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from .config import AppSettings
from .ingest import checksum_bytes, chunk_text, parse_document
from .retrieval import RetrievalHit, RetrievalService
from .secrets import SecretProvider


SUPPORTED_LOCAL_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")


def _local_uri(key: str) -> str:
    return f"local://{key}"


def _metadata_json(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, sort_keys=True)


def _flatten_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
    flat: dict[str, str | int | float | bool] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)):
            flat[key] = value
        elif value is not None:
            flat[key] = json.dumps(value, sort_keys=True)
    return flat


def _restore_metadata(flat: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    raw = flat.get("metadata_json")
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                metadata.update(loaded)
        except json.JSONDecodeError:
            pass
    metadata.setdefault("_key", flat.get("key"))
    metadata.setdefault("_chunk_index", flat.get("chunk_index"))
    metadata.setdefault("_content_type", flat.get("content_type"))
    metadata.setdefault("_checksum", flat.get("checksum"))
    return metadata


class LocalChromaEmbeddingMixin:
    settings: AppSettings
    secret_provider: SecretProvider
    _embeddings: Any | None

    def _embed(self, text: str) -> list[float] | None:
        try:
            if self._embeddings is None:
                from langchain_openai import AzureOpenAIEmbeddings

                secrets = self.secret_provider.load_azure_openai()
                self._embeddings = AzureOpenAIEmbeddings(
                    azure_endpoint=secrets.endpoint,
                    api_key=secrets.api_key,
                    api_version=secrets.api_version,
                    azure_deployment=secrets.embedding_deployment,
                )
            return list(self._embeddings.embed_query(text))
        except Exception:
            return None


class LocalChromaCollectionMixin:
    settings: AppSettings
    _collection: Any | None

    def _get_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        import chromadb

        Path(self.settings.chroma_persist_dir).mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=self.settings.chroma_persist_dir)
        self._collection = client.get_or_create_collection(self.settings.chroma_collection)
        return self._collection


class LocalChromaIngestionJob(LocalChromaEmbeddingMixin, LocalChromaCollectionMixin):
    def __init__(self, settings: AppSettings, secret_provider: SecretProvider):
        self.settings = settings
        self.secret_provider = secret_provider
        self.local_data_dir = Path(settings.local_data_dir)
        self._embeddings: Any | None = None
        self._collection: Any | None = None

    def run(self) -> dict[str, Any]:
        existing_manifest = self._load_manifest()
        previous_backend = existing_manifest.get("vector_backend")
        previous_collection = existing_manifest.get("chroma_collection")
        force_reindex = previous_backend != "chroma" or previous_collection != self.settings.chroma_collection
        existing_by_key = {
            str(document.get("key", "")): document
            for document in existing_manifest.get("documents", [])
            if isinstance(document, dict) and document.get("key")
        }

        raw_documents = self._load_raw_documents()
        seen_keys: set[str] = set()
        manifest_documents: list[dict[str, Any]] = []
        indexed_chunks = 0
        indexed_documents = 0
        skipped_documents = 0
        deleted_documents = 0
        deleted_chunks = 0

        for raw_document in raw_documents:
            key = raw_document["key"]
            body = raw_document["body"]
            seen_keys.add(key)
            checksum = checksum_bytes(body)
            existing_document = existing_by_key.get(key)
            if existing_document and existing_document.get("checksum") == checksum and not force_reindex:
                skipped_documents += 1
                unchanged = dict(existing_document)
                unchanged["ingestion_status"] = "skipped_unchanged"
                manifest_documents.append(unchanged)
                continue

            if existing_document:
                deleted_chunks += self._delete_document_chunks(key)

            document = parse_document(key, body)
            chunks = chunk_text(
                document.text,
                chunk_size=self.settings.ingestion_chunk_size,
                chunk_overlap=self.settings.ingestion_chunk_overlap,
            )
            self._index_chunks(document.key, document.title, document.content_type, document.checksum, document.metadata, chunks)
            indexed_chunks += len(chunks)
            indexed_documents += 1
            manifest_documents.append(
                {
                    "key": document.key,
                    "title": document.title,
                    "content_type": document.content_type,
                    "checksum": document.checksum,
                    "metadata": document.metadata,
                    "chunk_count": len(chunks),
                    "ingestion_status": "indexed",
                }
            )

        if not force_reindex:
            for key in sorted(set(existing_by_key) - seen_keys):
                deleted_chunks += self._delete_document_chunks(key)
                deleted_documents += 1

        total_chunks = sum(int(document.get("chunk_count") or 0) for document in manifest_documents)
        manifest = {
            "vector_backend": "chroma",
            "chroma_collection": self.settings.chroma_collection,
            "previous_vector_backend": previous_backend,
            "previous_chroma_collection": previous_collection,
            "force_reindex": force_reindex,
            "documents": manifest_documents,
            "indexed_chunks": indexed_chunks,
            "total_chunks": total_chunks,
            "indexed_documents": indexed_documents,
            "skipped_documents": skipped_documents,
            "deleted_documents": deleted_documents,
            "deleted_chunks": deleted_chunks,
        }
        self._write_manifest(manifest)
        return manifest

    def _index_chunks(
        self,
        key: str,
        title: str,
        content_type: str,
        checksum: str,
        metadata: dict[str, Any],
        chunks: list[str],
    ) -> None:
        collection = self._get_collection()
        ids: list[str] = []
        documents: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict[str, Any]] = []
        for chunk_index, chunk in enumerate(chunks):
            embedding = self._embed(chunk)
            if embedding is None:
                continue
            ids.append(f"{key}:{checksum}:{chunk_index}")
            documents.append(chunk)
            embeddings.append(embedding)
            chunk_metadata = {
                **_flatten_metadata(metadata),
                "metadata_json": _metadata_json(metadata),
                "key": key,
                "title": title,
                "uri": _local_uri(key),
                "chunk_index": chunk_index,
                "checksum": checksum,
                "content_type": content_type,
            }
            metadatas.append(chunk_metadata)
        if ids:
            collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

    def _delete_document_chunks(self, key: str) -> int:
        collection = self._get_collection()
        try:
            existing = collection.get(where={"key": key})
            ids = list(existing.get("ids", []))
            if ids:
                collection.delete(ids=ids)
            return len(ids)
        except Exception:
            return 0

    def _load_raw_documents(self) -> list[dict[str, Any]]:
        raw_root = self._path_for_key(self.settings.s3_raw_prefix.strip("/") or "raw")
        if not raw_root.exists():
            return []
        documents: list[dict[str, Any]] = []
        for path in sorted(raw_root.rglob("*")):
            if not path.is_file() or not path.name.lower().endswith(SUPPORTED_LOCAL_EXTENSIONS):
                continue
            key = path.relative_to(self.local_data_dir).as_posix()
            documents.append({"key": key, "body": path.read_bytes()})
        return documents

    def _load_manifest(self) -> dict[str, Any]:
        path = self._path_for_key(self.settings.s3_manifest_key)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            return manifest if isinstance(manifest, dict) else {"documents": []}
        except Exception:
            return {"documents": []}

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        path = self._path_for_key(self.settings.s3_manifest_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _path_for_key(self, key: str) -> Path:
        safe_key = key.replace("\\", "/").lstrip("/")
        path = (self.local_data_dir / safe_key).resolve()
        root = self.local_data_dir.resolve()
        if root != path and root not in path.parents:
            raise ValueError("Local path escapes LOCAL_DATA_DIR")
        return path


class LocalChromaRetrievalService(LocalChromaEmbeddingMixin, LocalChromaCollectionMixin, RetrievalService):
    def __init__(self, settings: AppSettings, secret_provider: SecretProvider):
        self.settings = settings
        self.secret_provider = secret_provider
        self.local_data_dir = Path(settings.local_data_dir)
        self._embeddings: Any | None = None
        self._collection: Any | None = None
        self.last_timing_ms: dict[str, int] = {}

    def search(
        self,
        query: str,
        top_k: int | None = None,
        document_keys: Sequence[str] | None = None,
    ) -> list[RetrievalHit]:
        import time

        started = time.perf_counter()
        result_limit = top_k or self.settings.rag_top_k
        collection = self._get_collection()
        filtered_keys = list(dict.fromkeys(key for key in (document_keys or []) if key))
        vector = self._embed(query)
        if vector is not None:
            query_kwargs: dict[str, Any] = {
                "query_embeddings": [vector],
                "n_results": result_limit,
                "include": ["documents", "metadatas", "distances"],
            }
            if filtered_keys:
                query_kwargs["where"] = {"key": {"$in": filtered_keys}}
            results = collection.query(**query_kwargs)
            hits = self._hits_from_query_results(results)
        else:
            hits = self._keyword_hits(collection, query, filtered_keys, result_limit)

        if not hits:
            hits = self._raw_file_keyword_hits(query, filtered_keys, result_limit)

        neighbor_hits = self._fetch_neighbor_hits(collection, hits)
        if neighbor_hits:
            for hit in neighbor_hits:
                hit.metadata.setdefault("_retrieval_strategy", "neighbor")
            hits = self._merge_hits(hits + neighbor_hits)
        self.last_timing_ms = {
            "embedding_ms": 0 if vector is None else 1,
            "opensearch_ms": 0,
            "neighbor_ms": 0,
            "vector_hits": len(hits) if vector is not None else 0,
            "keyword_hits": len(hits) if vector is None else 0,
            "neighbor_hits": len(neighbor_hits),
            "returned_hits": len(hits),
            "total_ms": int((time.perf_counter() - started) * 1000),
        }
        return hits

    def _raw_file_keyword_hits(
        self,
        query: str,
        document_keys: list[str],
        limit: int,
    ) -> list[RetrievalHit]:
        manifest = self._load_manifest()
        wanted_keys = set(document_keys)
        terms = [
            term.lower().strip(".,?!:;()[]{}")
            for term in query.split()
            if len(term.strip(".,?!:;()[]{}")) >= 3
        ]
        hits: list[RetrievalHit] = []
        for record in manifest.get("documents", []):
            if not isinstance(record, dict):
                continue
            key = str(record.get("key") or "")
            if not key or (wanted_keys and key not in wanted_keys):
                continue
            path = self._path_for_key(key)
            if not path.exists() or not path.is_file():
                continue
            try:
                document = parse_document(key, path.read_bytes())
            except Exception:
                continue
            chunks = chunk_text(
                document.text,
                chunk_size=self.settings.ingestion_chunk_size,
                chunk_overlap=self.settings.ingestion_chunk_overlap,
            )
            metadata = dict(record.get("metadata") or document.metadata)
            base_haystack = " ".join(
                [
                    str(record.get("title") or document.title),
                    key,
                    json.dumps(metadata, sort_keys=True),
                ]
            ).lower()
            for chunk_index, chunk in enumerate(chunks):
                haystack = f"{base_haystack}\n{chunk}".lower()
                score = sum(1 for term in terms if term and term in haystack)
                if not terms or score:
                    chunk_metadata = {
                        **metadata,
                        "_key": key,
                        "_chunk_index": chunk_index,
                        "_content_type": document.content_type,
                        "_checksum": document.checksum,
                        "_retrieval_strategy": "raw_keyword",
                    }
                    hits.append(
                        RetrievalHit(
                            title=str(record.get("title") or document.title),
                            uri=_local_uri(key),
                            text=chunk,
                            score=float(score),
                            metadata=chunk_metadata,
                        )
                    )
        hits.sort(key=lambda hit: hit.score or 0, reverse=True)
        return hits[:limit]

    def _load_manifest(self) -> dict[str, Any]:
        path = self._path_for_key(self.settings.s3_manifest_key)
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            return manifest if isinstance(manifest, dict) else {"documents": []}
        except Exception:
            return {"documents": []}

    def _path_for_key(self, key: str) -> Path:
        safe_key = key.replace("\\", "/").lstrip("/")
        path = (self.local_data_dir / safe_key).resolve()
        root = self.local_data_dir.resolve()
        if root != path and root not in path.parents:
            raise ValueError("Local path escapes LOCAL_DATA_DIR")
        return path

    def _hits_from_query_results(self, results: dict[str, Any]) -> list[RetrievalHit]:
        documents = (results.get("documents") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]
        hits: list[RetrievalHit] = []
        for index, text in enumerate(documents):
            metadata = dict(metadatas[index] or {})
            score = None
            if index < len(distances) and distances[index] is not None:
                score = 1.0 / (1.0 + float(distances[index]))
            hits.append(self._hit_from_chroma(str(text), metadata, score, "vector"))
        return hits

    def _keyword_hits(
        self,
        collection: Any,
        query: str,
        document_keys: list[str],
        limit: int,
    ) -> list[RetrievalHit]:
        existing = (
            collection.get(where={"key": {"$in": document_keys}})
            if document_keys
            else collection.get()
        )
        terms = [term.lower().strip(".,?!:;()[]{}") for term in query.split() if len(term) >= 3]
        hits: list[RetrievalHit] = []
        for text, metadata in zip(existing.get("documents", []), existing.get("metadatas", [])):
            haystack = " ".join([str(text), json.dumps(metadata or {})]).lower()
            score = sum(1 for term in terms if term and term in haystack)
            if score:
                hits.append(self._hit_from_chroma(str(text), dict(metadata or {}), float(score), "keyword"))
        hits.sort(key=lambda hit: hit.score or 0, reverse=True)
        return hits[:limit]

    def _fetch_neighbor_hits(self, collection: Any, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        neighbor_count = max(0, self.settings.rag_neighbor_chunks)
        if not neighbor_count:
            return []
        neighbor_hits: list[RetrievalHit] = []
        for hit in hits:
            key = str(hit.metadata.get("_key") or "")
            chunk_index = hit.metadata.get("_chunk_index")
            if not key or chunk_index is None:
                continue
            try:
                index = int(chunk_index)
            except (TypeError, ValueError):
                continue
            wanted = [item for item in range(index - neighbor_count, index + neighbor_count + 1) if item >= 0 and item != index]
            existing = collection.get(where={"key": key})
            for text, metadata in zip(existing.get("documents", []), existing.get("metadatas", [])):
                if int((metadata or {}).get("chunk_index", -1)) in wanted:
                    neighbor_hits.append(self._hit_from_chroma(str(text), dict(metadata or {}), None, "neighbor"))
        return self._merge_hits(neighbor_hits)

    def _hit_from_chroma(
        self,
        text: str,
        flat_metadata: dict[str, Any],
        score: float | None,
        strategy: str,
    ) -> RetrievalHit:
        metadata = _restore_metadata(flat_metadata)
        metadata["_retrieval_strategy"] = strategy
        return RetrievalHit(
            title=str(flat_metadata.get("title") or flat_metadata.get("key") or "Untitled"),
            uri=str(flat_metadata.get("uri") or _local_uri(str(flat_metadata.get("key") or ""))),
            text=text,
            score=score,
            metadata=metadata,
        )
