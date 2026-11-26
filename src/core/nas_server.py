import base64
import html
import urllib.parse
from datetime import datetime
from pathlib import Path

from aiohttp import web

from sandbox.container import (
    IGNORE_DIRS,
    export_workspace_zip,
    resolve_workspace_path,
)

from .config import (
    get_nas_enable,
    get_nas_password,
    get_nas_port,
    get_nas_user,
    get_workspace_dir,
)
from .nas_samba import (
    ensure_samba_service,
    get_local_ip,
    get_nas_status_summary,
    stop_samba_service,
)
from .nas_webdav import handle_webdav

__all__ = ["WorkspaceNASServer", "get_nas_status_summary", "nas_server"]


# --- Basic Auth Middleware (NAS_USER / NAS_PASSWORD が設定されている場合に有効化) ---
@web.middleware
async def basic_auth_middleware(request: web.Request, handler):
    expected_user = get_nas_user()
    expected_pass = get_nas_password()
    if not expected_user or not expected_pass:
        return await handler(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            user, pw = decoded.split(":", 1)
            if user == expected_user and pw == expected_pass:
                return await handler(request)
        except (ValueError, UnicodeDecodeError):
            pass

    return web.Response(
        status=401,
        headers={"WWW-Authenticate": 'Basic realm="SIer Workspace NAS"'},
        text="401 Unauthorized",
    )


# --- Web UI Handlers (iOS / Android / Windows ブラウザ対応) ---
async def handle_web_ui(request: web.Request) -> web.StreamResponse:
    rel_q = request.query.get("path", "")
    target = resolve_workspace_path(rel_q)
    ws_root = get_workspace_dir().resolve()

    if not target or not target.exists():
        raise web.HTTPNotFound(text="Path not found")

    if target.is_file():
        if request.query.get("download") == "1":
            return web.FileResponse(target)
        content = target.read_text(encoding="utf-8", errors="replace")
        rel_str = str(target.relative_to(ws_root))
        parent_str = "" if str(target.parent.relative_to(ws_root)) == "." else str(target.parent.relative_to(ws_root))
        page = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Edit ~/{html.escape(rel_str)}</title>
        <style>body{{background:#0d1117;color:#c9d1d9;font-family:monospace;padding:16px;margin:0}}
        textarea{{width:100%;height:72vh;background:#161b22;color:#f0f6fc;border:1px solid #30363d;padding:10px;box-sizing:border-box;font-family:monospace}}
        button,a.btn{{background:#238636;color:#fff;border:none;padding:8px 16px;text-decoration:none;border-radius:6px;cursor:pointer;display:inline-block;margin-right:8px}}
        a.sec{{background:#30363d}}</style></head><body>
        <h3>📄 ~/{html.escape(rel_str)}</h3>
        <form method="POST" action="/api/save">
          <input type="hidden" name="path" value="{html.escape(rel_str)}">
          <textarea name="content">{html.escape(content)}</textarea><br><br>
          <button type="submit">💾 Save</button>
          <a class="btn sec" href="/?path={urllib.parse.quote(rel_str)}&download=1">⬇ Download</a>
          <a class="btn sec" href="/?path={urllib.parse.quote(parent_str)}">⬅ Back</a>
        </form></body></html>"""
        return web.Response(text=page, content_type="text/html")

    rel_dir = "" if target == ws_root else str(target.relative_to(ws_root))
    rows: list[str] = []
    if target != ws_root:
        p_rel = "" if target.parent == ws_root else str(target.parent.relative_to(ws_root))
        rows.append(f'<tr><td>📁 <a href="/?path={urllib.parse.quote(p_rel)}">.. (Parent)</a></td><td>-</td><td>-</td></tr>')

    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if item.name in IGNORE_DIRS:
            continue
        item_rel = str(item.relative_to(ws_root))
        q_path = urllib.parse.quote(item_rel)
        mtime = datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        if item.is_dir():
            rows.append(f'<tr><td>📁 <a href="/?path={q_path}">{html.escape(item.name)}/</a></td><td>DIR</td><td>{mtime}</td></tr>')
        else:
            size_kb = f"{item.stat().st_size / 1024:.1f} KB"
            rows.append(
                f'<tr><td>📄 <a href="/?path={q_path}">{html.escape(item.name)}</a></td>'
                f'<td>{size_kb}</td><td>{mtime} | <a href="/?path={q_path}&download=1">⬇</a></td></tr>'
            )

    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>SIer Workspace NAS (~/)</title>
    <style>body{{background:#0d1117;color:#c9d1d9;font-family:sans-serif;padding:16px;max-width:960px;margin:auto}}
    a{{color:#58a6ff;text-decoration:none}} table{{width:100%;border-collapse:collapse;margin-top:12px}}
    th,td{{padding:10px;border-bottom:1px solid #21262d;text-align:left}}
    .bar{{display:flex;gap:8px;flex-wrap:wrap;background:#161b22;padding:12px;border-radius:8px;margin-bottom:12px}}
    input,button,a.btn{{background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:6px 12px;border-radius:6px}}</style></head><body>
    <h2>📂 Workspace NAS : ~/{html.escape(rel_dir)}</h2>
    <div class="bar">
      <form method="POST" action="/api/upload" enctype="multipart/form-data">
        <input type="hidden" name="dir" value="{html.escape(rel_dir)}">
        <input type="file" name="file" required>
        <button type="submit">⬆ Upload</button>
      </form>
      <form method="POST" action="/api/mkdir">
        <input type="hidden" name="dir" value="{html.escape(rel_dir)}">
        <input type="text" name="name" placeholder="New folder / file.txt" required>
        <button type="submit">➕ Create</button>
      </form>
      <a class="btn" href="/api/zip?path={urllib.parse.quote(rel_dir or '.')}">📦 Download ZIP</a>
    </div>
    <small>WebDAV Mount URL: <code>/dav/</code></small>
    <table><thead><tr><th>Name</th><th>Size</th><th>Modified</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></body></html>"""
    return web.Response(text=page, content_type="text/html")


async def handle_api_save(request: web.Request) -> web.StreamResponse:
    data = await request.post()
    rel_path = str(data.get("path", ""))
    content = str(data.get("content", ""))
    target = resolve_workspace_path(rel_path)
    if not target or target.is_dir():
        raise web.HTTPBadRequest(text="Invalid file path")
    target.write_text(content, encoding="utf-8")
    raise web.HTTPFound(location=f"/?path={urllib.parse.quote(rel_path)}")


async def handle_api_upload(request: web.Request) -> web.StreamResponse:
    reader = await request.multipart()
    cur_dir = ""
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "dir":
            cur_dir = (await part.read(decode=True)).decode("utf-8", errors="replace")
        elif part.name == "file" and part.filename:
            rel_dest = f"{cur_dir}/{part.filename}".lstrip("/")
            target = resolve_workspace_path(rel_dest)
            if target:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as f:
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk:
                            break
                        f.write(chunk)
    raise web.HTTPFound(location=f"/?path={urllib.parse.quote(cur_dir)}")


async def handle_api_mkdir(request: web.Request) -> web.StreamResponse:
    data = await request.post()
    cur_dir = str(data.get("dir", ""))
    name = str(data.get("name", "")).strip()
    rel_dest = f"{cur_dir}/{name}".lstrip("/")
    target = resolve_workspace_path(rel_dest)
    if target:
        if "." in Path(name).name:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(exist_ok=True)
        else:
            target.mkdir(parents=True, exist_ok=True)
    raise web.HTTPFound(location=f"/?path={urllib.parse.quote(cur_dir)}")


async def handle_api_zip(request: web.Request) -> web.StreamResponse:
    rel_path = request.query.get("path", ".")
    zip_path = export_workspace_zip(rel_path)
    return web.FileResponse(zip_path)


class WorkspaceNASServer:
    def __init__(self) -> None:
        self.runner: web.AppRunner | None = None

    async def start(self) -> None:
        ensure_samba_service()
        if not get_nas_enable() or self.runner is not None:
            return

        app = web.Application(middlewares=[basic_auth_middleware], client_max_size=512 * 1024 * 1024)
        app.router.add_get("/", handle_web_ui)
        app.router.add_post("/api/save", handle_api_save)
        app.router.add_post("/api/upload", handle_api_upload)
        app.router.add_post("/api/mkdir", handle_api_mkdir)
        app.router.add_get("/api/zip", handle_api_zip)
        app.router.add_route("*", "/dav", handle_webdav)
        app.router.add_route("*", "/dav/{tail:.*}", handle_webdav)

        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        port = get_nas_port()
        site = web.TCPSite(self.runner, "0.0.0.0", port)
        try:
            await site.start()
            print(f"[NAS] WebUI & WebDAV started on http://{get_local_ip()}:{port} (DAV: /dav/)")
        except OSError as exc:
            print(f"[NAS] Port {port} is already in use ({exc}). Skipping duplicate NAS server bind.")
            await self.runner.cleanup()
            self.runner = None


nas_server = WorkspaceNASServer()