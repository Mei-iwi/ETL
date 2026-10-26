from data_sync_etl.application.processing_state import processing_state_from_stages
from data_sync_etl.ports.repositories import UnitOfWork


class ListResources:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    def run(
            self,
            *,
            page: int = 1,
            size: int = 20,
            q: str | None = None,
            subject_id: str | None = None,
            grade_level_id: str | None = None,
            resource_type_code: str | None = None,
            allowed_resource_ids: frozenset[str] | None = None,
            deleted_only: bool = False,
    ) -> dict:

        offset = (page - 1) * size

        with self.uow() as repo:
            rows, total = repo.list_resources(
                keyword = q,
                subject_id = subject_id,
                grade_level_id = grade_level_id,
                resource_type_code = resource_type_code,
                offset = offset,
                limit = size,
                **({'deleted_only': True} if deleted_only else {}),
                **({'allowed_resource_ids': allowed_resource_ids} if allowed_resource_ids is not None else {}),
            )
            summaries = repo.resource_version_summaries([row['id'] for row in rows])

        def latest_version(resource_id):
            summary = summaries.get(resource_id)
            if summary is None:
                return None
            return {key: summary[key] for key in ('id', 'version_number', 'created_at', 'lifecycle_status', 'source_version_id')} | {
                'processing_status': processing_state_from_stages(summary['job'], summary['stages']) if summary['job'] else 'NOT_STARTED'}

        items = [
            {
                'id': row['id'],
                'title': row['title'],
                'provider_name': row['provider_name'],
                'resource_type_code': row['resource_type_code'],
                'resource_type_name_vi': row['resource_type_name_vi'],
                'publication_status': row['publication_status'],
                'updated_at': row['updated_at'],
                'deletion_revision': row.get('deletion_revision', 0),
                'master_deleted_at': row.get('master_deleted_at'),
                'latest_version': latest_version(row['id']),
            }
            for row in rows
        ]

        total_pages = (total + size - 1) // size

        return {
            'items': items,
            'pagination': {
                'page': page,
                'size': size,
                'total': total,
                'total_pages': total_pages
            }
        }
