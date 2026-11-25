import mimetypes
import shutil
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

from aiohttp import web

from sandbox.container import IGNORE_DIRS, resolve_workspace_path


# --- WebDAV Handlers (/dav/... : Windows / iOS / Android ネイティブマウント用) ---
def _dav_prop_response(href: str, path: Path) -> ET.Element:
    resp = ET.Element("{DAV:}response")
    ET.SubElement(resp, "{DAV:}href").text = href
    propstat = ET.SubElement(resp, "{DAV:}propstat")
    prop = ET.SubElement(propstat, "{DAV:}prop")

    ET.SubElement(prop, "{DAV:}displayname").text = path.name or "workspace"
    res_type = ET.SubElement(prop, "{DAV:}resourcetype")
    if path.is_dir():
        ET.SubElement(res_type, "{DAV:}collection")
    else:
        stat = path.stat()
        ET.SubElement(prop, "{DAV:}getcontentlength").text = str(stat.st_size)
        mime, _ = mimetypes.guess_type(path.name)
        ET.SubElement(prop, "{DAV:}getcontenttype").text = mime or "application/octet-stream"

    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    ET.SubElement(prop, "{DAV:}getlastmodified").text = format_datetime(mtime, usegmt=True)
    ET.SubElement(propstat, "{DAV:}status").text = "HTTP/1.1 200 OK"
    return resp


async def handle_webdav(request: web.Request) -> web.StreamResponse:
    tail = request.match_info.get("tail", "")
    target = resolve_workspace_path(tail)
    if not target:
        return web.Response(status=403, text="Forbidden")

    method = request.method.upper()
    if method == "OPTIONS":
        return web.Response(
            status=200,
            headers={
                "DAV": "1, 2",
                "Allow": "OPTIONS, GET, HEAD, PUT, DELETE, MKCOL, PROPFIND, MOVE, COPY",
                "MS-Author-Via": "DAV",
            },
        )

    if method == "PROPFIND":
        if not target.exists():
            return web.Response(status=404)
        depth = request.headers.get("Depth", "1")
        multistatus = ET.Element("{DAV:}multistatus")
        base_href = "/dav/" + tail.strip("/")
        if target.is_dir() and not base_href.endswith("/"):
            base_href += "/"
        multistatus.append(_dav_prop_response(base_href, target))

        if target.is_dir() and depth != "0":
            for child in sorted(target.iterdir()):
                if child.name in IGNORE_DIRS:
                    continue
                c_href = base_href + urllib.parse.quote(child.name) + ("/" if child.is_dir() else "")
                multistatus.append(_dav_prop_response(c_href, child))

        xml_bytes = ET.tostring(multistatus, encoding="utf-8", xml_declaration=True)
        return web.Response(status=207, body=xml_bytes, content_type="application/xml; charset=utf-8")

    if method in ("GET", "HEAD"):
        if not target.exists() or target.is_dir():
            return web.Response(status=404)
        return web.FileResponse(target)

    if method == "PUT":
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as f:
            async for chunk in request.content.iter_chunked(65536):
                f.write(chunk)
        return web.Response(status=201)

    if method == "MKCOL":
        target.mkdir(parents=True, exist_ok=True)
        return web.Response(status=201)

    if method == "DELETE":
        if not target.exists():
            return web.Response(status=404)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return web.Response(status=204)

    if method in ("MOVE", "COPY"):
        dest_hdr = request.headers.get("Destination", "")
        parsed = urllib.parse.urlparse(dest_hdr)
        dest_rel = urllib.parse.unquote(parsed.path)
        if "/dav/" in dest_rel:
            dest_rel = dest_rel.split("/dav/", 1)[1]
        dest_path = resolve_workspace_path(dest_rel)
        if not dest_path or not target.exists():
            return web.Response(status=400)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if method == "MOVE":
            shutil.move(str(target), str(dest_path))
        else:
            if target.is_dir():
                shutil.copytree(target, dest_path, dirs_exist_ok=True)
            else:
                shutil.copy2(target, dest_path)
        return web.Response(status=201)

    return web.Response(status=405)