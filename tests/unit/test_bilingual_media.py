import io
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from conftest import ingest

from data_sync_etl.adapters.media import parse_srt, probe, select_track
from data_sync_etl.adapters.media_runtime import speech_windows
from data_sync_etl.application.bilingual import BilingualService, export_segments, paired_result
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.main import create_app
from data_sync_etl.ports.processing import OCRResult
from tests.local_admin_client import TestClient


def test_soft_deleted_media_routes_hide_content_and_block_processing(container):
    from tests.unit.test_resource_soft_delete import deletion

    version = ingest(container)
    container.settings.admin_enabled = True
    deletion(container)
    base = f"/api/v1/resource-versions/{version['id']}"
    with TestClient(create_app(container)) as client:
        for path in ("/bilingual", "/exports/json", "/exports/srt", "/processing-source", "/segments/missing/frame"):
            assert client.get(base + path).status_code == 404
        assert client.put(base + "/processing-options", json={}).status_code == 404
        assert client.post(base + "/translations/retry", json={}).status_code == 404


def finish(container, version):
    job = container.ocr.create_job(version["id"])
    for _ in range(30):
        if not container.worker.once("bilingual-test"):
            break
    return job


def test_unicode_stable_segments_raw_offsets_and_language():
    text = "Tiếng Việt giữ dấu.\n\nSố liệu 2026 và mã ETL-123."
    raw = OCRResult(text)
    first = paired_result(raw, "version-a", 2, {"source_language": "vi"})
    second = paired_result(raw, "version-a", 2, {"source_language": "vi"})
    assert first.text == text and first.metadata == second.metadata
    for segment in first.metadata["paired_segments"]:
        assert text[segment["raw_start"] : segment["raw_end"]] == segment["original_text"]
        assert segment["page_number"] == 2 and segment["source_language"] == "vi"


def test_translation_failure_preserves_original_retry_survives_restart(
    container, monkeypatch, tmp_path
):
    version = ingest(container)
    container.bilingual.configure(version["id"], {"source_language": "en", "target_language": "vi"})
    finish(container, version)
    before = container.bilingual.view(version["id"])
    assert before["items"] and all(s["translation_status"] == "failed" for s in before["items"])
    with container.uow() as repo:
        counts = {table: repo.count(table, {}) for table in ("ocr_page_results", "content_units")}
    container.settings.translation_model_root = tmp_path
    # Explicit mock: verifies checkpoint/link semantics, not translation quality.
    monkeypatch.setattr(
        container.bilingual,
        "runner",
        lambda *_: {
            "text": "Bản dịch kiểm thử có dấu 2026.",
            "provider": "mock",
            "version": "test",
        },
    )
    container.bilingual.configure(version["id"], {"target_language": "vi"}, retry_translation=True)
    restarted = BilingualService(container.uow, container.settings, container.bilingual.runner)
    assert restarted.once()
    after = restarted.view(version["id"])
    assert [s["segment_id"] for s in after["items"]] == [s["segment_id"] for s in before["items"]]
    assert [s["original_text"] for s in after["items"]] == [
        s["original_text"] for s in before["items"]
    ]
    assert all(s["translation_status"] == "completed" for s in after["items"])
    assert not restarted.once()
    with container.uow() as repo:
        assert counts == {table: repo.count(table, {}) for table in counts}
    with container.storage.open_stream(
        version["storage_bucket"], version["storage_object_key"]
    ) as stream:
        assert sha256(stream.read()).hexdigest() == version["source_hash"]


def test_same_language_does_not_invoke_translation(container, monkeypatch):
    version = ingest(container)
    container.bilingual.configure(version["id"], {"source_language": "vi", "target_language": "vi"})

    def forbidden(*_):
        pytest.fail("Same-language content must not load a translation model")

    monkeypatch.setattr(container.bilingual, "runner", forbidden)
    finish(container, version)
    items = container.bilingual.view(version["id"])["items"]
    assert items and all(
        s["translation_status"] == "same_language" and s["translated_text"] == s["original_text"]
        for s in items
    )


def test_options_locked_during_job_and_retries_bounded(container):
    version = ingest(container)
    container.ocr.create_job(version["id"])
    with pytest.raises(DomainError, match="PROCESSING_BUSY"):
        container.bilingual.configure(version["id"], {"source_language": "fr"})
    finish(container, version)
    for _ in range(container.settings.translation_max_retries):
        container.bilingual.configure(
            version["id"], {"source_language": "fr"}, retry_translation=True
        )
        container.bilingual.once()
    with pytest.raises(DomainError, match="TRANSLATION_RETRY_LIMIT"):
        container.bilingual.configure(version["id"], {}, retry_translation=True)


