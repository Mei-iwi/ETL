"""Local, bounded media extraction. External services are never invoked."""

import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from time import monotonic

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.format_validation import InvalidFileContent
from data_sync_etl.ports.processing import OCRResult


def command(args, timeout, code="MEDIA_DECODE_FAILED"):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=True)
    except FileNotFoundError:
        raise DomainError("MEDIA_RUNTIME_UNAVAILABLE") from None
    except subprocess.TimeoutExpired:
        raise DomainError("MEDIA_TIMEOUT") from None
    except (OSError, subprocess.CalledProcessError):
        raise DomainError(code) from None


def local_model_task(kind, payload, timeout, directory):
    """Use a killable child, with private file IPC rather than text in argv/logs."""
    request = Path(directory) / (kind + "-request.json")
    response = Path(directory) / (kind + "-response.json")
    request.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    command(
        [
            sys.executable,
            "-m",
            "data_sync_etl.adapters.media_runtime",
            kind,
            str(request),
            str(response),
        ],
        timeout,
        "MODEL_RUNTIME_UNAVAILABLE",
    )
    try:
        if response.stat().st_size > 16_000_000:
            raise DomainError("EXTRACTION_LIMIT_EXCEEDED")
        result = json.loads(response.read_text(encoding="utf-8"))
        if "error" in result:
            raise DomainError(result["error"])
        return result
    except DomainError:
        raise
    except (OSError, ValueError):
        raise DomainError("MODEL_RUNTIME_UNAVAILABLE") from None


def probe(path, settings, expected_extension=None):
    if path.stat().st_size > settings.media_max_size_bytes:
        raise InvalidFileContent("MEDIA_SIZE_EXCEEDED")
    raw = command(
        [
            settings.ffprobe_path,
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path.resolve()),
        ],
        min(settings.media_timeout_seconds, 30),
        "INVALID_MEDIA",
    ).stdout
    try:
        if len(raw) > 1_000_000:
            raise ValueError
        data = json.loads(raw)
        duration = float(data["format"]["duration"])
        formats = data["format"]["format_name"].split(",")
        streams = data["streams"]
        if not math.isfinite(duration) or duration <= 0 or len(streams) > 64:
            raise ValueError
        if (expected_extension or path.suffix.lower()) == ".mp3":
            valid = "mp3" in formats and any(
                s["codec_type"] == "audio" and s.get("codec_name") == "mp3" for s in streams
            )
        else:
            valid = "mp4" in formats and any(
                s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")
                for s in streams
            )
        if not valid:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise InvalidFileContent("INVALID_MEDIA") from None
    if duration > settings.media_max_duration_seconds:
        raise InvalidFileContent("MEDIA_DURATION_EXCEEDED")
    try:
        origin = float(data["format"].get("start_time", 0))
    except (ValueError, TypeError):
        raise InvalidFileContent("INVALID_MEDIA") from None
    if not math.isfinite(origin):
        raise InvalidFileContent("INVALID_MEDIA")
    return {
        "duration": duration,
        "timeline_origin": origin,
        "streams": [
            {
                k: s[k]
                for k in ("index", "codec_type", "codec_name", "tags", "disposition", "start_time")
                if k in s
            }
            for s in streams
        ],
    }


def select_track(streams, kind, requested=None):
    candidates = [s for s in streams if s["codec_type"] == kind]
    if requested is not None:
        found = [s for s in candidates if s["index"] == requested]
        if not found:
            raise DomainError("INVALID_MEDIA_TRACK")
        return found[0]
    # Container default first, then the lowest absolute stream index.
    return (
        min(candidates, key=lambda s: (not s.get("disposition", {}).get("default"), s["index"]))
        if candidates
        else None
    )


def parse_srt(text, duration):
    pattern = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            continue
        matches = pattern.findall(lines[timing])
        if len(matches) != 2:
            continue
        start, end = [
            int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000 for h, m, s, ms in matches
        ]
        body = re.sub(r"<[^>]+>", "", "\n".join(lines[timing + 1 :])).strip()
        if body and 0 <= start < end <= duration + 0.1:
            yield {
                "original_text": body,
                "source_type": "subtitle",
                "start_time": start,
                "end_time": min(end, duration),
            }


