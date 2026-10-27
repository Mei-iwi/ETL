"""Download public test audio only after an explicit flag; never used by runtime."""

import argparse
import json
from pathlib import Path
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-public-samples", action="store_true", required=True)
    parser.add_argument("--directory", type=Path, default=Path(".test-tmp/media-tools"))
    parser.add_argument(
        "--languages",
        nargs="+",
        choices=["vi_vn", "fr_fr", "ja_jp"],
        default=["vi_vn", "fr_fr", "ja_jp"],
    )
    args = parser.parse_args()
    # Optional test tooling, deliberately not part of the service dependencies.
    import fsspec
    import pyarrow.parquet as parquet

    args.directory.mkdir(parents=True, exist_ok=True)
    english = args.directory / "en.flac"
    if not english.exists():
        with urlopen(
            "https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/tests/data/jfk.flac",
            timeout=60,
        ) as response:
            english.write_bytes(response.read(5_000_000))
    for code in args.languages:
        audio = args.directory / (code + ".wav")
        reference = args.directory / (code + "-reference.json")
        if audio.exists() and reference.exists():
            continue
        url = f"https://huggingface.co/datasets/google/fleurs/resolve/refs%2Fconvert%2Fparquet/{code}/test/0000.parquet"
        with fsspec.open(url, "rb", block_size=1024 * 1024) as stream:
            row = next(parquet.ParquetFile(stream).iter_batches(batch_size=1)).to_pylist()[0]
        raw = row.pop("audio")["bytes"]
        row.pop("path", None)  # Dataset publisher's cache path is not useful provenance.
        audio.write_bytes(raw)
        reference.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
        print(f"{code}: public FLEURS sample {row['id']}, {len(raw)} bytes")


if __name__ == "__main__":
    main()
