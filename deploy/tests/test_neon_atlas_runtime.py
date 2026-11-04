import pytest
from urllib.parse import urlencode, urlunsplit

from test_runtime import load_runtime


def configured(runtime, tmp_path, monkeypatch):
    ca = tmp_path / 'ca.pem'
    ca.write_text('test-ca')
    monkeypatch.setattr(runtime, 'SOURCE_CA_FILE', ca)
    query = urlencode({'sslmode': 'verify-full', 'channel_binding': 'require', 'sslrootcert': str(ca)})
    return dict(DATABASE_URL='postgresql+psycopg://reader:example@master-postgres:5432/etl',
        SOURCE_DATABASE_URL=f'postgresql+psycopg://reader:example@source.neon.tech/db?{query}',
        MONGO_URI=urlunsplit(('mongodb+srv', 'reader:example@cluster.mongodb.net', '/', 'retryWrites=true', '')),
        MONGO_DATABASE='etl_content')


def test_neon_atlas_keeps_local_master_and_verified_source(tmp_path, monkeypatch):
    runtime = load_runtime('runtime')
    data = configured(runtime, tmp_path, monkeypatch)
    runtime.validate_source_neon(data, atlas=True)
    with pytest.raises(ValueError, match='MONGO_LOCAL_REQUIRED'):
        runtime.validate_source_neon(data)


@pytest.mark.parametrize('uri', [
    urlunsplit(('mongodb', 'reader:example@mongo', '/', '', '')),
    urlunsplit(('mongodb+srv', 'reader:example@cluster.mongodb.net', '/', 'tls=false', '')),
    urlunsplit(('mongodb+srv', 'reader:example@cluster.mongodb.net', '/', 'ssl=false', '')),
    urlunsplit(('mongodb+srv', 'reader:example@cluster.mongodb.net', '/', 'tlsInsecure=true', '')),
    urlunsplit(('mongodb+srv', 'reader:example@cluster.mongodb.net', '/', 'tlsAllowInvalidCertificates=true', '')),
    urlunsplit(('mongodb+srv', 'reader:example@cluster.example.org', '/', '', '')),
])
def test_neon_atlas_rejects_tls_bypass(tmp_path, monkeypatch, uri):
    runtime = load_runtime('runtime')
    data = configured(runtime, tmp_path, monkeypatch)
    data['MONGO_URI'] = uri
    with pytest.raises(ValueError, match='ATLAS_TLS_REQUIRED'):
        runtime.validate_source_neon(data, atlas=True)
