"""NassimDL CLI — command-line interface for the download manager."""

import asyncio
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import (
    Progress,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    TextColumn,
)

from nassimdl.models.config import AppConfig
from nassimdl.models.download import DownloadStatus
from nassimdl.core.manager import DownloadManager
from nassimdl.utils.units import format_size, format_speed, parse_size

app = typer.Typer(
    name="nassimdl",
    help="🚀 NassimDL — High-Speed Multi-Segment Download Manager",
    add_completion=False,
)
console = Console()


def run_async(coro):
    """Run an async coroutine from sync context."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


@app.command()
def add(
    url: str = typer.Argument(..., help="URL to download"),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="Output directory"),
    filename: Optional[str] = typer.Option(None, "-f", "--filename", help="Override filename"),
    segments: Optional[int] = typer.Option(None, "-s", "--segments", help="Number of segments (0 = Auto)"),
    hash_value: Optional[str] = typer.Option(None, "--hash", help="Expected file hash"),
    hash_type: Optional[str] = typer.Option(None, "--hash-type", help="Hash algorithm (md5, sha256)"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for download to complete"),
):
    """Add a new download."""

    async def _add():
        manager = DownloadManager()
        await manager.start()

        try:
            download_id = await manager.add_download(
                url=url,
                output_dir=output,
                filename=filename,
                num_segments=segments,
                hash_value=hash_value,
                hash_type=hash_type,
            )

            console.print(f"[green]✓[/green] Download added (ID: {download_id})")

            if wait:
                with Progress(
                    TextColumn("[bold blue]{task.fields[filename]}"),
                    BarColumn(bar_width=40),
                    "[progress.percentage]{task.percentage:>3.0f}%",
                    "•",
                    DownloadColumn(),
                    "•",
                    TransferSpeedColumn(),
                    "•",
                    TimeRemainingColumn(),
                    console=console,
                ) as progress:
                    task_id = progress.add_task(
                        "Downloading",
                        filename=filename or "...",
                        total=None,
                    )

                    while True:
                        dl = await manager.get_download(download_id)
                        if not dl:
                            break

                        progress.update(task_id, filename=dl.filename)

                        if dl.file_size and progress.tasks[task_id].total is None:
                            progress.update(task_id, total=dl.file_size)

                        progress.update(task_id, completed=dl.bytes_downloaded)

                        if dl.status == DownloadStatus.COMPLETED:
                            progress.update(task_id, completed=dl.file_size or dl.bytes_downloaded)
                            break
                        elif dl.status == DownloadStatus.ERROR:
                            console.print(f"[red]✗ Download failed:[/red] {dl.error_message}")
                            break

                        await asyncio.sleep(0.3)

                dl = await manager.get_download(download_id)
                if dl and dl.status == DownloadStatus.COMPLETED:
                    console.print(f"[green]✓ Download complete:[/green] {dl.output_path}")
        finally:
            await manager.stop()

    run_async(_add())


@app.command(name="list")
def list_downloads(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    all_downloads: bool = typer.Option(False, "--all", "-a", help="Show all downloads"),
):
    """List downloads."""

    async def _list():
        manager = DownloadManager()
        await manager.start()

        try:
            filter_status = None
            if status:
                try:
                    filter_status = DownloadStatus(status.lower())
                except ValueError:
                    console.print(f"[red]Invalid status:[/red] {status}")
                    console.print(f"Valid: {', '.join(s.value for s in DownloadStatus)}")
                    return

            downloads = await manager.list_downloads(filter_status)

            if not downloads:
                console.print("[dim]No downloads found.[/dim]")
                return

            table = Table(title="📥 Downloads", show_lines=True)
            table.add_column("ID", style="cyan", width=5)
            table.add_column("Filename", style="white", max_width=35)
            table.add_column("Status", width=12)
            table.add_column("Progress", width=10)
            table.add_column("Size", width=10)
            table.add_column("Segments", width=10)

            status_colors = {
                DownloadStatus.QUEUED: "dim",
                DownloadStatus.PROBING: "yellow",
                DownloadStatus.DOWNLOADING: "blue",
                DownloadStatus.PAUSED: "yellow",
                DownloadStatus.MERGING: "magenta",
                DownloadStatus.VERIFYING: "cyan",
                DownloadStatus.COMPLETED: "green",
                DownloadStatus.ERROR: "red",
            }

            for dl in downloads:
                color = status_colors.get(dl.status, "white")
                progress_pct = f"{dl.progress * 100:.1f}%" if dl.file_size else "—"
                size_str = format_size(dl.file_size) if dl.file_size else "Unknown"

                table.add_row(
                    str(dl.id),
                    dl.filename,
                    f"[{color}]{dl.status.value}[/{color}]",
                    progress_pct,
                    size_str,
                    f"{dl.num_segments} (Auto)" if dl.auto_segments and dl.num_segments > 0 else ("Auto" if dl.auto_segments else str(dl.num_segments)),
                )

            console.print(table)
        finally:
            await manager.stop()

    run_async(_list())


@app.command()
def pause(
    download_id: int = typer.Argument(..., help="Download ID to pause"),
):
    """Pause a download."""

    async def _pause():
        manager = DownloadManager()
        await manager.start()
        try:
            success = await manager.pause_download(download_id)
            if success:
                console.print(f"[yellow]⏸ Paused download {download_id}[/yellow]")
            else:
                console.print(f"[red]Download {download_id} not found or not active[/red]")
        finally:
            await manager.stop()

    run_async(_pause())


@app.command()
def resume(
    download_id: int = typer.Argument(..., help="Download ID to resume"),
):
    """Resume a paused download."""

    async def _resume():
        manager = DownloadManager()
        await manager.start()
        try:
            success = await manager.resume_download(download_id)
            if success:
                console.print(f"[green]▶ Resumed download {download_id}[/green]")
            else:
                console.print(f"[red]Download {download_id} not found or not paused[/red]")
        finally:
            await manager.stop()

    run_async(_resume())


@app.command()
def remove(
    download_id: int = typer.Argument(..., help="Download ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Remove without confirmation"),
):
    """Remove a download."""

    async def _remove():
        if not force:
            confirm = typer.confirm(f"Remove download {download_id}?")
            if not confirm:
                return

        manager = DownloadManager()
        await manager.start()
        try:
            success = await manager.remove_download(download_id)
            if success:
                console.print(f"[red]🗑 Removed download {download_id}[/red]")
            else:
                console.print(f"[red]Download {download_id} not found[/red]")
        finally:
            await manager.stop()

    run_async(_remove())


@app.command()
def config(
    max_speed: Optional[str] = typer.Option(None, "--max-speed", help="Max speed (e.g., 50MB, 0=unlimited)"),
    max_concurrent: Optional[int] = typer.Option(None, "--max-concurrent", help="Max concurrent downloads"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir", help="Default output directory"),
    segment_mode: Optional[str] = typer.Option(None, "--segment-mode", help="Stream selection mode (auto or manual)"),
    default_segments: Optional[int] = typer.Option(None, "--default-segments", help="Manual stream count (1-32)"),
):
    """View or update configuration."""
    cfg = AppConfig.load()
    changed = False

    if max_speed is not None:
        speed = parse_size(max_speed)
        cfg.network.max_speed_bytes = speed
        changed = True
        console.print(f"Max speed: {format_speed(speed) if speed > 0 else 'Unlimited'}")
    
    if max_concurrent is not None:
        cfg.download.max_concurrent = max_concurrent
        changed = True
        console.print(f"Max concurrent: {max_concurrent}")
    
    if output_dir is not None:
        cfg.download.output_dir = output_dir
        changed = True
        console.print(f"Output dir: {output_dir}")

    if segment_mode is not None:
        mode = segment_mode.strip().lower()
        if mode not in ("auto", "manual"):
            console.print("[red]Invalid --segment-mode (use 'auto' or 'manual')[/red]")
            raise typer.Exit(code=1)
        cfg.download.segment_mode = mode
        changed = True
        console.print(f"Segment mode: {mode}")

    if default_segments is not None:
        cfg.download.default_segments = max(1, min(32, default_segments))
        changed = True
        console.print(f"Default segments: {cfg.download.default_segments}")

    if changed:
        cfg.save()
    else:
        table = Table(title="⚙️  Configuration")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Output Directory", str(cfg.download.output_dir))
        table.add_row("Temp Directory", str(cfg.download.temp_dir))
        table.add_row("Max Concurrent", str(cfg.download.max_concurrent))
        table.add_row("Segment Mode", "Auto (Smart)" if cfg.download.segment_mode == "auto" else "Manual")
        table.add_row("Default Segments", str(cfg.download.default_segments))
        table.add_row("Max Speed", format_speed(cfg.network.max_speed_bytes) if cfg.network.max_speed_bytes > 0 else "Unlimited")
        table.add_row("Timeout", f"{cfg.network.timeout_seconds}s")
        table.add_row("Max Retries", str(cfg.network.max_retries))
        table.add_row("User Agent", cfg.network.user_agent)
        table.add_row("Log Level", cfg.logging.level)
        table.add_row("DB Path", str(cfg.db_path))
        console.print(table)


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    open_browser: bool = typer.Option(True, "--browser/--no-browser", help="Open browser automatically"),
):
    """Launch the NassimDL Web UI dashboard."""
    import uvicorn
    import webbrowser

    url = f"http://{host}:{port}"
    console.print(f"[bold green]🚀 Starting NassimDL Web Dashboard at {url}[/bold green]")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run("nassimdl.api.server:app", host=host, port=port, log_level="warning")


@app.command()
def gui():
    """Launch the native NFD Windows desktop application."""
    from nassimdl.gui.app import run_gui
    run_gui()


@app.command(name="app")
def run_app(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to listen on"),
    background: bool = typer.Option(False, "--background", "-b", help="Run in background daemon mode"),
):
    """Run NassimDL as a desktop background listener for Chrome downloads."""
    from nassimdl.app import is_port_in_use, get_running_pid, run_server
    import subprocess

    if is_port_in_use(port, host):
        console.print(f"[yellow]⚡ NassimDL is already running on http://{host}:{port}[/yellow]")
        return

    if background:
        cmd = [sys.executable, "-m", "nassimdl", "app", "--host", host, "--port", str(port)]
        creationflags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
        proc = subprocess.Popen(cmd, creationflags=creationflags, close_fds=True)
        console.print(f"[green]✓ NassimDL background service started (PID: {proc.pid})[/green]")
        console.print(f"[cyan]Listening at http://{host}:{port}[/cyan]")
        return

    console.print(f"[bold green]🚀 NassimDL Desktop App is active and listening for Chrome downloads at http://{host}:{port}[/bold green]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")
    run_server(host=host, port=port)


@app.command()
def stop():
    """Stop the running NassimDL background process."""
    from nassimdl.app import stop_app, get_running_pid
    pid = get_running_pid()
    if stop_app():
        console.print(f"[green]✓ Stopped NassimDL background service (PID: {pid or 'N/A'})[/green]")
    else:
        console.print("[yellow]NassimDL is not running.[/yellow]")


@app.command()
def status():
    """Check if the NassimDL background service is running."""
    from nassimdl.app import get_running_pid, is_port_in_use
    pid = get_running_pid()
    is_up = is_port_in_use(8000)
    if is_up:
        console.print(f"[bold green]● NassimDL is RUNNING[/bold green] (PID: {pid or 'active'}, Port: 8000)")
    else:
        console.print("[bold red]○ NassimDL is STOPPED[/bold red]")


@app.command()
def version():
    """Show version information."""
    from nassimdl import __version__
    console.print(f"[bold]NassimDL[/bold] v{__version__}")


if __name__ == "__main__":
    app()
