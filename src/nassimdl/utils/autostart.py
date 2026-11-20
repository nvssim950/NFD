"""Windows Auto-Start management for NFD."""

import os
import subprocess
from pathlib import Path


def get_startup_shortcut_path() -> Path:
    startup_dir = (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )
    return startup_dir / "NFD.lnk"


def is_autostart_enabled() -> bool:
    return get_startup_shortcut_path().exists()


def set_autostart(enable: bool) -> bool:
    shortcut_path = get_startup_shortcut_path()

    if not enable:
        if shortcut_path.exists():
            try:
                shortcut_path.unlink()
                return True
            except OSError:
                return False
        return True

    # Enable: create shortcut pointing to NFD.bat with --minimized
    root_dir = Path(__file__).resolve().parents[3]
    target_bat = root_dir / "NFD.bat"
    ico_path = root_dir / "NFD.ico"

    vbs_content = f'''Set oWS = CreateObject("WScript.Shell")
Set oLink = oWS.CreateShortcut("{shortcut_path}")
oLink.TargetPath = "{target_bat}"
oLink.Arguments = "--minimized"
oLink.WorkingDirectory = "{root_dir}"
oLink.Description = "NFD — Nassim Fast Downloader (Background)"
oLink.IconLocation = "{ico_path},0"
oLink.Save
'''
    temp_vbs = root_dir / "temp_startup.vbs"
    try:
        temp_vbs.write_text(vbs_content, encoding="utf-8")
        subprocess.run(["cscript", "//nologo", str(temp_vbs)], check=True)
        if temp_vbs.exists():
            temp_vbs.unlink()
        return True
    except Exception:
        if temp_vbs.exists():
            try:
                temp_vbs.unlink()
            except OSError:
                pass
        return False
