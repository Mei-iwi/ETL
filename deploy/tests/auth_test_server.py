"""QA image ONLY: isolated SQLite fixture, never external SOURCE/Master/Mongo."""
import sys
from pathlib import Path

sys.path.insert(0, '/app/tests')
from conftest import MemoryDocumentStore, migrate

from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.db.session import make_engine
from data_sync_etl.main import create_app

root = Path('/run/etl/auth-fixture')
root.mkdir(parents=True, exist_ok=True)
engine = make_engine('sqlite:///' + str(root / 'business.db'))
migrate(engine)
settings = Settings(_env_file=None, database_url='sqlite://', source_adapter='json',
                    source_fixture_root=Path('/app/fixtures/source'), ocr_engine='fake',
                    storage_root=root / 'local', index_embedding_model_path=None)
container = Container(settings, engine, MemoryDocumentStore())
app = create_app(container)
