"""Phase 2 frozen exploratory fusion/evidence/no-answer experiments, no DB.

Private output contains locators and text. Only aggregate/aliased reports may
be published. Existing test labels are never used for automatic deployment.
"""
import argparse
import hashlib
import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from time import perf_counter

from data_sync_etl.application.vector_quality import compile_qrels
from data_sync_etl.domain.concepts import concept_analysis_scope, empty_dictionary
from data_sync_etl.domain.context_ranking import content_terms
from data_sync_etl.domain.evaluation import checksum
from data_sync_etl.domain.evidence_selection import EvidenceCandidate, select_evidence
from data_sync_etl.domain.lexical import (
    analysis_scope,
    bm25,
    lexical_document,
    query_terms,
    snippet_text,
    surface_tokens,
)
from data_sync_etl.domain.retrieval_fusion import (
    query_form,
    vector_priority_rrf,
    version_budget,
    version_rrf,
)
from data_sync_etl.domain.search import normalize_query, ranks, rrf
from scripts.benchmark_hybrid_snapshot import load, peak_memory_bytes, quality, summary
from scripts.optimize_semantic_quality import check_vectors, inputs


def frozen(args):
    corpus, spec, prepared = load(args.corpus), load(args.spec), load(args.prepared)
    if (args.corpus.stat().st_size > 64 * 1024 * 1024 or len(corpus['resources']) > 100
            or len(spec['queries']) > 100 or checksum({k: v for k, v in corpus.items() if k != 'checksum'}) != corpus['checksum']):
        raise ValueError('Bounded verified corpus required')
    qrels = compile_qrels(corpus, spec)
    freeze = load(args.spec.with_name('freeze.json'))
    if (freeze['corpus_checksum'] != corpus['checksum'] or freeze['qrels_checksum'] != checksum(qrels)
            or freeze['specification_checksum'] != checksum(spec)
            or prepared['corpus_checksum'] != corpus['checksum']
            or prepared['specification_checksum'] != checksum(spec)):
        raise ValueError('Frozen input mismatch')
    matrix, artifacts = {}, {}
    for name, texts, entry, tag in (
        ('context', inputs(corpus, 'verified_full_context')[0], 'verified_full_context', 'contextual'),
        ('raw', inputs(corpus, 'raw_content')[0], 'raw_content', 'raw'),
        ('query', [normalize_query(q['query']) for q in qrels['queries']], 'new_queries', 'optimization_queries')):
        record = prepared['artifacts'][entry]
        if checksum([corpus['checksum'], prepared['model'], texts, tag]) != record['identity']:
            raise ValueError('Vector input/order identity mismatch')
        matrix[name] = check_vectors(Path(record['path']), len(texts))
        artifacts[name] = {'input_identity': record['identity'],
            'file_sha256': hashlib.sha256(Path(record['path']).read_bytes()).hexdigest()}
    _, chunks = inputs(corpus, 'raw_content')
    if len(chunks) > 2000:
        raise ValueError('Bounded chunks required')
    return corpus, qrels, prepared['model'], chunks, matrix, artifacts


def resource_order(scores, representatives, bags, versions):
    order, winners = [], {}
    for bag in sorted(scores, key=lambda k: (-scores[k], k != representatives.get(versions[k], k), k)):
        resource = bags[bag]['resource_id']
        if resource not in winners:
            order.append(resource)
            winners[resource] = bag
    return order, winners


def evaluate_row(query, method, selector, order, selected, chunk_order, elapsed, candidate_ids):
    snippets = [selected[r] for r in order if r in selected]
    resource_metric = quality(order, query['resource_qrels'])
    correct_resource = bool(order and query['resource_qrels'].get(order[0], 0) >= 2)
    grade = query['chunk_qrels'].get(snippets[0], 0) if snippets else None
    return {'query_id': query['query_id'], 'language': query['language'], 'category': query['category'],
        'split': query['split'], 'method': method, 'selector': selector, 'resource': resource_metric,
        'snippet': quality(snippets, query['chunk_qrels']), 'chunk': quality(chunk_order, query['chunk_qrels']),
        'ranking_ms': [elapsed], 'resource_order': order, 'selected_chunks': selected,
        'candidate_recall': quality(candidate_ids, query['resource_qrels'])['candidate_recall'],
        'resource_correct_evidence_wrong': correct_resource and (grade is None or grade < 2),
        'evidence_grade': grade, 'evidence_category': 'no_evidence' if grade is None else
            'direct' if grade >= 2 else 'partial_or_topic' if grade == 1 else 'unjudged_or_wrong'}


