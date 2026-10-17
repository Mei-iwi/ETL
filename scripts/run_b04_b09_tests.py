"""Safety entry point for isolated B04/B09 pytest runs (no repository dotenv)."""
import os
import sys
from pathlib import Path

import pytest
from pydantic_settings.sources.providers import dotenv

from data_sync_etl.config import Settings

Settings.model_config["env_file"] = None
_original_read = dotenv.read_env_file


def safe_read(path, *args, **kwargs):
    target = Path(path).resolve()
    if target.name == ".env" and target.parent in {Path.cwd(), Path(__file__).resolve().parents[3]}:
        raise RuntimeError("Repository dotenv forbidden in isolated acceptance")
    return _original_read(path, *args, **kwargs)


dotenv.read_env_file = safe_read
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
raise SystemExit(pytest.main(sys.argv[1:]))
