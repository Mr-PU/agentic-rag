from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ollama_base_url: str = "http://ollama:11434"
    qdrant_url: str = "http://qdrant:6333"

    # Postgres (metadata: documents, chunks)
    postgres_dsn: str = "postgresql+psycopg2://rag:rag@postgres:5432/rag"

    # Redis (job queue between api and worker)
    redis_url: str = "redis://redis:6379/0"
    ingest_queue_name: str = "ingest"

    # MinIO / S3 (raw uploaded file storage)
    s3_endpoint: str = "minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "documents"
    s3_secure: bool = False

    generator_model: str = "gemma3:12b"
    critic_model: str = "gemma3:12b"
    router_model: str = "qwen3:1.7b"
    embed_model: str = "nomic-embed-text"
    embed_dim: int = 768

    top_k: int = 6
    max_iterations: int = 3
    chunk_size: int = 800
    chunk_overlap: int = 120

    collection_name: str = "documents"

    class Config:
        env_file = ".env"


settings = Settings()
