"""Opt-in local media/model tests. Inputs are generated or explicitly supplied, never uploaded remotely."""

import json
import os
from hashlib import sha256
from pathlib import Path
from time import monotonic

import pytest
from PIL import Image, ImageDraw, ImageFont

from data_sync_etl.adapters.media import MediaExtractor, command, local_model_task
from data_sync_etl.adapters.ocr.engines import TesseractOCREngine
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app
from tests.indexing_support import setup_index
from tests.local_admin_client import TestClient

pytestmark = pytest.mark.skipif(
    os.environ.get("ETL_MEDIA_REAL_TESTS") != "1",
    reason="Set ETL_MEDIA_REAL_TESTS=1 with local FFmpeg, models and audio fixtures",
)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def media_container(container):
    settings = container.settings
    binary = ROOT / ".test-tmp/media-tools/ffmpeg/binaries/bin"
    settings.ffmpeg_path = os.environ.get("ETL_TEST_FFMPEG", str(binary / "ffmpeg.exe"))
    settings.ffprobe_path = os.environ.get("ETL_TEST_FFPROBE", str(binary / "ffprobe.exe"))
    settings.tesseract_path = os.environ.get(
        "ETL_TEST_TESSERACT", "C:/Program Files/Tesseract-OCR/tesseract.exe"
    )
    settings.whisper_model_path = Path(
        os.environ.get("ETL_TEST_WHISPER_MODEL", str(ROOT / "Models/faster-whisper-base"))
    )
    settings.translation_model_root = ROOT / "Models/argos"
    settings.media_max_duration_seconds = 60
    settings.media_timeout_seconds = 120
    settings.media_max_frames = 10
    settings.media_frame_interval_seconds = 2
    settings.admin_enabled = True
    container.ocr.media = MediaExtractor(
        settings, container.storage, TesseractOCREngine(settings.tesseract_path, "vie+eng", 30)
    )
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update("master_learning_resources", "resource-1", {"source_origin": "LOCAL"})
    setup_index(
        container
    )  # Explicit fake embedding fixture; media and translation models are real.
    return container


def process(container, path, *, language=None, ocr=False):
    with TestClient(create_app(container)) as client:
        upload = client.post(
            "/api/v1/resource/resource-1/versions",
            files={
                "file": (
                    path.name,
                    path.read_bytes(),
                    "audio/mpeg" if path.suffix == ".mp3" else "video/mp4",
                )
            },
        )
        assert upload.status_code == 201, upload.text
        version_id = upload.json()["resource_version_id"]
        base = "/api/v1/resource-versions/" + version_id
        assert (
            client.put(
                base + "/processing-options",
                json={"source_language": language, "target_language": "vi", "ocr_enabled": ocr},
            ).status_code
            == 200
        )
        response = client.post(base + "/process")
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        started = monotonic()
        for _ in range(8):
            if not container.worker.once("real-media"):
                break
        seconds = monotonic() - started
        status = client.get("/api/v1/processing-jobs/" + job_id)
        assert status.status_code == 200, status.text
        view = client.get(base + "/bilingual").json()
        print(
            json.dumps(
                {
                    "file": path.name,
                    "seconds": round(seconds, 3),
                    "status": status.json(),
                    "view": view,
                },
                ensure_ascii=True,
            )
        )
        assert view["source_hash"] == sha256(path.read_bytes()).hexdigest()
        assert client.get(base + "/exports/json").status_code == 200
        assert client.get(base + "/processing-source").content == path.read_bytes()
        assert client.post(base + "/process").json()["job_id"] == job_id
        return view, status.json()


def test_real_mp4_invalid_tracks_fall_back_to_container_streams(media_container, tmp_path, monkeypatch):
    c = media_container
    video = tmp_path / "mismatched-tracks.mp4"
    command([
        c.settings.ffmpeg_path, "-nostdin", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:r=1",
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
        "-t", "1", "-c:v", "libx264", "-c:a", "aac", "-y", str(video),
    ], 30)
    def forbidden_frames(*_):
        pytest.fail("MP4 with an audio track must never sample frames, even when silent")

    monkeypatch.setattr(c.ocr.media, "_frames", forbidden_frames)
    result = c.ocr.media.extract(video, {
        "audio_track": 0, "subtitle_track": 1, "ocr_enabled": True,
    })
    info = result.metadata["media"]
    assert info["selected_video_track"] == 0
    assert info["selected_audio_track"] == 1
    assert info["selected_subtitle_track"] is None
    assert info["audio_status"] == "silent"
    assert info["frame_ocr_status"] == "skipped_audio_present"
    assert result.metadata["warnings"] == [
        "INVALID_AUDIO_TRACK_USING_DEFAULT", "INVALID_SUBTITLE_TRACK_USING_DEFAULT",
    ]


