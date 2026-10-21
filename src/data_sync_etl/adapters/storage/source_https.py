"""Reserved provider: no trusted SOURCE URL contract has been approved.

Reject before DNS/network access, including URLs pretending to be bucket keys.
No arbitrary-URL HTTP client is provided by this adapter.
"""

from data_sync_etl.domain.core import SourceFileError


class DisabledHTTPSFileReader:
    def open_stream(self, bucket, object_key):
        raise SourceFileError('INVALID_SOURCE_LINK')
