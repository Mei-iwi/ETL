"""Internal, session-bound lexical export. No public endpoint or DB provisioning.

The principal provider MUST be a trusted server-side session boundary, not a
JSON grant, environment variable, CLI scope, or caller-supplied role header.
"""
import json
from collections import Counter
from pathlib import Path

from data_sync_etl.application.content_bags import bag_structure
from data_sync_etl.application.search_evaluation_snapshot import (
    EvaluationBag,
    SearchEvaluationSnapshot,
    capture_lexical_snapshot,
)
from data_sync_etl.application.search_review import ReviewCandidate
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess


def corpus_statistics(snapshot):
    documents = [bag.lexical for bag in snapshot.bags]
    return {'document_count': len(documents), 'total_length': sum(d.length for d in documents),
        'document_frequency': dict(sorted(Counter(t for d in documents for t in d.terms).items())),
        'field_policy': {'title_boost': snapshot.policy.title_boost,
                         'phrase_boost': snapshot.policy.phrase_boost}}


def verify_export(payload):
    required = {'kind', 'snapshot', 'root_units', 'model', 'statistics', 'principal_fingerprint', 'checksum'}
    if (not required <= set(payload) or set(payload) - required - {'review_candidates'}
            or payload['kind'] != 'trusted-lexical-export-v1'
            or digest({k: v for k, v in payload.items() if k != 'checksum'}) != payload['checksum']):
        raise ValueError('Export integrity mismatch')
    snapshot = SearchEvaluationSnapshot.model_validate(payload['snapshot'])
    if (payload['statistics'] != corpus_statistics(snapshot)
            or set(payload['root_units']) != {b.bag_id for b in snapshot.bags}
            or any(not units or len(set(units)) != len(units) for units in payload['root_units'].values())):
        raise ValueError('Export corpus/mapping mismatch')
    bags = {b.bag_id: b for b in snapshot.bags}
    for value in payload.get('review_candidates', []):
        candidate = ReviewCandidate.model_validate(value)
        bag = bags.get(candidate.bag_id)
        if (bag is None or bag.version_id != candidate.version_id or bag.resource_id != candidate.resource_id
                or bag.content_fingerprint != candidate.content_fingerprint
                or candidate.content_unit_id not in payload['root_units'][candidate.bag_id]):
            raise ValueError('Review evidence mapping mismatch')
    return snapshot


