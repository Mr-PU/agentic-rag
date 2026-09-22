import datetime
from contextlib import contextmanager

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

Base = declarative_base()
engine = create_engine(settings.postgres_dsn, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String, nullable=False)
    object_key = Column(String, nullable=True)  # key in MinIO/S3
    status = Column(String, nullable=False, default="pending")  # pending|processing|ready|failed
    error = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(String, primary_key=True)  # uuid string
    doc_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    doc_name = Column(String, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    source_page = Column(Integer, nullable=True)


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db(retries: int = 10, delay_seconds: float = 2.0):
    """Create tables if they don't exist. Retries because Postgres may not be
    accepting connections the instant this container starts."""
    import time

    last_error = None
    for attempt in range(1, retries + 1):
        try:
            Base.metadata.create_all(engine)
            return
        except Exception as e:  # noqa: BLE001
            last_error = e
            time.sleep(delay_seconds)
    raise RuntimeError(f"Could not initialize Postgres schema after {retries} attempts: {last_error}")


# --- Documents ---

def create_document(filename: str, object_key: str, status: str = "pending") -> int:
    with get_session() as s:
        doc = Document(filename=filename, object_key=object_key, status=status)
        s.add(doc)
        s.commit()
        s.refresh(doc)
        return doc.id


def set_document_status(doc_id: int, status: str, error: str | None = None):
    with get_session() as s:
        doc = s.get(Document, doc_id)
        if doc:
            doc.status = status
            doc.error = error
            s.commit()


def get_document(doc_id: int) -> dict | None:
    with get_session() as s:
        doc = s.get(Document, doc_id)
        if not doc:
            return None
        num_chunks = s.query(Chunk).filter(Chunk.doc_id == doc_id).count()
        row = _doc_to_dict(doc)
        row["num_chunks"] = num_chunks
        return row


def list_documents() -> list[dict]:
    with get_session() as s:
        docs = s.query(Document).order_by(Document.id.desc()).all()
        result = []
        for d in docs:
            num_chunks = s.query(Chunk).filter(Chunk.doc_id == d.id).count()
            row = _doc_to_dict(d)
            row["num_chunks"] = num_chunks
            result.append(row)
        return result


def delete_document(doc_id: int):
    with get_session() as s:
        s.query(Chunk).filter(Chunk.doc_id == doc_id).delete()
        s.query(Document).filter(Document.id == doc_id).delete()
        s.commit()


def _doc_to_dict(d: Document) -> dict:
    return {
        "id": d.id,
        "filename": d.filename,
        "object_key": d.object_key,
        "status": d.status,
        "error": d.error,
        "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
    }


# --- Chunks ---

def insert_chunks(rows: list[dict]):
    with get_session() as s:
        s.bulk_insert_mappings(Chunk, rows)
        s.commit()


def get_all_chunks() -> list[dict]:
    with get_session() as s:
        chunks = s.query(Chunk).all()
        return [_chunk_to_dict(c) for c in chunks]


def get_chunks_by_ids(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    with get_session() as s:
        chunks = s.query(Chunk).filter(Chunk.id.in_(ids)).all()
        return {c.id: _chunk_to_dict(c) for c in chunks}


def _chunk_to_dict(c: Chunk) -> dict:
    return {
        "id": c.id,
        "doc_id": c.doc_id,
        "doc_name": c.doc_name,
        "chunk_index": c.chunk_index,
        "text": c.text,
        "source_page": c.source_page,
    }
