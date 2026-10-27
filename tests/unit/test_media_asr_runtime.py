"""Offline model loading and original-language/timeline regression checks."""

from types import SimpleNamespace

import pytest

from data_sync_etl.adapters.media_runtime import transcribe


@pytest.fixture
def payload(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
        (model / name).write_text("invalid fixture", encoding="utf-8")
    return dict(
        model=str(model),
        audio="local.wav",
        device="cpu",
        compute_type="int8",
        threads=4,
        language=None,
        window_seconds=15,
    )


@pytest.mark.parametrize(
    "missing", ["directory", "config.json", "model.bin", "tokenizer.json", "vocabulary.json"]
)
def test_incomplete_model_never_constructs_or_downloads(payload, missing, monkeypatch):
    from pathlib import Path

    whisper = pytest.importorskip("faster_whisper")

    def forbidden(*args, **kwargs):
        pytest.fail("Incomplete local model must fail before any model/tokenizer loading")

    monkeypatch.setattr(whisper, "WhisperModel", forbidden)
    if missing == "directory":
        payload["model"] += "/absent"
    else:
        (Path(payload["model"]) / missing).unlink()
    assert transcribe(payload) == {"error": "STT_RUNTIME_UNAVAILABLE"}


def test_invalid_model_returns_controlled_error_without_fallback(payload, monkeypatch):
    whisper = pytest.importorskip("faster_whisper")
    calls = []

    def invalid(path, **kwargs):
        calls.append((path, kwargs))
        raise RuntimeError("Invalid CTranslate2 weights")

    monkeypatch.setattr(whisper, "WhisperModel", invalid)
    assert transcribe(payload) == {"error": "STT_RUNTIME_UNAVAILABLE"}
    assert len(calls) == 1 and calls[0][0] == payload["model"]
    assert calls[0][1]["local_files_only"] is True


@pytest.mark.parametrize("language", [None, "vi", "en"])
def test_local_runtime_preserves_language_text_offsets_and_filters(payload, monkeypatch, language):
    whisper = pytest.importorskip("faster_whisper")
    import faster_whisper.audio as audio
    import faster_whisper.vad as vad
    import numpy as np

    payload["language"] = language
    loaded, decoded = [], []
    segment = dict(
        text="  ETL-123: 25 Nguyễn Văn An.  ",
        start=0.1,
        end=0.7,
        no_speech_prob=0.1,
        avg_logprob=-0.2,
    )

    def infer(samples, **kwargs):
        decoded.append(kwargs)
        return iter(
            [
                SimpleNamespace(**segment),
                SimpleNamespace(**{**segment, "no_speech_prob": 0.95}),
                SimpleNamespace(**{**segment, "avg_logprob": -2}),
            ]
        ), SimpleNamespace(language="fr", language_probability=0.9)

    def model(path, **kwargs):
        loaded.append((path, kwargs))
        return SimpleNamespace(model=SimpleNamespace(is_multilingual=True), transcribe=infer)

    monkeypatch.setattr(whisper, "WhisperModel", model)
    monkeypatch.setattr(audio, "decode_audio", lambda *a, **k: np.zeros(48000))
    monkeypatch.setattr(
        vad, "get_speech_timestamps", lambda *a, **k: [{"start": 16000, "end": 32000}]
    )
    result = transcribe(payload)
    assert loaded == [
        (
            payload["model"],
            dict(
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
                num_workers=1,
                local_files_only=True,
            ),
        )
    ]
    assert decoded[0] == dict(
        task="transcribe",
        language=language,
        vad_filter=True,
        condition_on_previous_text=False,
        beam_size=3,
    )
    assert len(result["segments"]) == 1
    row = result["segments"][0]
    assert row["original_text"] == "ETL-123: 25 Nguyễn Văn An."
    assert row["source_language"] == (language or "fr")
    assert (row["start_time"], row["end_time"]) == (1.1, 1.7)
    assert result["provider"]["compute_type"] == "int8"


def test_vad_silence_produces_no_transcript(payload, monkeypatch):
    whisper = pytest.importorskip("faster_whisper")
    import faster_whisper.audio as audio
    import faster_whisper.vad as vad
    import numpy as np

    def forbidden(*args, **kwargs):
        pytest.fail("Silence must not be decoded into invented speech")

    monkeypatch.setattr(
        whisper,
        "WhisperModel",
        lambda *a, **k: SimpleNamespace(
            model=SimpleNamespace(is_multilingual=True), transcribe=forbidden
        ),
    )
    monkeypatch.setattr(audio, "decode_audio", lambda *a, **k: np.zeros(16000))
    monkeypatch.setattr(vad, "get_speech_timestamps", lambda *a, **k: [])
    assert transcribe(payload)["segments"] == []


def test_extractor_passes_configuration_and_adds_delayed_track_offset(tmp_path, monkeypatch):
    import wave

    from data_sync_etl.adapters.media import MediaExtractor
    from data_sync_etl.config import Settings

    settings = Settings(_env_file=None, whisper_model_path=tmp_path / "local-model")
    monkeypatch.setattr(
        "data_sync_etl.adapters.media.probe",
        lambda *_: {
            "duration": 10,
            "timeline_origin": 1,
            "streams": [
                {"index": 0, "codec_type": "video"},
                {"index": 1, "codec_type": "audio", "start_time": "3"},
            ],
        },
    )

    def decode(args, *_):
        with wave.open(str(args[-1]), "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 16000)

    def run(kind, request, timeout, directory):
        assert kind == "asr" and 0 < timeout <= settings.media_timeout_seconds
        assert request["model"] == str(settings.whisper_model_path)
        assert (request["device"], request["compute_type"], request["threads"]) == (
            "cpu",
            "int8",
            4,
        )
        assert request["language"] == "vi" and request["window_seconds"] == 15
        return {
            "segments": [
                {
                    "original_text": "ETL-123: 25",
                    "source_type": "speech",
                    "source_language": "vi",
                    "start_time": 0.1,
                    "end_time": 0.8,
                }
            ],
            "provider": {"model": "configured-model", "device": "cpu", "compute_type": "int8"},
        }

    monkeypatch.setattr("data_sync_etl.adapters.media.command", decode)
    monkeypatch.setattr("data_sync_etl.adapters.media.local_model_task", run)
    extractor = MediaExtractor(settings, None, None)
    monkeypatch.setattr(extractor, "_frames", lambda *_: pytest.fail("Audio must bypass OCR"))
    result = extractor.extract(
        tmp_path / "delayed.mp4", {"source_language": "vi", "ocr_enabled": True}
    )
    assert result.text == "ETL-123: 25"
    locator = result.metadata["logical_units"][0]["locator"]
    assert (locator["start_time"], locator["end_time"]) == (2.1, 2.8)
    assert result.metadata["media"]["audio_timeline_offset"] == 2
    assert result.metadata["media"]["frame_ocr_status"] == "skipped_audio_present"
