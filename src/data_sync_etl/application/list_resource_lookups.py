from math import ceil

from data_sync_etl.ports.repositories import UnitOfWork


class ListResourceLookups:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    @staticmethod
    def _paginate(items: list[dict], page: int, size: int) -> dict:
        total = len(items)
        offset = (page - 1) * size
        return {
            "items": items[offset : offset + size],
            "pagination": {
                "page": page,
                "size": size,
                "total": total,
                "total_pages": ceil(total / size) if total else 0,
            },
        }

    @staticmethod
    def _matches(query: str | None, *values: str) -> bool:
        if not query:
            return True
        term = query.strip().casefold()
        return not term or any(term in value.casefold() for value in values)

    def list_resource_types(self, *, page: int, size: int, query: str | None) -> dict:
        with self.uow() as repo:
            rows = repo.find("master_learning_resources")

        grouped: dict[str, list[dict]] = {}
        for row in rows:
            code = row["resource_type_code"].strip()
            if code:
                grouped.setdefault(code, []).append(row)

        items = []
        for code, group in grouped.items():
            identities = {
                (row["resource_type_id"], row["resource_type_name_vi"].strip())
                for row in group
            }
            valid = all(
                row["resource_type_is_active"] and row["resource_type_deleted_at"] is None
                for row in group
            )
            if len(identities) != 1 or not valid:
                continue
            _, name = next(iter(identities))
            if name and self._matches(query, code, name):
                items.append({"code": code, "name": name})

        items.sort(key=lambda item: (item["name"].casefold(), item["code"]))
        return self._paginate(items, page, size)

    def list_providers(self, *, page: int, size: int, query: str | None) -> dict:
        with self.uow() as repo:
            rows = repo.find("master_learning_resources")

        grouped: dict[str, list[dict]] = {}
        for row in rows:
            provider_id = (row["provider_id"] or "").strip()
            if provider_id:
                grouped.setdefault(provider_id, []).append(row)

        items = []
        for provider_id, group in grouped.items():
            identities = {
                (
                    (row["provider_name"] or "").strip(),
                    (row["provider_type"] or "").strip(),
                )
                for row in group
            }
            valid = all(
                row["provider_is_active"] and row["provider_deleted_at"] is None
                for row in group
            )
            if len(identities) != 1 or not valid:
                continue
            name, provider_type = next(iter(identities))
            if name and provider_type and self._matches(query, provider_id, name):
                items.append(
                    {
                        "id": provider_id,
                        "name": name,
                        "provider_type": provider_type,
                    }
                )

        items.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        return self._paginate(items, page, size)

    def list_grade_levels(self, *, page: int, size: int, query: str | None) -> dict:
        with self.uow() as repo:
            rows = repo.find("master_grade_levels")

        items = [
            {
                "id": row["id"],
                "grade_code": row["grade_code"],
                "grade_name_vi": row["grade_name_vi"],
            }
            for row in rows
            if row["grade_is_active"]
            and row["grade_deleted_at"] is None
            and self._matches(query, row["grade_code"], row["grade_name_vi"])
        ]
        items.sort(key=lambda item: (item["grade_name_vi"].casefold(), item["id"]))
        return self._paginate(items, page, size)
