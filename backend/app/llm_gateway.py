import asyncio
import json
import httpx

from app.config import settings

_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 3


async def _post_with_retry(url: str, payload: dict) -> dict:
    """POST to Ollama with a few retries — the first call to a model can be slow/flaky
    while Ollama loads it into memory, and this keeps the app resilient to that instead
    of surfacing a hard failure on the very first request."""
    last_error: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
        except httpx.HTTPStatusError as e:
            # Don't retry on 4xx (e.g. model not found) — surface immediately with detail
            raise RuntimeError(
                f"Ollama returned {e.response.status_code}: {e.response.text[:300]}"
            ) from e
    raise RuntimeError(f"Could not reach Ollama at {url} after {_MAX_RETRIES} attempts: {last_error}")


async def chat(model: str, messages: list[dict], temperature: float = 0.1, json_mode: bool = False) -> str:
    """Call Ollama's /api/chat endpoint (non-streaming) and return the text content."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if json_mode:
        payload["format"] = "json"

    data = await _post_with_retry(f"{settings.ollama_base_url}/api/chat", payload)
    return data.get("message", {}).get("content", "")


async def chat_json(model: str, messages: list[dict], temperature: float = 0.1, default: dict | None = None) -> dict:
    """Call chat() expecting a JSON object back; fall back gracefully if the model misbehaves."""
    raw = await chat(model, messages, temperature=temperature, json_mode=True)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Try to salvage a JSON object embedded in extra text
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        return default if default is not None else {}


async def embed(model: str, text: str) -> list[float]:
    data = await _post_with_retry(
        f"{settings.ollama_base_url}/api/embeddings",
        {"model": model, "prompt": text},
    )
    return data["embedding"]


async def is_reachable() -> bool:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{settings.ollama_base_url}/api/tags")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def embed_batch(model: str, texts: list[str]) -> list[list[float]]:
    # Ollama's embeddings endpoint is single-input; run sequentially to keep this simple/robust.
    vectors = []
    for t in texts:
        vectors.append(await embed(model, t))
    return vectors
