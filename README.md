# NFD — Nassim Fast Downloader

NFD is a high-speed, parallel download accelerator and manager built for Windows and Linux. It boosts download speeds by dividing files into concurrent byte-range streams, complete with a clean Windows desktop application, system tray support, and a Chrome extension for one-click browser integration.

---

## Key Features

- **Parallel Stream Acceleration**: Splits files into multiple concurrent streams via HTTP Range requests to maximize connection speeds.
- **Native Desktop GUI**: Clean desktop interface styled with native controls, Segoe UI typography, and system tray integration.
- **Chrome Extension Integration**: Automatically captures file downloads clicked in Chrome, cancels the standard browser downloader, and routes them to NFD.
- **Resilient Resume**: Uses disk-level byte inspection so interrupted or paused downloads resume with zero corrupted bytes.
- **System Tray & Windows Startup**: Can launch minimized on system startup and run quietly in the background.
- **Bandwidth Management**: Configurable speed limits and concurrent task queuing.
- **Docker Support**: Containerized headless daemon option for servers and NAS setups.

---

## Installation & Running

### Option 1: Native Windows App (Direct from Source)

1. Clone or download the repository.
2. Install the required dependencies:
   ```powershell
   pip install -e .
   ```
3. Launch the desktop application:
   - Double-click `NFD.bat` or `NFD.vbs` (runs quietly in the background), or run:
   ```powershell
   python NFD.py
   ```

### Option 2: Build a Standalone Executable (`.exe`)

To package NFD into a standalone executable that runs without requiring Python:
```powershell
pip install pyinstaller
python build_exe.py
```
The output directory will be created at `dist/NFD/NFD.exe`.

### Option 3: Docker (Headless Server)

Run NFD in a headless container with Docker Compose:
```bash
docker compose up -d
```
The REST API and browser interceptor will listen on port `8000`, saving completed downloads to `./downloads`.

---

## Chrome Extension Setup

1. Open Google Chrome (or any Chromium browser like Brave or Edge).
2. Navigate to `chrome://extensions`.
3. Enable **Developer mode** in the top-right corner.
4. Click **Load unpacked** and select the `extensions/chrome` directory from this repository.
5. Any download link you click in your browser will now automatically be accelerated by NFD.

---

## Command Line Interface (CLI)

NFD also includes a command-line tool:

```powershell
# Add a download
python -m nassimdl add "https://example.com/file.zip"

# Add with 8 parallel streams to a custom directory
python -m nassimdl add "https://example.com/file.zip" -s 8 -o "D:\Downloads"

# List active and past downloads
python -m nassimdl list

# Pause or resume a task
python -m nassimdl pause <ID>
python -m nassimdl resume <ID>

# Configure global speed limit (e.g. 10 MB/s)
python -m nassimdl config --max-speed 10MB
```

---

## Testing

Run the automated test suite:
```powershell
python -m pytest tests
```

---

## License

This project is licensed under the MIT License.