def test_real_silent_video_ocr_upload_export_and_search(media_container, tmp_path):
    c = media_container
    frame = tmp_path / "slide.png"
    image = Image.new("RGB", (1280, 720), "white")
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 54)
    ImageDraw.Draw(image).text(
        (100, 250), "Bài học phân số 2026\nDocument ETL-123", fill="black", font=font
    )
    image.save(frame)
    video = tmp_path / "silent-slide.mp4"
    command(
        [
            c.settings.ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            str(frame),
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ],
        30,
    )
    view, status = process(c, video, language="vi", ocr=True)
    assert status["processing_status"] == "CONTENT_READY"
    assert view["extraction"][0]["media"]["audio_status"] == "no_audio"
    assert view["extraction"][0]["media"]["frame_ocr_status"] == "completed"
    assert view["items"] and all(s["source_type"] == "ocr" for s in view["items"])
    assert any("2026" in s["original_text"] for s in view["items"])
    assert any("Bài học phân số" in s["original_text"] for s in view["items"])
    assert all(
        s.get("start_time") is None and s.get("observed_at") is not None for s in view["items"]
    )
    with TestClient(create_app(c)) as client:
        assert client.get(view["items"][0]["frame_url"]).status_code == 200
        assert (
            client.get(
                f"/api/v1/resource-versions/{view['resource_version_id']}/exports/srt"
            ).status_code
            == 404
        )
    with c.uow() as repo:
        units = repo.find("content_units", {"resource_version_id": view["resource_version_id"]})
        assert units and units[0]["source_locator"]["observed_at"] == 0
        assert units[0]["unit_type"] == "TXT_TEXT" and units[0]["page_from"] is None
        for unit in units:
            repo.update(
                "content_units",
                unit["id"],
                {"review_status": "APPROVED", "retrieval_eligible": True},
            )
    c.indexing.enqueue(view["resource_version_id"])
    assert c.indexing.once()
    c.indexing.bags.search_candidates = lambda *_, **__: list(c.indexing.bags.rows.values())
    result = c.search.search("document", method="bow", access=SearchAccess("local_demo"))
    assert len(result["items"]) == 1
    assert result["items"][0]["source_locator"]["observed_at"] == 0
    context = c.search.context(result["items"][0]["context_id"], access=SearchAccess("local_demo"))
    assert any(chunk["source_locator"]["source_type"] == "ocr" for chunk in context["chunks"])


def test_real_video_text_subtitles_without_audio(media_container, tmp_path):
    c = media_container
    subtitle = tmp_path / "captions.srt"
    subtitle.write_text(
        "1\n00:00:00,100 --> 00:00:02,000\nXin chào Việt Nam 2026\n", encoding="utf-8"
    )
    video = tmp_path / "subtitles.mp4"
    command(
        [
            c.settings.ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=640x360:d=3",
            "-i",
            str(subtitle),
            "-map",
            "0:v",
            "-map",
            "1:0",
            "-c:v",
            "libx264",
            "-c:s",
            "mov_text",
            "-y",
            str(video),
        ],
        30,
    )
    view, status = process(c, video, language="vi")
    assert status["processing_status"] == "CONTENT_READY"
    assert view["items"][0]["original_text"] == "Xin chào Việt Nam 2026"
    assert view["items"][0]["translation_status"] == "same_language"
    assert view["items"][0]["start_time"] == 0.1


def test_real_empty_video_and_silent_audio_have_explicit_status(media_container, tmp_path):
    c = media_container
    for extension, source in [
        (".mp4", "color=black:s=640x360:d=2"),
        (".mp3", "anullsrc=r=16000:cl=mono"),
    ]:
        path = tmp_path / ("empty" + extension)
        command(
            [
                c.settings.ffmpeg_path,
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                source,
                "-t",
                "2",
                "-y",
                str(path),
            ],
            30,
        )
        view, status = process(c, path)
        assert status["processing_status"] == "NO_EXTRACTABLE_CONTENT"
        assert view["items"] == []
        assert view["extraction"][0]["media"]["audio_status"] == (
            "no_audio" if extension == ".mp4" else "silent"
        )


