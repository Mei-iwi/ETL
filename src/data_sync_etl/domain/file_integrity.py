from hashlib import sha256
from typing import BinaryIO


def stream_hash(stream: BinaryIO) -> str:
    digest = sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()
