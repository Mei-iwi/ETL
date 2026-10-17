"""Deterministic format conversion of the existing artwork; no new dependency."""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend/src/assets/img/logoBook.png"
TARGET = ROOT / "frontend/public/favicon.png"


def render():
    with Image.open(SOURCE) as image:
        return image.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)


if __name__ == "__main__":
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    render().save(TARGET, format="PNG", optimize=True)
    print("FAVICON: existing artwork converted to genuine 64x64 PNG")
