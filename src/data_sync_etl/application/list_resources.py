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
            )

        items = [
            {
                'id': row['id'],
                'title': row['title'],
                'provider_name': row['provider_name'],
                'resource_type_code': row['resource_type_code'],
                'resource_type_name_vi': row['resource_type_name_vi'],
                'publication_status': row['publication_status'],
                'updated_at': row['updated_at'],
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