def rendered_grade(text, resource_id, annotations):
    def normalized(value):
        return ' '.join(unicodedata.normalize('NFC', value).casefold().split())
    rendered = normalized(text)
    return max((annotation['grade'] for annotation in annotations
                if resource_id in annotation['resource_ids']
                and any(normalized(anchor) in rendered for anchor in annotation['anchors'])), default=0)


def run(args):
    if args.output.exists() or not args.output.resolve().is_relative_to(Path('.test-tmp').resolve()):
        raise ValueError('New private output required')
    corpus, qrels, model, chunks, matrices, artifacts = frozen(args)
    annotations = {q['query_id']: q.get('annotations', []) for q in load(args.spec)['queries']}
    if not args.include_unreviewed_for_offline_analysis and any(
            c['review_status'] not in ('APPROVED', 'REVIEWED') or not c['retrieval_eligible'] for c in chunks.values()):
        raise ValueError('Explicit offline unreviewed-text study required; not runtime permission')
    keys = list(chunks)
    by_bag = defaultdict(list)
    bags, versions = {}, {}
    for resource in corpus['resources']:
        for bag in resource['bags']:
            bags[bag['id']] = {'resource_id': resource['resource_id'], 'version_id': resource['version_id']}
            versions[bag['id']] = resource['version_id']
    for key, chunk in chunks.items():
        if chunk['bag_id'] in bags:
            by_bag[chunk['bag_id']].append(key)
    lexical = {bag: lexical_document([chunks[k]['text'] for k in members]) for bag, members in by_bag.items()}
    dictionary = empty_dictionary()
    bm25('warmup', lexical)
    rows, cases, no_answer, profiles = [], [], [], []
    for qi, query in enumerate(qrels['queries']):
        started = perf_counter()
        with analysis_scope(), concept_analysis_scope():
            t = perf_counter()
            keywords = query_terms(query['query'])
            expansion = dictionary.expand(query['query'])
            expansion_ms = (perf_counter() - t) * 1000
            t = perf_counter()
            bow = bm25(query['query'], lexical, dictionary=dictionary, expansion=expansion)
            lexical_ms = (perf_counter() - t) * 1000
            t = perf_counter()
            context = dict(zip(keys, map(float, matrices['context'] @ matrices['query'][qi]), strict=True))
            raw = dict(zip(keys, map(float, matrices['raw'] @ matrices['query'][qi]), strict=True))
            dense, best = {}, {}
            for bag, members in by_bag.items():
                best[bag] = min(members, key=lambda k: (-context[k], k))
                if context[best[bag]] > 0.:
                    dense[bag] = context[best[bag]]
            dense_ms = (perf_counter() - t) * 1000
            pools = defaultdict(list)
            for key in keys:
                if context[key] > 0. and chunks[key]['bag_id'] in bags:
                    c = chunks[key]
                    pools[c['version_id']].append(EvidenceCandidate(c['version_id'], c['bag_id'], c, context[key]))
            arms = {'BoW': (bow, {}), 'Vector': (dense, {}), 'F0': (rrf(bow, dense), {})}
            base = {'bow': bow, 'vector': dense}
            t = perf_counter()
            configs = {'F1': None, 'F2-075': {'bow': .75, 'vector': 1.},
                       'F2-050': {'bow': .5, 'vector': 1.}, 'F2-125': {'bow': 1., 'vector': 1.25}}
            for name, weights in configs.items():
                fused = version_rrf(base, versions, weights=weights)
                arms[name] = fused.scores, fused.representatives
            priority = vector_priority_rrf(query['query'], base, versions)
            arms['F3'] = priority.scores, priority.representatives
            form = query_form(query['query'])
            weight = 1. if form in ('identifier', 'technical', 'acronym') else .75 if form == 'short' else .5
            aware = version_rrf(base, versions, weights={'bow': weight, 'vector': 1.})
            arms['F4'] = aware.scores, aware.representatives
            budget_recall = {}
            for limit in (5, 10, 20):
                for diversity in (False, True):
                    branches = {name: version_budget(branch, versions, limit) if diversity else
                                dict(sorted(branch.items(), key=lambda row: (-row[1], row[0]))[:limit])
                                for name, branch in base.items()}
                    fused = version_rrf(branches, versions)
                    name = f'F5-{limit}-' + ('version' if diversity else 'bag')
                    arms[name] = fused.scores, fused.representatives
                    candidate_ids = {bags[k]['resource_id'] for branch in branches.values() for k in branch}
                    budget_recall[name] = quality(list(candidate_ids), query['resource_qrels'])['candidate_recall']
            fusion_ms = (perf_counter() - t) * 1000
            evidence_start = perf_counter()
            selector_cache = {}
            for version_id, candidates in pools.items():
                ordered = sorted(candidates, key=lambda c: (-c.dense_score, c.chunk['id']))
                top = [c for c in ordered[:3] if ordered[0].dense_score - c.dense_score <= .08]
                selector_cache[version_id] = {
                    'E1': select_evidence(candidates, version_id=version_id, strategy='dense'),
                    'E2': max(top, key=lambda c: (raw[c.chunk['id']], -ordered.index(c))) if top else None,
                    'E3': select_evidence(candidates, version_id=version_id, strategy='coverage', query=query['query'],
                        keywords=keywords, dictionary=dictionary, expansion=expansion),
                    'E4': select_evidence(candidates, version_id=version_id, strategy='context', query=query['query'],
                        keywords=keywords, dictionary=dictionary, expansion=expansion)}
            evidence_ms = (perf_counter() - evidence_start) * 1000
        chunk_order = sorted((key for key in keys if context[key] > 0 and chunks[key]['bag_id'] in bags),
                             key=lambda k: (-context[k], k))
        method_orders, method_winners = {}, {}
        for method, (scores, representatives) in arms.items():
            order, winners = resource_order(scores, representatives, bags, versions)
            method_orders[method], method_winners[method] = order, winners
            for selector in ('E0', 'E1', 'E2', 'E3', 'E4'):
                selected = {}
                for resource, bag in winners.items():
                    candidate = selector_cache.get(versions[bag], {}).get(selector) if selector != 'E0' else None
                    selected[resource] = candidate.chunk['id'] if candidate is not None else best[bag]
                row = evaluate_row(query, method, selector, order, selected, chunk_order,
                    (perf_counter() - started) * 1000, list({bags[k]['resource_id'] for k in scores}))
                # Multi-arm experiment wall time is not a method latency sample.
                row['ranking_ms'] = []
                row['candidate_recall'] = budget_recall.get(method, row['candidate_recall'])
                windows = {resource: snippet_text(chunks[key]['text'], set(keywords), query=query['query'],
                    dictionary=dictionary, expansion=expansion) for resource, key in selected.items()}
                grades = {r: rendered_grade(windows[r], r, annotations[query['query_id']]) for r in order}
                row['rendered_snippet_direct_top1'] = bool(order and grades[order[0]] >= 2)
                row['rendered_snippet_direct_hits5'] = sum(grades[r] >= 2 for r in order[:5])
                row['resource_correct_rendered_wrong'] = bool(order and query['resource_qrels'].get(order[0], 0) >= 2
                                                              and grades[order[0]] < 2)
                row['locator_consistent'] = all(chunks[key]['version_id'] == versions[winners[r]] for r, key in selected.items())
                if method == 'BoW':
                    # Export lacks lexical titles/representation and production
                    # lexical snippet state. Do not borrow dense evidence and
                    # mislabel it as independent BoW/Chunk/Snippet quality.
                    row.update(chunk=None, snippet=None, rendered_snippet_direct_top1=None,
                               resource_correct_evidence_wrong=None, resource_correct_rendered_wrong=None,
                               evidence_grade=None, evidence_category='NOT_EVALUATED_BOW_APPROXIMATION')
                rows.append(row)
        # Explain the actual raw ranks and contributions, not a generic noise label.
        vector_metric = quality(method_orders['Vector'], query['resource_qrels'])
        hybrid_metric = quality(method_orders['F0'], query['resource_qrels'])
        if (vector_metric['precision1'] > hybrid_metric['precision1']
                or (vector_metric['mrr10'] or 0.) > (hybrid_metric['mrr10'] or 0.)):
            branch_ranks = {name: ranks(branch) for name, branch in base.items()}
            version_ranks = {name: ranks({versions[k]: score for k, score in
                version_budget(branch, versions, max(1, len(versions))).items()}) for name, branch in base.items()}
            trace = {}
            for method in ('BoW', 'Vector', 'F0', 'F1', 'F3'):
                trace[method] = []
                for resource in method_orders[method][:10]:
                    bag = method_winners[method][resource]
                    trace[method].append({'resource_id': resource, 'version_id': versions[bag], 'bag_id': bag,
                        'chunk_id': best[bag], 'raw_bow': bow.get(bag), 'raw_dense': dense.get(bag),
                        'bag_branch_ranks': {name: branch.get(bag) for name, branch in branch_ranks.items()},
                        'bag_rrf_contributions': {name: 1. / (60 + branch[bag]) for name, branch in branch_ranks.items() if bag in branch},
                        'version_branch_ranks': {name: branch.get(versions[bag]) for name, branch in version_ranks.items()},
                        'fusion_score': arms[method][0][bag],
                        'version_rrf_contributions': {name: 1. / (60 + branch[versions[bag]])
                                                      for name, branch in version_ranks.items() if versions[bag] in branch},
                        'selected_snippet': chunks[best[bag]]['text']})
            cases.append({'query_id': query['query_id'], 'query': query['query'], 'language': query['language'],
                'category': query['category'], 'resource_qrels': query['resource_qrels'], 'chunk_qrels': query['chunk_qrels'],
                'trace': trace, 'budget_recall': budget_recall,
                'reason': 'Inspect equal branch rank contributions and cross-Bag evidence; no label-based runtime rule.'})
        # Fixed diagnostic grids; no test-set threshold selection or runtime flag.
        for method in ('Vector', 'F0'):
            order, winners = method_orders[method], method_winners[method]
            version_scores = {r: max((context[c.chunk['id']] for c in pools[versions[winners[r]]]), default=0.) for r in order}
            for name, threshold in [('N0', 0.), *[(f'N1-{t:.2f}', t) for t in (.3, .4, .5, .6, .7)],
                                    ('N2-001', .01), ('N2-003', .03), ('N3', .5), ('N4', .55)]:
                keep = order
                if name.startswith('N1'):
                    keep = [r for r in order if version_scores[r] >= threshold]
                elif name.startswith('N2'):
                    margin = version_scores[order[0]] - version_scores[order[1]] if len(order) > 1 else 1.
                    keep = order if margin >= threshold else []
                elif name in ('N3', 'N4') and order:
                    candidate = selector_cache.get(versions[winners[order[0]]], {}).get('E1')
                    if candidate is None:
                        keep = []
                        no_answer.append({'query_id': query['query_id'], 'language': query['language'], 'category': query['category'],
                            'split': query['split'], 'method': method, 'policy': name, 'resource': quality(keep, query['resource_qrels']),
                            'chunk': None, 'snippet': None, 'ranking_ms': []})
                        continue
                    terms = content_terms(surface_tokens(query['query']))
                    coverage = len(terms & set(surface_tokens(candidate.chunk['text']))) / max(1, len(terms))
                    support = len(dictionary.evidence(dictionary.expand(query['query']), candidate.chunk['text']))
                    eligible = raw[candidate.chunk['id']] >= threshold and (coverage >= .5 if name == 'N3' else support > 0)
                    keep = order if eligible else []
                no_answer.append({'query_id': query['query_id'], 'language': query['language'], 'category': query['category'],
                    'split': query['split'], 'method': method, 'policy': name, 'resource': quality(keep, query['resource_qrels']),
                    'chunk': None, 'snippet': None, 'ranking_ms': []})
        profiles.append({'query_id': query['query_id'], 'expansion_ms': expansion_ms, 'bm25_ms': lexical_ms,
                         'cached_dense_ms': dense_ms, 'all_fusion_arms_ms': fusion_ms, 'all_evidence_arms_ms': evidence_ms})
        print(json.dumps({'completed_query': qi + 1}), flush=True)
    methods = sorted({r['method'] for r in rows})
    summaries = {m: {s: summary([r for r in rows if r['method'] == m and r['selector'] == s])
                    for s in ('E0', 'E1', 'E2', 'E3', 'E4')} for m in methods}
    for method in methods:
        for selector in ('E0', 'E1', 'E2', 'E3', 'E4'):
            positive = [r for r in rows if r['method'] == method and r['selector'] == selector and r['resource']['has_positive']]
            for target, field in [('rendered_snippet_p1_anchor_proxy', 'rendered_snippet_direct_top1'),
                    ('resource_correct_evidence_wrong_rate', 'resource_correct_evidence_wrong'),
                    ('resource_correct_rendered_wrong_rate', 'resource_correct_rendered_wrong')]:
                values = [r[field] for r in positive if r[field] is not None]
                summaries[method][selector][target] = sum(values) / len(values) if values else None
            summaries[method][selector]['locator_consistent'] = all(r['locator_consistent'] for r in positive)
    validation = {m: summary([r for r in rows if r['method'] == m and r['selector'] == 'E1' and r['split'] == 'validation'])
                  for m in methods}
    validation_e4 = {m: summary([r for r in rows if r['method'] == m and r['selector'] == 'E4' and r['split'] == 'validation'])
                     for m in methods if m != 'BoW'}
    base_gate = summary([r for r in rows if r['method'] == 'F0' and r['selector'] == 'E0' and r['split'] == 'validation'])
    eligible = [m for m in ('F2-050', 'F2-075', 'F2-125', 'F3', 'F4')
                if all(validation_e4[m]['resource'][key] >= base_gate['resource'][key]
                       for key in ('precision1', 'recall5', 'ndcg10'))
                and validation_e4[m]['snippet']['precision1'] >= base_gate['snippet']['precision1']]
    best_validation = max(eligible, key=lambda m: (validation_e4[m]['resource']['precision1'],
        validation_e4[m]['resource']['ndcg10'], validation_e4[m]['snippet']['precision1'], m)) if eligible else None
    report = {'status': 'EXPLORATORY_DRAFT_NOT_RUNTIME_APPROVAL', 'corpus_checksum': corpus['checksum'],
        'qrels_checksum': checksum(qrels), 'model': model, 'artifacts': artifacts,
        'bow_production_equivalent': 'NOT_RUN_MISSING_FULL_SNAPSHOT',
        'raw_text_override': args.include_unreviewed_for_offline_analysis,
        'seen_splits': {split: sum(q['split'] == split for q in qrels['queries']) for split in ('validation', 'test')},
        'summary': summaries, 'validation_only': validation, 'rows': rows, 'failures': cases,
        'validation_e4': validation_e4, 'validation_best_candidate': best_validation,
        'selection_status': 'EXPLORATORY_VALIDATION_RULE_ON_PREVIOUSLY_SEEN_DATA_NOT_PREREGISTERED',
        'excluded_unindexed_chunks': sum(c['bag_id'] not in bags for c in chunks.values()),
        'no_answer': {m: {p: summary([r for r in no_answer if r['method'] == m and r['policy'] == p])
                          for p in sorted({r['policy'] for r in no_answer})} for m in ('Vector', 'F0')},
        'no_answer_rows': no_answer, 'profiles': profiles, 'peak_process_rss_bytes': peak_memory_bytes(),
        'n5_calibration': 'NOT_RUN_NO_NEW_INDEPENDENT_LABELS',
        'limitations': ['Content-only BoW replay, not production-equivalent.', 'All splits previously seen; no fresh holdout.',
            'E0 approximates selected dense Bag passage, not full production ContextRanker.',
            'Snippet-level metrics use existing annotation anchors as a proxy; independent adequacy review still pending.',
            'E2 uses raw Chunk vectors available only in offline artifacts; no raw reindex/runtime change.',
            'Grade1 is partial-or-topic; unjudged is not independently proven wrong.',
            'Method latency is NOT_RUN here; profiles cover multiple experiment arms, not one HTTP request.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'summary': {m: v['E1']['resource'] for m, v in summaries.items()}, 'failures': len(cases)}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus', type=Path, required=True)
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--include-unreviewed-for-offline-analysis', action='store_true')
    run(p.parse_args())


if __name__ == '__main__':
    main()
