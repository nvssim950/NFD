from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pathlib import Path
import time

class DownloadStatus(str, Enum):
    QUEUED = "queued"
    PROBING = "probing"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    MERGING = "merging"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    ERROR = "error"

class SegmentStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"

@dataclass
class SegmentInfo:
    id: int  # unique segment ID (from DB)
    download_id: int
    index: int  # 0-based segment index
    start_byte: int
    end_byte: int  # inclusive
    bytes_downloaded: int = 0
    status: SegmentStatus = SegmentStatus.PENDING
    temp_path: Optional[Path] = None
    error_message: Optional[str] = None
    retries: int = 0

    @property
    def total_bytes(self) -> int:
        return self.end_byte - self.start_byte + 1

    @property
    def progress(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return self.bytes_downloaded / self.total_bytes

    @property
    def is_complete(self) -> bool:
        if self.status == SegmentStatus.COMPLETED:
            return True
        if self.total_bytes > 0 and self.bytes_downloaded >= self.total_bytes:
            return True
        return False

    @property
    def resume_byte(self) -> int:
        return self.start_byte + self.bytes_downloaded

@dataclass
class DownloadInfo:
    id: int
    url: str
    filename: str
    output_dir: Path
    file_size: Optional[int] = None  # None if unknown
    status: DownloadStatus = DownloadStatus.QUEUED
    num_segments: int = 1
    auto_segments: bool = False
    supports_range: bool = False
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    hash_type: Optional[str] = None  # "md5" or "sha256"
    expected_hash: Optional[str] = None
    segments: list[SegmentInfo] = field(default_factory=list)

    @property
    def bytes_downloaded(self) -> int:
        return sum(s.bytes_downloaded for s in self.segments)

    @property
    def progress(self) -> float:
        if not self.file_size or self.file_size == 0:
            return 0.0
        return self.bytes_downloaded / self.file_size

    @property
    def output_path(self) -> Path:
        return self.output_dir / self.filename

    @property
    def is_complete(self) -> bool:
        return self.status == DownloadStatus.COMPLETED

    @property
    def all_segments_complete(self) -> bool:
        return all(s.is_complete for s in self.segments)
