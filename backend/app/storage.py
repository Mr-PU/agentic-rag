import io
import time

from minio import Minio
from minio.error import S3Error

from app.config import settings

_client = Minio(
    settings.s3_endpoint,
    access_key=settings.s3_access_key,
    secret_key=settings.s3_secret_key,
    secure=settings.s3_secure,
)


def ensure_bucket(retries: int = 10, delay_seconds: float = 2.0):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            if not _client.bucket_exists(settings.s3_bucket):
                _client.make_bucket(settings.s3_bucket)
            return
        except Exception as e:  # noqa: BLE001
            last_error = e
            time.sleep(delay_seconds)
    raise RuntimeError(f"Could not reach MinIO after {retries} attempts: {last_error}")


def upload_bytes(object_key: str, data: bytes, content_type: str = "application/octet-stream"):
    _client.put_object(
        settings.s3_bucket,
        object_key,
        data=io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def download_to_path(object_key: str, dest_path: str):
    _client.fget_object(settings.s3_bucket, object_key, dest_path)


def delete_object(object_key: str):
    try:
        _client.remove_object(settings.s3_bucket, object_key)
    except S3Error:
        pass  # already gone / never existed — deletion is best-effort


def is_ready() -> bool:
    try:
        _client.bucket_exists(settings.s3_bucket)
        return True
    except Exception:
        return False
