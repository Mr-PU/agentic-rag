import asyncio
import logging
import os

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app import db, ingestion, agent, llm_gateway, vectorstore, storage, queue
from app.models import ChatRequest, ChatResponse, DocumentInfo, IngestResponse

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Local Agentic RAG")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    # These each retry internally — Postgres/Qdrant/MinIO may still be starting
    # up even though docker-compose/k8s report the containers "running".
    await asyncio.get_running_loop().run_in_executor(None, db.init_db)
    for attempt in range(5):
        try:
            vectorstore.ensure_collection()
            break
        except Exception as e:
            logger.warning("Qdrant not ready yet (attempt %d/5): %s", attempt + 1, e)
            await asyncio.sleep(2)
    await asyncio.get_running_loop().run_in_executor(None, storage.ensure_bucket)


@app.get("/health")
async def health():
    ollama_ok = await llm_gateway.is_reachable()
    qdrant_ok = vectorstore.is_ready()
    redis_ok = queue.is_ready()
    minio_ok = storage.is_ready()
    all_ok = all([ollama_ok, qdrant_ok, redis_ok, minio_ok])
    return {
        "status": "ok" if all_ok else "degraded",
        "ollama": ollama_ok,
        "qdrant": qdrant_ok,
        "redis": redis_ok,
        "minio": minio_ok,
    }


@app.post("/api/ingest", response_model=IngestResponse, status_code=202)
async def ingest(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    try:
        result = await ingestion.submit_ingest(file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not submit document for ingestion: {e}")
    return result


@app.get("/api/documents", response_model=list[DocumentInfo])
def documents():
    return db.list_documents()


@app.get("/api/documents/{doc_id}", response_model=DocumentInfo)
def get_document(doc_id: int):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    vectorstore.delete_by_doc_id(doc_id)
    if doc.get("object_key"):
        storage.delete_object(doc["object_key"])
    db.delete_document(doc_id)
    return {"deleted": doc_id}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")
    try:
        return await agent.run_agent(req.query, top_k=req.top_k, max_iterations=req.max_iterations)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent run failed: {e}")


# --- Static frontend ---
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
