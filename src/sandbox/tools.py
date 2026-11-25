import asyncio
import json
from typing import Any

from core.config import get_web_search_enable

from .ext_ops import (
    _tool_fetch_url,
    _tool_run_command,
    _tool_web_search,
)
from .file_ops import (
    _tool_add_lines_after,
    _tool_delete_files,
    _tool_delete_lines,
    _tool_list_files,
    _tool_read_file,
    _tool_replace_lines,
    _tool_write_files,
)
from .workspace_bundle import (
    INITIAL_DOCUMENTS,
    auto_apply_heavy_markdown,
    build_heavy_context_bundle,
    init_workspace_files,
    read_workspace_doc,
)

__all__ = [
    "INITIAL_DOCUMENTS",
    "TOOL_DISPATCH",
    "auto_apply_heavy_markdown",
    "build_heavy_context_bundle",
    "execute_tool",
    "init_workspace_files",
    "parse_tool_args",
    "read_workspace_doc",
]

TOOL_DISPATCH = {
    "list_files": _tool_list_files,
    "read_file": _tool_read_file,
    "write_files": _tool_write_files,
    "replace_lines": _tool_replace_lines,
    "add_lines_after": _tool_add_lines_after,
    "delete_lines": _tool_delete_lines,
    "delete_files": _tool_delete_files,
    "run_command": _tool_run_command,
    "web_search": _tool_web_search,
    "fetch_url": _tool_fetch_url,
}


def parse_tool_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            return {}
    return {}


_file_write_lock = asyncio.Lock()


async def execute_tool(name: str, raw_args: Any) -> str:
    args = parse_tool_args(raw_args)
    if name == "complete_phase":
        return str(args.get("summary", "Phase completed."))

    if name in ("web_search", "fetch_url") and not get_web_search_enable():
        return f"Error: Tool '{name}' is disabled by environment configuration (WEB_SEARCH_ENABLE=false)."

    fn = TOOL_DISPATCH.get(name)
    if not fn:
        return f"Error: Unknown tool '{name}'."

    if name in ("write_files", "replace_lines", "add_lines_after", "delete_lines", "delete_files"):
        async with _file_write_lock:
            return await asyncio.to_thread(fn, args)

    return await asyncio.to_thread(fn, args)