"""Bounded authenticated HTTP acceptance client for explicitly approved staging.

No .env loading, database access, forged identity, redirects, retries or raw
query/result/token logging. Server-issued CSRF/session and proxy key are required.
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sync_etl.application.search_review import write_review
from data_sync_etl.domain.evaluation import percentile


class HttpProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)
    environment: str
    base_url: str
    email: str
    password_file: str
    proxy_key_file: str
    expected_vector_path: str
    concurrency: int = Field(default=1, ge=1, le=8)
    repeats: int = Field(default=2, ge=1, le=20)
    timeout_seconds: int = Field(default=15, ge=1, le=30)
    queries: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def bounded(self):
        url = urlsplit(self.base_url)
        if (self.environment not in ('approved_staging', 'isolated_http_fixture')
                or self.expected_vector_path not in ('meaning_bag', 'legacy_bag', 'semantic_chunk')
                or url.username or url.password or url.query or url.fragment or url.path not in ('', '/')
                or not url.hostname or url.scheme not in ('http', 'https')
                or url.scheme == 'http' and url.hostname not in ('127.0.0.1', 'localhost', '::1')
                or len(self.queries) * self.repeats * 3 > 600
                or any(not q.strip() or len(q) > 1000 for q in self.queries)):
            raise ValueError('Explicit bounded staging origin/workload required')
        return self


def secret(path):
    p = Path(path)
    if not p.is_file() or p.stat().st_size > 8192:
        raise ValueError('Bounded secret file required')
    value = p.read_text(encoding='utf-8').strip()
    if not value:
        raise ValueError('Nonempty secret required')
    return value


def benchmark(profile, *, observer=None):
    origin = profile.base_url.rstrip('/')
    headers = {'Origin': origin, 'X-ETL-Proxy-Key': secret(profile.proxy_key_file)}
    password = secret(profile.password_file)
    rows, endpoint_checks = [], {}
    with httpx.Client(base_url=origin, headers=headers, timeout=profile.timeout_seconds,
                      follow_redirects=False, trust_env=False) as client:
        # Availability alone is not authentication. Confirm session is absent.
        endpoint_checks['unauthenticated_search'] = client.post('/api/v1/search', json={'query': 'probe', 'method': 'bow'}).status_code
        if endpoint_checks['unauthenticated_search'] not in (401, 403):
            raise ValueError('Authentication unexpectedly absent')
        csrf = client.get('/api/v1/auth/csrf')
        csrf.raise_for_status()
        client.headers['X-CSRF-Token'] = csrf.json()['csrf_token']
        login = client.post('/api/v1/auth/login', json={'email': profile.email, 'password': password})
        if login.status_code != 200:
            raise ValueError('Staging login rejected')
        del password
        me = client.get('/api/v1/auth/me')
        if me.status_code != 200:
            raise ValueError('Server session not established')
        # Login rotates session/CSRF; never reuse the pre-login token.
        client.headers['X-CSRF-Token'] = client.get('/api/v1/auth/csrf').json()['csrf_token']
        endpoint_checks['model_status'] = client.get('/api/v1/search/model-status').status_code

        def request(case):
            method, query_index, repeat = case
            started = perf_counter()
            try:
                response = client.post('/api/v1/search', json={'query': profile.queries[query_index], 'method': method,
                                                               'page': 1, 'size': 5})
                elapsed = (perf_counter() - started) * 1000
                payload = response.json() if response.status_code == 200 else {}
                if response.status_code == 200:
                    algorithm = payload.get('ranking_algorithm', '')
                    semantic = isinstance(payload.get('vector_index'), dict)
                    if method != 'bow' and (semantic != (profile.expected_vector_path == 'semantic_chunk')):
                        raise ValueError('Search path mismatch')
                    if method != 'bow' and profile.expected_vector_path == 'meaning_bag' and 'meaning' not in algorithm:
                        raise ValueError('Meaning Bag path mismatch')
                    if observer is not None:
                        observer(method, payload)  # Test/controller only, never printed.
                    if repeat == -1 and payload.get('items'):
                        item = payload['items'][0]
                        context_url = item.get('context_url', '')
                        if not context_url.startswith('/api/v1/chunks/') or not context_url.endswith('/context'):
                            raise ValueError('Invalid context locator')
                        context = client.get(context_url, params={'neighbors': 0})
                        endpoint_checks['context_' + method] = context.status_code
                        if (context.status_code != 200
                                or context.json()['content_unit_id'] != item['content_unit_id']):
                            raise ValueError('Context identity mismatch')
                return {'method': method, 'query_index': query_index, 'repeat': repeat, 'status': response.status_code,
                        'ms': elapsed, 'timeout': False}
            except httpx.TimeoutException:
                return {'method': method, 'query_index': query_index, 'repeat': repeat, 'status': None,
                        'ms': (perf_counter()-started)*1000, 'timeout': True}

        # First requests are separate from warm samples; do not call them a full
        # cold model/container startup unless the operator actually restarted it.
        try:
            first = [request((method, 0, -1)) for method in ('bow', 'vector', 'hybrid')]
            cases = [(method, qi, repeat) for repeat in range(profile.repeats)
                     for qi in range(len(profile.queries)) for method in ('bow', 'vector', 'hybrid')]
            start = perf_counter()
            with ThreadPoolExecutor(max_workers=profile.concurrency) as pool:
                rows.extend(pool.map(request, cases))
            duration = perf_counter()-start
        finally:
            logout = client.post('/api/v1/auth/logout')
            endpoint_checks['logout'] = logout.status_code
            endpoint_checks['revoked_session'] = client.get('/api/v1/auth/me').status_code
            if endpoint_checks['logout'] != 204 or endpoint_checks['revoked_session'] not in (401, 403):
                raise ValueError('Session revocation failed')
    return {'status': 'HTTP_MEASURED_NOT_RESEARCH_ACCEPTED', 'environment': profile.environment,
        'expected_vector_path': profile.expected_vector_path, 'concurrency': profile.concurrency,
        'queries': len(profile.queries), 'requests': len(rows), 'first_requests': first,
        'cold_model_startup': 'NOT_RUN', 'endpoint_checks': endpoint_checks,
        'requests_per_second': len(rows)/duration, 'errors': sum(r['status'] != 200 for r in rows),
        'timeouts': sum(r['timeout'] for r in rows), 'samples': rows,
        'latency': {method: {'p50_ms': percentile([r['ms'] for r in rows if r['method'] == method], .5),
            'p95_ms': percentile([r['ms'] for r in rows if r['method'] == method], .95)
                       if sum(r['method'] == method for r in rows) >= 20 else None,
            'samples': sum(r['method'] == method for r in rows)} for method in ('bow', 'vector', 'hybrid')},
        'server_cpu_ram_db_plans': 'NOT_COLLECTED_BY_CLIENT', 'raw_queries_or_results_logged': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    try:
        if (not args.config.resolve().is_relative_to(Path('.test-tmp').resolve())
                or args.config.stat().st_size > 65536 or args.output.exists()
                or not args.output.resolve().is_relative_to(Path('.test-tmp').resolve())):
            raise ValueError('Fresh private configuration/output required')
        profile = HttpProfile.model_validate_json(args.config.read_text(encoding='utf-8'))
        if args.validate_only:
            print(json.dumps({'status': 'CONFIG_VALIDATED_NO_NETWORK', 'environment': profile.environment}))
            return 0
        result = benchmark(profile)
        write_review(args.output, result)
        print(json.dumps({k: result[k] for k in ('status', 'environment', 'requests', 'errors', 'timeouts')}))
        return 0 if result['errors'] == 0 else 1
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        print(json.dumps({'status': 'BLOCKED', 'code': 'STAGING_AUTH_OR_MEASUREMENT_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
