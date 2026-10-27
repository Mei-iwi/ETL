"""Bounded read-only inventory of the entire derived root, including audit/state."""
import os
from pathlib import Path
from time import monotonic

from data_sync_etl.ports.concept_corpus import ConceptBlocked


class RootCapacity:
    def __init__(self, root, maximum, *, max_entries=20000, seconds=1.):
        self.root, self.maximum = Path(root), maximum
        self.max_entries, self.seconds = max_entries, seconds

    def snapshot(self):
        started, pending, total, entries, categories = monotonic(), [self.root], 0, 0, {}
        try:
            while pending:
                folder = pending.pop()
                with os.scandir(folder) as source:
                    for entry in source:
                        entries += 1
                        if entries > self.max_entries or monotonic() - started > self.seconds:
                            raise ConceptBlocked('CAPACITY_SCAN_BUDGET')
                        path = Path(entry.path)
                        if entry.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                            raise ConceptBlocked('ARTIFACT_LINK_FORBIDDEN')
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(path)
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            group = path.relative_to(self.root).parts[0]
                            categories[group] = categories.get(group, 0) + size
                            total += size
                        else:
                            raise ConceptBlocked('CAPACITY_UNKNOWN_ENTRY')
        except OSError:
            raise ConceptBlocked('CAPACITY_UNAVAILABLE') from None
        return {'logical_file_bytes': total, 'limit_bytes': self.maximum, 'entries': entries,
            'categories': categories, 'within_quota': total <= self.maximum,
            'hardlinks_counted_per_path': True, 'scan_seconds': monotonic() - started}
