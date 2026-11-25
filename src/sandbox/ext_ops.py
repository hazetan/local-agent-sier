import re
import urllib.request
from typing import Any

from ddgs import DDGS

from .container import (
    export_workspace_zip,
    run_in_sandbox,
)


def _tool_run_command(args: dict[str, Any]) -> str:
    cmd = str(args.get("command", "")).strip()
    cmd = re.sub(r"(?:</?div>|<channel\|>|<\|channel>thought|```)+$", "", cmd, flags=re.IGNORECASE).strip()
    if not cmd:
        return "Error: Empty command."
    return run_in_sandbox(cmd)


def _tool_web_search(args: dict[str, Any]) -> str:
    from ddgs.exceptions import DDGSException

    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: Empty query."
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "No results found."
        return "\n".join(
            f"{i}. {r.get('title')}\n   {r.get('body')}\n   URL: {r.get('href')}"
            for i, r in enumerate(results, 1)
        )
    except (DDGSException, OSError, TimeoutError, ValueError, RuntimeError) as exc:
        return f"Search error: {exc}"


def _tool_fetch_url(args: dict[str, Any]) -> str:
    url = str(args.get("url", "")).strip()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        text = re.sub(r"<(script|style).*?>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        return " ".join(re.sub(r"<[^>]+>", " ", text).split())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return f"Fetch error: {exc}"


def _tool_export_zip(args: dict[str, Any]) -> str:
    sub = str(args.get("directory", "~/"))
    zip_path = export_workspace_zip(sub)
    return f"ZIP_CREATED:{zip_path}"