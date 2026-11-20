"""FastAPI backend with REST endpoints and WebSocket for live download monitoring."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nassimdl.core.manager import DownloadManager
from nassimdl.models.config import AppConfig
from nassimdl.models.download import DownloadInfo, DownloadStatus
from nassimdl.utils.units import format_eta, format_size, format_speed

manager: Optional[DownloadManager] = None
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager
    manager = DownloadManager()
    await manager.start()
    yield
    await manager.stop()


app = FastAPI(title="NassimDL API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AddDownloadRequest(BaseModel):
    url: str
    output_dir: Optional[str] = None
    filename: Optional[str] = None
    segments: Optional[int] = Field(default=None, ge=1, le=64)
    hash_value: Optional[str] = None
    hash_type: Optional[str] = None


class ConfigUpdateRequest(BaseModel):
    max_speed_bytes: Optional[int] = None
    max_concurrent: Optional[int] = None


def serialize_download(dl: DownloadInfo, active_task=None) -> dict:
    speed = active_task.total_speed if active_task else 0.0
    eta = active_task.eta if active_task else None

    segments_data = []
    for s in dl.segments:
        segments_data.append(
            {
                "index": s.index,
                "start_byte": s.start_byte,
                "end_byte": s.end_byte,
                "total_bytes": s.total_bytes,
                "bytes_downloaded": s.bytes_downloaded,
                "progress": round(s.progress * 100, 1),
                "status": s.status.value,
                "retries": s.retries,
            }
        )

    return {
        "id": dl.id,
        "url": dl.url,
        "filename": dl.filename,
        "output_dir": str(dl.output_dir),
        "output_path": str(dl.output_path),
        "file_size": dl.file_size,
        "file_size_formatted": format_size(dl.file_size) if dl.file_size else "Unknown",
        "bytes_downloaded": dl.bytes_downloaded,
        "bytes_downloaded_formatted": format_size(dl.bytes_downloaded),
        "progress": round(dl.progress * 100, 1),
        "status": dl.status.value,
        "num_segments": dl.num_segments,
        "supports_range": dl.supports_range,
        "speed": speed,
        "speed_formatted": format_speed(speed),
        "eta": eta,
        "eta_formatted": format_eta(eta),
        "error_message": dl.error_message,
        "segments": segments_data,
    }


@app.get("/api/downloads")
async def get_downloads():
    if not manager:
        raise HTTPException(status_code=503, detail="Manager not ready")
    all_dls = await manager.list_downloads()
    results = []
    for dl in all_dls:
        task = manager.queue.get_task(dl.id)
        results.append(serialize_download(dl, task))
    return results


@app.post("/api/downloads")
async def add_download(req: AddDownloadRequest):
    if not manager:
        raise HTTPException(status_code=503, detail="Manager not ready")
    output_dir = Path(req.output_dir).expanduser() if req.output_dir else None
    download_id = await manager.add_download(
        url=req.url,
        output_dir=output_dir,
        filename=req.filename,
        num_segments=req.segments,
        hash_value=req.hash_value,
        hash_type=req.hash_type,
    )
    return {"id": download_id, "message": "Download queued"}


@app.post("/api/downloads/{download_id}/pause")
async def pause_download(download_id: int):
    if not manager:
        raise HTTPException(status_code=503, detail="Manager not ready")
    success = await manager.pause_download(download_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot pause download")
    return {"success": True}


@app.post("/api/downloads/{download_id}/resume")
async def resume_download(download_id: int):
    if not manager:
        raise HTTPException(status_code=503, detail="Manager not ready")
    success = await manager.resume_download(download_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot resume download")
    return {"success": True}


@app.delete("/api/downloads/{download_id}")
async def remove_download(download_id: int):
    if not manager:
        raise HTTPException(status_code=503, detail="Manager not ready")
    success = await manager.remove_download(download_id)
    if not success:
        raise HTTPException(status_code=404, detail="Download not found")
    return {"success": True}


@app.get("/api/config")
async def get_config():
    if not manager:
        raise HTTPException(status_code=503, detail="Manager not ready")
    cfg = manager.config
    return {
        "max_concurrent": cfg.download.max_concurrent,
        "default_segments": cfg.download.default_segments,
        "output_dir": str(cfg.download.output_dir),
        "max_speed_bytes": cfg.network.max_speed_bytes,
        "max_speed_formatted": format_speed(cfg.network.max_speed_bytes)
        if cfg.network.max_speed_bytes > 0
        else "Unlimited",
    }


@app.post("/api/config")
async def update_config(req: ConfigUpdateRequest):
    if not manager:
        raise HTTPException(status_code=503, detail="Manager not ready")
    if req.max_speed_bytes is not None:
        manager.set_speed_limit(req.max_speed_bytes)
    if req.max_concurrent is not None:
        manager.config.download.max_concurrent = req.max_concurrent
    return {"success": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            if manager:
                all_dls = await manager.list_downloads()
                data = []
                total_speed = 0.0
                for dl in all_dls:
                    task = manager.queue.get_task(dl.id)
                    s_dl = serialize_download(dl, task)
                    if task:
                        total_speed += s_dl["speed"]
                    data.append(s_dl)

                await websocket.send_json(
                    {
                        "type": "stats",
                        "total_speed": total_speed,
                        "total_speed_formatted": format_speed(total_speed),
                        "active_count": manager.queue.active_count,
                        "downloads": data,
                    }
                )
            await asyncio.sleep(0.3)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
