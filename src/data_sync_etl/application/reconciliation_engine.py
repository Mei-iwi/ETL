"""Construct reconciliation through the UnitOfWork infrastructure port."""
from data_sync_etl.domain.reconciliation import FLAG_DESYNC_BLOB_SIZE as FLAG_DESYNC_BLOB_SIZE
from data_sync_etl.domain.reconciliation import FLAG_DESYNC_CHECKSUM as FLAG_DESYNC_CHECKSUM
from data_sync_etl.domain.reconciliation import FLAG_DESYNC_METADATA as FLAG_DESYNC_METADATA
from data_sync_etl.domain.reconciliation import FLAG_MUTATED_DOWNSTREAM as FLAG_MUTATED_DOWNSTREAM
from data_sync_etl.domain.reconciliation import FLAG_MUTATED_SOURCE as FLAG_MUTATED_SOURCE
from data_sync_etl.domain.reconciliation import FLAG_NOT_VERIFIED as FLAG_NOT_VERIFIED
from data_sync_etl.domain.reconciliation import FLAG_ORPHAN_MASTER as FLAG_ORPHAN_MASTER
from data_sync_etl.domain.reconciliation import FLAG_ORPHAN_SOURCE as FLAG_ORPHAN_SOURCE
from data_sync_etl.domain.reconciliation import FLAG_SYNCED as FLAG_SYNCED
from data_sync_etl.domain.reconciliation import METADATA_FIELDS as METADATA_FIELDS
from data_sync_etl.domain.reconciliation import STREAM_TABLE_MAPPING as STREAM_TABLE_MAPPING
from data_sync_etl.domain.reconciliation import metadata_differences as metadata_differences
from data_sync_etl.domain.reconciliation import serialize_json_value as serialize_json_value


class ReconciliationEngine:
    def __new__(cls, uow, source_adapter=None, storage_root="storage", max_ratio=0.1, batch_size=500):
        return uow.reconciliation_engine(source_adapter=source_adapter, storage_root=storage_root,
                                         max_ratio=max_ratio, batch_size=batch_size)