def test_exports_never_turn_ocr_into_speech_and_prefer_subtitle():
    items = [
        {
            "source_type": "speech",
            "original_text": "speech alternative",
            "start_time": 1.2,
            "end_time": 3.4,
        },
        {
            "source_type": "subtitle",
            "original_text": "Xin chào Việt Nam",
            "start_time": 1.2,
            "end_time": 3.4,
        },
        {"source_type": "ocr", "original_text": "Slide 2026", "observed_at": 2},
    ]
    srt = export_segments({"items": items}, "srt")
    assert "00:00:01,200 --> 00:00:03,400" in srt and "Xin chào Việt Nam" in srt
    assert "Slide" not in srt and "speech alternative" not in srt
    assert export_segments({"items": items}, "vtt").startswith("WEBVTT\n\n")
    with pytest.raises(DomainError, match="EXPORT_NOT_AVAILABLE"):
        export_segments({"items": items}, "txt", translated=True)


def test_subtitle_unicode_timing_and_invalid_cues():
    data = "1\n00:00:00,100 --> 00:00:01,500\n<b>Tiếng Việt</b>\n\n2\n00:00:03,000 --> 00:00:02,000\nbad\n"
    rows = list(parse_srt(data, 4))
    assert rows == [
        {
            "original_text": "Tiếng Việt",
            "source_type": "subtitle",
            "start_time": 0.1,
            "end_time": 1.5,
        }
    ]


def test_track_selection_is_explicit_default_then_index():
    streams = [
        {"index": 3, "codec_type": "audio"},
        {"index": 1, "codec_type": "audio", "disposition": {"default": 1}},
        {"index": 2, "codec_type": "subtitle"},
    ]
    assert select_track(streams, "audio")["index"] == 1
    assert select_track(streams, "audio", 3)["index"] == 3
    with pytest.raises(DomainError, match="INVALID_MEDIA_TRACK"):
        select_track(streams, "audio", 2)


