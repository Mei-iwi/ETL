"""Fail-closed authorization for an independently approved SOURCE OCR contract.

Database privileges and metadata write contracts never grant OCR authority.
This policy carries no database credentials and does not perform writes.
"""
import re
from dataclasses import dataclass

from data_sync_etl.domain.core import DomainError

_TABLES = frozenset({'ocr_jobs', 'ocr_page_tasks', 'content_units'})
_ACTIONS = frozenset({'select', 'insert', 'update'})
_LIFECYCLES = frozenset({'UPLOADED', 'PENDING_PRE_REVIEW', 'QUEUED', 'PROCESSING',
                         'READY_FOR_REVIEW', 'APPROVED', 'PUBLISHED'})
_COLUMN = re.compile(r'^[a-z][a-z0-9_]{0,62}$')


def _strings(value):
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item or item != item.strip() for item in value
    ) or len(value) != len(set(value)):
        raise DomainError('SOURCE_OCR_CONTRACT_INVALID')
    return frozenset(value)


@dataclass(frozen=True)
class SourceOCRContract:
    namespace: str
    tables: frozenset[str]
    columns: tuple[tuple[str, frozenset[str]], ...]
    actions: frozenset[str]
    roles: frozenset[str]
    actor_ids: frozenset[str]
    version_ids: frozenset[str]
    lifecycles: frozenset[str]
    statement_timeout_seconds: int

    @classmethod
    def from_owner_contract(cls, value, *, expected_namespace):
        if (not isinstance(value, dict) or value.get('backend') != 'postgresql'
                or value.get('entity_type') != 'ocr_processing' or value.get('schema') != 'public'
                or not isinstance(expected_namespace, str) or not expected_namespace.strip()
                or value.get('namespace') != expected_namespace):
            raise DomainError('SOURCE_OCR_CONTRACT_MISMATCH')
        tables, actions = _strings(value.get('tables')), _strings(value.get('actions'))
        columns = value.get('columns')
        if not isinstance(columns, dict) or set(columns) != tables:
            raise DomainError('SOURCE_OCR_CONTRACT_INVALID')
        column_policy = tuple(sorted((table, _strings(fields)) for table, fields in columns.items()))
        roles, actors = _strings(value.get('allowed_roles')), _strings(value.get('allowed_actor_ids'))
        versions, lifecycles = _strings(value.get('allowed_version_ids')), _strings(value.get('allowed_lifecycles'))
        timeout = value.get('statement_timeout_seconds')
        if (not tables.issubset(_TABLES) or not actions.issubset(_ACTIONS)
                or any(not _COLUMN.fullmatch(field) for _, fields in column_policy for field in fields)
                or not lifecycles.issubset(_LIFECYCLES) or any(role != role.upper() for role in roles)
                or any(len(item) > 30 for item in (*actors, *versions))
                or type(timeout) is not int or not 1 <= timeout <= 600):
            raise DomainError('SOURCE_OCR_CONTRACT_INVALID')
        return cls(expected_namespace, tables, column_policy, actions, roles, actors, versions, lifecycles, timeout)

    def allows(self, *, namespace, table, action, fields, role, actor_id, source_version_id, lifecycle):
        """All dimensions must match; IDs must originate from trusted server mapping."""
        return (isinstance(fields, (set, frozenset)) and bool(fields)
                and fields.issubset(dict(self.columns).get(table, frozenset()))
                and namespace == self.namespace and table in self.tables and action in self.actions
                and role in self.roles and actor_id in self.actor_ids
                and source_version_id in self.version_ids and lifecycle in self.lifecycles)
