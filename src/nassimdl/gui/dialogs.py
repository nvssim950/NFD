"""NFD Dialogs: Add Download, Settings, and About."""

import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from nassimdl.utils.units import parse_size, format_size, format_speed

STREAM_PRESETS = ("Auto", "1", "2", "4", "6", "8", "12", "16", "24", "32")


def parse_stream_setting(raw_val: str) -> tuple[bool, int | None]:
    """Parse stream setting string into (is_auto, segment_count)."""
    val = str(raw_val).strip()
    if not val or val.lower().startswith("auto"):
        return True, None
    count = int(val)
    if count < 1 or count > 32:
        raise ValueError("Parallel streams must be 'Auto' or a number from 1 to 32.")
    return False, count


def format_stream_setting(mode: str, segments: int) -> str:
    """Format stream mode/count for combobox display."""
    if mode == "auto":
        return "Auto"
    return str(max(1, min(32, segments)))


def get_icon_paths() -> tuple[str | None, str | None]:
    ico_path = None
    png_path = None
    candidates_ico = [
        Path(__file__).resolve().parents[3] / "NFD.ico",
        Path(__file__).resolve().parent / "NFD.ico",
        Path("NFD.ico").resolve(),
    ]
    for c in candidates_ico:
        if c.exists():
            ico_path = str(c)
            break

    candidates_png = [
        Path(__file__).resolve().parents[3] / "extensions" / "chrome" / "icon.png",
        Path(__file__).resolve().parent / "icon.png",
        Path("icon.png").resolve(),
    ]
    for c in candidates_png:
        if c.exists():
            png_path = str(c)
            break

    return ico_path, png_path


