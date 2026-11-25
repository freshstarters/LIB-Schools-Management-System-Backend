"""S3-compatible object storage (Cloudflare R2 / AWS S3).

Fails closed when production needs object storage and credentials are blank.
Local filesystem media is allowed only when DEBUG=True and STORAGE_BACKEND=local.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible

PRESIGNED_GET_SECONDS = 24 * 60 * 60


def storage_backend_name():
    return str(getattr(settings, "STORAGE_BACKEND", "") or "").strip().lower()


def storage_credentials():
    return {
        "access_key": str(getattr(settings, "AWS_ACCESS_KEY_ID", "") or "").strip(),
        "secret_key": str(getattr(settings, "AWS_SECRET_ACCESS_KEY", "") or "").strip(),
        "bucket": str(getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or "").strip(),
        "endpoint": str(getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "").strip(),
    }


def assert_storage_configured():
    """Raise ImproperlyConfigured when the active backend cannot serve files."""
    backend = storage_backend_name()
    debug = bool(getattr(settings, "DEBUG", False))
    if backend == "local":
        if not debug:
            raise ImproperlyConfigured(
                "STORAGE_BACKEND=local is allowed only when DEBUG=True."
            )
        return "local"
    if backend == "s3":
        creds = storage_credentials()
        missing = [
            name
            for name, value in (
                ("AWS_ACCESS_KEY_ID", creds["access_key"]),
                ("AWS_SECRET_ACCESS_KEY", creds["secret_key"]),
                ("AWS_STORAGE_BUCKET_NAME", creds["bucket"]),
                ("AWS_S3_ENDPOINT_URL", creds["endpoint"]),
            )
            if not value
        ]
        if missing:
            raise ImproperlyConfigured(
                "Object storage is unavailable: set " + ", ".join(missing) + "."
            )
        return "s3"
    raise ImproperlyConfigured("STORAGE_BACKEND must be s3 or local.")


@deconstructible
class PrivateS3Storage(Storage):
    """Private S3/R2 objects. Reads go through short-lived presigned GET URLs."""

    def __init__(self):
        assert_storage_configured()
        if storage_backend_name() != "s3":
            raise ImproperlyConfigured("PrivateS3Storage requires STORAGE_BACKEND=s3.")
        creds = storage_credentials()
        import boto3
        from botocore.client import Config

        self.bucket_name = creds["bucket"]
        self._client = boto3.client(
            "s3",
            endpoint_url=creds["endpoint"],
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )

    def _open(self, name, mode="rb"):
        from django.core.files.base import ContentFile

        response = self._client.get_object(Bucket=self.bucket_name, Key=name)
        return ContentFile(response["Body"].read())

    def _save(self, name, content):
        cleaned = self.get_available_name(name)
        content.open()
        body = content.read()
        extra = {}
        content_type = getattr(content, "content_type", None)
        if content_type:
            extra["ContentType"] = content_type
        self._client.put_object(
            Bucket=self.bucket_name,
            Key=cleaned,
            Body=body,
            **extra,
        )
        return cleaned

    def delete(self, name):
        self._client.delete_object(Bucket=self.bucket_name, Key=name)

    def exists(self, name):
        try:
            self._client.head_object(Bucket=self.bucket_name, Key=name)
            return True
        except Exception:
            return False

    def size(self, name):
        response = self._client.head_object(Bucket=self.bucket_name, Key=name)
        return int(response["ContentLength"])

    def url(self, name):
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": name},
            ExpiresIn=PRESIGNED_GET_SECONDS,
        )

    def get_accessed_time(self, name):
        raise NotImplementedError("S3 storage does not track accessed time.")

    def get_created_time(self, name):
        raise NotImplementedError("S3 storage does not track created time.")

    def get_modified_time(self, name):
        response = self._client.head_object(Bucket=self.bucket_name, Key=name)
        return response["LastModified"]


def media_storage():
    """Return the configured default media storage instance."""
    backend = assert_storage_configured()
    if backend == "local":
        location = getattr(settings, "MEDIA_ROOT", "") or "media"
        base = getattr(settings, "MEDIA_URL", "") or "/media/"
        return FileSystemStorage(location=location, base_url=base)
    return PrivateS3Storage()


def photo_url(field_file):
    """Presigned or local URL for an ImageField/FileField value."""
    if not field_file or not getattr(field_file, "name", None):
        return None
    return field_file.storage.url(field_file.name)
