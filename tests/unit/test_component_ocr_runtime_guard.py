import pytest

from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError


def test_component_opt_in_fails_closed_before_creating_connections():
    settings = Settings(_env_file=None, ocr_component_pipeline_enabled=True)
    with pytest.raises(DomainError, match='BLOCKED_COMPONENT_PIPELINE_NOT_READY'):
        Container(settings=settings)
