from app.config import settings
from app import llm_gateway, retrieval
from app.models import ChatResponse, Citation, TraceStep

PLANNER_PROMPT = """You are a search planner for a retrieval-augmented QA system.
Given the user's question (and, if provided, notes on what evidence is still missing),
output a JSON object with a single key "queries": a list of 1 to 3 short search
queries that would help find the evidence needed to answer the question.

Respond with ONLY the JSON object, e.g.:
{"queries": ["example search query one", "example search query two"]}
"""

CRITIC_PROMPT = """You are a critical evaluator for a RAG system. You are given the user's
question and a set of retrieved evidence snippets (numbered). Decide whether the evidence
is sufficient to answer the question accurately.

Respond with ONLY a JSON object of this shape:
{"sufficient": true/false, "missing_info": "short description of what's missing, or empty string", "refined_query": "a better search query to fill the gap, or empty string"}
"""

GENERATOR_PROMPT = """You are a careful assistant that answers questions using ONLY the
provided evidence snippets (numbered). Cite evidence inline using square brackets with the
snippet number, e.g. [1], [2]. If the evidence does not contain the answer, say so honestly
instead of guessing.

Respond with ONLY a JSON object of this shape:
{"answer": "your answer text with [n] citations", "used_snippets": [1, 2]}
"""


def _format_evidence(evidence: list[dict]) -> str:
    lines = []
    for i, e in enumerate(evidence, start=1):
        page = f", page {e['source_page']}" if e.get("source_page") else ""
        lines.append(f"[{i}] (source: {e['doc_name']}{page})\n{e['text']}")
    return "\n\n".join(lines)


async def run_agent(query: str, top_k: int | None = None, max_iterations: int | None = None) -> ChatResponse:
    top_k = top_k or settings.top_k
    max_iterations = max_iterations or settings.max_iterations

    trace: list[TraceStep] = []
    evidence_by_chunk: dict[str, dict] = {}
    missing_info = ""
    iterations = 0

    for iteration in range(1, max_iterations + 1):
        iterations = iteration

        # 1. Planner: turn the question (+ any gap notes) into search queries
        planner_user_msg = query if not missing_info else f"{query}\n\nStill missing: {missing_info}"
        plan = await llm_gateway.chat_json(
            settings.router_model,
            [
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": planner_user_msg},
            ],
            default={"queries": [query]},
        )
        search_queries = plan.get("queries") or [query]
        trace.append(TraceStep(step="planner", detail={"iteration": iteration, "queries": search_queries}))

        # 2. Retrieve evidence for each search query, dedup by chunk id
        for sq in search_queries:
            hits = await retrieval.hybrid_search(sq, top_k=top_k)
            for h in hits:
                evidence_by_chunk[h["chunk_id"]] = h
        trace.append(
            TraceStep(
                step="retrieval",
                detail={"iteration": iteration, "num_evidence_so_far": len(evidence_by_chunk)},
            )
        )

        if not evidence_by_chunk:
            break

        evidence = list(evidence_by_chunk.values())

        # 3. Critic: is this evidence enough?
        critic_result = await llm_gateway.chat_json(
            settings.critic_model,
            [
                {"role": "system", "content": CRITIC_PROMPT},
                {
                    "role": "user",
                    "content": f"Question: {query}\n\nEvidence:\n{_format_evidence(evidence)}",
                },
            ],
            default={"sufficient": True, "missing_info": "", "refined_query": ""},
        )
        trace.append(TraceStep(step="critic", detail={"iteration": iteration, **critic_result}))

        if critic_result.get("sufficient", True):
            break

        missing_info = critic_result.get("missing_info", "") or critic_result.get("refined_query", "")
        if not missing_info:
            break  # critic flagged insufficient but gave no direction; stop looping

    evidence = list(evidence_by_chunk.values())

    if not evidence:
        answer = "I couldn't find any relevant information in the ingested documents to answer that."
        trace.append(TraceStep(step="generator", detail={"note": "no evidence available"}))
        return ChatResponse(answer=answer, citations=[], trace=trace, iterations=iterations)

    # 4. Generator: produce the final answer, grounded in evidence
    gen_result = await llm_gateway.chat_json(
        settings.generator_model,
        [
            {"role": "system", "content": GENERATOR_PROMPT},
            {
                "role": "user",
                "content": f"Question: {query}\n\nEvidence:\n{_format_evidence(evidence)}",
            },
        ],
        default=None,
    )

    answer = gen_result.get("answer") if gen_result else None
    if not answer:
        # Fallback: plain (non-JSON) generation if the model didn't cooperate with JSON mode
        answer = await llm_gateway.chat(
            settings.generator_model,
            [
                {
                    "role": "system",
                    "content": "Answer the question using only the provided evidence, citing snippet numbers like [1].",
                },
                {
                    "role": "user",
                    "content": f"Question: {query}\n\nEvidence:\n{_format_evidence(evidence)}",
                },
            ],
        )

    used_indices = gen_result.get("used_snippets", []) if gen_result else []
    citations = []
    for i, e in enumerate(evidence, start=1):
        if not used_indices or i in used_indices:
            citations.append(
                Citation(
                    doc_name=e["doc_name"],
                    chunk_index=e["chunk_index"],
                    snippet=e["text"][:300],
                )
            )

    trace.append(TraceStep(step="generator", detail={"used_snippets": used_indices or "all"}))

    return ChatResponse(answer=answer, citations=citations, trace=trace, iterations=iterations)
