"""Provision once; verify and encode without network at container startup."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath

MODEL_ID = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
REVISION = 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
DIMENSION = 384


def contract(model_id=None, revision=None, dimension=None):
    model_id = model_id or os.environ.get('INDEX_EMBEDDING_MODEL_ID', MODEL_ID)
    revision = revision or os.environ.get('INDEX_EMBEDDING_MODEL_VERSION', REVISION)
    dimension = int(dimension if dimension is not None else os.environ.get('INDEX_EMBEDDING_DIMENSION', DIMENSION))
    if not model_id.strip() or not re.fullmatch(r'[a-f0-9]{40}', revision) or not 1 <= dimension <= 4096:
        raise ValueError('MODEL_CONTRACT_INVALID')
    return model_id, revision, dimension


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def model_files(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError('MODEL_DIRECTORY_REQUIRED')
    files = []
    for path in sorted(root.rglob('*'), key=lambda item: item.relative_to(root).as_posix()):
        if any(part in ('.git', '.cache') for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ValueError('MODEL_SYMLINK_REJECTED')
        if path.is_file() and path.name != 'etl-model-manifest.json':
            files.append(path)
    return files


def manifest(root, *, model_id=None, revision=None, dimension=None):
    model_id, revision, dimension = contract(model_id, revision, dimension)
    if (root / 'etl-model-revision.txt').read_text(encoding='utf-8').strip() != revision:
        raise ValueError('MODEL_REVISION_MISMATCH')
    modules = json.loads((root / 'modules.json').read_text(encoding='utf-8'))
    if not modules or not (list(root.rglob('*.safetensors')) or (root / 'pytorch_model.bin').is_file()):
        raise ValueError('MODEL_ARTIFACTS_MISSING')
    if any(not (root / module.get('path', '')).resolve().is_relative_to(root.resolve()) for module in modules):
        raise ValueError('MODEL_MODULE_PATH_REJECTED')
    return {'model_id': model_id, 'revision': revision, 'dimension': dimension,
            'files': [{'path': path.relative_to(root).as_posix(), 'size': path.stat().st_size,
                       'sha256': checksum(path)} for path in model_files(root)]}


def verify(root, manifest_path, *, model_id=None, revision=None, dimension=None):
    model_id, revision, dimension = contract(model_id, revision, dimension)
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (data.get('model_id'), data.get('revision'), data.get('dimension')) != (model_id, revision, dimension):
        raise ValueError('MODEL_CONTRACT_MISMATCH')
    for item in data.get('files', []):
        path = PurePosixPath(item['path'])
        if path.is_absolute() or '..' in path.parts or '\\' in str(path):
            raise ValueError('MODEL_MANIFEST_PATH_REJECTED')
    # Earlier Windows manifests used case-insensitive Path ordering. File order
    # is not part of the checksum contract; accept either platform's ordering.
    normalized = {**data, 'files': sorted(data.get('files', []), key=lambda item: item['path'])}
    if manifest(root, model_id=model_id, revision=revision, dimension=dimension) != normalized:
        raise ValueError('MODEL_CHECKSUM_MISMATCH')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'manifest', 'verify', 'encode'])
    parser.add_argument('--directory', type=Path, default=Path('/opt/models/minilm'))
    parser.add_argument('--manifest', type=Path, default=Path('/opt/model-manifest.json'))
    parser.add_argument('--accept-model-license', action='store_true')
    parser.add_argument('--model-id')
    parser.add_argument('--revision')
    parser.add_argument('--dimension', type=int)
    args = parser.parse_args()
    try:
        model_id, revision, dimension = contract(args.model_id, args.revision, args.dimension)
        configured = dict(model_id=model_id, revision=revision, dimension=dimension)
        if args.action == 'prepare':
            if not args.accept_model_license:
                raise ValueError('MODEL_LICENSE_ACK_REQUIRED')
            os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '0'
            from huggingface_hub import snapshot_download
            snapshot_download(model_id, revision=revision, local_dir=str(args.directory))
            (args.directory / 'etl-model-revision.txt').write_text(revision + '\n', encoding='utf-8')
        if args.action in ('prepare', 'manifest'):
            args.manifest.write_text(json.dumps(manifest(args.directory, **configured), indent=2) + '\n', encoding='utf-8')
        else:
            verify(args.directory, args.manifest, **configured)
        if args.action == 'encode':
            os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
            from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
            model = LocalSentenceEmbedding(args.directory, model_id, revision, dimension)
            model.prepare()
            assert len(model.embed('Học sinh tìm hiểu phép cộng phân số.')) == dimension
        print(json.dumps({'status': 'PASS', 'operation': args.action, 'dimension': dimension}))
        return 0
    except Exception:
        print(json.dumps({'status': 'BLOCKED', 'operation': args.action, 'code': 'MODEL_VERIFICATION_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
