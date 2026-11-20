"""End-to-end tests with a real local HTTP server supporting Range requests."""

import asyncio
import hashlib
import pytest
from pathlib import Path
from aiohttp import web
from nassimdl.models.config import AppConfig
from nassimdl.models.download import DownloadStatus
from nassimdl.core.manager import DownloadManager


@pytest.fixture
async def mock_http_server(tmp_path: Path):
    """Start a local aiohttp server that serves test files and supports Range headers."""
    # Create a 2 MB test file
    test_content = b"0123456789ABCDEF" * (128 * 1024)  # 2 MB
    test_file = tmp_path / "server_file.dat"
    test_file.write_bytes(test_content)

    async def handle_download(request):
        range_header = request.headers.get("Range")
        total_size = len(test_content)

        if not range_header:
            return web.Response(
                body=test_content,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(total_size),
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": 'attachment; filename="server_file.dat"',
                },
            )

        # Parse range: bytes=start-end
        range_val = range_header.replace("bytes=", "").strip()
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else total_size - 1

        if start >= total_size or end >= total_size or start > end:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{total_size}"})

        chunk = test_content[start : end + 1]
        return web.Response(
            body=chunk,
            status=206,
            headers={
                "Content-Range": f"bytes {start}-{end}/{total_size}",
                "Content-Length": str(len(chunk)),
                "Content-Type": "application/octet-stream",
                "Accept-Ranges": "bytes",
            },
        )

    async def handle_no_range(request):
        return web.Response(
            body=b"No range content here!",
            headers={
                "Content-Type": "text/plain",
                "Content-Length": str(len(b"No range content here!")),
                "Content-Disposition": 'attachment; filename="norange.txt"',
            },
        )

    app = web.Application()
    app.router.add_get("/file.dat", handle_download)
    app.router.add_get("/norange.txt", handle_no_range)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()

    port = site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    yield base_url, test_content

    await runner.cleanup()


@pytest.mark.asyncio
async def test_end_to_end_multisegment_download(mock_http_server, tmp_path: Path):
    base_url, expected_content = mock_http_server
    url = f"{base_url}/file.dat"

    cfg = AppConfig()
    cfg.db_path = tmp_path / "test_manager.db"
    cfg.download.output_dir = tmp_path / "downloads"
    cfg.download.temp_dir = tmp_path / "temp"
    cfg.download.default_segments = 4

    manager = DownloadManager(cfg)
    await manager.start()

    try:
        dl_id = await manager.add_download(
            url=url,
            num_segments=4,
        )

        dl = await manager.wait_for_download(dl_id)

        assert dl.status == DownloadStatus.COMPLETED
        assert dl.output_path.exists()
        assert dl.output_path.read_bytes() == expected_content
        assert dl.file_size == len(expected_content)
        assert dl.num_segments == 4
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_end_to_end_with_hash_verification(mock_http_server, tmp_path: Path):
    base_url, expected_content = mock_http_server
    url = f"{base_url}/file.dat"
    expected_hash = hashlib.sha256(expected_content).hexdigest()

    cfg = AppConfig()
    cfg.db_path = tmp_path / "test_hash.db"
    cfg.download.output_dir = tmp_path / "downloads_hash"
    cfg.download.temp_dir = tmp_path / "temp_hash"

    manager = DownloadManager(cfg)
    await manager.start()

    try:
        dl_id = await manager.add_download(
            url=url,
            hash_value=expected_hash,
            hash_type="sha256",
        )

        dl = await manager.wait_for_download(dl_id)

        assert dl.status == DownloadStatus.COMPLETED
        assert dl.output_path.exists()
        assert dl.output_path.read_bytes() == expected_content
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_single_segment_fallback(mock_http_server, tmp_path: Path):
    base_url, _ = mock_http_server
    url = f"{base_url}/norange.txt"

    cfg = AppConfig()
    cfg.db_path = tmp_path / "test_fallback.db"
    cfg.download.output_dir = tmp_path / "downloads_fb"
    cfg.download.temp_dir = tmp_path / "temp_fb"

    manager = DownloadManager(cfg)
    await manager.start()

    try:
        dl_id = await manager.add_download(url=url)
        dl = await manager.wait_for_download(dl_id)

        assert dl.status == DownloadStatus.COMPLETED
        assert dl.output_path.exists()
        assert dl.output_path.read_bytes() == b"No range content here!"
        assert dl.num_segments == 1
    finally:
        await manager.stop()
