from typing import Any, Protocol


class ContentDocumentPort(Protocol):
    def upsert_ocr_page(self, document: dict[str, Any]) -> None: ...

    def upsert_content_unit(self, document: dict[str, Any]) -> None: ...

    def reconcile_content_units(
        self, version_id: str, unit_ids: list[str], generation: int | None = None,
    ) -> None: ...
