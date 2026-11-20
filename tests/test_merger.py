"""Unit tests for FileMerger and hash verification."""

import pytest
from pathlib import Path
from nassimdl.core.merger import FileMerger
from nassimdl.utils.hash import compute_hash, verify_hash


@pytest.mark.asyncio
async def test_file_merger(tmp_path: Path):
    seg1 = tmp_path / "seg1.part"
    seg2 = tmp_path / "seg2.part"
    output = tmp_path / "output.bin"

    data1 = b"Hello, "
    data2 = b"World!"
    seg1.write_bytes(data1)
    seg2.write_bytes(data2)

    result_path = await FileMerger.merge([seg1, seg2], output, expected_size=len(data1) + len(data2))
    assert result_path.exists()
    assert result_path.read_bytes() == b"Hello, World!"

    # Test cleanup
    await FileMerger.cleanup_segments([seg1, seg2])
    assert not seg1.exists()
    assert not seg2.exists()


@pytest.mark.asyncio
async def test_hash_verification(tmp_path: Path):
    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"Test content for hashing")

    sha256 = await compute_hash(test_file, "sha256")
    assert isinstance(sha256, str)
    assert len(sha256) == 64

    is_valid = await verify_hash(test_file, sha256, "sha256")
    assert is_valid is True

    is_invalid = await verify_hash(test_file, "wrong_hash", "sha256")
    assert is_invalid is False