@pytest.mark.parametrize("language,fixture", [("vi", "vi_vn.wav"), ("en", "en.flac")])
def test_real_multilingual_mp3_and_mp4_speech(media_container, tmp_path, language, fixture):
    c = media_container
    audio = ROOT / ".test-tmp/media-tools" / fixture
    assert audio.is_file(), "Download the documented public test audio first"
    mp3 = tmp_path / (language + ".mp3")
    command(
        [
            c.settings.ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(audio),
            "-t",
            "15",
            "-y",
            str(mp3),
        ],
        30,
    )
    view, status = process(c, mp3, language=language)
    assert status["processing_status"] == "CONTENT_READY"
    assert view["items"] and all(
        s["source_type"] == "speech" and s["source_language"] == language for s in view["items"]
    )
    assert all(0 <= s["start_time"] < s["end_time"] <= 15.1 for s in view["items"])
    assert all(s["translated_text"] for s in view["items"])
    if language == "vi":
        assert any("văn" in s["original_text"].lower() for s in view["items"])
    if language == "en":
        mp4 = tmp_path / "spoken.mp4"
        command(
            [
                c.settings.ffmpeg_path,
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=black:s=640x360:d=15",
                "-i",
                str(mp3),
                "-shortest",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-y",
                str(mp4),
            ],
            30,
        )
        video_view, video_status = process(c, mp4, language="en", ocr=True)
        assert video_status["processing_status"] == "CONTENT_READY" and video_view["items"]
        assert all(item["source_type"] == "speech" for item in video_view["items"])
        assert video_view["extraction"][0]["media"]["frame_ocr_status"] == "skipped_audio_present"


def test_real_translation_preserves_both_sentences_and_identifiers(media_container, tmp_path):
    c = media_container
    result = local_model_task(
        "translate",
        {
            "source": "en",
            "target": "vi",
            "text": "The price is 25 dollars. Please keep document ETL-123.",
            "model_root": str(c.settings.translation_model_root.resolve()),
            "threads": 4,
            "scratch": str(tmp_path),
        },
        60,
        tmp_path,
    )
    assert (
        "25" in result["text"]
        and "ETL-123" in result["text"]
        and len(result["text"].splitlines()) == 2
    )


def test_real_invalid_container_and_duration_limits(media_container, tmp_path):
    c = media_container
    path = tmp_path / "invalid.mp3"
    path.write_bytes(b"ID3" + bytes(100))
    with pytest.raises(DomainError, match="INVALID_MEDIA"):
        c.format_registry.find_validator(path.name).validate_path(path)
    valid = tmp_path / "long.mp3"
    command(
        [
            c.settings.ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "3",
            "-y",
            str(valid),
        ],
        30,
    )
    c.settings.media_max_duration_seconds = 1
    with pytest.raises(DomainError, match="MEDIA_DURATION_EXCEEDED"):
        c.format_registry.find_validator(valid.name).validate_path(valid)


def test_real_language_switch_preserves_original_timeline(media_container, tmp_path):
    c = media_container
    vi = ROOT / ".test-tmp/media-tools/vi_vn.wav"
    en = ROOT / ".test-tmp/media-tools/en.flac"
    path = tmp_path / "mixed.mp3"
    command(
        [
            c.settings.ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(vi),
            "-i",
            str(en),
            "-filter_complex",
            "[0:a]aresample=16000[a];[1:a]aresample=16000[b];[a][b]concat=n=2:v=0:a=1[out]",
            "-map",
            "[out]",
            "-y",
            str(path),
        ],
        30,
    )
    view, status = process(c, path)
    assert status["processing_status"] == "CONTENT_READY"
    assert any(s["source_language"] == "vi" and s["start_time"] < 11.34 for s in view["items"])
    assert any(s["source_language"] == "en" and s["start_time"] >= 11.34 for s in view["items"])
    assert all(s["start_time"] < s["end_time"] for s in view["items"])


def test_real_delayed_audio_track_keeps_video_timestamps(media_container, tmp_path):
    c = media_container
    audio = ROOT / ".test-tmp/media-tools/en.flac"
    path = tmp_path / "delayed.mp4"
    command(
        [
            c.settings.ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=640x360:d=15",
            "-itsoffset",
            "2",
            "-i",
            str(audio),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-t",
            "15",
            "-y",
            str(path),
        ],
        30,
    )
    view, status = process(c, path, language="en")
    assert status["processing_status"] == "CONTENT_READY"
    offset = view["extraction"][0]["media"]["audio_timeline_offset"]
    assert 1.9 < offset < 2.1
    assert view["items"] and min(s["start_time"] for s in view["items"]) >= offset
