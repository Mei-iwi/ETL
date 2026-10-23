"""Exercise launchers with a fake Docker executable; never contact a database."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("shell", ["powershell", "bash"])
@pytest.mark.parametrize("mode,overlay", [
    ("source-neon", "compose.source-neon.yml"),
    ("remote", "compose.remote-db.yml"),
    ("same-host", None),
    ("$(touch unexpected)", "INVALID"),
])
def test_launcher_selects_database_overlay_without_executing_env(tmp_path, shell, mode, overlay):
    executable = shutil.which("powershell.exe" if shell == "powershell" else "bash")
    if shell == "bash" and os.name == "nt":
        executable = "C:/Program Files/Git/bin/bash.exe"
    if not executable or not Path(executable).exists():
        pytest.skip("Launcher shell unavailable")
    (tmp_path / "docker.cmd").write_text("@echo off\n@echo %*\n", encoding="ascii")
    stub = tmp_path / "docker"
    stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n', encoding="ascii")
    stub.chmod(0o755)
    env_file = tmp_path / "settings.env"
    env_file.write_text(f"DATABASE_MODE='{mode}' # configuration\n", encoding="ascii")
    env = os.environ.copy()
    for key in ("DATABASE_MODE", "ETL_SOURCE_NEON", "ETL_REMOTE_DB", "ETL_WITH_DRIVE"):
        env.pop(key, None)
    env["ETL_DOCKER_ENV_FILE"] = str(env_file)
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    if shell == "powershell":
        command = [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "deploy/scripts/etl-docker.ps1"), "-Action", "Status"]
    else:
        command = [executable, str(ROOT / "deploy/scripts/etl-docker.sh"), "status"]
    result = subprocess.run(command, env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    if overlay == "INVALID":
        assert result.returncode != 0
        assert "Invalid DATABASE_MODE" in output
    else:
        assert result.returncode == 0, output
        if overlay:
            assert overlay in output
        else:
            assert "compose.source-neon.yml" not in output
            assert "compose.remote-db.yml" not in output
    assert not (tmp_path / "unexpected").exists()