def test_mp4_audio_failure_does_not_fall_back_to_frame_ocr(tmp_path, monkeypatch):
    import wave

    from data_sync_etl.adapters.media import MediaExtractor

    monkeypatch.setattr("data_sync_etl.adapters.media.probe", lambda *_: {
        "duration": 1, "timeline_origin": 0,
        "streams": [{"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "audio"}],
    })

    def decode(args, *_):
        with wave.open(str(args[-1]), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 16000)

    def unavailable(*_):
        raise DomainError("STT_RUNTIME_UNAVAILABLE")

    def forbidden_frames(*_):
        pytest.fail("An audio failure must not trigger frame extraction")

    monkeypatch.setattr("data_sync_etl.adapters.media.command", decode)
    monkeypatch.setattr("data_sync_etl.adapters.media.local_model_task", unavailable)
    extractor = MediaExtractor(Settings(_env_file=None), None, None)
    monkeypatch.setattr(extractor, "_frames", forbidden_frames)
    with pytest.raises(DomainError, match="STT_RUNTIME_UNAVAILABLE"):
        extractor.extract(tmp_path / "video.mp4", {"ocr_enabled": True})


def test_invalid_optional_tracks_preserve_video_extraction(tmp_path, monkeypatch):
    from data_sync_etl.adapters.media import MediaExtractor

    monkeypatch.setattr(
        "data_sync_etl.adapters.media.probe",
        lambda *_: {
            "duration": 4, "timeline_origin": 0,
            "streams": [{"index": 0, "codec_type": "video"}],
        },
    )
    extractor = MediaExtractor(Settings(_env_file=None), None, None)

    def frames(ff, directory, info, segments, options, remaining):
        segments.append({"original_text": "Slide 1", "source_type": "ocr", "observed_at": 0})

    monkeypatch.setattr(extractor, "_frames", frames)
    result = extractor.extract(tmp_path / "video.mp4", {"audio_track": 0, "subtitle_track": 1})
    assert result.text == "Slide 1"
    assert result.metadata["extraction_status"] == "partial"
    assert result.metadata["warnings"] == [
        "INVALID_AUDIO_TRACK_USING_DEFAULT", "INVALID_SUBTITLE_TRACK_USING_DEFAULT",
    ]
    assert result.metadata["media"]["selected_audio_track"] is None
    assert result.metadata["media"]["selected_subtitle_track"] is None


@pytest.mark.parametrize(
    "duration,formats,streams,error",
    [
        ("nan", "mp3", [{"codec_type": "audio", "codec_name": "mp3"}], "INVALID_MEDIA"),
        ("99999", "mp3", [{"codec_type": "audio", "codec_name": "mp3"}], "MEDIA_DURATION_EXCEEDED"),
        ("2", "wav", [{"codec_type": "audio", "codec_name": "pcm_s16le"}], "INVALID_MEDIA"),
        ("2", "mp4", [{"codec_type": "audio", "codec_name": "aac"}], "INVALID_MEDIA"),
    ],
)
def test_probe_rejects_bad_duration_and_disguised_container(
    tmp_path, monkeypatch, duration, formats, streams, error
):
    path = tmp_path / "sample.mp3"
    path.write_bytes(b"ID3invalid")
    monkeypatch.setattr(
        "data_sync_etl.adapters.media.command",
        lambda *_: SimpleNamespace(
            stdout=json.dumps(
                {"format": {"duration": duration, "format_name": formats}, "streams": streams}
            ).encode()
        ),
    )
    with pytest.raises(DomainError, match=error):
        probe(path, Settings(_env_file=None))


def test_new_routes_deny_unauthenticated_and_export_absent_translations(container):
    version = ingest(container)
    base = f"/api/v1/resource-versions/{version['id']}"
    with TestClient(create_app(container)) as client:
        for route in ("/bilingual", "/exports/json", "/processing-source", "/segments/no/frame"):
            assert client.get(base + route).status_code == 403
        container.settings.admin_enabled = True
        assert (
            client.put(base + "/processing-options", json={"source_language": "vi"}).status_code
            == 200
        )
        assert (
            client.put(base + "/processing-options", json={"unknown_setting": True}).status_code
            == 422
        )
        assert client.get(base + "/exports/txt?translated=true").status_code == 404
        assert client.get(base + "/bilingual").json()["items"] == []


def test_registry_enables_only_requested_media_and_existing_documents():
    extensions = {row["extension"] for row in FormatValidatorRegistry().describe()}
    assert extensions == {
        ".pdf",
        ".docx",
        ".xlsx",
        ".txt",
        ".csv",
        ".pptx",
        ".png",
        ".jpg",
        ".jpeg",
        ".mp3",
        ".mp4",
    }
    assert FormatValidatorRegistry().find_validator("a.mp4").text_extraction_supported
    assert (
        not FormatValidatorRegistry(media_registration=True)
        .find_validator("a.mp4")
        .text_extraction_supported
    )


def test_media_output_uses_individual_provenance_and_no_fabricated_ocr_interval():
    raw = OCRResult(
        "Xin chào\n\nSlide 1",
        metadata={
            "source_type": "media",
            "logical_units": [
                {
                    "start": 0,
                    "end": 8,
                    "locator": {
                        "source_type": "speech",
                        "start_time": 2,
                        "end_time": 3,
                        "source_language": "vi",
                    },
                },
                {
                    "start": 10,
                    "end": 17,
                    "locator": {"source_type": "ocr", "observed_at": 4, "source_language": "en"},
                },
            ],
        },
    )
    paired = paired_result(raw, "media", 1, {})
    speech, ocr = paired.metadata["paired_segments"]
    assert speech["source_type"] == "speech" and speech["start_time"] == 2
    assert ocr["source_type"] == "ocr" and "start_time" not in ocr and "page_number" not in ocr


def test_invalid_upload_has_no_persisted_writes(container):
    container.settings.admin_enabled = True
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update("master_learning_resources", "resource-1", {"source_origin": "LOCAL"})
    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resource/resource-1/versions",
            files={"file": ("fake.mp4", io.BytesIO(b"not a video"), "video/mp4")},
        )
        assert response.status_code == 400
    with container.uow() as repo:
        assert repo.count("resource_versions", {}) == 0


def test_vad_windows_keep_short_speech_context_without_losing_original_offsets():
    ranges = [
        {"start": 1600, "end": 8000},
        {"start": 10000, "end": 24000},
        {"start": 100000, "end": 120000},
    ]
    assert speech_windows(ranges, 48000) == [
        {"start": 1600, "end": 24000},
        {"start": 100000, "end": 120000},
    ]
    assert ranges[0]["end"] == 8000  # Caller-owned VAD results remain unchanged.


def test_model_error_keeps_its_specific_safe_code(tmp_path, monkeypatch):
    from data_sync_etl.adapters.media import local_model_task

    def fake_child(*_):
        (tmp_path / "asr-response.json").write_text(
            '{"error":"STT_RUNTIME_UNAVAILABLE"}', encoding="utf-8"
        )

    monkeypatch.setattr("data_sync_etl.adapters.media.command", fake_child)
    with pytest.raises(DomainError, match="^STT_RUNTIME_UNAVAILABLE$"):
        local_model_task("asr", {}, 1, tmp_path)
