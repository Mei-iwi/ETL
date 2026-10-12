from math import ceil

from data_sync_etl.ports.subject_reader import SubjectReader


class ListSubjectsUseCase:
    def __init__(self, reader: SubjectReader):
        self.__reader = reader

    def execute(
        self,
        *,
        page: int = 1,
        size: int = 20,
        keyword: str | None = None,
        grade_level_id: str | None = None,
    ) -> dict:
        if page < 1:
            raise ValueError("page must be >=1")
        if not 1 <= size <= 100:
            raise ValueError("size must be between 1 and 100")
        keyword = keyword.strip() if keyword else None
        keyword = keyword or None
        grade_level_id = grade_level_id.strip() if grade_level_id else None
        grade_level_id = grade_level_id or None

        items, total = self.__reader.list_subjects(
            keyword=keyword, grade_level_id=grade_level_id, offset=(page - 1) * size, limit=size
        )
        result = {
            "items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "slug": item.slug,
                    "grade_level_id": item.grade_level_id,
                }
                for item in items
            ],
            "pagination": {
                "page": page,
                "size": size,
                "total": total,
                "total_pages": ceil(total / size) if total else 0,
            },
        }
        if not items:
            result["message"] = "Không có môn học nào"
        return result
