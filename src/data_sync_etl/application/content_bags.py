"""Bag roots must be real PostgreSQL ContentUnits, never synthetic Chunk entities."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import (
    ContentBagDocument,
    ContentUnitDocument,
    DocumentId,
    Int32,
    normalize_keywords,
)
from data_sync_etl.ports.mongo_foundation import EmbeddingPort


class StructuredUnit(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)
    id: DocumentId
    resource_version_id: DocumentId
    parent_unit_id: DocumentId | None
    unit_type: str
    sequence_no: Int32
    title: str | None = None
    retrieval_eligible: bool = False


@dataclass
class BagBuildResult:
    status: str
    reason: str | None
    bags: list[ContentBagDocument]


def bag_structure(units):
    """Validate the entire tree, return selected roots and their descendant leaf IDs."""
    nodes = {u['id']: u for u in units}
    if len(nodes) != len(units) or len({u['resource_version_id'] for u in units}) > 1:
        raise DomainError('Invalid hierarchy membership')
    children = {key: [] for key in nodes}
    for node in units:
        parent = node['parent_unit_id']
        if parent is not None:
            if parent not in nodes:
                raise DomainError('Missing or cross-version parent')
            children[parent].append(node['id'])
    done = set()
    for key in nodes:
        path = set()
        current = key
        while current is not None and current not in done:
            if current in path:
                raise DomainError('Cycle in hierarchy')
            path.add(current)
            current = nodes[current]['parent_unit_id']
        done.update(path)
    kind = next((k for k in ('CHAPTER', 'LESSON', 'SECTION')
                 if any(u['unit_type'] == k for u in units)), None)
    roots = {}
    for node in units:
        if node['unit_type'] != kind:
            continue
        parent = node['parent_unit_id']
        while parent is not None and nodes[parent]['unit_type'] != kind:
            parent = nodes[parent]['parent_unit_id']
        if parent is not None:
            continue
        if not children[node['id']]:
            raise DomainError('Bag root requires descendant content leaves')
        pending, leaves = [node['id']], []
        while pending:
            current = pending.pop()
            if children[current]:
                pending.extend(children[current])
            else:
                if nodes[current]['unit_type'] in ('CHAPTER', 'LESSON', 'SECTION'):
                    raise DomainError('Outline node requires descendant content leaves')
                leaves.append(current)
        roots[node['id']] = sorted(leaves)
    return roots


class ContentBagBuilder:
    def __init__(self, embedding: EmbeddingPort | None = None):
        self.embedding = embedding

    def build(
        self,
        version_id: str,
        units: list[StructuredUnit],
        leaves: list[ContentUnitDocument],
        *,
        generation: int,
        content_fingerprint: str,
    ) -> BagBuildResult:
        try:
            nodes = [StructuredUnit.model_validate(unit) for unit in units]
            content = [
                ContentUnitDocument.model_validate(
                    leaf.bson() if isinstance(leaf, ContentUnitDocument) else leaf
                )
                for leaf in leaves
            ]
        except ValidationError:
            raise DomainError("Invalid bag tree or leaf document") from None
        by_id = {u.id: u for u in nodes}
        if len(by_id) != len(nodes) or any(u.resource_version_id != version_id for u in nodes):
            raise DomainError("Duplicate or cross-version bag membership")
        children = {key: [] for key in by_id}
        for node in nodes:
            if node.parent_unit_id is not None:
                if node.parent_unit_id not in by_id:
                    raise DomainError("Missing or cross-version bag parent")
                children[node.parent_unit_id].append(node.id)
        # Walk parents iteratively; reject cycles even outside the selected root hierarchy.
        done = set()
        for node in nodes:
            path = set()
            current = node.id
            while current is not None and current not in done:
                if current in path:
                    raise DomainError("Cycle in content unit hierarchy")
                path.add(current)
                current = by_id[current].parent_unit_id
            done.update(path)
        leaf_by_id = {leaf.id: leaf for leaf in content}
        if len(leaf_by_id) != len(content):
            raise DomainError("Duplicate leaf membership")
        for leaf in content:
            if (
                leaf.id not in by_id
                or children[leaf.id]
                or leaf.resource_version_id != version_id
                or not leaf.is_current
                or leaf.projection_generation != generation
            ):
                raise DomainError("Non-leaf, stale or cross-version keyword membership")
        selected = next(
            (
                kind
                for kind in ("CHAPTER", "LESSON", "SECTION")
                if any(u.unit_type == kind for u in nodes)
            ),
            None,
        )
        if selected is None:
            return BagBuildResult("DEFERRED", "CHAPTER/LESSON/SECTION hierarchy unavailable", [])
        model = self.embedding
        if model is None:
            return BagBuildResult("BLOCKED", "Real embedding runtime not configured", [])
        if (
            not model.model_id.strip()
            or not model.version.strip()
            or not 1 <= model.dimension <= 4096
        ):
            raise DomainError("Invalid embedding runtime configuration")
        roots = []
        for node in nodes:
            if node.unit_type != selected:
                continue
            parent = node.parent_unit_id
            while parent is not None and by_id[parent].unit_type != selected:
                parent = by_id[parent].parent_unit_id
            if parent is None:
                roots.append(node)
        roots.sort(key=lambda u: (u.sequence_no, u.id))
        bags = []
        for root in roots:
            if not children[root.id]:
                return BagBuildResult('DEFERRED', 'Bag root requires descendant content leaves', [])
            pending = [root.id]
            collected = []
            while pending:
                current = pending.pop()
                if children[current]:
                    pending.extend(children[current])
                else:
                    if by_id[current].unit_type in ('CHAPTER', 'LESSON', 'SECTION'):
                        return BagBuildResult('DEFERRED', 'Outline node requires descendant content leaves', [])
                    leaf = leaf_by_id.get(current)
                    if leaf is None or leaf.keywords is None:
                        return BagBuildResult("DEFERRED", "Leaf keywords not supplied", [])
                    collected.extend(leaf.keywords)
            keywords = normalize_keywords(collected)
            if not keywords:
                return BagBuildResult("DEFERRED", "Leaf keyword extraction pending", [])
            text = " | ".join(keywords)
            vector = model.embed(text)
            if len(vector) != model.dimension:
                raise DomainError("Embedding dimension mismatch")
            stamp = now()
            try:
                bags.append(
                    ContentBagDocument(
                        _id=root.id,
                        resource_version_id=version_id,
                        unit_type=selected,
                        title=root.title,
                        keywords=keywords,
                        embedding_text=text,
                        embedding=vector,
                        embedding_model=f"{model.model_id}@{model.version}",
                        projection_generation=generation,
                        content_fingerprint=content_fingerprint,
                        is_current=True,
                        created_at=stamp,
                        updated_at=stamp,
                    )
                )
            except ValidationError:
                raise DomainError("Invalid model embedding output") from None
        return BagBuildResult("BUILT", None, bags)
