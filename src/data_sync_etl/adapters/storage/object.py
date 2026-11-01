"""S3-compatible object storage adapters.

The implementation intentionally has no cloud SDK dependency.  It uses the
AWS Signature V4 HTTP protocol, which is supported by Neon Object Storage,
Cloudflare R2, AWS S3 and Supabase S3.  A small in-memory adapter is included
for isolated tests and local contract checks; it is never selected for a
production profile.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from data_sync_etl.domain.core import DomainError, UploadTooLargeError
from data_sync_etl.ports.cloud_storage import CloudObject, CloudStorageHealth, CloudStoragePort
from data_sync_etl.ports.processing import StoredFile

BLOCK_SIZE = 1024 * 1024
_UNSET = object()


def _safe_key(key: str) -> str:
    if not isinstance(key, str) or not key or "\\" in key or key.startswith("/"):
        raise DomainError("CLOUD_STORAGE_INVALID_OBJECT_KEY")
    parts = key.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise DomainError("CLOUD_STORAGE_INVALID_OBJECT_KEY")
    return "/".join(parts)


def _read_secret(path: Path | None, value: str | None = None) -> str | None:
    if path is not None:
        try:
            secret = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DomainError("CLOUD_STORAGE_SECRET_UNAVAILABLE") from exc
        if not secret:
            raise DomainError("CLOUD_STORAGE_SECRET_UNAVAILABLE")
        return secret
    return value.strip() if isinstance(value, str) and value.strip() else None


class InMemoryObjectStorageAdapter(CloudStoragePort):
    """Deterministic fake S3 used by tests and dry-run diagnostics."""

    def __init__(self, *, provider: str = "fake", provider_id: str = "fake-local",
                 bucket: str = "learning-resources"):
        self.provider = provider
        self.provider_id = provider_id
        self.bucket = bucket
        self.objects: dict[str, tuple[bytes, str]] = {}

    def validate_config(self):
        if not self.bucket or not self.provider_id:
            raise DomainError("CLOUD_STORAGE_CONFIGURATION_INVALID")

    def health(self, *, probe=False):
        return CloudStorageHealth(self.provider, self.provider_id, "in-memory", True, True if probe else None)

    def put_object(self, stream, *, object_key, content_type, max_size_bytes=None, expected_sha256=None):
        key = _safe_key(object_key)
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        while True:
            block = stream.read(BLOCK_SIZE)
            if not block:
                break
            size += len(block)
            if max_size_bytes is not None and size > max_size_bytes:
                raise UploadTooLargeError("Uploaded file exceeds the maximum allowed size")
            digest.update(block)
            chunks.append(block)
        if size < 1:
            raise DomainError("CLOUD_STORAGE_EMPTY_OBJECT")
        sha = digest.hexdigest()
        if expected_sha256 and expected_sha256.lower() != sha:
            raise DomainError("CLOUD_STORAGE_CHECKSUM_MISMATCH")
        self.objects.setdefault(key, (b"".join(chunks), content_type))
        payload, stored_type = self.objects[key]
        return CloudObject(self.provider_id, self.bucket, key, len(payload), hashlib.sha256(payload).hexdigest(), stored_type)

    def head_object(self, object_key):
        key = _safe_key(object_key)
        if key not in self.objects:
            raise DomainError("CLOUD_STORAGE_OBJECT_NOT_FOUND")
        payload, content_type = self.objects[key]
        return CloudObject(self.provider_id, self.bucket, key, len(payload), hashlib.sha256(payload).hexdigest(), content_type)

    def get_stream(self, object_key, *, byte_range=None):
        key = _safe_key(object_key)
        payload = self.objects.get(key)
        if payload is None:
            raise DomainError("CLOUD_STORAGE_OBJECT_NOT_FOUND")
        data = payload[0]
        if byte_range is not None:
            start, end = byte_range
            if start < 0 or end < start:
                raise DomainError("CLOUD_STORAGE_INVALID_RANGE")
            data = data[start:end + 1]
        return io.BytesIO(data)

    def presign_read(self, object_key, *, ttl_seconds):
        if ttl_seconds < 1:
            raise DomainError("CLOUD_STORAGE_INVALID_TTL")
        self.head_object(object_key)
        return f"https://fake.invalid/{self.bucket}/{quote(_safe_key(object_key), safe='/')}"

    def presign_upload(self, object_key, *, content_type, ttl_seconds):
        if ttl_seconds < 1:
            raise DomainError("CLOUD_STORAGE_INVALID_TTL")
        _safe_key(object_key)
        return f"https://fake.invalid/{self.bucket}/{quote(object_key, safe='/')}"

    def delete_object(self, object_key):
        self.objects.pop(_safe_key(object_key), None)


class S3CompatibleObjectStorageAdapter(CloudStoragePort):
    """Minimal SigV4 client for private S3-compatible buckets.

    This client deliberately exposes only bounded operations needed by ETL.
    It does not list buckets, follow redirects, or accept a URL from a caller.
    """

    def __init__(self, *, provider: str, provider_id: str, endpoint_url: str, region: str,
                 bucket: str, access_key_id: str | None, secret_access_key: str | None,
                 force_path_style: bool = True, signature_version: str = "s3v4",
                 connect_timeout: int = 10, read_timeout: int = 60):
        self.provider = provider
        self.provider_id = provider_id
        self.endpoint_url = endpoint_url.rstrip("/")
        self.region = region
        self.bucket = bucket
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.force_path_style = force_path_style
        self.signature_version = signature_version
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._endpoint = urlsplit(self.endpoint_url)

    def validate_config(self):
        if self.signature_version != "s3v4":
            raise DomainError("CLOUD_STORAGE_UNSUPPORTED_SIGNATURE")
        if self._endpoint.scheme != "https" or not self._endpoint.hostname:
            raise DomainError("CLOUD_STORAGE_ENDPOINT_MUST_BE_HTTPS")
        hostname = self._endpoint.hostname.rstrip('.').lower()
        if hostname in {'localhost', 'metadata.google.internal'}:
            raise DomainError("CLOUD_STORAGE_ENDPOINT_NOT_ALLOWED")
        try:
            address = ip_address(hostname)
            if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
                raise DomainError("CLOUD_STORAGE_ENDPOINT_NOT_ALLOWED")
        except ValueError:
            pass
        if not self.region or not self.bucket or not self.provider_id:
            raise DomainError("CLOUD_STORAGE_CONFIGURATION_INVALID")
        if not self.access_key_id or not self.secret_access_key:
            raise DomainError("CLOUD_STORAGE_CREDENTIALS_UNAVAILABLE")
        _safe_key(self.bucket)

    def _target(self, key: str | None = None):
        path = self._endpoint.path.rstrip("/")
        host = self._endpoint.netloc
        if self.force_path_style:
            path += "/" + quote(self.bucket, safe="")
        else:
            host = f"{self.bucket}.{host}"
        if key:
            path += "/" + quote(_safe_key(key), safe="/")
        return host, path or "/"

    @staticmethod
    def _hmac(key: bytes, value: str) -> bytes:
        return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()

    def _sign(self, method: str, key: str | None, *, query=None, headers=None, payload_hash="UNSIGNED-PAYLOAD"):
        self.validate_config()
        query = query or {}
        headers = {k.lower(): str(v).strip() for k, v in (headers or {}).items()}
        host, path = self._target(key)
        headers.setdefault("host", host)
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        headers["x-amz-date"] = amz_date
        headers["x-amz-content-sha256"] = payload_hash
        canonical_query = "&".join(f"{quote(str(k), safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(query.items()))
        canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        signed_headers = ";".join(sorted(headers))
        canonical = "\n".join((method, path, canonical_query, canonical_headers, signed_headers, payload_hash))
        scope = f"{date}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(("AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()))
        k_date = self._hmac(("AWS4" + self.secret_access_key).encode(), date)
        k_region = self._hmac(k_date, self.region)
        k_service = self._hmac(k_region, "s3")
        signing = self._hmac(self._hmac(k_service, "aws4_request"), string_to_sign)
        headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={self.access_key_id}/{scope}, "
                                     f"SignedHeaders={signed_headers}, Signature={signing.hex()}")
        url = urlunsplit((self._endpoint.scheme, host, path, canonical_query, ""))
        return url, headers

    def _request(self, method: str, key: str | None, *, body=None, headers=None, byte_range=None):
        req_headers = dict(headers or {})
        if byte_range is not None:
            req_headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        payload_hash = hashlib.sha256(body).hexdigest() if isinstance(body, bytes) else "UNSIGNED-PAYLOAD"
        url, signed = self._sign(method, key, headers=req_headers, payload_hash=payload_hash)
        request = Request(url, data=body, headers={k: v for k, v in signed.items()}, method=method)
        for attempt in range(3):
            try:
                return urlopen(request, timeout=self.read_timeout)
            except HTTPError as exc:
                if exc.code == 404:
                    raise DomainError("CLOUD_STORAGE_OBJECT_NOT_FOUND") from None
                if exc.code in (401, 403):
                    raise DomainError("CLOUD_STORAGE_ACCESS_DENIED") from None
                if exc.code == 409:
                    raise DomainError("CLOUD_STORAGE_CONFLICT") from None
                if (exc.code == 429 or exc.code >= 500) and attempt < 2:
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                if exc.code == 429 or exc.code >= 500:
                    raise DomainError("CLOUD_STORAGE_TRANSIENT") from None
                raise DomainError("CLOUD_STORAGE_REQUEST_FAILED") from None
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < 2:
                    time.sleep(0.1 * (2 ** attempt))
                    continue
                raise DomainError("CLOUD_STORAGE_UNAVAILABLE") from exc
        raise DomainError("CLOUD_STORAGE_UNAVAILABLE")

    def health(self, *, probe=False):
        try:
            self.validate_config()
            if probe:
                response = self._request("HEAD", None)
                response.close()
            return CloudStorageHealth(self.provider, self.provider_id, self._endpoint.hostname or "", True, True if probe else None)
        except DomainError as exc:
            return CloudStorageHealth(self.provider, self.provider_id, self._endpoint.hostname or "", False, False if probe else None, str(exc))

    def put_object(self, stream, *, object_key, content_type, max_size_bytes=None, expected_sha256=None):
        key = _safe_key(object_key)
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        while True:
            block = stream.read(BLOCK_SIZE)
            if not block:
                break
            size += len(block)
            if max_size_bytes is not None and size > max_size_bytes:
                raise UploadTooLargeError("Uploaded file exceeds the maximum allowed size")
            digest.update(block)
            chunks.append(block)
        if size < 1:
            raise DomainError("CLOUD_STORAGE_EMPTY_OBJECT")
        sha = digest.hexdigest()
        if expected_sha256 and expected_sha256.lower() != sha:
            raise DomainError("CLOUD_STORAGE_CHECKSUM_MISMATCH")
        response = self._request("PUT", key, body=b"".join(chunks),
                                headers={"Content-Type": content_type, "x-amz-meta-sha256": sha})
        response.close()
        return CloudObject(self.provider_id, self.bucket, key, size, sha, content_type)

    def head_object(self, object_key):
        key = _safe_key(object_key)
        response = self._request("HEAD", key)
        try:
            size = int(response.headers.get("Content-Length", "0"))
            return CloudObject(self.provider_id, self.bucket, key, size, response.headers.get("x-amz-meta-sha256", ""), response.headers.get_content_type(), response.headers.get("ETag"))
        finally:
            response.close()

    def get_stream(self, object_key, *, byte_range=None):
        return self._request("GET", _safe_key(object_key), byte_range=byte_range)

    def _presign(self, method, key, *, ttl_seconds, content_type=None):
        self.validate_config()
        if not 1 <= ttl_seconds <= 900:
            raise DomainError("CLOUD_STORAGE_INVALID_TTL")
        host, path = self._target(_safe_key(key))
        now = datetime.now(timezone.utc)
        date = now.strftime("%Y%m%d")
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        scope = f"{date}/{self.region}/s3/aws4_request"
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{self.access_key_id}/{scope}",
            "X-Amz-Date": amz_date, "X-Amz-Expires": str(ttl_seconds), "X-Amz-SignedHeaders": "host",
        }
        canonical_query = "&".join(f"{quote(str(k), safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(params.items()))
        canonical = "\n".join((method, path, canonical_query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"))
        string_to_sign = "\n".join(("AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()))
        k_date = self._hmac(("AWS4" + self.secret_access_key).encode(), date)
        k_region = self._hmac(k_date, self.region)
        k_service = self._hmac(k_region, "s3")
        key_bytes = self._hmac(self._hmac(k_service, "aws4_request"), string_to_sign)
        params["X-Amz-Signature"] = key_bytes.hex()
        return urlunsplit((self._endpoint.scheme, host, path, urlencode(params), ""))

    def presign_read(self, object_key, *, ttl_seconds):
        return self._presign("GET", object_key, ttl_seconds=ttl_seconds)

    def presign_upload(self, object_key, *, content_type, ttl_seconds):
        return self._presign("PUT", object_key, ttl_seconds=ttl_seconds, content_type=content_type)

    def delete_object(self, object_key):
        response = self._request("DELETE", _safe_key(object_key))
        response.close()


class CloudStorageAdapter:
    """Compatibility adapter implementing the existing processing StoragePort."""

    def __init__(self, cloud: CloudStoragePort, *, max_upload_bytes: int = 512 * 1024 * 1024,
                 object_prefix: str = "learning-resources/", providers: dict[str, CloudStoragePort] | None = None):
        self.cloud = cloud
        self.provider_id = cloud.provider_id
        self.providers = {self.provider_id: cloud, **(providers or {})}
        self.root = None
        self.max_upload_bytes = max_upload_bytes
        self.object_prefix = object_prefix.strip("/") + "/" if object_prefix else ""

    def provider_for(self, provider_id: str | None):
        """Resolve a persisted provider locator without guessing another one."""
        if not provider_id:
            raise DomainError("CLOUD_STORAGE_PROVIDER_ID_MISSING")
        provider = self.providers.get(provider_id)
        if provider is None:
            raise DomainError("CLOUD_STORAGE_PROVIDER_NOT_REGISTERED")
        return provider

    def for_provider(self, provider_id: str | None):
        provider = self.provider_for(provider_id)
        if provider is self.cloud:
            return self
        return CloudStorageAdapter(provider, max_upload_bytes=self.max_upload_bytes,
                                   object_prefix=self.object_prefix, providers=self.providers)

    def _key(self, key):
        # The persisted locator includes the configured prefix.  Keep it in
        # every provider operation so a provider swap cannot silently remap an
        # existing object to a different namespace.
        return _safe_key(key)

    def open_stream(self, bucket, object_key):
        if bucket != self.cloud.bucket:
            raise DomainError("CLOUD_STORAGE_BUCKET_MISMATCH")
        return self.cloud.get_stream(self._key(object_key))

    def exists(self, bucket, object_key):
        try:
            self.snapshot(bucket, object_key)
            return True
        except DomainError as exc:
            if str(exc) == "CLOUD_STORAGE_OBJECT_NOT_FOUND":
                return False
            raise

    def get_local_path(self, bucket, object_key):
        raise DomainError("CLOUD_STORAGE_NO_LOCAL_PATH")

    def snapshot(self, bucket, object_key):
        if bucket != self.cloud.bucket:
            raise DomainError("CLOUD_STORAGE_BUCKET_MISMATCH")
        obj = self.cloud.head_object(self._key(object_key))
        return StoredFile(bucket, object_key, obj.sha256, created=False)

    def save_stream(self, stream, *, max_size_bytes=None, suffix=".bin", validate_path=None):
        digest = hashlib.sha256()
        data = io.BytesIO()
        size = 0
        while True:
            block = stream.read(BLOCK_SIZE)
            if not block:
                break
            size += len(block)
            if max_size_bytes is not None and size > max_size_bytes:
                raise UploadTooLargeError("Uploaded file exceeds the maximum allowed size")
            digest.update(block)
            data.write(block)
        if not size:
            raise DomainError("Uploaded file is empty")
        source_hash = digest.hexdigest()
        if validate_path is not None:
            fd, temp_name = tempfile.mkstemp(prefix='etl-upload-check-', suffix=suffix.lower() or '.bin')
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(data.getbuffer())
                validate_path(temp_path)
            finally:
                temp_path.unlink(missing_ok=True)
        key = f"{self.object_prefix}{source_hash[:2]}/{source_hash}{suffix.lower()}"
        data.seek(0)
        obj = self.cloud.put_object(data, object_key=key, content_type="application/octet-stream",
                                    max_size_bytes=max_size_bytes, expected_sha256=source_hash)
        return StoredFile(self.cloud.bucket, obj.object_key, source_hash, created=True)

    def delete(self, bucket, object_key):
        if bucket != self.cloud.bucket:
            raise DomainError("CLOUD_STORAGE_BUCKET_MISMATCH")
        self.cloud.delete_object(self._key(object_key))

    @contextmanager
    def temporary_path(self, bucket: str, object_key: str, *, cancelled=None):
        """Bounded ETL_MANAGED scratch only; SOURCE_EXTERNAL never calls this."""
        fd, name = tempfile.mkstemp(prefix="etl-cloud-", suffix=PurePosixPath(object_key).suffix or ".bin")
        path = Path(name)
        try:
            try:
                stream = self.open_stream(bucket, object_key)
            except Exception:
                os.close(fd)
                raise
            try:
                with os.fdopen(fd, "wb") as output:
                    total = 0
                    while True:
                        if cancelled is not None and cancelled.is_set():
                            raise DomainError("CLOUD_STORAGE_REQUEST_CANCELLED")
                        block = stream.read(BLOCK_SIZE)
                        if not block:
                            break
                        total += len(block)
                        if total > self.max_upload_bytes:
                            raise UploadTooLargeError("Stored object exceeds the processing limit")
                        output.write(block)
            finally:
                stream.close()
            yield path
        finally:
            path.unlink(missing_ok=True)

    def image_path(self, task_id: str, claim_token: str) -> Path:
        # Media frame extraction still requires a path; this is ephemeral scratch,
        # not an original-file storage root.  The caller owns cleanup semantics.
        fd, name = tempfile.mkstemp(prefix=f"etl-frame-{task_id}-{claim_token}-", suffix=".png")
        os.close(fd)
        return Path(name)


def storage_from_settings(settings):
    mode = getattr(settings, "etl_file_storage_mode", "local")
    if mode == "local":
        if getattr(settings, "app_environment", None) == "production":
            raise DomainError("CLOUD_STORAGE_LOCAL_BACKEND_FORBIDDEN")
        return None
    provider = settings.etl_file_storage_provider
    providers: dict[str, CloudStoragePort] = {}
    if provider == "fake":
        cloud = InMemoryObjectStorageAdapter(provider_id=settings.etl_file_storage_provider_id,
                                             bucket=settings.etl_file_storage_bucket)
    else:
        cloud = S3CompatibleObjectStorageAdapter(
            provider=provider, provider_id=settings.etl_file_storage_provider_id,
            endpoint_url=settings.etl_file_storage_endpoint_url or "",
            region=settings.etl_file_storage_region or "",
            bucket=settings.etl_file_storage_bucket or "",
            access_key_id=_read_secret(settings.etl_file_storage_access_key_id_file, settings.etl_file_storage_access_key_id),
            secret_access_key=_read_secret(settings.etl_file_storage_secret_access_key_file, settings.etl_file_storage_secret_access_key),
            force_path_style=settings.etl_file_storage_force_path_style,
            signature_version=settings.etl_file_storage_signature_version,
            connect_timeout=settings.etl_file_storage_connect_timeout_seconds,
            read_timeout=settings.etl_file_storage_read_timeout_seconds,
        )
    cloud.validate_config()
    legacy_file = settings.etl_file_storage_legacy_read_registry_file
    if legacy_file is not None:
        try:
            raw_legacy = json.loads(Path(legacy_file).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise DomainError('CLOUD_STORAGE_LEGACY_REGISTRY_UNAVAILABLE') from exc
        if not isinstance(raw_legacy, dict):
            raise DomainError('CLOUD_STORAGE_LEGACY_REGISTRY_INVALID')
        for legacy_id, item in raw_legacy.items():
            if legacy_id == cloud.provider_id or not isinstance(item, dict):
                continue
            try:
                legacy = S3CompatibleObjectStorageAdapter(
                    provider=str(item['provider']), provider_id=str(legacy_id),
                    endpoint_url=str(item['endpoint_url']), region=str(item['region']),
                    bucket=str(item['bucket']),
                    access_key_id=_read_secret(Path(item['access_key_id_file']) if item.get('access_key_id_file') else None),
                    secret_access_key=_read_secret(Path(item['secret_access_key_file']) if item.get('secret_access_key_file') else None),
                    force_path_style=bool(item.get('force_path_style', True)),
                    signature_version=str(item.get('signature_version', 's3v4')),
                )
                legacy.validate_config()
                providers[legacy_id] = legacy
            except (KeyError, TypeError, ValueError, DomainError):
                # A malformed legacy entry is not a valid fallback.  Keep it
                # out of the registry so a persisted unknown ID fails closed.
                continue
    return CloudStorageAdapter(cloud, max_upload_bytes=settings.etl_file_storage_max_upload_bytes,
                               object_prefix=settings.etl_file_storage_object_prefix,
                               providers=providers)
