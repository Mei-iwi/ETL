"""Detached PostgreSQL authority rows for one initial or final search phase."""

from copy import deepcopy


class SearchAuthorityPhase:
    def __init__(self, records, histories, semantic=None):
        self.records, self.histories = records, histories
        self.semantic = semantic

    def bind(self, repository):
        return SearchAuthorityView(repository, self)


class SearchAuthorityView:
    def __init__(self, repository, phase):
        self.repository, self.phase = repository, phase

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def get(self, table, key, *, lock=False):
        if not lock and table in self.phase.records:
            return deepcopy(self.phase.records[table].get(key))
        return self.repository.get(table, key, lock=lock)

    def find(self, table, filters=None, *, order=(), limit=None, lock=False, skip_locked=False):
        filters = filters or {}
        layouts = {'ocr_page_tasks': ('job_id', ('page_num', 'id')),
                   'ocr_page_results': ('page_task_id', ()),
                   'content_units': ('resource_version_id', ('sequence_no', 'id'))}
        if not lock and not skip_locked and table in layouts:
            field, expected_order = layouts[table]
            if set(filters) == {field} and order == expected_order and (table == 'content_units' or limit == 1):
                rows = self.phase.histories[table].get(filters[field], [])
                return deepcopy(rows if limit is None else rows[:limit])
        if (not lock and not skip_locked and table in self.phase.histories
                and 'resource_version_id' in filters
                and set(filters) <= {'resource_version_id', 'stage'}
                and order == ('-created_at', '-id')
                and (table != 'ocr_jobs' or limit == 1)):
            rows = self.phase.histories[table].get(filters['resource_version_id'], [])
            rows = [row for row in rows if all(row.get(key) == value for key, value in filters.items())]
            return deepcopy(rows if limit is None else rows[:limit])
        return self.repository.find(table, filters, order=order, limit=limit, lock=lock, skip_locked=skip_locked)

    def search_unit_texts(self, unit_ids):
        if len(unit_ids) > 10000 or len(set(unit_ids)) != len(unit_ids):
            return self.repository.search_unit_texts(unit_ids)
        return {key: deepcopy(self.phase.records['content_unit_texts'][key]) for key in unit_ids
                if key in self.phase.records['content_unit_texts']}

    def semantic_metadata(self, resource_id):
        if self.phase.semantic is not None and resource_id in self.phase.semantic:
            return deepcopy(self.phase.semantic[resource_id])
        return self.repository.semantic_metadata(resource_id)
