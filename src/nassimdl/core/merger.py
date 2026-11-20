"""Merge downloaded segment temp files into the final output file."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MERGE_BUFFER_SIZE = 8 * 1024 * 1024  # 8 MB buffer for fast multi-GB disk merges


class FileMerger:
    """Merges segment temporary files into a single output file."""

    @staticmethod
    async def merge(
        segment_paths: list[Path],
        output_path: Path,
        expected_size: Optional[int] = None,
        on_progress: Optional[callable] = None,
    ) -> Path:
        """
        Merge segment files in order into the final output file.
        
        Args:
            segment_paths: Ordered list of segment temp file paths.
            output_path: Final destination path.
            expected_size: Expected total file size for verification.
            on_progress: Optional callback(bytes_merged, total_bytes).
            
        Returns:
            Path to the merged output file.
            
        Raises:
            FileNotFoundError: If a segment file is missing.
            ValueError: If merged size doesn't match expected size.
        """
        # Verify all segment files exist
        for i, path in enumerate(segment_paths):
            if not path.exists():
                raise FileNotFoundError(
                    f"Segment {i} temp file not found: {path}"
                )

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Handle filename conflicts
        output_path = FileMerger._resolve_conflict(output_path)

        total_merged = 0
        total_expected = expected_size or sum(p.stat().st_size for p in segment_paths)

        logger.info(f"Merging {len(segment_paths)} segments into {output_path}")

        # Merge in a thread to avoid blocking the event loop
        def _merge_sync():
            nonlocal total_merged
            with open(output_path, "wb") as outfile:
                for seg_path in segment_paths:
                    with open(seg_path, "rb") as seg_file:
                        while True:
                            chunk = seg_file.read(MERGE_BUFFER_SIZE)
                            if not chunk:
                                break
                            outfile.write(chunk)
                            total_merged += len(chunk)

        await asyncio.get_event_loop().run_in_executor(None, _merge_sync)

        # Verify size
        actual_size = output_path.stat().st_size
        if expected_size and actual_size != expected_size:
            raise ValueError(
                f"Merged file size mismatch: expected {expected_size}, got {actual_size}"
            )

        logger.info(f"Merge complete: {output_path} ({actual_size} bytes)")
        return output_path

    @staticmethod
    def _resolve_conflict(path: Path) -> Path:
        """If file exists, add (1), (2), etc. to the filename."""
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            new_path = parent / f"{stem} ({counter}){suffix}"
            if not new_path.exists():
                return new_path
            counter += 1

    @staticmethod
    async def cleanup_segments(segment_paths: list[Path]) -> None:
        """Remove segment temp files after successful merge."""
        for path in segment_paths:
            try:
                if path.exists():
                    path.unlink()
                    logger.debug(f"Removed temp segment: {path}")
            except OSError as e:
                logger.warning(f"Failed to remove temp segment {path}: {e}")
