from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

from app.config import settings

_client = QdrantClient(url=settings.qdrant_url)


def ensure_collection():
    existing = [c.name for c in _client.get_collections().collections]
    if settings.collection_name not in existing:
        _client.create_collection(
            collection_name=settings.collection_name,
            vectors_config=VectorParams(size=settings.embed_dim, distance=Distance.COSINE),
        )


def is_ready() -> bool:
    try:
        _client.get_collections()
        return True
    except Exception:
        return False


def upsert(chunk_ids: list[str], vectors: list[list[float]], payloads: list[dict]):
    points = [
        PointStruct(id=_stable_id(cid), vector=vec, payload={**payload, "chunk_id": cid})
        for cid, vec, payload in zip(chunk_ids, vectors, payloads)
    ]
    _client.upsert(collection_name=settings.collection_name, points=points)


def search(vector: list[float], top_k: int) -> list[dict]:
    results = _client.search(
        collection_name=settings.collection_name,
        query_vector=vector,
        limit=top_k,
    )
    return [
        {"chunk_id": r.payload.get("chunk_id"), "score": r.score, "payload": r.payload}
        for r in results
    ]


def delete_by_doc_id(doc_id: int):
    _client.delete(
        collection_name=settings.collection_name,
        points_selector=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        ),
    )


def _stable_id(chunk_id: str) -> int:
    # Qdrant point IDs must be int or UUID; derive a stable positive int from the chunk id string.
    import hashlib

    return int(hashlib.sha1(chunk_id.encode()).hexdigest()[:16], 16) % (2**63 - 1)
