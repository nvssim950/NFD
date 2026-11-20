"""NassimDL Background Application & System Tray Service."""

import os
import sys
import time
import signal
import socket
import logging
import subprocess
from pathlib import Path

import uvicorn

logger = logging.getLogger("nassimdl.app")

PID_FILE = Path.home() / ".nassimdl" / "nassimdl.pid"


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if the given host:port is already listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def get_pid_on_port(port: int = 8000) -> int | None:
    """Look up process ID listening on the given port on Windows."""
    try:
        cmd = f'powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"'
        out = subprocess.check_output(cmd, shell=True).decode().strip()
        lines = [line.strip() for line in out.splitlines() if line.strip().isdigit()]
        if lines:
            return int(lines[0])
    except Exception:
        pass
    return None


def get_running_pid() -> int | None:
    """Read the active PID from the pid file or port lookup."""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(1, False, pid)  # PROCESS_TERMINATE
            if handle:
                kernel32.CloseHandle(handle)
                return pid
        except Exception:
            pass
    return get_pid_on_port(8000)


def write_pid():
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def remove_pid():
    if PID_FILE.exists():
        try:
            PID_FILE.unlink()
        except OSError:
            pass


def run_server(host: str = "127.0.0.1", port: int = 8000):
    """Run Uvicorn server in a blocking call."""
    write_pid()
    try:
        config = uvicorn.Config(
            "nassimdl.api.server:app",
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        server.run()
    finally:
        remove_pid()


def stop_app() -> bool:
    """Stop the running background NassimDL process."""
    pid = get_running_pid()
    if not pid:
        remove_pid()
        return False

    try:
        subprocess.run(f"taskkill /F /PID {pid}", shell=True, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        remove_pid()
        return True
    except Exception:
        remove_pid()
        return False
