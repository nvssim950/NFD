import pytest
from pathlib import Path
from nassimdl.models.config import AppConfig


def test_config_save_load_roundtrip(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg = AppConfig()
    assert cfg.download.segment_mode == "auto"

    cfg.download.segment_mode = "manual"
    cfg.download.default_segments = 16
    cfg.download.max_concurrent = 5
    cfg.network.max_speed_bytes = 10 * 1024 * 1024
    cfg.save(cfg_file)

    assert cfg_file.exists()

    loaded = AppConfig.load(cfg_file)
    assert loaded.download.segment_mode == "manual"
    assert loaded.download.default_segments == 16
    assert loaded.download.max_concurrent == 5
    assert loaded.network.max_speed_bytes == 10 * 1024 * 1024


def test_config_segments_by_size():
    cfg = AppConfig()
    assert cfg.download.get_segments_for_size(1024) == 1
    assert cfg.download.get_segments_for_size(2 * 1024 * 1024) == 1
    assert cfg.download.get_segments_for_size(20 * 1024 * 1024) == 4
    assert cfg.download.get_segments_for_size(100 * 1024 * 1024) == 8
    assert cfg.download.get_segments_for_size(600 * 1024 * 1024) == 12
    assert cfg.download.get_segments_for_size(2 * 1024 * 1024 * 1024) == 16
