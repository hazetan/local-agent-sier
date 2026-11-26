import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from agents.auditor_agent import continuous_auditor_loop
from agents.dev_pipeline import continuous_dev_pipeline_loop
from agents.pm_agent import handle_client_message
from core.config import get_reset_workspace_on_startup, get_workspace_dir
from core.dashboard_render import collect_dashboard_metrics, collect_raw_stream_snapshot
from core.llm_client import warmup_models
from core.logger import now_jst_str, system_logger
from core.nas_server import nas_server
from core.telemetry import telemetry
from fastapi import (
    APIRouter,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from sandbox.container import (
    ensure_sandbox_container,
    export_workspace_zip,
    resolve_workspace_path,
)
from sandbox.file_ops import _tool_list_files
from sandbox.tools import init_workspace_files

sier_router = APIRouter(prefix="/api/sier", tags=["SIer Agent"])
STATIC_DIR = Path(__file__).resolve().parent / "static"

class WebMonitorHub:
    """WebSocket接続管理およびリアルタイム・イベント配信ハブ"""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.feed_history: deque[dict[str, Any]] = deque(maxlen=100)
        self._bg_tasks: list[asyncio.Task[Any]] = []
        self._last_raw_push_t: float = 0.0

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        await ws.send_json({
            "type": "init",
            "metrics": await collect_dashboard_metrics(),
            "feed": list(self.feed_history),
        })

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self.clients:
            return
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except (WebSocketDisconnect, RuntimeError, OSError):
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def add_feed_event(self, sender: str, text: str) -> None:
        event = {"timestamp": now_jst_str(), "sender": sender, "text": text}
        self.feed_history.append(event)
        await self.broadcast({"type": "feed", "event": event})

    async def on_raw_telemetry_update(self) -> None:
        """LLMストリーミング中・ツール実行直後に呼び出される即時プッシュコールバック"""
        await self.broadcast({
            "type": "raw_stream",
            "data": collect_raw_stream_snapshot(),
        })

    async def on_pipeline_phase_done(self, message_text: str) -> None:
        """SE / PG / QA フェーズ完了時のリアルタイム通知"""
        await self.add_feed_event("PIPELINE", message_text)
        await self.broadcast({
            "type": "metrics",
            "data": await collect_dashboard_metrics(),
        })

    async def _periodic_metrics_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            if self.clients:
                await self.broadcast({
                    "type": "metrics",
                    "data": await collect_dashboard_metrics(),
                })

    async def start_background_workers(self) -> None:
        if self._bg_tasks:
            return
        await nas_server.start()

        telemetry.on_raw_update = self.on_raw_telemetry_update

        self._bg_tasks.extend([
            asyncio.create_task(warmup_models(), name="web_warmup"),
            asyncio.create_task(self._periodic_metrics_loop(), name="web_metrics_loop"),
            asyncio.create_task(
                continuous_dev_pipeline_loop(on_phase_complete=self.on_pipeline_phase_done),
                name="web_dev_pipeline",
            ),
            asyncio.create_task(continuous_auditor_loop(), name="web_auditor"),
        ])
        system_logger.log("WEB_APP", "SIer Web background workers started.")

    async def stop_background_workers(self) -> None:
        for task in self._bg_tasks:
            if not task.done():
                task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            self._bg_tasks.clear()
        await nas_server.stop()
        system_logger.log("WEB_APP", "SIer Web background workers stopped.")


monitor_hub = WebMonitorHub()


@asynccontextmanager
async def sier_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """既存のFastAPIアプリの lifespan に組み込むためのコンテキストマネージャ"""
    system_logger.reset()
    await monitor_hub.start_background_workers()
    try:
        yield
    finally:
        await monitor_hub.stop_background_workers()


@sier_router.websocket("/ws")
async def websocket_monitor_endpoint(ws: WebSocket) -> None:
    await monitor_hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        monitor_hub.disconnect(ws)


@sier_router.get("/status")
async def get_current_status() -> dict[str, Any]:
    return await collect_dashboard_metrics()


@sier_router.post("/chat")
async def post_client_chat(
    message: str = Form(""),
    files: list[UploadFile] = File(default=[]),
) -> dict[str, Any]:
    """テキストと添付ファイルを受け取り、文字数制限なしでPMエージェントの応答を返す"""
    sections: list[str] = []
    for up_file in files:
        raw_bytes = await up_file.read()
        if not raw_bytes or b"\x00" in raw_bytes[:1024]:
            continue
        try:
            text_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text_content = raw_bytes.decode("cp932", errors="replace")
        sections.append(f"=== 添付ファイル: {up_file.filename} ===\n{text_content.strip()}")

    clean_text = message.strip()
    if sections:
        att_block = "\n\n".join(sections)
        clean_text = f"{clean_text}\n\n{att_block}".strip() if clean_text else att_block

    if not clean_text:
        raise HTTPException(status_code=400, detail="Message and text attachments are both empty.")

    await monitor_hub.add_feed_event("CLIENT", clean_text)
    reply_text = await handle_client_message(clean_text)
    await monitor_hub.add_feed_event("PM", reply_text)

    return {"reply": reply_text, "timestamp": now_jst_str()}


@sier_router.get("/workspace/files")
async def list_workspace_files(directory: str = "~/") -> dict[str, Any]:
    listing = await asyncio.to_thread(_tool_list_files, {"directory": directory})
    return {"directory": directory, "listing": listing}


@sier_router.get("/workspace/file")
async def read_workspace_file_raw(path: str) -> dict[str, Any]:
    target = resolve_workspace_path(path)
    if not target or not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail=f"File '{path}' not found.")
    content = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
    rel = str(target.relative_to(get_workspace_dir().resolve()))
    return {"path": f"~/{rel}", "content": content}


@sier_router.get("/workspace/zip")
async def download_workspace_zip(path: str = ".") -> FileResponse:
    zip_path = await asyncio.to_thread(export_workspace_zip, path)
    return FileResponse(zip_path, filename=zip_path.name)


@sier_router.get("/dashboard")
async def serve_dashboard_html() -> FileResponse:
    """特設ダッシュボードHTMLを返す"""
    return FileResponse(STATIC_DIR / "dashboard.html", media_type="text/html")


@sier_router.get("/static/sier_dashboard.js")
async def serve_dashboard_js() -> FileResponse:
    """フロントエンド用JSモジュールを返す"""
    return FileResponse(STATIC_DIR / "sier_dashboard.js", media_type="application/javascript")