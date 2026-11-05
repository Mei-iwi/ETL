"""Hydrate a bounded, reauthorized candidate set using existing Search policy."""

from data_sync_etl.domain.metadata_search import metadata_allowed, metadata_mapping_allowed


class RelatedLearningAuthority:
    def __init__(self, uow, search, mapping, settings):
        self.uow, self.search, self.mapping, self.settings = uow, search, mapping, settings

    def read(self, seed, access):
        with self.uow() as repo:
            selected = repo.metadata_snapshots({'resource_id': seed}, access, limit=1)
            others = repo.metadata_snapshots({}, access, limit=100)
        rows = {entry[0]['resource_id']: entry for entry in [*others, *selected]}
        rows = {key: value for key, value in rows.items()
                if metadata_allowed(value[1], value[2], access)
                and metadata_mapping_allowed(value[1], value[2], value[4],
                    self.settings.source_file_allowed_statuses)}
        allowed = {row['resource_id'] for row in self.search._authorized_metadata(
            [value[0] for value in rows.values()], access)}
        if seed not in allowed:
            return {}
        ids = [seed, *sorted(allowed - {seed})[:99]]
        relations = self.mapping.resource_relations(ids)
        result = {}
        for key in ids:
            row, resource, version, _, _ = rows[key]
            result[key] = {
                'title': row['resource_title'], 'version_id': version['id'],
                'source_hash': version['source_hash'],
                'subject_ids': relations[key]['subject_ids'],
                'grade_ids': relations[key]['grade_ids'],
            }
        return result
