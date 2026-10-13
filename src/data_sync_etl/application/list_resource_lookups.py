from math import ceil

from data_sync_etl.ports.repositories import UnitOfWork


class ListResourceLookups:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    @staticmethod
    def _canonical_active_identity(
        group: list[dict],
        *,
        identity_fields: tuple[str, ...],
        active_field: str,
        deleted_field: str,
        freshness_field: str,
    ) -> tuple[str, ...] | None:
        """Return the most common valid identity in a denormalized lookup group.

        Master resources carry a snapshot of provider/type metadata. A stale
        resource row must not make the whole lookup disappear when another row
        still contains the current active metadata.
        """
        candidates: dict[tuple[str, ...], tuple[int, object]] = {}
        for row in group:
            if not row[active_field] or row[deleted_field] is not None:
                continue
            identity = tuple((row[field] or '').strip() for field in identity_fields)
            if not all(identity):
                continue
            count, latest = candidates.get(identity, (0, None))
            timestamp = row.get(freshness_field) or row.get("updated_at")
            if latest is None or (timestamp is not None and timestamp > latest):
                latest = timestamp
            candidates[identity] = (count + 1, latest)

        if not candidates:
            return None

        # Prefer the majority snapshot, then the freshest metadata snapshot.
        # Identity is a deterministic tie-breaker for legacy rows with equal data.
        return max(
            candidates,
            key=lambda identity: (
                candidates[identity][0],
                candidates[identity][1] is not None,
                candidates[identity][1],
                identity,
            ),
        )

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
            identity = self._canonical_active_identity(
                group,
                identity_fields=("resource_type_id", "resource_type_name_vi"),
                active_field="resource_type_is_active",
                deleted_field="resource_type_deleted_at",
                freshness_field="resource_type_updated_at",
            )
            if identity is None:
                continue
            _, name = identity
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
            identity = self._canonical_active_identity(
                group,
                identity_fields=("provider_name", "provider_type"),
                active_field="provider_is_active",
                deleted_field="provider_deleted_at",
                freshness_field="provider_updated_at",
            )
            if identity is None:
                continue
            name, provider_type = identity
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
