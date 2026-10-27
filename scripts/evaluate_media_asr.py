"""Evaluate explicitly supplied local audio/reference pairs without network access."""

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path
from time import monotonic

from data_sync_etl.adapters.media import local_model_task


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for index, value in enumerate(left, 1):
        current = [index]
        for position, other in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[position] + 1,
                    previous[position - 1] + (value != other),
                )
            )
        previous = current
    return previous[-1]


def metrics(reference, hypothesis, language):
    def normalize(text):
        return re.sub(r"[^\w\s]", "", text.casefold()).split()

    expected, actual = normalize(reference), normalize(hypothesis)
    characters = "".join(expected)
    return {
        "wer_whitespace": (
            edit_distance(expected, actual) / max(len(expected), 1)
            if language not in ("ja", "zh", "ko")
            else None
        ),
        "cer": edit_distance(characters, "".join(actual)) / max(len(characters), 1),
        "normalization": "casefold; discard punctuation/whitespace for CER; whitespace tokens for WER",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--samples", type=Path, default=Path(".test-tmp/media-tools"))
    parser.add_argument("--languages", nargs="+", default=["vi_vn", "fr_fr", "ja_jp"])
    parser.add_argument("--report", type=Path, default=Path(".test-tmp/media-quality.json"))
    parser.add_argument(
        "--timeout", type=int, default=120, help="Finite timeout per sample, including model load"
    )
    args = parser.parse_args()
    if not 1 <= args.timeout <= 7200:
        parser.error("--timeout must be between 1 and 7200 seconds")
    rows = []
    for code in args.languages:
        audio = args.samples / (code + ".wav")
        reference = json.loads(
            (args.samples / (code + "-reference.json")).read_text(encoding="utf-8")
        )
        started = monotonic()
        with tempfile.TemporaryDirectory(prefix="etl-evaluate-") as scratch:
            result = local_model_task(
                "asr",
                {
                    "audio": str(audio.resolve()),
                    "model": str(args.model.resolve()),
                    "device": "cpu",
                    "compute_type": "int8",
                    "threads": 4,
                    "language": None,
                    "window_seconds": 15,
                },
                args.timeout,
                scratch,
            )
        text = " ".join(s["original_text"] for s in result["segments"])
        rows.append(
            {
                "dataset": "google/fleurs",
                "sample_id": reference["id"],
                "language": code,
                "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                "duration_seconds": reference["num_samples"] / 16000,
                "seconds": round(monotonic() - started, 3),
                "reference": reference["raw_transcription"],
                "hypothesis": text,
                **metrics(reference["transcription"], text, code.split("_")[0]),
                "provider": result["provider"],
                "timings": result.get("timings"),
                "segments": result["segments"],
            }
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            [
                {key: row[key] for key in ("language", "seconds", "wer_whitespace", "cer")}
                for row in rows
            ]
        )
    )


if __name__ == "__main__":
    main()
