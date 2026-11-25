import asyncio
import json
from typing import Any

from .ext_ops import (
    _tool_export_zip,
    _tool_fetch_url,
    _tool_run_command,
    _tool_web_search,
)
from .file_ops import (
    _tool_add_lines_after,
    _tool_delete_files,
    _tool_delete_lines,
    _tool_edit_lines,
    _tool_get_file_outline,
    _tool_list_files,
    _tool_read_file,
    _tool_read_files,
    _tool_replace_in_file,
    _tool_search_files,
    _tool_write_file,
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
    "read_files": _tool_read_files,
    "get_file_outline": _tool_get_file_outline,
    "write_file": _tool_write_file,
    "write_files": _tool_write_files,
    "edit_lines": _tool_edit_lines,
    "add_lines_after": _tool_add_lines_after,
    "delete_lines": _tool_delete_lines,
    "delete_files": _tool_delete_files,
    "replace_in_file": _tool_replace_in_file,
    "search_files": _tool_search_files,
    "run_command": _tool_run_command,
    "web_search": _tool_web_search,
    "fetch_url": _tool_fetch_url,
    "export_zip": _tool_export_zip,
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
    fn = TOOL_DISPATCH.get(name)
    if not fn:
        return f"Error: Unknown tool '{name}'."

    if name in ("write_file", "write_files", "edit_lines", "add_lines_after", "delete_lines", "delete_files", "replace_in_file"):
        async with _file_write_lock:
            return await asyncio.to_thread(fn, args)

    return await asyncio.to_thread(fn, args)