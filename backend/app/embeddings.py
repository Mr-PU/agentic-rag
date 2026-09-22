from app.config import settings
from app import llm_gateway


async def embed_texts(texts: list[str]) -> list[list[float]]:
    return await llm_gateway.embed_batch(settings.embed_model, texts)


async def embed_query(text: str) -> list[float]:
    return await llm_gateway.embed(settings.embed_model, text)
