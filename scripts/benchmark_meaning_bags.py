"""Real offline model check of observed multilingual groups and descriptions."""

import json
import os
from pathlib import Path

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.domain.meaning_bags import build_meanings, match_meaning


def main():
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch

    torch.set_num_threads(2)
    path = Path('Models/bge-m3')
    revision = (path / 'etl-model-revision.txt').read_text(encoding='utf-8').strip()
    model = LocalSentenceEmbedding(path, 'BAAI/bge-m3', revision, 1024)
    model.prepare()
    docs = {
        'ml': 'Học máy\nMachine learning\nMáy học\n機械学習\nA system learns patterns from training data to make predictions.',
        'binary': 'Binary search repeatedly halves the search interval in a sorted list to locate a target value.',
        'sorting': 'Quicksort partitions an array around a pivot to sort the elements.',
        'auth': 'Authentication verifies a user identity before allowing access to the system.',
        'plants': 'Plants absorb sunlight through leaves and obtain water through their roots.',
    }
    bags = {key: build_meanings([(key, text)], key, None, model, 0.8) for key, text in docs.items()}
    checks = [
        ('machine learning', 'ml'), ('apprentissage automatique', 'ml'), ('機械学習', 'ml'),
        ('hệ thống học quy luật từ dữ liệu để đưa ra dự đoán', 'ml'),
        ('tìm một giá trị bằng cách liên tục chia đôi khoảng tìm kiếm', 'binary'),
        ('kiểm tra danh tính người dùng trước khi cho phép truy cập', 'auth'),
        ('How to bake a chocolate cake in an oven', None),
    ]
    rows = []
    for query, expected in checks:
        vector = model.embed(query)
        scores = {key: match_meaning(vector, bag)[0] for key, bag in bags.items()}
        accepted = sorted((key for key, value in scores.items() if value >= 0.6), key=lambda key: -scores[key])
        rows.append({'query': query, 'expected': expected, 'accepted': accepted, 'scores': scores,
                     'pass': (bool(accepted) and accepted[0] == expected) if expected else not accepted})
        print(json.dumps(rows[-1], ensure_ascii=True), flush=True)
    report = {'model': model.model_id, 'revision': revision, 'dimension': model.dimension,
              'group_threshold': 0.8, 'retrieval_threshold': 0.6,
              'groups': {key: [[bag.members[i].text for i in group.members] for group in bag.groups]
                         for key, bag in bags.items()}, 'checks': rows,
              'limitations': ['Small authored smoke corpus, not an independent quality evaluation.',
                              'Groups contain observed source expressions only; model generates no translations.']}
    output = Path('storage/benchmarks/meaning-bags.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
