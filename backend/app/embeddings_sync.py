import time

import httpx

from app.config import settings

_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 3


def _embed_one(text: str) -> list[float]:
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(
                    f"{settings.ollama_base_url}/api/embeddings",
                    json={"model": settings.embed_model, "prompt": text},
                )
                resp.raise_for_status()
                return resp.json()["embedding"]
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            last_error = e
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(
        f"Could not reach Ollama ({settings.ollama_base_url}) for embeddings after {_MAX_RETRIES} attempts: {last_error}"
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    return [_embed_one(t) for t in texts]
