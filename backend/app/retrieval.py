from rank_bm25 import BM25Okapi

from app.config import settings
from app import db, embeddings, vectorstore


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


def _bm25_search(query: str, top_k: int) -> list[dict]:
    chunks = db.get_all_chunks()
    if not chunks:
        return []

    corpus = [_tokenize(c["text"]) for c in chunks]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(_tokenize(query))

    ranked = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)[:top_k]
    return [{"chunk_id": c["id"], "score": float(s), "chunk": c} for c, s in ranked if s > 0]


async def hybrid_search(query: str, top_k: int) -> list[dict]:
    query_vector = await embeddings.embed_query(query)
    vector_hits = vectorstore.search(query_vector, top_k=top_k)
    bm25_hits = _bm25_search(query, top_k=top_k)

    # Reciprocal Rank Fusion
    RRF_K = 60
    fused_scores: dict[str, float] = {}

    for rank, hit in enumerate(vector_hits):
        cid = hit["chunk_id"]
        fused_scores[cid] = fused_scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)

    for rank, hit in enumerate(bm25_hits):
        cid = hit["chunk_id"]
        fused_scores[cid] = fused_scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)

    if not fused_scores:
        return []

    top_ids = sorted(fused_scores.keys(), key=lambda k: fused_scores[k], reverse=True)[:top_k]
    chunk_map = db.get_chunks_by_ids(top_ids)

    results = []
    for cid in top_ids:
        c = chunk_map.get(cid)
        if not c:
            continue
        results.append(
            {
                "chunk_id": cid,
                "doc_name": c["doc_name"],
                "chunk_index": c["chunk_index"],
                "text": c["text"],
                "source_page": c["source_page"],
                "fused_score": fused_scores[cid],
            }
        )
    return results
