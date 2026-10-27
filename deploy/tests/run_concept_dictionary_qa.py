"""Isolated QA entrypoint: discard inherited service configuration, never secrets.

Run ONLY inside a network-disabled disposable QA container. Database fixtures
create their own SQLite/explicit TEST resources; production DSNs are not inputs.
"""
import os
import sys

from data_sync_etl.config import Settings


def main():
    for name in Settings.model_fields:
        os.environ.pop(name.upper(), None)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    import pytest
    return pytest.main(sys.argv[1:])


if __name__ == '__main__':
    raise SystemExit(main())
