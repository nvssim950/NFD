"""Unit tests for units formatting and parsing utilities."""

from nassimdl.utils.units import format_size, format_speed, format_eta, parse_size


def test_format_size():
    assert format_size(500) == "500 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1024 * 1024 * 15.5) == "15.5 MB"
    assert format_size(1024 ** 3 * 2.5) == "2.5 GB"
    assert format_size(-1) == "Unknown"


def test_format_speed():
    assert format_speed(0) == "0 B/s"
    assert format_speed(1024 * 1024 * 5.2) == "5.2 MB/s"


def test_format_eta():
    assert format_eta(None) == "∞"
    assert format_eta(0) == "0s"
    assert format_eta(45) == "45s"
    assert format_eta(125) == "2:05"
    assert format_eta(3665) == "1:01:05"


def test_parse_size():
    assert parse_size("500B") == 500
    assert parse_size("10KB") == 10 * 1024
    assert parse_size("50MB") == 50 * 1024 * 1024
    assert parse_size("2GB") == 2 * 1024 ** 3