class MediaExtractor:
    def __init__(self, settings, storage, engine):
        self.settings, self.storage, self.engine = settings, storage, engine

    def extract(self, path, options):
        settings = self.settings
        info = probe(path, settings)
        started = monotonic()

        def remaining():
            value = settings.media_timeout_seconds - (monotonic() - started)
            if value <= 0:
                raise DomainError("MEDIA_TIMEOUT")
            return value

        segments, warnings = [], []

        def requested_track(kind):
            requested = options.get(kind + "_track")
            try:
                return select_track(info["streams"], kind, requested)
            except DomainError:
                # An absent or mismatched optional track must not prevent the
                # other extraction branches from processing valid content.
                warnings.append(f"INVALID_{kind.upper()}_TRACK_USING_DEFAULT")
                info["requested_" + kind + "_track"] = requested
                return select_track(info["streams"], kind)

        audio = requested_track("audio")
        subtitle = requested_track("subtitle")
        video = select_track(
            [s for s in info["streams"] if not s.get("disposition", {}).get("attached_pic")],
            "video",
        )
        info.update(
            selected_audio_track=audio["index"] if audio else None,
            selected_subtitle_track=subtitle["index"] if subtitle else None,
            selected_video_track=video["index"] if video else None,
            audio_status="no_audio" if not audio else "not_processed",
            visual_status="unavailable" if options.get("visual_enabled") else "disabled",
        )
        ff = [
            settings.ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-threads",
            str(settings.media_cpu_threads),
            "-protocol_whitelist",
            "file",
            "-i",
            str(path.resolve()),
        ]
        with tempfile.TemporaryDirectory(prefix="etl-media-") as scratch:
            directory = Path(scratch)
            if subtitle:
                if subtitle.get("codec_name") in (
                    "mov_text",
                    "subrip",
                    "ass",
                    "ssa",
                    "webvtt",
                    "text",
                ):
                    output = directory / "subtitles.srt"
                    try:
                        command(
                            [*ff, "-map", f"0:{subtitle['index']}", "-f", "srt", "-y", str(output)],
                            remaining(),
                        )
                        if output.stat().st_size > 4_000_000:
                            raise DomainError("EXTRACTION_LIMIT_EXCEEDED")
                        for segment in parse_srt(
                            output.read_text(encoding="utf-8-sig"), info["duration"]
                        ):
                            segment["source_language"] = subtitle.get("tags", {}).get(
                                "language", "und"
                            )
                            segment["track_index"] = subtitle["index"]
                            segments.append(segment)
                    except DomainError as exc:
                        warnings.append(str(exc))
                else:
                    warnings.append("IMAGE_SUBTITLE_REQUIRES_OCR")
            if audio:
                output = directory / "audio.wav"
                try:
                    offset = max(
                        0,
                        float(audio.get("start_time", info["timeline_origin"]))
                        - info["timeline_origin"],
                    )
                    if not math.isfinite(offset):
                        raise DomainError("INVALID_MEDIA")
                    info["audio_timeline_offset"] = offset
                    command(
                        [
                            *ff,
                            "-map",
                            f"0:{audio['index']}",
                            "-vn",
                            "-ac",
                            "1",
                            "-ar",
                            "16000",
                            "-t",
                            str(settings.media_max_duration_seconds),
                            "-f",
                            "wav",
                            "-y",
                            str(output),
                        ],
                        remaining(),
                    )
                    # Exact zero PCM is distinct from audio rejected by VAD.
                    with wave.open(str(output), "rb") as wav:
                        silent = True
                        while pcm := wav.readframes(16000):
                            if any(pcm):
                                silent = False
                                break
                    if silent:
                        info["audio_status"] = "silent"
                    else:
                        result = local_model_task(
                            "asr",
                            {
                                "audio": str(output),
                                "model": str(settings.whisper_model_path or ""),
                                "device": settings.whisper_device,
                                "compute_type": settings.whisper_compute_type,
                                "threads": settings.media_cpu_threads,
                                "language": options.get("source_language"),
                                "window_seconds": settings.asr_window_seconds,
                                "max_segments": settings.media_max_segments,
                            },
                            remaining(),
                            directory,
                        )
                        for segment in result["segments"]:
                            segment["start_time"] += offset
                            segment["end_time"] = min(
                                info["duration"], segment["end_time"] + offset
                            )
                            if segment["start_time"] < segment["end_time"]:
                                segments.append(segment)
                        info["audio_status"] = (
                            "speech" if result["segments"] else "no_speech_detected"
                        )
                        info["asr"] = result["provider"]
                except DomainError as exc:
                    info["audio_status"] = (
                        "decode_failed" if str(exc) == "MEDIA_DECODE_FAILED" else "unavailable"
                    )
                    warnings.append(str(exc))
            if path.suffix.lower() == ".mp4":
                if audio:
                    info["frame_ocr_status"] = "skipped_audio_present"
                elif options.get("ocr_enabled", True):
                    info["frame_ocr_status"] = "completed"
                    try:
                        self._frames(ff, directory, info, segments, options, remaining)
                    except DomainError as exc:
                        info["frame_ocr_status"] = "failed"
                        warnings.append(str(exc))
                else:
                    info["frame_ocr_status"] = "disabled"
        if options.get("visual_enabled"):
            warnings.append("VISUAL_MODEL_UNAVAILABLE")
        blocking = [
            warning
            for warning in warnings
            if warning not in (
                "VISUAL_MODEL_UNAVAILABLE", "IMAGE_SUBTITLE_REQUIRES_OCR",
                "INVALID_AUDIO_TRACK_USING_DEFAULT", "INVALID_SUBTITLE_TRACK_USING_DEFAULT",
            )
        ]
        if not segments and blocking:
            raise DomainError(blocking[0])
        if (
            len(segments) > settings.media_max_segments
            or sum(len(s["original_text"]) for s in segments) > 1_000_000
        ):
            raise DomainError("EXTRACTION_LIMIT_EXCEEDED")
        texts, units, offset = [], [], 0
        for segment in segments:
            text = segment["original_text"]
            units.append(
                {
                    "start": offset,
                    "end": offset + len(text),
                    "title": segment["source_type"],
                    "locator": {k: v for k, v in segment.items() if k != "original_text"},
                }
            )
            texts.append(text)
            offset += len(text) + 2
        return OCRResult(
            "\n\n".join(texts),
            engine_name="local_media",
            engine_version="1",
            metadata={
                "source_type": "media",
                "logical_units": units,
                "media": info,
                "warnings": warnings,
                "extraction_status": "partial"
                if warnings and segments
                else "completed"
                if segments
                else "no_extractable_content",
            },
        )

    def _frames(self, ff, directory, info, segments, options, remaining):
        settings = self.settings
        previous_image, previous_text = None, None
        scene_budget = settings.media_max_frames // 2 if settings.media_scene_detection else 0
        periodic_budget = settings.media_max_frames - scene_budget
        interval = max(settings.media_frame_interval_seconds, info["duration"] / periodic_budget)
        info["frame_sampling"] = {
            "interval_seconds": settings.media_frame_interval_seconds,
            "effective_interval_seconds": interval,
            "max_frames": settings.media_max_frames,
            "scene_detection": settings.media_scene_detection,
        }
        candidates = []
        scale = f"scale={settings.media_frame_width}:{settings.media_frame_width}:force_original_aspect_ratio=decrease"
        video = next(s for s in info["streams"] if s["index"] == info["selected_video_track"])
        video_offset = max(
            0, float(video.get("start_time", info["timeline_origin"])) - info["timeline_origin"]
        )
        info["video_timeline_offset"] = video_offset
        # Timestamp is the actual requested observation, never an invented display interval.
        for index in range(min(periodic_budget, math.ceil(info["duration"] / interval))):
            timestamp = video_offset + index * interval
            if timestamp >= info["duration"]:
                break
            image_path = directory / f"periodic-{index}.png"
            result = command(
                [
                    *ff[:-2],
                    "-ss",
                    str(timestamp),
                    *ff[-2:],
                    "-v",
                    "info",
                    "-map",
                    f"0:{info['selected_video_track']}",
                    "-frames:v",
                    "1",
                    "-vf",
                    scale + ",showinfo",
                    "-y",
                    str(image_path),
                ],
                remaining(),
            )
            if image_path.exists():
                observed = re.search(rb"pts_time:([0-9.]+)", result.stderr)
                if observed:
                    candidates.append((timestamp + float(observed[1]), image_path))
        if scene_budget:
            # showinfo reports original PTS after select; never infer timestamps from frame indices.
            try:
                result = command(
                    [
                        *ff,
                        "-v",
                        "info",
                        "-map",
                        f"0:{info['selected_video_track']}",
                        "-vf",
                        f"select='gt(scene,{settings.media_scene_threshold})',{scale},showinfo",
                        "-fps_mode",
                        "vfr",
                        "-frames:v",
                        str(scene_budget),
                        "-y",
                        str(directory / "scene-%03d.png"),
                    ],
                    min(remaining(), 60),
                )
                timestamps = [float(t) for t in re.findall(rb"pts_time:([0-9.]+)", result.stderr)]
                for timestamp, image_path in zip(
                    timestamps, sorted(directory.glob("scene-*.png")), strict=False
                ):
                    if 0 <= timestamp <= info["duration"]:
                        candidates.append((timestamp, image_path))
            except DomainError:
                info["frame_sampling"]["scene_status"] = "unavailable_periodic_retained"
        for timestamp, image_path in sorted(candidates):
            if not image_path.exists():
                continue
            image_hash = hashlib.sha256(image_path.read_bytes()).digest()
            if image_hash == previous_image:
                continue
            previous_image = image_hash
            engine = self.engine
            if options.get("ocr_languages"):
                from data_sync_etl.adapters.ocr.engines import TesseractOCREngine

                engine = TesseractOCREngine(
                    settings.tesseract_path, options["ocr_languages"], settings.ocr_timeout_seconds
                )
            result = engine.recognize(image_path)
            text = result.text.strip()
            if not text or text == previous_text:
                previous_text = text
                continue
            previous_text = text
            with image_path.open("rb") as stream:
                stored = self.storage.save_stream(stream, suffix=".png")
            # Published content-addressed bytes may already be shared by another
            # upload. Never unlink them on later failure; private scratch is
            # cleaned by TemporaryDirectory and orphan inventory remains explicit.
            segments.append(
                {
                    "original_text": text,
                    "source_type": "ocr",
                    "source_language": options.get("source_language") or "und",
                    "observed_at": timestamp,
                    "start_time": None,
                    "end_time": None,
                    "frame": {"bucket": stored.bucket, "object_key": stored.object_key},
                    "ocr_engine": result.engine_name,
                    "ocr_version": result.engine_version,
                }
            )