class TrustedLexicalExporter:
    def __init__(self, search, principal_provider, *, environment):
        if environment not in ('isolated_fixture', 'approved_staging'):
            raise ValueError('Explicit isolated fixture or approved staging required')
        self.search, self.principal_provider, self.environment = search, principal_provider, environment

    def _principal(self):
        access = self.principal_provider()
        if (not isinstance(access, SearchAccess) or not access.actor_id
                or access.scope not in ('authenticated_user', 'authenticated_admin')):
            raise SearchError('EVALUATION_AUTH_REQUIRED', 403)
        return access

    def capture(self, *, filters=None, include_review_candidates=False):
        access = self._principal()
        if self.search.settings.search_approved_accent_variants_enabled:
            # V1 records one expansion policy, not a union of query branches.
            # Refuse a misleading native-BM25 parity claim until a V2 export exists.
            raise SearchError('EVALUATION_UNSUPPORTED_QUERY_POLICY', 409)
        dictionary = self.search.dictionary.fingerprint
        policy_before = self.search.settings.search_expansion.model_dump()
        captured = []
        review_candidates = []

        def capture(rows, snapshots, policy, metadata):
            admitted = [EvaluationBag(bag_id=bag.id, version_id=bag.resource_version_id,
                resource_id=metadata[bag.resource_version_id]['resource_id'], generation=bag.projection_generation,
                content_fingerprint=bag.content_fingerprint, eligibility='verified_authorized_snapshot',
                lexical=bag.lexical) for bag, *_ in rows.values()]
            if not admitted:
                raise SearchError('EVALUATION_NO_ACCESSIBLE_CORPUS', 403)
            snapshot = capture_lexical_snapshot(sorted(admitted, key=lambda b: b.bag_id),
                self.search.dictionary, policy,
                provenance='isolated_fixture' if self.environment == 'isolated_fixture' else 'approved_test_export',
                min_coverage=self.search.settings.search_bow_min_coverage)
            units = {b.bag_id: sorted(bag_structure(snapshots[b.version_id][2])[b.bag_id]) for b in admitted}
            if include_review_candidates:
                for bag in admitted:
                    metadata_row = metadata[bag.version_id]
                    for chunk in self.search.indexing._preview(snapshots[bag.version_id]):
                        if chunk['content_unit_id'] not in units[bag.bag_id]:
                            continue
                        review_candidates.append(ReviewCandidate(resource_id=bag.resource_id, version_id=bag.version_id,
                            bag_id=bag.bag_id, chunk_id=chunk['id'], content_unit_id=chunk['content_unit_id'],
                            title=metadata_row['resource_title'], version_number=metadata_row['version_number'],
                            evidence=chunk['text'], snippet=chunk['text'][:400],
                            content_fingerprint=bag.content_fingerprint, page_from=chunk['page_from'], page_to=chunk['page_to']).model_dump())
                        if len(review_candidates) > 30000:
                            raise SearchError('EVALUATION_REVIEW_LIMIT', 422)
            captured.append((snapshot, units))

        self.search.search('evaluation snapshot probe', 'bow', filters=filters, size=1, access=access,
                           _batch_texts=True, _batch_bags=True, _evaluation_capture=capture)
        if self._principal() != access:
            raise SearchError('EVALUATION_ACCESS_CHANGED', 403)
        if (dictionary != self.search.dictionary.fingerprint
                or policy_before != self.search.settings.search_expansion.model_dump()
                or self.search.settings.search_approved_accent_variants_enabled):
            raise SearchError('EVALUATION_POLICY_CHANGED', 409)
        if len(captured) != 1:
            raise SearchError('EVALUATION_SNAPSHOT_INCOMPLETE', 409)
        snapshot, units = captured[0]
        embedding = self.search.indexing.embedding
        payload = {'kind': 'trusted-lexical-export-v1', 'snapshot': snapshot.model_dump(),
            'root_units': units, 'model': {'id': embedding.model_id, 'revision': embedding.version,
                'dimension': self.search.settings.index_embedding_dimension},
            'statistics': corpus_statistics(snapshot),
            'principal_fingerprint': digest([access.scope, access.actor_id, sorted(access.resource_ids),
                access.owner_id, access.allow_public, access.allow_owned_unreviewed,
                access.allow_shared_unreviewed])}
        if include_review_candidates:
            payload['review_candidates'] = review_candidates
        payload['checksum'] = digest(payload)
        verify_export(payload)
        return payload

    def run(self, output=None, *, filters=None, dry_run=False):
        # Dry-run performs the SAME authorization and validation, without a write.
        path = Path(output) if output is not None else None
        if not dry_run and (path is None or path.exists()
                or not path.resolve().is_relative_to(Path('.test-tmp').resolve())):
            raise ValueError('Fresh private .test-tmp output required')
        payload = self.capture(filters=filters)
        if not dry_run:
            serialized = json.dumps(payload, ensure_ascii=False)
            access = self._principal()  # Serialization must not outlive a revoked session.
            current = digest([access.scope, access.actor_id, sorted(access.resource_ids),
                              access.owner_id, access.allow_public, access.allow_owned_unreviewed,
                              access.allow_shared_unreviewed])
            if current != payload['principal_fingerprint']:
                raise SearchError('EVALUATION_ACCESS_CHANGED', 403)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents clobbering an operator/reviewer artifact.
            with path.open('x', encoding='utf-8') as stream:
                stream.write(serialized)
        snapshot = payload['snapshot']
        return {'status': 'PARTIAL_FIXTURE_ONLY' if self.environment == 'isolated_fixture' else 'EXPORTED_NOT_ACCEPTED',
            'dry_run': dry_run, 'bags': len(snapshot['bags']),
            'versions': len({b['version_id'] for b in snapshot['bags']}),
            'resources': len({b['resource_id'] for b in snapshot['bags']}), 'checksum': payload['checksum']}
