import os
import subprocess
import tempfile
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from data_sync_etl.adapters.ocr.engines import TesseractOCREngine

pytestmark = pytest.mark.skipif(os.environ.get('ETL_DOCKER_NATIVE_TEST') != '1', reason='Linux container native runtime required')


def test_python312_nonroot_cpu_and_languages():
    import sys

    import torch
    assert sys.version_info[:2] == (3, 12)
    assert os.getuid() == 10001
    assert torch.version.cuda is None
    result = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, check=True, timeout=10)
    assert {'vie', 'eng'} <= set(result.stdout.splitlines())
    from data_sync_etl.main import app
    assert app.title


def test_real_vietnamese_english_scan_and_native_pdf():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'scan.png'
        image = Image.new('RGB', (1500, 240), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 48)
        draw.text((30, 30), 'Học sinh học Toán và Tiếng Việt.', font=font, fill='black')
        draw.text((30, 110), 'Education and mathematics', font=font, fill='black')
        image.save(path)
        result = TesseractOCREngine('/usr/bin/tesseract', 'vie+eng', 45).recognize(path)
        assert 'Tiếng Việt' in result.text and 'Education' in result.text
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), 'Native PDF text for ETL.')
            assert 'Native PDF' in pdf[0].get_text()
