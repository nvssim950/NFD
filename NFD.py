"""NFD — Nassim Fast Downloader (Desktop Application)."""

import sys
from pathlib import Path

# Ensure src/ is on sys.path
src_dir = Path(__file__).parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from nassimdl.gui.app import run_gui

if __name__ == "__main__":
    run_gui()
