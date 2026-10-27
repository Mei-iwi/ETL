import subprocess

import pytest

from data_sync_etl.adapters import media
from data_sync_etl.domain.core import DomainError


@pytest.mark.parametrize("name", ["ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"])
def test_current_path_has_priority(name, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda value: "/tools/" + value)
    monkeypatch.setattr(media, "_windows_search_path", lambda: pytest.fail("unneeded registry read"))
    assert media.resolve_media_tool(name) == "/tools/" + name


@pytest.mark.parametrize("name", ["ffmpeg", "ffprobe"])
def test_worker_with_stale_path_uses_persisted_windows_path(name, monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "win32")
    monkeypatch.setattr(media, "_windows_search_path", lambda: "updated-path")
    monkeypatch.setattr(
        media.shutil, "which",
        lambda value, path=None: "C:/media tools/" + value + ".exe" if path == "updated-path" else None,
    )
    calls = []
    monkeypatch.setattr(media.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    media.command([name, "-version"], 30)
    assert calls == [(["C:/media tools/" + name + ".exe", "-version"],
                      dict(capture_output=True, timeout=30, check=True))]


def test_missing_tool_returns_controlled_error(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr(media.shutil, "which", lambda value: None)
    with pytest.raises(DomainError, match="^MEDIA_RUNTIME_UNAVAILABLE$"):
        media.command(["ffprobe", "-version"], 30)


def test_explicit_missing_path_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda *_: pytest.fail("explicit path overridden"))

    def missing(args, **kwargs):
        assert args[0] == "C:/missing/ffmpeg.exe"
        raise FileNotFoundError

    monkeypatch.setattr(media.subprocess, "run", missing)
    with pytest.raises(DomainError, match="^MEDIA_RUNTIME_UNAVAILABLE$"):
        media.command(["C:/missing/ffmpeg.exe", "-version"], 30)


@pytest.mark.parametrize("exception, code", [
    (subprocess.TimeoutExpired("ffmpeg", 1), "MEDIA_TIMEOUT"),
    (subprocess.CalledProcessError(1, "ffmpeg"), "INVALID_MEDIA"),
])
def test_resolved_tool_preserves_error_codes(exception, code, monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda value: "/tools/" + value)

    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(media.subprocess, "run", fail)
    with pytest.raises(DomainError, match="^" + code + "$"):
        media.command(["ffmpeg", "-version"], 1, "INVALID_MEDIA")
