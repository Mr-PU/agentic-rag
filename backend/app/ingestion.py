import os
import tempfile
import uuid

from fastapi import UploadFile
from pypdf import PdfReader

from app.config import settings
from app import db, chunking, storage

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


def _extract_pages(path: str, filename: str) -> list[tuple[str, int | None]]:
    """Return list of (text, page_number) tuples. page_number is None for plain text files."""
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".pdf":
        reader = PdfReader(path)
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append((text, i + 1))
        return pages

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return [(f.read(), None)]


# --- API side: called synchronously from the /api/ingest request handler ---
# Only does the cheap, fast parts (validate + store raw file + enqueue). All the
# slow parts (parsing, embedding, indexing) happen in the worker so the HTTP
# request returns immediately.

async def submit_ingest(file: UploadFile) -> dict:
    from app import queue  # local import: keeps the API import graph light

    safe_name = os.path.basename(file.filename or "").strip()
    if not safe_name:
        raise ValueError("Invalid filename")

    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"File too large (max {MAX_FILE_SIZE_BYTES // (1024*1024)} MB)")
    if not contents:
        raise ValueError("File is empty")

    object_key = f"{uuid.uuid4().hex}/{safe_name}"
    storage.ensure_bucket()
    storage.upload_bytes(object_key, contents)

    doc_id = db.create_document(filename=safe_name, object_key=object_key, status="pending")
    queue.enqueue_ingest_job(doc_id)

    return {"doc_id": doc_id, "filename": safe_name, "status": "pending"}


# --- Worker side: run in the `worker` container/process, consumed from Redis ---

def process_document(doc_id: int):
    from app import vectorstore, embeddings_sync  # sync embedding wrapper, see below

    doc = db.get_document(doc_id)
    if not doc:
        return  # deleted before the worker got to it

    db.set_document_status(doc_id, "processing")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, doc["filename"])
            storage.download_to_path(doc["object_key"], local_path)

            pages = _extract_pages(local_path, doc["filename"])
            if not pages:
                raise ValueError("Could not extract any text (is the PDF scanned/image-only?)")

            all_chunk_texts: list[str] = []
            chunk_rows: list[dict] = []
            chunk_index = 0

            for page_text, page_num in pages:
                for piece in chunking.chunk_text(page_text, settings.chunk_size, settings.chunk_overlap):
                    chunk_id = str(uuid.uuid4())
                    all_chunk_texts.append(piece)
                    chunk_rows.append(
                        {
                            "id": chunk_id,
                            "doc_id": doc_id,
                            "doc_name": doc["filename"],
                            "chunk_index": chunk_index,
                            "text": piece,
                            "source_page": page_num,
                        }
                    )
                    chunk_index += 1

            if not chunk_rows:
                raise ValueError("Document produced no chunks (empty after extraction)")

            vectors = embeddings_sync.embed_texts(all_chunk_texts)

            vectorstore.ensure_collection()
            vectorstore.upsert(
                chunk_ids=[r["id"] for r in chunk_rows],
                vectors=vectors,
                payloads=[
                    {
                        "doc_id": r["doc_id"],
                        "doc_name": r["doc_name"],
                        "chunk_index": r["chunk_index"],
                        "text": r["text"],
                        "source_page": r["source_page"],
                    }
                    for r in chunk_rows
                ],
            )

            db.insert_chunks(chunk_rows)

        db.set_document_status(doc_id, "ready")

    except Exception as e:  # noqa: BLE001
        db.set_document_status(doc_id, "failed", error=str(e))
        raise
