"""Offline Vector evaluation. Reuses the project's metrics, never changes BoW/RRF.

Inputs are frozen operator-provided corpus/qrels; no database or HTTP credentials.
Draft labels are allowed for exploratory reports but cannot imply acceptance.
"""
import math
from collections import defaultdict

from data_sync_etl.domain.evaluation import checksum, metrics, percentile
from data_sync_etl.domain.semantic import diverse_candidates as diverse_candidates


def evaluate_ranking(ranked, qrels):
    """Grade2/3 answers vs grade1 topical overlap; nDCG retains graded relevance."""
    binary = {key: grade if grade >= 2 else 0 for key, grade in qrels.items()}
    positive = any(binary.values())
    p1, p5 = metrics(ranked, binary, 1), metrics(ranked, binary, 5)
    at10, graded = metrics(ranked, binary, 10), metrics(ranked, qrels, 10)
    return {'precision1': p1['precision'], 'precision5': p5['precision'],
            'recall5': p5['recall'], 'mrr10': at10['mrr'], 'ndcg10': graded['ndcg'],
            'hit1': int(p1['precision'] > 0), 'hit5': int(p5['precision'] > 0),
            'has_positive': positive, 'abstained': not ranked}


def summarize(rows):
    positive = [row for row in rows if row['metrics']['has_positive']]
    negative = [row for row in rows if not row['metrics']['has_positive']]
    result = {'queries': len(rows), 'positive_queries': len(positive), 'no_answer_queries': len(negative)}
    for name in ('precision1', 'precision5', 'recall5', 'mrr10', 'ndcg10', 'hit1', 'hit5'):
        values = [row['metrics'][name] for row in positive if row['metrics'][name] is not None]
        result[name] = sum(values) / len(values) if values else None
    result['no_answer_false_positive_rate'] = (sum(not row['metrics']['abstained'] for row in negative) / len(negative)
                                               if negative else None)
    latency = [row['latency_ms'] for row in rows if row.get('latency_ms') is not None]
    result['p50_ms'], result['p95_ms'] = percentile(latency, .5), percentile(latency, .95)
    return result


def calibrate_threshold(validation_rows):
    """Max macro F1 on validation only; thresholds never selected from test rows.

    Explicit tie-break: recall then lower threshold. Reports are exploratory with
    draft labels. Caller must not automatically change runtime configuration.
    """
    if not validation_rows or any(row['split'] != 'validation' for row in validation_rows):
        raise ValueError('Validation-only calibration required')
    thresholds = sorted({0., 1., *[float(hit['score']) for row in validation_rows for hit in row['hits']]})
    best, observations = None, []
    for threshold in thresholds:
        f1, recalls = [], []
        for row in validation_rows:
            relevant = {key for key, grade in row['qrels'].items() if grade >= 2}
            retrieved = {hit['id'] for hit in row['hits'] if hit['score'] > threshold}
            if not relevant:
                f1.append(float(not retrieved))
                continue
            hits = len(relevant & retrieved)
            precision = hits / len(retrieved) if retrieved else 0.
            recall = hits / len(relevant)
            recalls.append(recall)
            f1.append(2 * precision * recall / (precision + recall) if precision + recall else 0.)
        cell = {'threshold': threshold, 'macro_f1': sum(f1) / len(f1),
                'recall': sum(recalls) / len(recalls) if recalls else 0.}
        observations.append(cell)
        if best is None or (cell['macro_f1'], cell['recall'], -threshold) > (
                best['macro_f1'], best['recall'], -best['threshold']):
            best = cell
    return {'selected': best, 'validation_query_ids': [row['query_id'] for row in validation_rows],
            'grid': observations}


def compile_qrels(corpus, specification):
    """Resolve reviewed evidence anchors to immutable version/chunk identifiers.

    Anchors are annotation aids only; they NEVER run on a production search path.
    No rankings/model scores are used as labels. Missing evidence is an error.
    """
    import unicodedata
    def normal(text):
        return ' '.join(unicodedata.normalize('NFC', text).casefold().split())
    resources = {row['resource_id']: row for row in corpus['resources']}
    queries, ids = [], set()
    for query in specification['queries']:
        if query['query_id'] in ids or query['split'] not in ('validation', 'test'):
            raise ValueError('Invalid query identity/split')
        ids.add(query['query_id'])
        resource_qrels, chunk_qrels, evidence = {}, {}, []
        for annotation in query.get('annotations', []):
            for resource_id in annotation['resource_ids']:
                resource = resources[resource_id]
                matched = []
                for chunk in resource['chunks']:
                    if any(normal(anchor) in normal(chunk['text']) for anchor in annotation['anchors']):
                        chunk_qrels[chunk['id']] = max(chunk_qrels.get(chunk['id'], 0), annotation['grade'])
                        matched.append(chunk['id'])
                        evidence.append({'resource_id': resource_id, 'resource_version_id': resource['version_id'],
                                         'chunk_id': chunk['id'], 'grade': annotation['grade']})
                if not matched:
                    raise ValueError('Annotation has no supporting chunk: ' + resource_id + '/' + query['query_id'])
                resource_qrels[resource_id] = max(resource_qrels.get(resource_id, 0), annotation['grade'])
        queries.append({key: query[key] for key in ('query_id', 'query', 'language', 'category', 'split')} |
                       {'resource_qrels': resource_qrels, 'chunk_qrels': chunk_qrels, 'evidence': evidence})
    return {'label_status': specification.get('label_status', 'DRAFT'), 'corpus_checksum': corpus['checksum'],
            'specification_checksum': checksum(specification), 'queries': queries}


def grouped_hits(scores, chunks):
    """Resource presentation: one best chunk, never merge distinct resource IDs."""
    grouped = {}
    for key in sorted(scores, key=lambda key: (-scores[key], key)):
        chunk = chunks[key]
        resource = chunk['resource_id']
        grouped.setdefault(resource, {'id': resource, 'score': scores[key],
            'chunk_id': chunk.get('chunk_id', key), 'resource_version_id': chunk['version_id']})
    return sorted(grouped.values(), key=lambda row: (-row['score'], row['id']))


def score_distribution(rows):
    groups = defaultdict(list)
    for row in rows:
        for hit in row['hits']:
            grade = row['qrels'].get(hit['id'], 0)
            groups['answer' if grade >= 2 else 'partial' if grade == 1 else 'unjudged_or_negative'].append(hit['score'])
    return {name: {'count': len(values), 'min': min(values), 'p50': percentile(values, .5),
                   'p95': percentile(values, .95), 'max': max(values)} for name, values in groups.items()}


def validate_scores(values):
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
        raise ValueError('Nonfinite ranking score')
