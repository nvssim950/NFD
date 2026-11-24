from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import sys

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib

@dataclass
class DownloadConfig:
    max_concurrent: int = 3
    default_segments: int = 8
    output_dir: Path = field(default_factory=lambda: Path.home() / "Downloads")
    temp_dir: Path = field(default_factory=lambda: Path.home() / ".nassimdl" / "tmp")
    segments_by_size: dict[int, int] = field(default_factory=lambda: {
        1: 1, 10: 4, 100: 8, 1000: 16
    })

    def get_segments_for_size(self, size_bytes: int) -> int:
        """Return the optimal number of segments based on file size."""
        size_mb = size_bytes / (1024 * 1024)
        result = 1
        for threshold_mb, segments in sorted(self.segments_by_size.items()):
            if size_mb >= threshold_mb:
                result = segments
        return result

@dataclass
class NetworkConfig:
    max_speed_bytes: int = 0  # 0 = unlimited
    timeout_seconds: int = 30
    max_retries: int = 5
    retry_delay_seconds: float = 2.0
    user_agent: str = "NassimDL/0.1.0"

@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Path = field(default_factory=lambda: Path.home() / ".nassimdl" / "nassimdl.log")

@dataclass
class AppConfig:
    download: DownloadConfig = field(default_factory=DownloadConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    db_path: Path = field(default_factory=lambda: Path.home() / ".nassimdl" / "nassimdl.db")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AppConfig":
        """Load config from TOML file, falling back to defaults."""
        config = cls()
        if path is None:
            path = Path.home() / ".nassimdl" / "config.toml"
        if path.exists():
            with open(path, "rb") as f:
                data = tomllib.load(f)
            dl = data.get("download", {})
            if "max_concurrent" in dl:
                config.download.max_concurrent = dl["max_concurrent"]
            if "default_segments" in dl:
                config.download.default_segments = dl["default_segments"]
            if "output_dir" in dl:
                config.download.output_dir = Path(dl["output_dir"]).expanduser()
            if "temp_dir" in dl:
                config.download.temp_dir = Path(dl["temp_dir"]).expanduser()
            if "segments_by_size" in dl:
                config.download.segments_by_size = {
                    int(k): v for k, v in dl["segments_by_size"].items()
                }
            net = data.get("network", {})
            if "max_speed_bytes" in net:
                config.network.max_speed_bytes = net["max_speed_bytes"]
            if "timeout_seconds" in net:
                config.network.timeout_seconds = net["timeout_seconds"]
            if "max_retries" in net:
                config.network.max_retries = net["max_retries"]
            if "retry_delay_seconds" in net:
                config.network.retry_delay_seconds = net["retry_delay_seconds"]
            if "user_agent" in net:
                config.network.user_agent = net["user_agent"]
            log = data.get("logging", {})
            if "level" in log:
                config.logging.level = log["level"]
            if "file" in log:
                config.logging.file = Path(log["file"]).expanduser()
        return config

    def save(self, path: Optional[Path] = None) -> None:
        """Save configuration to TOML file."""
        if path is None:
            path = Path.home() / ".nassimdl" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)

        output_dir_str = str(self.download.output_dir).replace("\\", "/")
        temp_dir_str = str(self.download.temp_dir).replace("\\", "/")
        log_file_str = str(self.logging.file).replace("\\", "/")

        segments_map = ", ".join(f'"{k}" = {v}' for k, v in sorted(self.download.segments_by_size.items()))

        content = f"""[download]
max_concurrent = {self.download.max_concurrent}
default_segments = {self.download.default_segments}
output_dir = "{output_dir_str}"
temp_dir = "{temp_dir_str}"
segments_by_size = {{ {segments_map} }}

[network]
max_speed_bytes = {self.network.max_speed_bytes}
timeout_seconds = {self.network.timeout_seconds}
max_retries = {self.network.max_retries}
retry_delay_seconds = {self.network.retry_delay_seconds}
user_agent = "{self.network.user_agent}"

[logging]
level = "{self.logging.level}"
file = "{log_file_str}"
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

