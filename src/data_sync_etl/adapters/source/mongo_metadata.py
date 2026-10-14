"""Read-only two-collection source; no schema/index/seed or write methods."""

from data_sync_etl.adapters.mongo.foundation_store import MongoMetadataStore
from data_sync_etl.domain.core import DomainError


class MongoMetadataSourceReader:
    def __init__(self, database, destination):
        self.database, self.destination = database, destination
        self._reader = MongoMetadataStore(database)

    def assert_distinct_destination(self):
        if self.database.name.casefold() != self.destination.name.casefold():
            return
        # Server identity detects aliases, credentials and alternate replica-set nodes.
        source = self.database.client.admin.command('hello')
        target = self.destination.client.admin.command('hello')
        source_id = source.get('topologyVersion', {}).get('processId')
        target_id = target.get('topologyVersion', {}).get('processId')
        shared_cluster = bool(set(source.get('hosts', [])) & set(target.get('hosts', [])))
        if (source_id is None or target_id is None or source_id == target_id or shared_cluster
                or source.get('msg') == 'isdbgrid' or target.get('msg') == 'isdbgrid'):
            raise DomainError('SOURCE_MONGO_SAME_DESTINATION')

    def get_user(self, document_id):
        return self._reader.get_user(document_id)

    def get_resource(self, document_id):
        return self._reader.get_resource(document_id)
