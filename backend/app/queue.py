from redis import Redis
from rq import Queue

from app.config import settings

_redis_conn = Redis.from_url(settings.redis_url)
ingest_queue = Queue(settings.ingest_queue_name, connection=_redis_conn)


def is_ready() -> bool:
    try:
        _redis_conn.ping()
        return True
    except Exception:
        return False


def enqueue_ingest_job(doc_id: int):
    # Referenced by dotted path so the worker process (a separate container/import)
    # resolves and imports it independently of the API process.
    return ingest_queue.enqueue(
        "app.ingestion.process_document",
        doc_id,
        job_timeout=600,
    )
