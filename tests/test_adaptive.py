"""Tests for Smart Auto segment sizing, runtime server adaptation, and GUI stream parsing."""

import asyncio
import time
from pathlib import Path
from aiohttp import web
import pytest

from nassimdl.core.adaptive import AdaptiveConcurrencyGate, compute_auto_segments
from nassimdl.core.manager import DownloadManager
from nassimdl.gui.dialogs import format_stream_setting, parse_stream_setting
from nassimdl.models.config import AppConfig, DownloadConfig
from nassimdl.models.download import DownloadStatus
from nassimdl.utils.http import ProbeResult, parse_retry_after


def test_compute_auto_segments_size_and_server_signals():
    cfg = DownloadConfig()

    # No range support -> always 1
    assert compute_auto_segments(
        cfg,
        ProbeResult("https://x/f", 2 * 1024**3, False, "f", None),
    ) == 1

    # 2 GB with range -> 16
    assert compute_auto_segments(
        cfg,
        ProbeResult("https://x/f", 2 * 1024**3, True, "f", None),
    ) == 16

    # 100 MB -> 8
    assert compute_auto_segments(
        cfg,
        ProbeResult("https://x/f", 100 * 1024**2, True, "f", None),
    ) == 8

    # Tight rate limit remaining header caps initial count
    assert compute_auto_segments(
        cfg,
        ProbeResult("https://x/f", 2 * 1024**3, True, "f", None, ratelimit_remaining=3),
    ) == 3

    # High origin latency caps conservatively to 4
    assert compute_auto_segments(
        cfg,
        ProbeResult("https://x/f", 2 * 1024**3, True, "f", None, probe_latency_ms=2500.0),
    ) == 4

    # Server sent Retry-After during probe -> cap conservatively to 2
    assert compute_auto_segments(
        cfg,
        ProbeResult("https://x/f", 2 * 1024**3, True, "f", None, retry_after_seconds=2.0),
    ) == 2


def test_parse_retry_after_and_stream_setting():
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("0.1") == 0.5
    assert parse_retry_after("120") == 30.0
    assert parse_retry_after("invalid-date") is None

    assert parse_stream_setting("Auto") == (True, None)
    assert parse_stream_setting(" auto ") == (True, None)
    assert parse_stream_setting("") == (True, None)
    assert parse_stream_setting("16") == (False, 16)
    assert parse_stream_setting("1") == (False, 1)
    assert parse_stream_setting("32") == (False, 32)

    assert format_stream_setting("auto", 8) == "Auto"
    assert format_stream_setting("manual", 16) == "16"

    with pytest.raises(ValueError):
        parse_stream_setting("0")
    with pytest.raises(ValueError):
        parse_stream_setting("33")
    with pytest.raises(ValueError):
        parse_stream_setting("abc")


def test_adaptive_gate_throttle_and_ramp():
    gate = AdaptiveConcurrencyGate(total_segments=16, auto_mode=True)
    # Slow-start starts at 4
    assert gate.current_limit == 4

    # Test burst debouncing: immediate repeated reports within 1.5s only halve once
    assert gate.report_throttle(status_code=429, retry_after=0.5) is True
    assert gate.current_limit == 2
    # Sibling burst call right after
    assert gate.report_throttle(status_code=429, retry_after=0.5) is True
    assert gate.current_limit == 2

    # After debounce window passes, another throttle drops 2 -> 1
    gate._last_throttle_time = time.monotonic() - 2.0
    assert gate.report_throttle(status_code=429, retry_after=0.5) is True
    assert gate.current_limit == 1


def test_adaptive_gate_speed_ramp_and_saturation():
    gate = AdaptiveConcurrencyGate(total_segments=16, auto_mode=True)
    gate._active = 4
    gate._last_step_time = time.monotonic() - 2.0

    # Baseline 2 MB/s -> steps 4 -> 8
    gate.evaluate_speed(2_000_000, remaining_segments=16)
    assert gate.current_limit == 8

    # Simulate 8 active workers and higher speed (+50% -> 3 MB/s) -> steps 8 -> 12
    gate._active = 8
    gate._last_step_time = time.monotonic() - 2.0
    gate.evaluate_speed(3_000_000, remaining_segments=16)
    assert gate.current_limit == 12

    # Simulate speed plateauing at 3.05 MB/s (< +12%) -> locks ceiling at 12
    gate._active = 12
    gate._last_step_time = time.monotonic() - 2.0
    gate.evaluate_speed(3_050_000, remaining_segments=16)
    assert gate.current_limit == 12
    assert gate._max_limit == 12


