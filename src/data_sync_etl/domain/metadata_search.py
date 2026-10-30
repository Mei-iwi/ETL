"""Versioned, allowlisted resource metadata; no dependency on content artifacts."""

import re
import unicodedata

from data_sync_etl.domain.indexing import IndexingError, digest
from data_sync_etl.domain.lexical import (
    ANALYZER_VERSION,
    lexical_document,
    query_terms,
    surface_tokens,
)
from data_sync_etl.domain.search_access import denied

TEMPLATE = 'resource-metadata-v1'
GENERATION = 1
SEARCH_FIELDS = ('title', 'description', 'tags')
CONTEXT_FIELDS = ('subject', 'grade')


class IndexedMetadataCorpus(dict):
    """Request-local authority snapshot plus verified corpus statistics, not a cache."""
    ready_keys = None
    lengths = None
    index_fingerprint = None


def candidate_terms(fields):
    return sorted({term for name in SEARCH_FIELDS for term in surface_tokens(fields.get(name, ''))})


def candidate_query(query, dictionary, policy, domain=None):
    if not query_terms(query):
        return []
    expansion = dictionary.expand(query, domain=domain, policy=policy)
    terms = set(surface_tokens(query))
    for term in expansion.terms:
        terms.update(surface_tokens(term.term))
    if len(terms) > 512:
        raise IndexingError('METADATA_QUERY_LIMIT', 422)
    return sorted(terms)


def metadata_allowed(resource, version, access):
    if (not access.internal or not resource or not version
            or resource.get('is_active') is not True or resource.get('publication_status') != 'PUBLISHED'
            or version.get('resource_id') != resource.get('id')
            or version.get('lifecycle_status') not in ('METADATA_ONLY', 'INGESTED', 'APPROVED', 'PUBLISHED')
            or denied(resource) or denied(version) or version.get('rejection_reason')):
        return False
    if access.scope != 'authenticated_user':
        return True
    owned = (resource['id'] in access.resource_ids and access.owner_id is not None
             and resource.get('created_by') == access.owner_id)
    if not access.allow_public and not owned:
        return False
    # Published metadata follows the explicit authenticated shared-read policy.
    # Resource/version restrictions above always take precedence.
    return bool((access.allow_public and access.allow_shared_unreviewed)
                or (owned and access.allow_owned_unreviewed)
                or (version.get('final_approved_by') and version.get('final_approved_at')))


def clean(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise IndexingError('METADATA_FIELD_INVALID', 422)
    value = ' '.join(unicodedata.normalize('NFC', value).split())
    if re.search(r'https?://|file:|[A-Za-z]:[\\/]|\\\\|(?:password|secret|token|X-Amz-\w+)\s*[=:]', value, re.I):
        raise IndexingError('METADATA_FIELD_RESTRICTED', 422)
    return value


def projection(resource, version, curriculum, extension=None):
    fields = {'title': clean(resource.get('title') or '', 500)}
    if extension is not None:
        fields['description'] = clean(extension.description or '', 8000)
        tags = extension.tags or []
        if len(tags) > 64:
            raise IndexingError('METADATA_TAG_LIMIT', 422)
        fields['tags'] = '; '.join(sorted({clean(tag, 120) for tag in tags} - {''}))
    fields['subject'] = clean('; '.join(curriculum.get('subjects', [])), 2000)
    fields['grade'] = clean('; '.join(curriculum.get('grades', [])), 500)
    fields = {key: value for key, value in fields.items() if value}
    if not any(fields.get(key) for key in SEARCH_FIELDS):
        return None
    text = '\n'.join(f'{key.title()}: {fields[key]}' for key in (*SEARCH_FIELDS, *CONTEXT_FIELDS) if key in fields)
    # Authors, emails, ownership, storage keys, raw JSON never enter model text.
    # Resource-level Mongo description/tags inherit ONLY the selected current
    # version. There is no historical extension revision contract in this schema.
    authority = [resource['id'], version['id'], resource.get('mongo_id'),
                 resource.get('updated_at'), version.get('updated_at'), version.get('source_hash'),
                 resource.get('provider_name'), resource.get('resource_type_code')]
    suffix = version.get('storage_object_key', '').rsplit('.', 1)[-1].lower()
    resource_format = suffix if suffix in ('pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'png', 'jpg', 'jpeg', 'mp3', 'mp4') else None
    return {'resource_id': resource['id'], 'resource_version_id': version['id'], 'fields': fields,
            'resource_format': resource_format,
            'text': text, 'template': TEMPLATE, 'generation': GENERATION,
            'projection_fingerprint': digest([TEMPLATE, authority, fields] + (
                [resource['deletion_revision']] if resource.get('deletion_revision') else [])),
            'lexical_fingerprint': digest([TEMPLATE, ANALYZER_VERSION, fields]),
            'dense_input_fingerprint': digest([TEMPLATE, text])}


def lexical_fields(snapshot):
    return {key: lexical_document([snapshot['fields'].get(key, '')]) for key in SEARCH_FIELDS}
