"""NFD — Nassim Fast Downloader
Professional Native Windows Desktop Application.
"""

import asyncio
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox

from nassimdl.core.manager import DownloadManager
from nassimdl.gui.dialogs import AddDownloadDialog, SettingsDialog, AboutDialog, get_icon_paths
from nassimdl.gui.tray import WindowsTrayIcon
from nassimdl.models.config import AppConfig
from nassimdl.models.download import DownloadInfo, DownloadStatus
from nassimdl.utils.units import format_eta, format_size, format_speed


class NFDApp(tk.Tk):
    """Main application window for NFD."""

    def __init__(self):
        super().__init__()
        self.title("NFD — Nassim Fast Downloader")
        self.geometry("1260x780")
        self.minsize(980, 600)

        # Set Global High-DPI Fonts across all widgets
        self._configure_global_fonts()

        # Set Application Icon (replaces default feather)
        self._set_app_icon()

        self._apply_native_style()

        self.config_data = AppConfig.load()
        self.manager: DownloadManager | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread: threading.Thread | None = None

        self.selected_filter = "all"
        self.downloads_cache: list[DownloadInfo] = []
        self.current_download_id: int | None = None
        self._shown_tray_hint = False

        # Build UI layout
        self._build_menu()
        self._build_toolbar()
        self._build_main_layout()
        self._build_status_bar()

        # Initialize System Tray
        self._init_system_tray()

        # Start core async engine in background thread
        self._start_engine_thread()

        # Check if started in background mode
        if "--minimized" in sys.argv:
            self.withdraw()

        # Periodic GUI refresh
        self.after(300, self._refresh_loop)

        # Position paned splitter comfortably after window map
        self.after(200, self._adjust_paned_sash)

        # Handle clean window close -> Minimize to tray
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_global_fonts(self):
        """Scale and modernize system font styles globally for all Tk widgets."""
        for name, size, weight in [
            ("TkDefaultFont", 11, "normal"),
            ("TkTextFont", 11, "normal"),
            ("TkHeadingFont", 11, "bold"),
            ("TkMenuFont", 11, "normal"),
            ("TkCaptionFont", 11, "normal"),
            ("TkSmallCaptionFont", 10, "normal"),
        ]:
            try:
                f = tkfont.nametofont(name)
                f.configure(family="Segoe UI", size=size, weight=weight)
            except Exception:
                pass

        self.font_main = ("Segoe UI", 11)
        self.font_bold = ("Segoe UI", 11, "bold")
        self.font_header = ("Segoe UI", 11, "bold")
        self.font_toolbar = ("Segoe UI", 10, "bold")
        self.font_mono = ("Consolas", 11)

    def _set_app_icon(self):
        """Set high-res window and taskbar icons."""
        ico, png = get_icon_paths()
        if png:
            try:
                self._icon_img = tk.PhotoImage(file=png)
                self.iconphoto(True, self._icon_img)
            except Exception:
                pass
        if ico:
            try:
                self.iconbitmap(ico)
            except Exception:
                pass

    def _apply_native_style(self):
        """Configure clean, native Windows look & feel with spacious row heights."""
        style = ttk.Style(self)
        available_themes = style.theme_names()
        if "vista" in available_themes:
            style.theme_use("vista")
        elif "winnative" in available_themes:
            style.theme_use("winnative")

        # Generous row heights and comfortable padding for modern high-res monitors
        style.configure("Treeview.Heading", font=self.font_header, padding=8)
        style.configure("Treeview", font=self.font_main, rowheight=40)
        style.configure("TButton", font=self.font_toolbar, padding=[12, 6])
        style.configure("TLabel", font=self.font_main)
        style.configure("TNotebook.Tab", font=self.font_main, padding=[16, 8])

    def _build_menu(self):
        menubar = tk.Menu(self)

        # File
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Add URL...", accelerator="Ctrl+N", command=self.action_add_url)
        file_menu.add_separator()
        file_menu.add_command(label="Preferences...", accelerator="Ctrl+P", command=self.action_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Minimize to Tray", command=self._on_close)
        file_menu.add_command(label="Quit NFD Completely", command=self.action_quit_completely)
        menubar.add_cascade(label="File", menu=file_menu)

        # Tasks
        dl_menu = tk.Menu(menubar, tearoff=0)
        dl_menu.add_command(label="Resume", accelerator="Ctrl+R", command=self.action_resume)
        dl_menu.add_command(label="Pause", accelerator="Ctrl+Space", command=self.action_pause)
        dl_menu.add_separator()
        dl_menu.add_command(label="Open File", accelerator="Enter", command=self.action_open_file)
        dl_menu.add_command(label="Open Download Folder", accelerator="Ctrl+O", command=self.action_open_folder)
        dl_menu.add_separator()
        dl_menu.add_command(label="Clear Finished / Inactive", command=self.action_clear_finished)
        dl_menu.add_command(label="Remove Task", accelerator="Del", command=self.action_remove)
        menubar.add_cascade(label="Tasks", menu=dl_menu)

        # Help
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About NFD", command=lambda: AboutDialog(self))
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

        # Keybindings
        self.bind("<Control-n>", lambda e: self.action_add_url())
        self.bind("<Control-p>", lambda e: self.action_settings())
        self.bind("<Control-r>", lambda e: self.action_resume())
        self.bind("<Delete>", lambda e: self.action_remove())
        self.bind("<Return>", lambda e: self.action_open_file())

    def _build_toolbar(self):
        toolbar = ttk.Frame(self, padding="10 8 10 8")
        toolbar.pack(side=tk.TOP, fill=tk.X)

        pad_x = 5

        ttk.Button(toolbar, text="➕  Add URL", command=self.action_add_url, width=14).pack(
            side=tk.LEFT, padx=pad_x
        )
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(toolbar, text="▶  Resume", command=self.action_resume, width=12).pack(
            side=tk.LEFT, padx=pad_x
        )
        ttk.Button(toolbar, text="⏸  Pause", command=self.action_pause, width=12).pack(
            side=tk.LEFT, padx=pad_x
        )
        ttk.Button(toolbar, text="✕  Remove", command=self.action_remove, width=12).pack(
            side=tk.LEFT, padx=pad_x
        )
        ttk.Button(toolbar, text="🧹  Clear Finished", command=self.action_clear_finished, width=16).pack(
            side=tk.LEFT, padx=pad_x
        )

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Button(toolbar, text="📂  Open Folder", command=self.action_open_folder, width=15).pack(
            side=tk.LEFT, padx=pad_x
        )
        ttk.Button(toolbar, text="⚙  Settings", command=self.action_settings, width=13).pack(
            side=tk.LEFT, padx=pad_x
        )

    def _build_main_layout(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Left Sidebar (Categories)
        sidebar = ttk.Frame(paned, width=220, padding="10")
        paned.add(sidebar, weight=0)

        ttk.Label(sidebar, text="CATEGORIES", font=("Segoe UI", 10, "bold"), foreground="#64748b").pack(
            anchor=tk.W, pady=(0, 10)
        )

        self.filter_buttons = {}
        filters = [
            ("all", "📥   All Tasks"),
            ("downloading", "⬇   Downloading"),
            ("paused", "⏸   Paused"),
            ("completed", "✅   Completed"),
            ("error", "⚠️   Failed"),
        ]

        for code, label in filters:
            btn = tk.Button(
                sidebar,
                text=label,
                font=self.font_main,
                anchor=tk.W,
                relief=tk.FLAT,
                padx=14,
                pady=8,
                bg="#e2e8f0" if code == "all" else self.cget("bg"),
                activebackground="#cbd5e1",
                cursor="hand2",
                command=lambda c=code: self._set_filter(c),
            )
            btn.pack(fill=tk.X, pady=3)
            self.filter_buttons[code] = btn

        # Right Content (Table + Detail Splitter)
        self.content_paned = ttk.PanedWindow(paned, orient=tk.VERTICAL)
        paned.add(self.content_paned, weight=1)

        # Table Frame
        table_frame = ttk.Frame(self.content_paned)
        self.content_paned.add(table_frame, weight=3)

        cols = ("id", "filename", "size", "progress", "status", "speed", "eta", "segments", "date")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")

        self.tree.heading("id", text="#")
        self.tree.heading("filename", text="File Name")
        self.tree.heading("size", text="Size")
        self.tree.heading("progress", text="Progress")
        self.tree.heading("status", text="Status")
        self.tree.heading("speed", text="Speed")
        self.tree.heading("eta", text="ETA")
        self.tree.heading("segments", text="Streams")
        self.tree.heading("date", text="Added")

        self.tree.column("id", width=60, anchor=tk.CENTER)
        self.tree.column("filename", width=320, anchor=tk.W)
        self.tree.column("size", width=120, anchor=tk.E)
        self.tree.column("progress", width=220, anchor=tk.W)
        self.tree.column("status", width=130, anchor=tk.CENTER)
        self.tree.column("speed", width=120, anchor=tk.E)
        self.tree.column("eta", width=100, anchor=tk.CENTER)
        self.tree.column("segments", width=110, anchor=tk.CENTER)
        self.tree.column("date", width=160, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)
        hsb.grid(row=1, column=0, sticky=tk.EW)

        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self.action_open_file())

        # Context Menu on Table
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Open File", command=self.action_open_file)
        self.context_menu.add_command(label="Open Containing Folder", command=self.action_open_folder)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Resume", command=self.action_resume)
        self.context_menu.add_command(label="Pause", command=self.action_pause)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Remove Task", command=self.action_remove)
        self.tree.bind("<Button-3>", self._show_context_menu)

        # Bottom Inspector Notebook (Tabs)
        inspector_frame = ttk.Frame(self.content_paned)
        self.content_paned.add(inspector_frame, weight=2)

        notebook = ttk.Notebook(inspector_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Segments (Parallel Connections)
        tab_segments = ttk.Frame(notebook, padding="10")
        notebook.add(tab_segments, text="Parallel Streams (Segments)")

        seg_cols = ("idx", "range", "downloaded", "prog", "status")
        self.seg_tree = ttk.Treeview(tab_segments, columns=seg_cols, show="headings", height=5)
        self.seg_tree.heading("idx", text="Stream #")
        self.seg_tree.heading("range", text="Byte Range")
        self.seg_tree.heading("downloaded", text="Transferred")
        self.seg_tree.heading("prog", text="Progress")
        self.seg_tree.heading("status", text="Status")

        self.seg_tree.column("idx", width=110, anchor=tk.CENTER)
        self.seg_tree.column("range", width=300, anchor=tk.W)
        self.seg_tree.column("downloaded", width=160, anchor=tk.E)
        self.seg_tree.column("prog", width=240, anchor=tk.W)
        self.seg_tree.column("status", width=130, anchor=tk.CENTER)

        seg_sb = ttk.Scrollbar(tab_segments, orient=tk.VERTICAL, command=self.seg_tree.yview)
        self.seg_tree.configure(yscrollcommand=seg_sb.set)
        self.seg_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        seg_sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Tab 2: File Info
        tab_info = ttk.Frame(notebook, padding="12")
        notebook.add(tab_info, text="Details & Destination")

        self.info_text = tk.Text(
            tab_info, wrap=tk.WORD, font=self.font_mono, bg="#f8fafc", relief=tk.FLAT, height=6
        )
        self.info_text.pack(fill=tk.BOTH, expand=True)
        self.info_text.insert(tk.END, "Select a task above to view technical properties.")
        self.info_text.config(state=tk.DISABLED)

    def _adjust_paned_sash(self):
        """Position the horizontal and vertical sashes comfortably."""
        try:
            h = self.winfo_height()
            if h > 400:
                self.content_paned.sashpos(0, int(h * 0.58))
        except Exception:
            pass

    def _build_status_bar(self):
        status_bar = ttk.Frame(self, relief=tk.SUNKEN, padding="8 10 8 10")
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.status_left = ttk.Label(status_bar, text="Ready", font=self.font_main)
        self.status_left.pack(side=tk.LEFT, padx=10)

        self.status_speed = ttk.Label(
            status_bar, text="Total Speed: 0 B/s", font=self.font_bold, foreground="#0284c7"
        )
        self.status_speed.pack(side=tk.RIGHT, padx=16)

    def _set_filter(self, code: str):
        self.selected_filter = code
        for c, btn in self.filter_buttons.items():
            btn.config(bg="#e2e8f0" if c == code else self.cget("bg"))
        self._render_table()

    def _show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    # ── Engine Thread ──────────────────────────────────────────────────

    def _start_engine_thread(self):
        def _runner():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.manager = DownloadManager(self.config_data)
            self.loop.run_until_complete(self.manager.start())

            # Start local background API listener for Chrome extension
            self._start_chrome_listener_in_loop()

            self.loop.run_forever()

        self.loop_thread = threading.Thread(target=_runner, daemon=True)
        self.loop_thread.start()

    def _start_chrome_listener_in_loop(self):
        """Start local HTTP server to receive Chrome extension downloads with CORS support."""
        from aiohttp import web

        cors_headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }

        async def handle_options(request):
            return web.Response(headers=cors_headers)

        async def handle_get_config(request):
            return web.json_response({
                "status": "ok",
                "app": "NFD",
                "version": "1.0.0",
                "connected": True,
            }, headers=cors_headers)

        async def handle_post_download(request):
            try:
                data = await request.json()
                url = data.get("url")
                filename = data.get("filename")
                if url:
                    self.after(0, lambda: self._prompt_incoming_download(url, filename))
                    return web.json_response({"status": "received"}, headers=cors_headers)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400, headers=cors_headers)
            return web.json_response({"error": "missing url"}, status=400, headers=cors_headers)

        app = web.Application()
        app.router.add_route("OPTIONS", "/{tail:.*}", handle_options)
        app.router.add_get("/api/config", handle_get_config)
        app.router.add_get("/api/health", handle_get_config)
        app.router.add_post("/api/downloads", handle_post_download)
        runner = web.AppRunner(app)

        async def _run_http():
            await runner.setup()
            from nassimdl.app import get_pid_on_port
            stale_pid = get_pid_on_port(8000)
            if stale_pid and stale_pid != os.getpid():
                try:
                    subprocess.run(
                        f"taskkill /F /PID {stale_pid}",
                        shell=True,
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

            site = web.TCPSite(runner, "127.0.0.1", 8000)
            try:
                await site.start()
            except OSError:
                pass

        asyncio.run_coroutine_threadsafe(_run_http(), self.loop)

    def _prompt_incoming_download(self, url: str, filename: str | None):
        """Called when Chrome catches a download and forwards it to NFD."""
        self.lift()
        self.attributes("-topmost", True)
        self.after_idle(self.attributes, "-topmost", False)

        AddDownloadDialog(
            self,
            initial_url=url,
            initial_filename=filename or "",
            default_dir=self.config_data.download.output_dir,
            default_mode=self.config_data.download.segment_mode,
            default_segments=self.config_data.download.default_segments,
            on_submit=self._dispatch_add_download,
        )

    # ── Actions ────────────────────────────────────────────────────────

    def action_add_url(self):
        clipboard_text = ""
        try:
            clipboard_text = self.clipboard_get().strip()
            if not (clipboard_text.startswith("http://") or clipboard_text.startswith("https://")):
                clipboard_text = ""
        except Exception:
            pass

        AddDownloadDialog(
            self,
            initial_url=clipboard_text,
            default_dir=self.config_data.download.output_dir,
            default_mode=self.config_data.download.segment_mode,
            default_segments=self.config_data.download.default_segments,
            on_submit=self._dispatch_add_download,
        )

    def _dispatch_add_download(self, url, filename, output_dir, segments, hash_val):
        if not self.manager or not self.loop:
            return

        future = asyncio.run_coroutine_threadsafe(
            self.manager.add_download(
                url=url,
                output_dir=output_dir,
                filename=filename,
                num_segments=segments,
                hash_value=hash_val,
                hash_type="sha256" if hash_val else None,
                auto_segments=(segments is None),
            ),
            self.loop,
        )

        def _done(f):
            try:
                f.result()
            except Exception as err:
                self.after(0, lambda: messagebox.showerror("Download Error", str(err), parent=self))

        future.add_done_callback(_done)

    def action_resume(self):
        dl_id = self._get_selected_id()
        if dl_id and self.manager and self.loop:
            asyncio.run_coroutine_threadsafe(self.manager.resume_download(dl_id), self.loop)

    def action_pause(self):
        dl_id = self._get_selected_id()
        if dl_id and self.manager and self.loop:
            asyncio.run_coroutine_threadsafe(self.manager.pause_download(dl_id), self.loop)

    def action_remove(self):
        dl_id = self._get_selected_id()
        if not dl_id:
            return
        if messagebox.askyesno("Confirm Removal", f"Remove download task #{dl_id}?", parent=self):
            if self.manager and self.loop:
                asyncio.run_coroutine_threadsafe(self.manager.remove_download(dl_id), self.loop)

    def action_clear_finished(self):
        """Remove all completed or failed downloads from the list."""
        if not self.manager or not self.loop:
            return

        to_remove = [
            d.id for d in self.downloads_cache
            if d.status in (DownloadStatus.COMPLETED, DownloadStatus.ERROR)
        ]
        if not to_remove:
            return

        async def _clear():
            for did in to_remove:
                await self.manager.remove_download(did)

        asyncio.run_coroutine_threadsafe(_clear(), self.loop)

    def action_open_folder(self):
        dl = self._get_selected_download()
        if not dl:
            return
        folder = str(dl.output_dir)
        if os.path.exists(folder):
            os.startfile(folder)

    def action_open_file(self):
        dl = self._get_selected_download()
        if not dl:
            return
        file_path = str(dl.output_path)
        if os.path.exists(file_path):
            os.startfile(file_path)
        else:
            messagebox.showinfo("File Not Found", f"File does not exist yet:\n{file_path}", parent=self)

    def action_settings(self):
        SettingsDialog(self, self.config_data, on_save=self._on_settings_saved)

    def _on_settings_saved(self):
        if self.manager:
            self.manager.set_speed_limit(self.config_data.network.max_speed_bytes)
            self.manager.config.download.max_concurrent = self.config_data.download.max_concurrent
            self.manager.config.download.segment_mode = self.config_data.download.segment_mode
            self.manager.config.download.default_segments = self.config_data.download.default_segments

    # ── Refresh Loop ───────────────────────────────────────────────────

    def _refresh_loop(self):
        if self.manager and self.loop:
            future = asyncio.run_coroutine_threadsafe(self.manager.list_downloads(), self.loop)

            def _got_list(f):
                try:
                    dls = f.result()
                    self.after(0, lambda: self._update_data(dls))
                except Exception:
                    pass

            future.add_done_callback(_got_list)

        self.after(300, self._refresh_loop)

    def _update_data(self, downloads: list[DownloadInfo]):
        self.downloads_cache = downloads
        self._render_table()
        self._render_details()

        # Update stats
        total = len(downloads)
        active = sum(1 for d in downloads if d.status == DownloadStatus.DOWNLOADING)
        completed = sum(1 for d in downloads if d.status == DownloadStatus.COMPLETED)

        global_speed = 0.0
        if self.manager:
            for d in downloads:
                task = self.manager.queue.get_task(d.id)
                if task:
                    global_speed += task.total_speed

        self.status_left.config(
            text=f"Total Tasks: {total}   |   Active: {active}   |   Completed: {completed}"
        )
        self.status_speed.config(text=f"Total Speed: {format_speed(global_speed)}")

    def _render_table(self):
        selected_id = self._get_selected_id()

        # Filter
        visible = []
        for dl in self.downloads_cache:
            if self.selected_filter == "all":
                visible.append(dl)
            elif self.selected_filter == dl.status.value:
                visible.append(dl)

        existing_items = set(self.tree.get_children())
        visible_ids = set()

        for dl in visible:
            item_id = str(dl.id)
            visible_ids.add(item_id)

            speed_str = "—"
            eta_str = "—"
            task = self.manager.queue.get_task(dl.id) if self.manager else None
            if task:
                speed_str = format_speed(task.total_speed)
                eta_str = format_eta(task.eta)

            if dl.auto_segments:
                if dl.status in (DownloadStatus.QUEUED, DownloadStatus.PROBING) and dl.num_segments <= 0:
                    streams_str = "Auto"
                elif task and dl.status == DownloadStatus.DOWNLOADING and task.current_concurrency_limit < dl.num_segments:
                    streams_str = f"{task.current_concurrency_limit}/{dl.num_segments} (Auto)"
                else:
                    streams_str = f"{dl.num_segments} (Auto)"
            else:
                streams_str = f"{dl.num_segments} Streams"

            progress_bar_chars = self._progress_bar_text(dl.progress)
            created_str = datetime.fromtimestamp(dl.created_at).strftime("%Y-%m-%d %H:%M")

            values = (
                dl.id,
                dl.filename,
                format_size(dl.file_size) if dl.file_size else "Unknown",
                f"{progress_bar_chars}  {dl.progress * 100:.1f}%",
                dl.status.value.upper(),
                speed_str,
                eta_str,
                streams_str,
                created_str,
            )

            if item_id in existing_items:
                self.tree.item(item_id, values=values)
            else:
                self.tree.insert("", tk.END, iid=item_id, values=values)

        # Remove items no longer in view
        for old_id in existing_items - visible_ids:
            self.tree.delete(old_id)

        # Restore selection
        if selected_id and str(selected_id) in visible_ids:
            self.tree.selection_set(str(selected_id))

    def _progress_bar_text(self, progress: float) -> str:
        width = 10
        filled = int(progress * width)
        return "■" * filled + "□" * (width - filled)

    def _on_tree_select(self, event):
        sel_id = self._get_selected_id()
        if sel_id != self.current_download_id:
            self.current_download_id = sel_id
            self._render_details()

    def _render_details(self):
        dl = self._get_selected_download()
        if not dl:
            self.info_text.config(state=tk.NORMAL)
            self.info_text.delete("1.0", tk.END)
            self.info_text.insert(tk.END, "No task selected.")
            self.info_text.config(state=tk.DISABLED)
            self.seg_tree.delete(*self.seg_tree.get_children())
            return

        # Update Info text
        task = self.manager.queue.get_task(dl.id) if self.manager else None
        if dl.auto_segments:
            if dl.num_segments <= 0:
                stream_info = "Auto (Waiting for size & server probe)"
            elif task and task.adaptation_reason:
                stream_info = f"Auto ({dl.num_segments} segments, active limit {task.current_concurrency_limit} — {task.adaptation_reason})"
            else:
                stream_info = f"Auto ({dl.num_segments} segments)"
        else:
            stream_info = f"Manual ({dl.num_segments} Streams)"

        self.info_text.config(state=tk.NORMAL)
        self.info_text.delete("1.0", tk.END)
        info_lines = [
            f"File Name:        {dl.filename}",
            f"Destination:      {dl.output_path}",
            f"Source URL:       {dl.url}",
            f"Size:             {format_size(dl.file_size) if dl.file_size else 'Unknown'} ({dl.bytes_downloaded:,} bytes transferred)",
            f"Streams:          {stream_info}",
            f"Range Support:    {'Yes (Multi-Segment Acceleration Active)' if dl.supports_range else 'No (Single-Stream Fallback)'}",
            f"Checksum:         {dl.hash_type.upper() + ': ' + dl.expected_hash if dl.expected_hash else 'None specified'}",
        ]
        if dl.error_message:
            info_lines.append(f"Error Details:    {dl.error_message}")

        self.info_text.insert(tk.END, "\n".join(info_lines))
        self.info_text.config(state=tk.DISABLED)

        # Update Segments tree
        self.seg_tree.delete(*self.seg_tree.get_children())
        for s in dl.segments:
            prog_bar = self._progress_bar_text(s.progress)
            self.seg_tree.insert(
                "",
                tk.END,
                values=(
                    f"Stream #{s.index + 1}",
                    f"{s.start_byte:,} – {s.end_byte:,}",
                    format_size(s.bytes_downloaded),
                    f"{prog_bar}  {s.progress * 100:.1f}%",
                    s.status.value.capitalize(),
                ),
            )

    def _get_selected_id(self) -> int | None:
        sel = self.tree.selection()
        if sel:
            try:
                return int(sel[0])
            except ValueError:
                return None
        return None

    def _get_selected_download(self) -> DownloadInfo | None:
        sel_id = self._get_selected_id()
        if sel_id:
            for dl in self.downloads_cache:
                if dl.id == sel_id:
                    return dl
        return None

    def _init_system_tray(self):
        """Initialize native Windows system tray icon."""
        ico, _ = get_icon_paths()
        if ico:
            try:
                self.tray = WindowsTrayIcon(
                    icon_path=ico,
                    tooltip="NFD — Nassim Fast Downloader (Active)",
                    on_click=self._on_tray_click,
                    on_right_click=self._on_tray_right_click,
                )
                self.tray.start()
            except Exception:
                self.tray = None
        else:
            self.tray = None

        # Build popup menu for tray
        self.tray_menu = tk.Menu(self, tearoff=0)
        self.tray_menu.add_command(label="Open NFD Window", command=self.action_show_window)
        self.tray_menu.add_command(label="Add New Download...", command=self.action_add_url)
        self.tray_menu.add_separator()
        self.tray_menu.add_command(label="Preferences...", command=self.action_settings)
        self.tray_menu.add_separator()
        self.tray_menu.add_command(label="Quit NFD Completely", command=self.action_quit_completely)

    def _on_tray_click(self):
        self.after(0, self.action_show_window)

    def _on_tray_right_click(self):
        self.after(0, self._show_tray_menu)

    def _show_tray_menu(self):
        try:
            # Query cursor position
            cursor_pos = self.winfo_pointerxy()
            self.tray_menu.post(cursor_pos[0], cursor_pos[1])
        except Exception:
            pass

    def action_show_window(self):
        """Bring window to foreground and restore."""
        self.deiconify()
        self.state("normal")
        self.lift()
        self.attributes("-topmost", True)
        self.after_idle(self.attributes, "-topmost", False)
        self.focus_force()

    def _on_close(self):
        """Minimize to tray instead of quitting when user clicks [X]."""
        self.withdraw()
        if not self._shown_tray_hint:
            self._shown_tray_hint = True
            if self.tray:
                self.tray.show_notification(
                    "NFD is Running in the Background",
                    "NFD is minimized to the system tray. Downloads and Chrome interception remain active."
                )

    def action_quit_completely(self):
        """Completely exit the NFD application."""
        if self.tray:
            self.tray.stop()
        if self.manager and self.loop:
            asyncio.run_coroutine_threadsafe(self.manager.stop(), self.loop)
        self.destroy()
        sys.exit(0)


def run_gui():
    app = NFDApp()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
