"""Bounded container validation for registration only. No decoding or ASR."""

from data_sync_etl.domain.format_validation import InvalidFileContent, _BaseFormatValidator


class Mp3RegistrationValidator(_BaseFormatValidator):
    extension = '.mp3'
    mime_types = frozenset({'audio/mpeg'})
    max_size_bytes = 50 * 1024 * 1024
    media_category = 'AUDIO'

    def _matches_signature(self, header):
        return header.startswith(b'ID3') or len(header) >= 2 and header[0] == 255 and header[1] & 224 == 224

    def validate_path(self, path):
        with path.open('rb') as stream:
            first = stream.read(10)
            offset = 0
            if first[:3] == b'ID3':
                if first[3] not in (2, 3, 4) or any(value & 128 for value in first[6:10]):
                    raise InvalidFileContent('INVALID_MEDIA')
                offset = sum(value << (7 * (3 - i)) for i, value in enumerate(first[6:10])) + 10
                if first[3] == 4 and first[5] & 16:
                    offset += 10
                if offset > 1024 * 1024:
                    raise InvalidFileContent('INVALID_MEDIA')
            size = path.stat().st_size
            # Two complete MPEG Layer III frames prove more than an ID3 prefix.
            for _ in range(2):
                stream.seek(offset)
                header = stream.read(4)
                if len(header) != 4 or header[0] != 255 or header[1] & 224 != 224:
                    raise InvalidFileContent('INVALID_MEDIA')
                version, layer = (header[1] >> 3) & 3, (header[1] >> 1) & 3
                bitrate, rate = header[2] >> 4, (header[2] >> 2) & 3
                if version == 1 or layer != 1 or bitrate in (0, 15) or rate == 3:
                    raise InvalidFileContent('INVALID_MEDIA')
                rates = [44100, 48000, 32000]
                sample_rate = rates[rate] // (1 if version == 3 else 2 if version == 2 else 4)
                table = ([0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
                         if version == 3 else [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160])
                offset += (144000 if version == 3 else 72000) * table[bitrate] // sample_rate + ((header[2] >> 1) & 1)
                if offset > size:
                    raise InvalidFileContent('INVALID_MEDIA')


class Mp4RegistrationValidator(_BaseFormatValidator):
    extension = '.mp4'
    mime_types = frozenset({'video/mp4'})
    max_size_bytes = 50 * 1024 * 1024
    media_category = 'VIDEO'

    def _matches_signature(self, header):
        return len(header) >= 12 and header[4:8] == b'ftyp' and header[8:12] in {
            b'isom', b'iso2', b'iso4', b'iso5', b'iso6', b'mp41', b'mp42', b'avc1', b'M4V '}

    def validate_path(self, path):
        size, offset, seen = path.stat().st_size, 0, set()
        with path.open('rb') as stream:
            for _ in range(10000):
                if offset == size:
                    break
                stream.seek(offset)
                header = stream.read(8)
                if len(header) != 8:
                    raise InvalidFileContent('INVALID_MEDIA')
                length, kind = int.from_bytes(header[:4], 'big'), header[4:]
                minimum = 8
                if length == 1:
                    extra = stream.read(8)
                    if len(extra) != 8:
                        raise InvalidFileContent('INVALID_MEDIA')
                    length, minimum = int.from_bytes(extra, 'big'), 16
                if length == 0:
                    length = size - offset
                if length <= minimum or offset + length > size or (offset == 0 and kind != b'ftyp'):
                    raise InvalidFileContent('INVALID_MEDIA')
                if kind == b'ftyp' and (length < 20 or length > 1024):
                    raise InvalidFileContent('INVALID_MEDIA')
                seen.add(kind)
                offset += length
            if offset != size or not {b'ftyp', b'moov', b'mdat'} <= seen:
                raise InvalidFileContent('INVALID_MEDIA')
