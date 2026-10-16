"""Resource-level evaluation; independent of transport, persistence and Search."""
import hashlib
import json
import math
import re

METHODS = ('bow', 'vector', 'hybrid')
OPAQUE_ID = re.compile(r'^[0-7][0-9A-HJKMNP-TV-Z]{25}$')


class EvaluationError(ValueError):
    def __init__(self, code, status=422):
        self.code, self.status = code, status
        super().__init__(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def validate_id(value):
    if not isinstance(value, str) or not OPAQUE_ID.fullmatch(value):
        raise EvaluationError('EVALUATION_NOT_FOUND', 404)
    return value


def metrics(ranked, qrels, k):
    ranked = list(dict.fromkeys(ranked))[:k]
    positives = {key for key, grade in qrels.items() if grade >= 1}
    hits = sum(key in positives for key in ranked)
    reciprocal = next((1 / rank for rank, key in enumerate(ranked, 1) if key in positives), 0.)
    dcg = sum((2 ** qrels.get(key, 0) - 1) / math.log2(rank + 1) for rank, key in enumerate(ranked, 1))
    ideal = sum((2 ** grade - 1) / math.log2(rank + 1)
                for rank, grade in enumerate(sorted(qrels.values(), reverse=True)[:k], 1))
    return {'precision': hits / k, 'recall': hits / len(positives) if positives else None,
            'mrr': reciprocal if positives else None, 'ndcg': dcg / ideal if ideal else None}


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def aggregate(observations, k_values):
    result = {}
    for method in METHODS:
        rows = [row for row in observations if row['method'] == method]
        valid = [row for row in rows if row['error_code'] is None]
        latency = [row['latency_ms'] for row in valid]
        by_k = {}
        for k in k_values:
            cells = [row['metrics'][str(k)] for row in valid]
            by_k[str(k)] = {}
            for name in ('precision', 'recall', 'mrr', 'ndcg'):
                values = [cell[name] for cell in cells if cell[name] is not None]
                by_k[str(k)][name] = {'value': sum(values) / len(values) if values else None,
                                     'valid_queries': len(values), 'excluded_queries': len(cells) - len(values)}
        result[method] = {'successful_queries': len(valid), 'failed_queries': len(rows) - len(valid),
                          'latency_ms': {'mean': sum(latency) / len(latency) if latency else None,
                                         'p50': percentile(latency, .5), 'p95': percentile(latency, .95)},
                          'metrics': by_k}
    return result


def csv_cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text
