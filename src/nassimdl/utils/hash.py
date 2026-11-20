"""File integrity verification using hash checksums."""

import hashlib
from pathlib import Path
from typing import Optional


SUPPORTED_ALGORITHMS = {"md5", "sha256", "sha1", "sha512"}
CHUNK_SIZE = 8192 * 1024  # 8 MB chunks for hashing


async def compute_hash(file_path: Path, algorithm: str = "sha256") -> str:
    """Compute the hash of a file. Runs in chunks to avoid memory issues."""
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}. Use one of {SUPPORTED_ALGORITHMS}")

    hasher = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


async def verify_hash(file_path: Path, expected_hash: str,
                      algorithm: str = "sha256") -> bool:
    """Verify a file's hash against an expected value."""
    actual_hash = await compute_hash(file_path, algorithm)
    return actual_hash.lower() == expected_hash.lower()


def detect_hash_type(hash_string: str) -> Optional[str]:
    """Guess hash algorithm from string length."""
    lengths = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}
    return lengths.get(len(hash_string))
