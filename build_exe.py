"""Build script to create a standalone Windows executable for NFD."""

import os
import subprocess
import sys
from pathlib import Path


def build():
    root = Path(__file__).resolve().parent
    icon_path = root / "NFD.ico"
    entry_script = root / "NFD.py"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name=NFD",
        f"--icon={icon_path}",
        f"--add-data={icon_path};.",
        f"--paths={root / 'src'}",
        "--collect-all=nassimdl",
        "--hidden-import=aiosqlite",
        "--hidden-import=aiohttp",
        "--hidden-import=tkinter",
        "--hidden-import=sqlite3",
        "--hidden-import=uvicorn",
        "--hidden-import=fastapi",
        str(entry_script),
    ]

    print("Running PyInstaller...")
    subprocess.run(cmd, check=True, cwd=str(root))
    print("Build completed successfully. Output is in dist/NFD/")


if __name__ == "__main__":
    build()
