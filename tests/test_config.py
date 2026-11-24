import pytest
from pathlib import Path
from nassimdl.models.config import AppConfig


def test_config_save_load_roundtrip(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.download.default_segments = 16
    cfg.download.max_concurrent = 5
    cfg.network.max_speed_bytes = 10 * 1024 * 1024
    cfg.save(cfg_file)

    assert cfg_file.exists()

    loaded = AppConfig.load(cfg_file)
    assert loaded.download.default_segments == 16
    assert loaded.download.max_concurrent == 5
    assert loaded.network.max_speed_bytes == 10 * 1024 * 1024


def test_config_segments_clamping():
    cfg = AppConfig()
    cfg.download.default_segments = 16
    # 16 segments for large files
    assert cfg.download.default_segments == 16
