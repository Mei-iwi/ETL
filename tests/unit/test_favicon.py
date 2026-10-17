from hashlib import sha256
from io import BytesIO

from PIL import Image

from scripts.build_favicon import SOURCE, TARGET, render


def test_favicon_is_real_png_derived_from_unchanged_avif_artwork():
    before = sha256(SOURCE.read_bytes()).hexdigest()
    with Image.open(SOURCE) as original:
        assert original.format == "AVIF" and original.size == (626, 626)
    data = TARGET.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(data)) as favicon:
        assert favicon.format == "PNG" and favicon.size == (64, 64)
        assert favicon.convert("RGBA").tobytes() == render().tobytes()
    assert sha256(SOURCE.read_bytes()).hexdigest() == before
