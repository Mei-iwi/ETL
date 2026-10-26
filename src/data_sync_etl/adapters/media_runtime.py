"""Isolated local model process. No implicit model downloads or remote APIs."""

import importlib.metadata
import json
import os
import re
import sys
from pathlib import Path
from time import monotonic


def speech_windows(intervals, maximum_samples, gap_samples=16000):
    windows = []
    for interval in intervals:
        if (
            windows
            and interval["start"] - windows[-1]["end"] <= gap_samples
            and interval["end"] - windows[-1]["start"] <= maximum_samples
        ):
            windows[-1]["end"] = interval["end"]
        else:
            windows.append(dict(interval))
    return windows


def transcribe(payload):
    model_path = Path(payload["model"])
    # faster-whisper may fetch a tokenizer when tokenizer.json is absent,
    # even with local_files_only=True. Reject incomplete installs first.
    required = ("config.json", "model.bin", "tokenizer.json")
    if (
        not payload["model"]
        or not model_path.is_dir()
        or model_path.name.endswith(".en")
        or any(not (model_path / name).is_file() for name in required)
        or not any((model_path / name).is_file() for name in ("vocabulary.json", "vocabulary.txt"))
    ):
        return {"error": "STT_RUNTIME_UNAVAILABLE"}

    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    started = monotonic()
    try:
        model = WhisperModel(
            str(model_path),
            device=payload["device"],
            compute_type=payload["compute_type"],
            cpu_threads=payload["threads"],
            num_workers=1,
            local_files_only=True,
        )
    except (OSError, ValueError, RuntimeError):
        return {"error": "STT_RUNTIME_UNAVAILABLE"}
    load_seconds = monotonic() - started
    revision = None
    metadata = model_path / ".cache/huggingface/download/model.bin.metadata"
    try:
        commit = metadata.read_text(encoding="utf-8").splitlines()[0]
        if re.fullmatch(r"[0-9a-f]{40}", commit):
            revision = commit
    except (OSError, UnicodeError, IndexError):
        pass  # Optional download provenance must not prevent local inference.
    if not model.model.is_multilingual:
        return {"error": "STT_MULTILINGUAL_MODEL_REQUIRED"}
    started = monotonic()
    audio = decode_audio(payload["audio"], sampling_rate=16000)
    speech = get_speech_timestamps(
        audio,
        VadOptions(min_silence_duration_ms=300, max_speech_duration_s=payload["window_seconds"]),
    )
    output = []
    for interval in speech_windows(speech, payload["window_seconds"] * 16000):
        start, end = interval["start"], interval["end"]
        # Independent VAD windows permit language changes; use absolute source offsets.
        samples = np.asarray(audio[start:end])
        language = payload.get("language")
        segments, info = model.transcribe(
            samples,
            task="transcribe",
            language=language,
            vad_filter=True,
            condition_on_previous_text=False,
            beam_size=3,
        )
        for segment in segments:
            if not segment.text.strip() or segment.no_speech_prob > 0.6 or segment.avg_logprob < -1:
                continue
            if len(output) >= payload.get("max_segments", 5000):
                return {"error": "EXTRACTION_LIMIT_EXCEEDED"}
            output.append(
                {
                    "original_text": segment.text.strip(),
                    "source_type": "speech",
                    "source_language": language
                    or (info.language if info.language_probability >= 0.5 else "und"),
                    "start_time": start / 16000 + segment.start,
                    "end_time": min(end / 16000, start / 16000 + segment.end),
                    "asr_scores": {
                        "avg_logprob": segment.avg_logprob,
                        "no_speech_prob": segment.no_speech_prob,
                    },
                }
            )
    return {
        "segments": output,
        "provider": {
            "name": "faster-whisper",
            "version": importlib.metadata.version("faster-whisper"),
            "model": model_path.name,
            "revision": revision,
            "device": payload["device"],
            "compute_type": payload["compute_type"],
            "language_detection": "independent_vad_windows",
            "beam_size": 3,
            "condition_on_previous_text": False,
            "window_seconds": payload["window_seconds"],
            "cpu_threads": payload["threads"],
            "ctranslate2_version": importlib.metadata.version("ctranslate2"),
        },
        "timings": {
            "model_load_seconds": round(load_seconds, 3),
            "decode_vad_inference_seconds": round(monotonic() - started, 3),
        },
    }


def translate(payload):
    import ctranslate2

    # Read only explicitly installed packages; no SBD auto-downloads or user-wide settings.
    os.environ["ARGOS_PACKAGES_DIR"] = payload["model_root"]
    os.environ["XDG_DATA_HOME"] = str(Path(payload["scratch"]) / "data")
    os.environ["XDG_CONFIG_HOME"] = str(Path(payload["scratch"]) / "config")
    os.environ["XDG_CACHE_HOME"] = str(Path(payload["scratch"]) / "cache")
    from argostranslate.package import Package

    root = Path(payload["model_root"])
    packages = []
    for path in root.glob("*/metadata.json"):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if (
            metadata.get("from_code") == payload["source"]
            and metadata.get("to_code") == payload["target"]
        ):
            packages.append(path.parent)
    if not packages:
        return {"error": "TRANSLATION_MODEL_UNAVAILABLE"}
    package = Package(sorted(packages)[-1])
    translator = ctranslate2.Translator(
        str(package.package_path / "model"),
        device="cpu",
        compute_type="int8",
        inter_threads=1,
        intra_threads=payload["threads"],
    )
    sentences = [s for s in re.split(r"(?<=[.!?。！？])\s+|\n+", payload["text"]) if s.strip()]
    tokenized = [package.tokenizer.encode(sentence) for sentence in sentences]
    results = translator.translate_batch(
        tokenized,
        beam_size=4,
        max_input_length=0,
        max_decoding_length=4096,
        target_prefix=[[package.target_prefix]] * len(sentences) if package.target_prefix else None,
    )
    translated = []
    for result in results:
        if len(result.hypotheses[0]) >= 4096:
            return {"error": "TRANSLATION_OUTPUT_LIMIT"}
        text = package.tokenizer.decode(result.hypotheses[0])
        if package.target_prefix and text.startswith(package.target_prefix):
            text = text[len(package.target_prefix) :]
        if not text.strip():
            return {"error": "EMPTY_TRANSLATION"}
        translated.append(text.strip())
    return {
        "text": "\n".join(translated),
        "provider": "argos-ctranslate2",
        "version": importlib.metadata.version("argostranslate"),
        "model_version": package.package_version,
    }


def main():
    kind, request, response = sys.argv[1:]
    try:
        payload = json.loads(Path(request).read_text(encoding="utf-8"))
        result = {"asr": transcribe, "translate": translate}[kind](payload)
    except Exception:
        result = {
            "error": "STT_RUNTIME_UNAVAILABLE" if kind == "asr" else "TRANSLATION_MODEL_UNAVAILABLE"
        }
    Path(response).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