class AddDownloadDialog(tk.Toplevel):
    """Professional dialog to add a new download URL."""

    def __init__(
        self,
        parent,
        initial_url="",
        initial_filename="",
        default_dir=None,
        default_mode="auto",
        default_segments=8,
        on_submit=None,
    ):
        super().__init__(parent)
        self.title("Add New Download — NFD")

        ico, png = get_icon_paths()
        if ico:
            try:
                self.iconbitmap(ico)
            except Exception:
                pass
        if png:
            try:
                img = tk.PhotoImage(file=png)
                self.iconphoto(False, img)
            except Exception:
                pass

        self.transient(parent)
        self.grab_set()

        self.on_submit = on_submit
        self.default_dir = Path(default_dir or (Path.home() / "Downloads"))
        self.default_mode = default_mode if default_mode in ("auto", "manual") else "auto"
        self.default_segments = max(1, min(32, default_segments))

        # Spacious, high-DPI friendly geometry
        w = 760
        h = 420
        self.geometry(f"{w}x{h}")
        self.minsize(680, 380)

        # Center on screen
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self.destroy())

        self._create_widgets(initial_url, initial_filename)

    def _create_widgets(self, initial_url, initial_filename):
        pad = {"padx": 20, "pady": 10}

        main_frame = ttk.Frame(self, padding="24 20 24 20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        font_label = ("Segoe UI", 11)
        font_bold = ("Segoe UI", 11, "bold")

        # URL
        ttk.Label(main_frame, text="Download URL:", font=font_bold).grid(
            row=0, column=0, sticky=tk.W, **pad
        )
        self.url_var = tk.StringVar(value=initial_url)
        self.url_entry = ttk.Entry(main_frame, textvariable=self.url_var, font=font_label)
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky=tk.EW, padx=20, pady=10, ipady=4)

        # Filename
        ttk.Label(main_frame, text="Save As (Filename):", font=font_label).grid(
            row=1, column=0, sticky=tk.W, **pad
        )
        self.filename_var = tk.StringVar(value=initial_filename)
        self.filename_entry = ttk.Entry(main_frame, textvariable=self.filename_var, font=font_label)
        self.filename_entry.grid(row=1, column=1, columnspan=2, sticky=tk.EW, padx=20, pady=10, ipady=4)

        # Save Folder
        ttk.Label(main_frame, text="Save Folder:", font=font_label).grid(
            row=2, column=0, sticky=tk.W, **pad
        )
        self.dir_var = tk.StringVar(value=str(self.default_dir))
        self.dir_entry = ttk.Entry(main_frame, textvariable=self.dir_var, font=font_label)
        self.dir_entry.grid(row=2, column=1, sticky=tk.EW, padx=(20, 10), pady=10, ipady=4)
        ttk.Button(main_frame, text="Browse...", command=self._browse_dir, width=12).grid(
            row=2, column=2, sticky=tk.E, padx=(0, 20), pady=10
        )

        # Options: Parallel Segments & Checksum
        opt_frame = ttk.Frame(main_frame)
        opt_frame.grid(row=3, column=0, columnspan=3, sticky=tk.EW, padx=20, pady=14)

        ttk.Label(opt_frame, text="Parallel Streams (Connections):", font=font_label).pack(
            side=tk.LEFT
        )
        self.segments_var = tk.StringVar(
            value=format_stream_setting(self.default_mode, self.default_segments)
        )
        self.seg_combo = ttk.Combobox(
            opt_frame,
            values=STREAM_PRESETS,
            textvariable=self.segments_var,
            width=8,
            font=font_label,
        )
        self.seg_combo.pack(side=tk.LEFT, padx=(10, 26))

        ttk.Label(opt_frame, text="SHA-256 (optional):", font=font_label).pack(side=tk.LEFT)
        self.hash_var = tk.StringVar()
        self.hash_entry = ttk.Entry(opt_frame, textvariable=self.hash_var, font=font_label, width=28)
        self.hash_entry.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True, ipady=3)

        # Separator
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).grid(
            row=4, column=0, columnspan=3, sticky=tk.EW, pady=18
        )

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=5, column=0, columnspan=3, sticky=tk.E, padx=20)

        ttk.Button(btn_frame, text="Cancel", command=self.destroy, width=14).pack(
            side=tk.RIGHT, padx=(12, 0)
        )
        download_btn = ttk.Button(
            btn_frame, text="Start Download", command=self._confirm, width=18
        )
        download_btn.pack(side=tk.RIGHT)

        main_frame.columnconfigure(1, weight=1)

        if not initial_url:
            self.url_entry.focus_set()
        else:
            self.filename_entry.focus_set()

    def _browse_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.dir_var.get(), parent=self)
        if chosen:
            self.dir_var.set(chosen)

    def _confirm(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a download URL.", parent=self)
            return

        filename = self.filename_var.get().strip() or None
        output_dir = Path(self.dir_var.get().strip() or self.default_dir)
        try:
            is_auto, segments = parse_stream_setting(self.segments_var.get())
        except Exception as e:
            messagebox.showwarning("Invalid Streams", str(e), parent=self)
            return
        hash_val = self.hash_var.get().strip() or None

        self.destroy()
        if self.on_submit:
            self.on_submit(url, filename, output_dir, None if is_auto else segments, hash_val)


class SettingsDialog(tk.Toplevel):
    """NFD Preferences dialog."""

    def __init__(self, parent, config, on_save=None):
        super().__init__(parent)
        self.title("Preferences — NFD")

        ico, png = get_icon_paths()
        if ico:
            try:
                self.iconbitmap(ico)
            except Exception:
                pass
        if png:
            try:
                img = tk.PhotoImage(file=png)
                self.iconphoto(False, img)
            except Exception:
                pass

        self.transient(parent)
        self.grab_set()

        self.config = config
        self.on_save = on_save

        w = 640
        h = 395
        self.geometry(f"{w}x{h}")
        self.minsize(580, 350)

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())

        self._create_widgets()

    def _create_widgets(self):
        frame = ttk.Frame(self, padding="28")
        frame.pack(fill=tk.BOTH, expand=True)

        font_label = ("Segoe UI", 11)

        # Max Concurrent
        ttk.Label(frame, text="Max Concurrent Downloads:", font=font_label).grid(
            row=0, column=0, sticky=tk.W, pady=12
        )
        self.concurrent_var = tk.StringVar(value=str(self.config.download.max_concurrent))
        ttk.Spinbox(frame, from_=1, to=10, textvariable=self.concurrent_var, width=8, font=font_label).grid(
            row=0, column=1, sticky=tk.W, pady=12
        )

        # Default Segments
        ttk.Label(frame, text="Default Parallel Streams:", font=font_label).grid(
            row=1, column=0, sticky=tk.NW, pady=(12, 4)
        )
        seg_box = ttk.Frame(frame)
        seg_box.grid(row=1, column=1, sticky=tk.W, pady=(12, 4))
        self.segments_var = tk.StringVar(
            value=format_stream_setting(self.config.download.segment_mode, self.config.download.default_segments)
        )
        ttk.Combobox(
            seg_box,
            values=STREAM_PRESETS,
            textvariable=self.segments_var,
            width=10,
            font=font_label,
        ).pack(anchor=tk.W)
        ttk.Label(
            seg_box,
            text="Auto picks 1–16 by file size & adapts to server limits",
            font=("Segoe UI", 9),
            foreground="#64748b",
        ).pack(anchor=tk.W, pady=(4, 0))

        # Max Speed Limit
        ttk.Label(frame, text="Speed Limit (0 for unlimited):", font=font_label).grid(
            row=2, column=0, sticky=tk.W, pady=12
        )
        self.speed_var = tk.StringVar(
            value=str(self.config.network.max_speed_bytes // (1024 * 1024))
            if self.config.network.max_speed_bytes > 0
            else "0"
        )
        speed_frame = ttk.Frame(frame)
        speed_frame.grid(row=2, column=1, sticky=tk.W, pady=12)
        ttk.Entry(speed_frame, textvariable=self.speed_var, width=8, font=font_label).pack(side=tk.LEFT)
        ttk.Label(speed_frame, text=" MB/s", font=font_label).pack(side=tk.LEFT)

        # Auto-start with Windows
        from nassimdl.utils.autostart import is_autostart_enabled, set_autostart
        self.autostart_var = tk.BooleanVar(value=is_autostart_enabled())
        ttk.Checkbutton(
            frame,
            text="Run NFD in the background when Windows starts",
            variable=self.autostart_var,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=10)

        # Buttons
        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(
            row=4, column=0, columnspan=2, sticky=tk.EW, pady=18
        )

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=2, sticky=tk.E)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy, width=14).pack(side=tk.RIGHT, padx=(10, 0))
        ttk.Button(btn_frame, text="Save Changes", command=self._save, width=16).pack(side=tk.RIGHT)

    def _save(self):
        try:
            from nassimdl.utils.autostart import set_autostart
            set_autostart(self.autostart_var.get())

            raw_conc = str(self.concurrent_var.get()).strip()
            max_concurrent = int(raw_conc) if raw_conc else 3
            self.config.download.max_concurrent = max(1, min(10, max_concurrent))

            is_auto, seg_count = parse_stream_setting(self.segments_var.get())
            if is_auto:
                self.config.download.segment_mode = "auto"
            else:
                self.config.download.segment_mode = "manual"
                self.config.download.default_segments = seg_count

            raw_speed = str(self.speed_var.get()).strip()
            speed_mb = int(raw_speed) if raw_speed else 0
            self.config.network.max_speed_bytes = max(0, speed_mb) * 1024 * 1024

            self.config.save()
        except Exception as e:
            messagebox.showerror("Invalid Input", f"Please check your input values: {e}", parent=self)
            return

        self.destroy()
        if self.on_save:
            self.on_save()


