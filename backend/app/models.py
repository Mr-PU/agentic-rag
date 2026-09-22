from typing import Any, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    max_iterations: Optional[int] = None


class Citation(BaseModel):
    doc_name: str
    chunk_index: int
    snippet: str


class TraceStep(BaseModel):
    step: str
    detail: Any = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    iterations: int = 0


class DocumentInfo(BaseModel):
    id: int
    filename: str
    status: str
    error: Optional[str] = None
    num_chunks: int
    uploaded_at: str


class IngestResponse(BaseModel):
    doc_id: int
    filename: str
    status: str