@pytest.mark.asyncio
async def test_e2e_auto_mode_adapts_to_throttling_server(tmp_path: Path):
    """Verify Auto mode adapts down cleanly when server returns 429 above 2 connections."""
    payload = b"NFD-ADAPTIVE-TEST-" * (64 * 1024)  # ~1.12 MB
    active_requests = 0
    throttle_hits = 0

    async def handle_file(request: web.Request):
        nonlocal active_requests, throttle_hits
        active_requests += 1
        try:
            if request.method == "HEAD":
                return web.Response(
                    status=200,
                    headers={
                        "Content-Length": str(len(payload)),
                        "Accept-Ranges": "bytes",
                        "Content-Disposition": 'attachment; filename="throttled.bin"',
                    },
                )

            # Reject > 2 simultaneous GET range requests with 429 Too Many Requests
            if active_requests > 2:
                throttle_hits += 1
                return web.Response(
                    status=429,
                    text="Too many concurrent connections",
                    headers={"Retry-After": "0.5"},
                )

            # Small transfer delay so concurrent requests actually overlap
            await asyncio.sleep(0.08)

            range_hdr = request.headers.get("Range")
            total = len(payload)
            if not range_hdr:
                return web.Response(
                    body=payload,
                    headers={"Content-Length": str(total), "Accept-Ranges": "bytes"},
                )
            start_s, end_s = range_hdr.replace("bytes=", "").split("-")
            start = int(start_s)
            end = int(end_s) if end_s else total - 1
            chunk = payload[start : end + 1]
            return web.Response(
                body=chunk,
                status=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{total}",
                    "Content-Length": str(len(chunk)),
                    "Accept-Ranges": "bytes",
                },
            )
        finally:
            active_requests -= 1

    app = web.Application()
    app.router.add_route("*", "/throttled.bin", handle_file)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    cfg = AppConfig()
    cfg.db_path = tmp_path / "adaptive.db"
    cfg.download.output_dir = tmp_path / "out"
    cfg.download.temp_dir = tmp_path / "tmp"
    cfg.download.segment_mode = "auto"
    cfg.download.segments_by_size = {0: 8}  # Force 8 initial segments on 1 MB test payload
    cfg.network.retry_delay_seconds = 0.2

    manager = DownloadManager(cfg)
    await manager.start()
    try:
        dl_id = await manager.add_download(url=f"http://127.0.0.1:{port}/throttled.bin")
        dl = await asyncio.wait_for(manager.wait_for_download(dl_id, poll_interval=0.1), timeout=30.0)

        assert dl.status == DownloadStatus.COMPLETED
        assert dl.auto_segments is True
        assert dl.num_segments == 8
        assert throttle_hits > 0
        assert dl.output_path.read_bytes() == payload

        # Verify manual 1-segment override is also strictly respected
        dl_manual_id = await manager.add_download(
            url=f"http://127.0.0.1:{port}/throttled.bin",
            filename="manual1.bin",
            num_segments=1,
            auto_segments=False,
        )
        dl_manual = await asyncio.wait_for(manager.wait_for_download(dl_manual_id, poll_interval=0.1), timeout=30.0)
        assert dl_manual.status == DownloadStatus.COMPLETED
        assert dl_manual.auto_segments is False
        assert dl_manual.num_segments == 1
    finally:
        await manager.stop()
        await runner.cleanup()


def test_gui_dialogs_auto_and_manual_modes(tmp_path: Path):
    """Verify AddDownloadDialog and SettingsDialog handle Auto and manual 16 cleanly."""
    import tkinter as tk
    from unittest.mock import patch
    from nassimdl.gui.dialogs import AddDownloadDialog, SettingsDialog

    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("Tk display unavailable in this environment")

    try:
        submitted = []
        dlg = AddDownloadDialog(
            root,
            initial_url="https://example.com/file.iso",
            default_dir=tmp_path,
            default_mode="auto",
            default_segments=8,
            on_submit=lambda *args: submitted.append(args),
        )
        assert dlg.segments_var.get() == "Auto"
        dlg._confirm()
        assert len(submitted) == 1
        assert submitted[0][3] is None  # None indicates Auto mode

        # Now test manual override 16
        submitted2 = []
        dlg2 = AddDownloadDialog(
            root,
            initial_url="https://example.com/file2.iso",
            default_dir=tmp_path,
            default_mode="auto",
            default_segments=8,
            on_submit=lambda *args: submitted2.append(args),
        )
        dlg2.segments_var.set("16")
        dlg2._confirm()
        assert len(submitted2) == 1
        assert submitted2[0][3] == 16

        # Test SettingsDialog saving Auto then Manual 16 (mock autostart registry calls)
        cfg = AppConfig()
        cfg_file = tmp_path / "settings_test.toml"
        cfg.save = lambda p=cfg_file: AppConfig.save(cfg, p)
        saved_flag = []
        with patch("nassimdl.utils.autostart.is_autostart_enabled", return_value=False), \
             patch("nassimdl.utils.autostart.set_autostart"):
            sdlg = SettingsDialog(root, cfg, on_save=lambda: saved_flag.append(True))
            assert sdlg.segments_var.get() == "Auto"
            sdlg.segments_var.set("16")
            sdlg._save()
            assert saved_flag == [True]
            assert cfg.download.segment_mode == "manual"
            assert cfg.download.default_segments == 16

            # Reopen SettingsDialog and switch back to Auto
            sdlg2 = SettingsDialog(root, cfg)
            assert sdlg2.segments_var.get() == "16"
            sdlg2.segments_var.set("Auto")
            sdlg2._save()
            assert cfg.download.segment_mode == "auto"
    finally:
        root.destroy()