class AboutDialog(tk.Toplevel):
    """About NFD dialog."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("About NFD")

        ico, png = get_icon_paths()
        if ico:
            try:
                self.iconbitmap(ico)
            except Exception:
                pass
        if png:
            try:
                img = tk.PhotoImage(file=png)
                self.iconphoto(False, img)
            except Exception:
                pass

        w = 480
        h = 280
        self.geometry(f"{w}x{h}")
        self.minsize(440, 260)
        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

        frame = ttk.Frame(self, padding="28")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="NFD", font=("Segoe UI", 20, "bold"), foreground="#0078d4").pack()
        ttk.Label(frame, text="Nassim Fast Downloader", font=("Segoe UI", 12, "bold")).pack(pady=(4, 6))
        ttk.Label(frame, text="Version 1.0.0 (Production)", font=("Segoe UI", 10)).pack()

        desc = (
            "Professional Multi-Segment Download Accelerator for Windows.\n"
            "Accelerates downloads using parallel HTTP Range connections,\n"
            "automatic Chrome integration, and persistent resume."
        )
        ttk.Label(frame, text=desc, font=("Segoe UI", 10), justify=tk.CENTER).pack(pady=16)

        ttk.Button(frame, text="Close", command=self.destroy, width=14).pack(pady=(4, 0))
