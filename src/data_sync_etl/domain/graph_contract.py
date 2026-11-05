"""Light-node contract. Graph keys are business IDs, never internal Neo4j IDs."""

from dataclasses import dataclass
from hashlib import sha256
from unicodedata import normalize

LABELS = ('EducationLevel', 'GradeLevel', 'Subject', 'LearningResource',
          'ResourceVersion', 'ContentUnit')
RELATIONS = (
    ('EducationLevel', 'HAS_GRADE', 'GradeLevel'),
    ('GradeLevel', 'HAS_SUBJECT', 'Subject'),
    ('LearningResource', 'HAS_SUBJECT', 'Subject'),
    ('LearningResource', 'FOR_GRADE', 'GradeLevel'),
    ('LearningResource', 'HAS_VERSION', 'ResourceVersion'),
    ('ResourceVersion', 'CONTAINS', 'ContentUnit'),
    ('ContentUnit', 'HAS_CHILD', 'ContentUnit'),
    ('ResourceVersion', 'DERIVED_FROM', 'ResourceVersion'),
)


class GraphContractError(ValueError):
    """Codes only: callers must not echo source data or connection exceptions."""


def graph_id(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 30
            or value != value.strip() or value != normalize('NFC', value)
            or any(ord(ch) < 33 or ord(ch) == 127 for ch in value)
            or '://' in value):
        raise GraphContractError('GRAPH_ID_INVALID')
    return value


@dataclass(frozen=True)
class GraphSnapshot:
    nodes: frozenset[tuple[str, str]]
    edges: frozenset[tuple[str, str, str, str, str]]

    def __post_init__(self):
        for label, key in self.nodes:
            if label not in LABELS:
                raise GraphContractError('GRAPH_LABEL_INVALID')
            graph_id(key)
        for left, lid, relation, right, rid in self.edges:
            if (left, relation, right) not in RELATIONS:
                raise GraphContractError('GRAPH_RELATION_INVALID')
            if (left, lid) not in self.nodes or (right, rid) not in self.nodes:
                raise GraphContractError('GRAPH_ORPHAN_EDGE')

    @property
    def fingerprint(self):
        import json
        return sha256(json.dumps([sorted(self.nodes), sorted(self.edges)],
                                 separators=(',', ':')).encode()).hexdigest()

    def counts(self):
        return {
            'nodes': {label: sum(n[0] == label for n in self.nodes) for label in LABELS},
            'edges': {'/'.join(shape): sum((e[0], e[2], e[3]) == shape for e in self.edges)
                      for shape in RELATIONS},
        }


def _indexed(rows):
    result = {}
    for row in rows:
        key = graph_id(row['id'])
        if key in result:
            raise GraphContractError('GRAPH_DUPLICATE_ID')
        result[key] = row
    return result


def _acyclic(parents):
    done = set()
    for start in parents:
        seen, key = set(), start
        while key is not None and key not in done:
            if key in seen:
                raise GraphContractError('GRAPH_PARENT_CYCLE')
            seen.add(key)
            key = parents[key]
        done.update(seen)


def build_snapshot(tables):
    """Reject the complete bad snapshot before any graph mutation."""
    grades = _indexed(tables['master_grade_levels'])
    subjects = _indexed(tables['subjects'])
    resources = _indexed(tables['master_learning_resources'])
    versions = _indexed(tables['resource_versions'])
    units = _indexed(tables['content_units'])
    nodes = set()
    for label, rows in [('GradeLevel', grades), ('Subject', subjects),
                        ('LearningResource', resources), ('ResourceVersion', versions),
                        ('ContentUnit', units)]:
        nodes.update((label, key) for key in rows)
    edges, education = set(), {}

    def edge(shape, left, right):
        value = (shape[0], graph_id(left), shape[1], shape[2], graph_id(right))
        if value in edges:
            raise GraphContractError('GRAPH_DUPLICATE_EDGE')
        edges.add(value)

    for key, grade in grades.items():
        eid = graph_id(grade['education_level_id'])
        identity = (grade.get('source_namespace'), grade.get('source_backend'),
                    grade['education_code'], grade['education_name_vi'])
        if eid in education and education[eid] != identity:
            raise GraphContractError('GRAPH_EDUCATION_IDENTITY_CONFLICT')
        education[eid] = identity
        nodes.add(('EducationLevel', eid))
        edge(RELATIONS[0], eid, key)
    for key, subject in subjects.items():
        if subject['grade_id'] is not None:
            edge(RELATIONS[1], subject['grade_id'], key)
    for row in tables['resource_subjects']:
        edge(RELATIONS[2], row['resource_id'], row['subject_id'])
    for row in tables['resource_grade_levels']:
        edge(RELATIONS[3], row['resource_id'], row['grade_level_id'])
    for key, version in versions.items():
        edge(RELATIONS[4], version['resource_id'], key)
        parent = version['parent_version_id']
        if parent is not None:
            if parent not in versions or versions[parent]['resource_id'] != version['resource_id']:
                raise GraphContractError('GRAPH_VERSION_PARENT_INVALID')
            edge(RELATIONS[7], key, parent)
    for key, unit in units.items():
        edge(RELATIONS[5], unit['resource_version_id'], key)
        parent = unit['parent_unit_id']
        if parent is not None:
            if parent not in units or units[parent]['resource_version_id'] != unit['resource_version_id']:
                raise GraphContractError('GRAPH_UNIT_PARENT_INVALID')
            edge(RELATIONS[6], parent, key)
    _acyclic({key: u['parent_unit_id'] for key, u in units.items()})
    _acyclic({key: v['parent_version_id'] for key, v in versions.items()})
    return GraphSnapshot(frozenset(nodes), frozenset(edges))
