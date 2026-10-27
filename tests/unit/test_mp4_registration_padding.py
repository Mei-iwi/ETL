"""MP4 registration accepts padding without relaxing data-box bounds."""
import pytest

from data_sync_etl.domain.format_validation import InvalidFileContent
from data_sync_etl.domain.media_registration import Mp4RegistrationValidator


def box(kind, payload=b''):
    return (8 + len(payload)).to_bytes(4, 'big') + kind + payload


def container(padding):
    return box(b'ftyp', b'isom' + bytes(4) + b'isom') + box(b'moov', b'0') + padding + box(b'mdat', b'0')


@pytest.mark.parametrize('padding', [box(b'free'), b'\x00\x00\x00\x01free' + (16).to_bytes(8, 'big'), box(b'free', b'padding')])
def test_header_only_or_populated_free_padding_is_valid(tmp_path, padding):
    path = tmp_path / 'padding.mp4'
    path.write_bytes(container(padding))
    Mp4RegistrationValidator().validate_path(path)


@pytest.mark.parametrize('padding', [box(b'moov'), box(b'mdat'), box(b'junk'), (7).to_bytes(4, 'big') + b'free',
                                   (99999).to_bytes(4, 'big') + b'free', b'\x00\x00\x00\x01free'])
def test_empty_required_boxes_truncation_and_overflow_are_rejected(tmp_path, padding):
    path = tmp_path / 'invalid.mp4'
    path.write_bytes(container(padding))
    with pytest.raises(InvalidFileContent):
        Mp4RegistrationValidator().validate_path(path)
