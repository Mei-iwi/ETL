"""Component-owned, immutable OCR and normalized-content documents.

The caller must provide a separately configured component database and perform
source authorization and physical-isolation checks before constructing this
adapter. Nothing in this module creates collections or connects to SOURCE.
"""
import hashlib
import json
import re

from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.write_concern import WriteConcern

from data_sync_etl.domain.component_ocr_probe import OCRProbe, probe_ocr

_HASH = re.compile(r'^[0-9a-f]{64}$')


class ComponentContentError(RuntimeError):
    pass


def _identity(namespace, version_id, generation, kind, item_id):
    if (not isinstance(namespace, str) or not namespace.strip()
            or not isinstance(version_id, str) or not version_id.strip()
            or type(generation) is not int or generation < 1
            or not isinstance(item_id, str) or not item_id.strip()):
        raise ComponentContentError('COMPONENT_INVALID_IDENTITY')
    # Canonical length-safe serialization avoids ambiguous concatenations.
    encoded = json.dumps([namespace, version_id, generation, kind, item_id],
                         ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return hashlib.sha256(encoded).hexdigest()


class MongoComponentContent:
    def __init__(self, database):
        self.pages = database['component_ocr_pages'].with_options(write_concern=WriteConcern(w='majority'))
        self.units = database['component_content_units'].with_options(write_concern=WriteConcern(w='majority'))
        self.manifests = database['component_ocr_manifests'].with_options(write_concern=WriteConcern(w='majority'))

    @staticmethod
    def _put(collection, document):
        try:
            try:
                collection.update_one({'_id': document['_id']}, {'$setOnInsert': document}, upsert=True)
            except DuplicateKeyError:
                # A competing writer may have inserted the same deterministic ID.
                pass
            saved = collection.find_one({'_id': document['_id']})
        except PyMongoError:
            raise ComponentContentError('COMPONENT_CONTENT_UNAVAILABLE') from None
        if saved is None or any(saved.get(k) != value for k, value in document.items()):
            raise ComponentContentError('COMPONENT_CONTENT_CONFLICT')
        return document['_id']

    @staticmethod
    def _text_fields(text, source_hash, fingerprint):
        if (not isinstance(text, str) or not isinstance(source_hash, str) or not _HASH.fullmatch(source_hash)
                or not isinstance(fingerprint, str) or not _HASH.fullmatch(fingerprint)):
            raise ComponentContentError('COMPONENT_INVALID_CONTENT')
        return {'source_hash': source_hash, 'fingerprint': fingerprint,
                'checksum': hashlib.sha256(text.encode('utf-8')).hexdigest()}

    def put_page(self, *, namespace, version_id, generation, page_num, source_hash, fingerprint, raw_text,
                 engine_name=None, engine_version=None, confidence=None, result_metadata=None):
        if type(page_num) is not int or page_num < 1:
            raise ComponentContentError('COMPONENT_INVALID_IDENTITY')
        document = {'_id': _identity(namespace, version_id, generation, 'page', str(page_num)),
                    'namespace': namespace, 'version_id': version_id, 'generation': generation,
                    'page_num': page_num, 'raw_text': raw_text, 'verified': True,
                    'engine_name': engine_name, 'engine_version': engine_version,
                    'confidence': confidence, 'result_metadata': result_metadata,
                    **self._text_fields(raw_text, source_hash, fingerprint)}
        return self._put(self.pages, document)

    def put_manifest(self, *, namespace, version_id, generation, total_pages, source_hash, fingerprint):
        if type(total_pages) is not int or not 1 <= total_pages <= 2000:
            raise ComponentContentError('COMPONENT_INVALID_MANIFEST')
        self._text_fields('', source_hash, fingerprint)
        document = {'_id': _identity(namespace, version_id, generation, 'manifest', 'manifest'),
                    'namespace': namespace, 'version_id': version_id, 'generation': generation,
                    'total_pages': total_pages, 'source_hash': source_hash, 'fingerprint': fingerprint,
                    'verified': True}
        return self._put(self.manifests, document)

    def get_manifest(self, *, namespace, version_id, generation):
        key = _identity(namespace, version_id, generation, 'manifest', 'manifest')
        try:
            document = self.manifests.find_one({'_id': key})
        except PyMongoError:
            raise ComponentContentError('COMPONENT_CONTENT_UNAVAILABLE') from None
        if document is None:
            return None
        if (document.get('namespace') != namespace or document.get('version_id') != version_id
                or document.get('generation') != generation or document.get('verified') is not True
                or type(document.get('total_pages')) is not int or not 1 <= document['total_pages'] <= 2000):
            raise ComponentContentError('COMPONENT_CONTENT_CORRUPT')
        return document

    def probe(self, *, authorized, namespace, version_id, generation, source_hash, fingerprint):
        """Batch-read the manifest and pages without opening the source file."""
        if not authorized:
            return OCRProbe('UNAUTHORIZED')
        try:
            manifest = self.get_manifest(namespace=namespace, version_id=version_id, generation=generation)
            args = dict(authorized=True, available=True, namespace=namespace, version_id=version_id,
                        generation=generation, expected_hash=source_hash,
                        expected_fingerprint=fingerprint, manifest=manifest)
            initial = probe_ocr(**args, pages={})
            if initial.status != 'PARTIAL':
                return initial
            keys = [_identity(namespace, version_id, generation, 'page', str(page_num))
                    for page_num in range(1, manifest['total_pages'] + 1)]
            documents = self.pages.find({'_id': {'$in': keys}})
            pages = {}
            for document in documents:
                page_num = document.get('page_num')
                if (type(page_num) is not int or not 1 <= page_num <= len(keys)
                        or document.get('_id') != keys[page_num - 1] or page_num in pages):
                    raise ComponentContentError('COMPONENT_CONTENT_CORRUPT')
                pages[page_num] = document
            return probe_ocr(**args, pages=pages)
        except PyMongoError:
            raise ComponentContentError('COMPONENT_CONTENT_UNAVAILABLE') from None

    def put_unit(self, *, namespace, version_id, generation, unit_id, source_hash, fingerprint, text):
        document = {'_id': _identity(namespace, version_id, generation, 'unit', unit_id),
                    'namespace': namespace, 'version_id': version_id, 'generation': generation,
                    'unit_id': unit_id, 'text': text, 'verified': True,
                    **self._text_fields(text, source_hash, fingerprint)}
        return self._put(self.units, document)

    def get_page(self, *, namespace, version_id, generation, page_num, source_hash, fingerprint):
        if type(page_num) is not int or page_num < 1:
            raise ComponentContentError('COMPONENT_INVALID_IDENTITY')
        key = _identity(namespace, version_id, generation, 'page', str(page_num))
        try:
            document = self.pages.find_one({'_id': key})
        except PyMongoError:
            raise ComponentContentError('COMPONENT_CONTENT_UNAVAILABLE') from None
        if document is None:
            return None
        identity = {'namespace': namespace, 'version_id': version_id, 'generation': generation,
                    'page_num': page_num, 'source_hash': source_hash, 'fingerprint': fingerprint,
                    'verified': True}
        if any(document.get(k) != v for k, v in identity.items()):
            raise ComponentContentError('COMPONENT_CONTENT_CONFLICT')
        fields = self._text_fields(document.get('raw_text'), source_hash, fingerprint)
        if document.get('checksum') != fields['checksum']:
            raise ComponentContentError('COMPONENT_CONTENT_CORRUPT')
        return document

    def get_unit(self, *, namespace, version_id, generation, unit_id, source_hash, fingerprint):
        key = _identity(namespace, version_id, generation, 'unit', unit_id)
        try:
            document = self.units.find_one({'_id': key})
        except PyMongoError:
            raise ComponentContentError('COMPONENT_CONTENT_UNAVAILABLE') from None
        if document is None:
            return None
        identity = {'namespace': namespace, 'version_id': version_id, 'generation': generation,
                    'unit_id': unit_id, 'source_hash': source_hash, 'fingerprint': fingerprint,
                    'verified': True}
        if any(document.get(k) != v for k, v in identity.items()):
            raise ComponentContentError('COMPONENT_CONTENT_CONFLICT')
        fields = self._text_fields(document.get('text'), source_hash, fingerprint)
        if document.get('checksum') != fields['checksum']:
            raise ComponentContentError('COMPONENT_CONTENT_CORRUPT')
        return document
