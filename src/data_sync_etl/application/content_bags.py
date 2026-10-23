"""Bag roots must be real PostgreSQL ContentUnits, never synthetic Chunk entities."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, ValidationError

from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.lexical import lexical_document
from data_sync_etl.domain.meaning_bags import build_meanings
from data_sync_etl.domain.mongo_documents import (
    FALLBACK_BAG_TYPES,
    STRUCTURED_BAG_TYPES,
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
    def order(key):
        return nodes[key].get('sequence_no', 0), key
    # Iterative postorder computes the highest structural level in each subtree.
    traversal, pending = [], sorted((key for key in nodes if nodes[key]['parent_unit_id'] is None), key=order)
    while pending:
        key = pending.pop()
        traversal.append(key)
        pending.extend(children[key])
    rank = {kind: index for index, kind in enumerate(STRUCTURED_BAG_TYPES)}
    best = {}
    for key in reversed(traversal):
        best[key] = min([rank.get(nodes[key]['unit_type'], 99), *[best[ch] for ch in children[key]]])
        if nodes[key]['unit_type'] in rank and not children[key]:
            raise DomainError('Outline node requires descendant content leaves')
    roots, covered = {}, set()
    pending = sorted((key for key in nodes if nodes[key]['parent_unit_id'] is None), key=order, reverse=True)
    while pending:
        key = pending.pop()
        kind = nodes[key]['unit_type']
        if kind in rank and rank[kind] == best[key]:
            descendants, leaves = [key], []
            while descendants:
                current = descendants.pop()
                if children[current]:
                    descendants.extend(children[current])
                elif nodes[current].get('text', '').strip():
                    leaves.append(current)
            if leaves:
                roots[key] = sorted(leaves, key=order)
                covered.update(leaves)
            continue
        pending.extend(sorted(children[key], key=order, reverse=True))
    for key in sorted(nodes, key=order):
        node = nodes[key]
        if (not children[key] and key not in covered and node['unit_type'] in FALLBACK_BAG_TYPES
                and node.get('text', '').strip()):
            roots[key] = [key]
    return dict(sorted(roots.items(), key=lambda item: order(item[0])))


def representation_mode(units, roots):
    by_id = {u['id']: u for u in units}
    modes = {'structured' if by_id[key]['unit_type'] in STRUCTURED_BAG_TYPES else 'fallback' for key in roots}
    return next(iter(modes)) if len(modes) == 1 else 'mixed' if modes else 'unavailable'


class ContentBagBuilder:
    def __init__(self, embedding: EmbeddingPort | None = None, *, meaning_threshold=None):
        self.embedding = embedding
        self.meaning_threshold = meaning_threshold

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
        roots = bag_structure([{**u.model_dump(), 'text': leaf_by_id[u.id].text if u.id in leaf_by_id else ''}
                               for u in nodes])
        if not roots:
            return BagBuildResult("DEFERRED", "No eligible content leaves", [])
        if any(leaf_by_id[key].keywords is None for membership in roots.values() for key in membership):
            return BagBuildResult('DEFERRED', 'Leaf keywords not supplied', [])
        roots = {root: membership for root, membership in roots.items()
                 if any(leaf_by_id[key].keywords for key in membership)}
        if not roots:
            return BagBuildResult('DEFERRED', 'No keyword-bearing content roots', [])
        model = self.embedding
        if model is None:
            return BagBuildResult("BLOCKED", "Real embedding runtime not configured", [])
        if (
            not model.model_id.strip()
            or not model.version.strip()
            or not 1 <= model.dimension <= 4096
        ):
            raise DomainError("Invalid embedding runtime configuration")
        bags = []
        for root_id, membership in roots.items():
            root = by_id[root_id]
            collected = []
            for key in membership:
                leaf = leaf_by_id.get(key)
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
            meanings = (build_meanings([(key, leaf_by_id[key].text) for key in membership],
                                       root.id, root.title, model, self.meaning_threshold)
                        if self.meaning_threshold is not None else None)
            try:
                bags.append(
                    ContentBagDocument(
                        _id=root.id,
                        resource_version_id=version_id,
                        unit_type=root.unit_type,
                        title=root.title,
                        keywords=keywords,
                        lexical=lexical_document([leaf_by_id[key].text for key in membership], root.title),
                        **({'meanings': meanings} if meanings is not None else {}),
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
