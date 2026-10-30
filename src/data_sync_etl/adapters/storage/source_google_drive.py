"""Read-only Drive blobs, bounded in-memory reads, explicit folder allowlist; no export."""

import json
import random
import re
import time
from contextlib import contextmanager
from io import BytesIO

from data_sync_etl.adapters.media import probe
from data_sync_etl.domain.core import SourceFileError
from data_sync_etl.domain.format_validation import FormatValidatorRegistry

API = 'https://www.googleapis.com/drive/v3/files/'
SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
FOLDER = 'application/vnd.google-apps.folder'
ID = re.compile(r'[A-Za-z0-9_-]{1,200}')
BUCKET = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,254}')
CHUNK = 256 * 1024
FIELDS = 'id,name,mimeType,size,parents,trashed,version,capabilities(canDownload)'


class SourceGoogleDriveFileReader:
    def __init__(self, settings, *, session_factory=None, clock=time.monotonic, sleep=time.sleep):
        self.settings, self.roots = settings, dict(settings.source_drive_buckets)
        self.session_factory, self.clock, self.sleep = session_factory, clock, sleep
        self.filename_aliases = dict(settings.source_drive_filename_aliases)
        self.filename_policy = getattr(settings, 'source_drive_filename_policy', 'exact')
        if self.filename_policy not in ('exact', 'canonical_id'):
            raise SourceFileError('INVALID_DRIVE_FILENAME_POLICY')
        for key, actual_name in self.filename_aliases.items():
            file_id, original_name = self._reference(key)
            if not isinstance(actual_name, str):
                raise SourceFileError('INVALID_DRIVE_FILENAME_ALIAS')
            self._reference(f'files/{file_id}/{actual_name}')
            if original_name.rsplit('.', 1)[-1].lower() != actual_name.rsplit('.', 1)[-1].lower():
                raise SourceFileError('INVALID_DRIVE_FILENAME_ALIAS')
        self.formats = FormatValidatorRegistry(media_registration=settings.source_metadata_media_enabled,
                                             media_processing=not settings.source_metadata_media_enabled,
                                             media_probe=lambda path, extension: probe(path, settings, extension),
                                             media_max_size_bytes=settings.media_max_size_bytes)
        if any(not BUCKET.fullmatch(k) or not ID.fullmatch(v) for k, v in self.roots.items()):
            raise SourceFileError('INVALID_DRIVE_BUCKET_CONFIG')

    def configuration_check(self):
        if not self.settings.source_drive_enabled:
            raise SourceFileError('DRIVE_DISABLED')
        if not self.roots:
            raise SourceFileError('DRIVE_BUCKETS_REQUIRED')
        if self.session_factory is None and (
                self.settings.source_drive_credentials_file is None
                or not self.settings.source_drive_credentials_file.is_file()):
            raise SourceFileError('DRIVE_CREDENTIALS_UNAVAILABLE')

    @contextmanager
    def _session(self):
        self.configuration_check()
        if self.session_factory is not None:
            with self.session_factory() as session:
                try:
                    yield session
                except OSError:
                    raise SourceFileError('DRIVE_UNAVAILABLE', transient=True) from None
            return
        # Optional runtime: Local-only imports never load Google auth/requests.
        try:
            from google.auth.exceptions import RefreshError
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2.credentials import Credentials as UserCredentials
            from google.oauth2.service_account import Credentials as ServiceCredentials
            from requests.exceptions import RequestException
        except ImportError:
            raise SourceFileError('DRIVE_RUNTIME_UNAVAILABLE') from None
        try:
            with self.settings.source_drive_credentials_file.open(encoding='utf-8') as file:
                config = json.load(file)
            if (config.get('type') != self.settings.source_drive_auth_type
                    or config.get('token_uri', 'https://oauth2.googleapis.com/token') != 'https://oauth2.googleapis.com/token'):
                raise ValueError('Invalid credential type/endpoint')
            credentials = (ServiceCredentials.from_service_account_info(config, scopes=[SCOPE])
                           if config['type'] == 'service_account' else
                           UserCredentials.from_authorized_user_info(config, scopes=[SCOPE]))
        except Exception:
            raise SourceFileError('DRIVE_CREDENTIALS_INVALID') from None
        with AuthorizedSession(credentials, max_refresh_attempts=1,
                               refresh_timeout=self.settings.source_drive_timeout_seconds) as session:
            try:
                yield session
            except RefreshError:
                raise SourceFileError('DRIVE_CREDENTIALS_EXPIRED') from None
            except RequestException:
                raise SourceFileError('DRIVE_UNAVAILABLE', transient=True) from None

    def _deadline(self, deadline, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise SourceFileError('SOURCE_REQUEST_CANCELLED')
        if self.clock() >= deadline:
            raise SourceFileError('DRIVE_TIMEOUT', transient=True)

    def _get(self, session, file_id, deadline, *, media=False, cancelled=None):
        params = {'supportsAllDrives': 'true', **({'alt': 'media'} if media else {'fields': FIELDS})}
        attempts = self.settings.source_drive_max_attempts
        for attempt in range(attempts):
            self._deadline(deadline, cancelled)
            try:
                response = session.get(API + file_id, params=params, stream=True, allow_redirects=False,
                    timeout=min(self.settings.source_drive_timeout_seconds, max(.1, deadline - self.clock())))
            except (TimeoutError, ConnectionError, OSError):
                if attempt + 1 == attempts:
                    raise SourceFileError('DRIVE_UNAVAILABLE', transient=True) from None
            else:
                if response.status_code == 200:
                    return response
                status = response.status_code
                response.close()
                if status not in (429, 500, 502, 503, 504):
                    code = {401: 'DRIVE_CREDENTIALS_EXPIRED', 403: 'DRIVE_PERMISSION_DENIED',
                            404: 'DRIVE_FILE_NOT_FOUND'}.get(status, 'DRIVE_REQUEST_REJECTED')
                    raise SourceFileError(code)
                if attempt + 1 == attempts:
                    raise SourceFileError('DRIVE_RATE_LIMITED' if status == 429 else 'DRIVE_UNAVAILABLE', transient=True)
            delay = min(2 ** attempt + random.uniform(0, .25), max(0, deadline - self.clock()))
            if cancelled is not None:
                if cancelled.wait(delay):
                    raise SourceFileError('SOURCE_REQUEST_CANCELLED')
            else:
                self.sleep(delay)
        raise SourceFileError('DRIVE_UNAVAILABLE', transient=True)

    def _metadata(self, session, file_id, deadline, cancelled=None):
        response = self._get(session, file_id, deadline, cancelled=cancelled)
        try:
            chunks, size = [], 0
            for block in response.iter_content(CHUNK):
                self._deadline(deadline, cancelled)
                size += len(block)
                if size > 64 * 1024:
                    raise SourceFileError('INVALID_DRIVE_METADATA')
                chunks.append(block)
            value = json.loads(b''.join(chunks))
            if not isinstance(value, dict) or value.get('id') != file_id or value.get('trashed') is not False:
                raise SourceFileError('INVALID_DRIVE_METADATA')
            return value
        except (ValueError, TypeError):
            raise SourceFileError('INVALID_DRIVE_METADATA') from None
        finally:
            response.close()

    def _inside_root(self, session, metadata, root, deadline, cancelled=None):
        seen, current = set(), metadata
        for _ in range(32):
            parents = current.get('parents')
            if not isinstance(parents, list) or len(parents) != 1 or not isinstance(parents[0], str):
                raise SourceFileError('DRIVE_OUTSIDE_ROOT')
            parent = parents[0]
            if not ID.fullmatch(parent) or parent in seen:
                raise SourceFileError('DRIVE_OUTSIDE_ROOT')
            seen.add(parent)
            current = self._metadata(session, parent, deadline, cancelled)
            if current.get('mimeType') != FOLDER:
                raise SourceFileError('DRIVE_OUTSIDE_ROOT')
            if parent == root:
                return
        raise SourceFileError('DRIVE_OUTSIDE_ROOT')

    @staticmethod
    def _reference(key):
        if not isinstance(key, str) or len(key) > 500:
            raise SourceFileError('INVALID_DRIVE_REFERENCE')
        parts = key.split('/')
        if (len(parts) != 3 or parts[0] != 'files' or not ID.fullmatch(parts[1])
                or not parts[2] or parts[2] in ('.', '..') or parts[2][-1:] in ('.', ' ')
                or any(ord(c) < 32 or ord(c) == 127 or c in '\\:<>"|?*%' for c in parts[2])):
            raise SourceFileError('INVALID_DRIVE_REFERENCE')
        return parts[1], parts[2]

    def _validate_blob(self, metadata, filename):
        mime = metadata.get('mimeType', '')
        if mime == 'application/vnd.google-apps.shortcut':
            raise SourceFileError('DRIVE_SHORTCUT_FORBIDDEN')
        if not isinstance(mime, str) or mime.startswith('application/vnd.google-apps.'):
            raise SourceFileError('UNSUPPORTED_DRIVE_NATIVE')
        expected_name = self.filename_aliases.get(f"files/{metadata.get('id')}/{filename}", filename)
        if self.filename_policy == 'exact':
            if metadata.get('name') != expected_name:
                raise SourceFileError('DRIVE_FILENAME_MISMATCH')
        else:
            # Drive ID is the identity. Renames are allowed only within the
            # original file type; folder/MIME/revision checks remain below and
            # the SOURCE resolver verifies SHA-256 before publishing bytes.
            _, actual_name = self._reference(f"files/{metadata.get('id')}/{metadata.get('name')}")
            if actual_name.rsplit('.', 1)[-1].lower() != filename.rsplit('.', 1)[-1].lower():
                raise SourceFileError('DRIVE_FILENAME_MISMATCH')
        try:
            validator = self.formats.find_validator(filename)
        except Exception:
            raise SourceFileError('UNSUPPORTED_SOURCE_FORMAT') from None
        registered_media = self.settings.source_metadata_media_enabled and validator.extension in ('.mp3', '.mp4')
        if mime not in validator.mime_types or (not validator.text_extraction_supported and not registered_media):
            raise SourceFileError('DRIVE_MIME_MISMATCH')
        size = metadata.get('size')
        if not isinstance(size, str) or not size.isascii() or not size.isdecimal() or len(size) > 15:
            raise SourceFileError('INVALID_DRIVE_METADATA')
        size = int(size)
        max_size = min(validator.max_size_bytes,
                       getattr(self.settings, 'source_content_max_bytes', 32 * 1024 * 1024))
        if not 0 < size <= max_size:
            raise SourceFileError('FILE_TOO_LARGE' if size else 'INVALID_SOURCE_FILE')
        if not isinstance(metadata.get('version'), str) or not metadata['version']:
            raise SourceFileError('INVALID_DRIVE_METADATA')
        capabilities = metadata.get('capabilities')
        if not isinstance(capabilities, dict) or capabilities.get('canDownload') is not True:
            raise SourceFileError('DRIVE_PERMISSION_DENIED')
        return size

    def check(self):
        deadline = self.clock() + self.settings.source_drive_total_seconds
        with self._session() as session:
            for root in set(self.roots.values()):
                if self._metadata(session, root, deadline).get('mimeType') != FOLDER:
                    raise SourceFileError('DRIVE_ROOT_INVALID')

    def open_stream(self, bucket, object_key, *, cancelled=None):
        if bucket not in self.roots:
            raise SourceFileError('DRIVE_BUCKET_NOT_ALLOWED')
        file_id, filename = self._reference(object_key)
        deadline = self.clock() + self.settings.source_drive_total_seconds
        memory = BytesIO()
        try:
            with self._session() as session:
                before = self._metadata(session, file_id, deadline, cancelled)
                size = self._validate_blob(before, filename)
                self._inside_root(session, before, self.roots[bucket], deadline, cancelled)
                response = self._get(session, file_id, deadline, media=True, cancelled=cancelled)
                try:
                    headers = getattr(response, 'headers', {}) or {}
                    media_type = headers.get('Content-Type', headers.get('content-type', ''))
                    if (not isinstance(media_type, str)
                            or media_type.split(';', 1)[0].strip().lower() in ('text/html', 'application/xhtml+xml')):
                        raise SourceFileError('INVALID_DRIVE_RESPONSE')
                    downloaded = 0
                    for block in response.iter_content(CHUNK):
                        self._deadline(deadline, cancelled)
                        downloaded += len(block)
                        if downloaded > size:
                            raise SourceFileError('DRIVE_SIZE_MISMATCH')
                        memory.write(block)
                    if downloaded != size:
                        raise SourceFileError('DRIVE_SIZE_MISMATCH', transient=True)
                finally:
                    response.close()
                after = self._metadata(session, file_id, deadline, cancelled)
                if any(before.get(k) != after.get(k) for k in ('version', 'name', 'size', 'mimeType', 'parents')):
                    raise SourceFileError('DRIVE_FILE_CHANGED')
                self._validate_blob(after, filename)
                self._inside_root(session, after, self.roots[bucket], deadline, cancelled)
            memory.seek(0)
            return memory
        except BaseException:
            memory.close()
            raise

    def revision(self, bucket, object_key, *, cancelled=None):
        if bucket not in self.roots:
            raise SourceFileError('DRIVE_BUCKET_NOT_ALLOWED')
        file_id, filename = self._reference(object_key)
        deadline = self.clock() + self.settings.source_drive_total_seconds
        with self._session() as session:
            metadata = self._metadata(session, file_id, deadline, cancelled)
            self._validate_blob(metadata, filename)
            self._inside_root(session, metadata, self.roots[bucket], deadline, cancelled)
            return tuple(metadata.get(k) if k != 'parents' else tuple(metadata[k])
                         for k in ('version', 'name', 'size', 'mimeType', 'parents'))

    def original_url(self, bucket, object_key):
        if bucket not in self.roots:
            raise SourceFileError('DRIVE_BUCKET_NOT_ALLOWED')
        file_id, _ = self._reference(object_key)
        return f'https://drive.google.com/file/d/{file_id}/view'

    def viewer_reference(self, bucket, object_key):
        """Return Drive's view link without downloading the file contents."""
        if bucket not in self.roots:
            raise SourceFileError('DRIVE_BUCKET_NOT_ALLOWED')
        file_id, filename = self._reference(object_key)
        deadline = self.clock() + self.settings.source_drive_total_seconds
        with self._session() as session:
            before = self._metadata(session, file_id, deadline)
            self._validate_viewer_file(before, filename)
            self._inside_root(session, before, self.roots[bucket], deadline)
            after = self._metadata(session, file_id, deadline)
            self._validate_viewer_file(after, filename)
            self._inside_root(session, after, self.roots[bucket], deadline)
        if any(before.get(key) != after.get(key)
               for key in ('version', 'name', 'size', 'mimeType', 'parents')):
            raise SourceFileError('DRIVE_FILE_CHANGED')
        return {
            'source_file_id': file_id,
            'file_name': after['name'],
            'mime_type': after['mimeType'],
            'revision': after['version'],
            'viewer_url': f'https://drive.google.com/file/d/{file_id}/view',
            'embedded_url': f'https://drive.google.com/file/d/{file_id}/preview',
        }

    def _validate_viewer_file(self, metadata, filename):
        mime = metadata.get('mimeType')
        if mime == FOLDER or mime == 'application/vnd.google-apps.shortcut':
            raise SourceFileError('DRIVE_SHORTCUT_FORBIDDEN')
        if not isinstance(mime, str) or not mime:
            raise SourceFileError('INVALID_DRIVE_METADATA')
        try:
            validator = self.formats.find_validator(filename)
        except Exception:
            raise SourceFileError('UNSUPPORTED_SOURCE_FORMAT') from None
        if mime not in validator.mime_types:
            raise SourceFileError('DRIVE_MIME_MISMATCH')
        expected_name = self.filename_aliases.get(f"files/{metadata.get('id')}/{filename}", filename)
        if self.filename_policy == 'exact' and metadata.get('name') != expected_name:
            raise SourceFileError('DRIVE_FILENAME_MISMATCH')
        if self.filename_policy == 'canonical_id':
            _, actual_name = self._reference(f"files/{metadata.get('id')}/{metadata.get('name')}")
            if actual_name.rsplit('.', 1)[-1].lower() != filename.rsplit('.', 1)[-1].lower():
                raise SourceFileError('DRIVE_FILENAME_MISMATCH')
        if not isinstance(metadata.get('version'), str) or not metadata['version']:
            raise SourceFileError('INVALID_DRIVE_METADATA')
