import os
import shutil
from pathlib import Path
from typing import Any

from core.config import get_io_chunk_bytes, get_workspace_dir

from .container import (
    IGNORE_DIRS,
    resolve_workspace_path,
)

PROTECTED_DOC_NAMES = ("README.md", "TASK.md", "DESIGN.md", "REPORT.md")


def _stream_read_lines(
    path: Path,
    start_line: int = 1,
    end_line: int | None = None,
) -> tuple[list[str], int]:
    """
    粒度可変のバッファサイズ (IO_CHUNK_BYTES) でファイルをストリーミング走査し、
    文字数や行数の切り詰めなしで指定範囲 (start_line〜end_line) の全行と総行数を返す。
    """
    buf_size = get_io_chunk_bytes()
    collected: list[str] = []
    total_lines = 0

    with path.open("r", encoding="utf-8", errors="replace", buffering=buf_size) as f:
        for idx, raw_line in enumerate(f, start=1):
            total_lines = idx
            if idx < start_line:
                continue
            if end_line is not None and idx > end_line:
                continue
            collected.append(raw_line.rstrip("\r\n"))

    return collected, total_lines


def _format_with_line_numbers(lines: list[str], start_line: int = 1) -> str:
    """各行の左側に1始まりの行番号 ('   1 | ...') を付与する"""
    if not lines:
        return "(Empty file)"
    width = max(4, len(str(start_line + len(lines) - 1)))
    return "\n".join(f"{idx:>{width}} | {line}" for idx, line in enumerate(lines, start=start_line))


def _tool_list_files(args: dict[str, Any]) -> str:
    raw_dir = str(args.get("directory", "~/"))
    target_dir = resolve_workspace_path(raw_dir)
    if not target_dir or not target_dir.exists() or not target_dir.is_dir():
        return f"Error: Directory '{raw_dir}' does not exist inside ~/."

    ws = get_workspace_dir().resolve()
    lines: list[str] = []
    for root, dirs, files in os.walk(target_dir):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
        rel_root = Path(root).relative_to(ws)
        prefix = "~/" if str(rel_root) == "." else f"~/{rel_root}/"
        for d in dirs:
            lines.append(f"[DIR]  {prefix}{d}/")
        for f in sorted(files):
            lines.append(f"[FILE] {prefix}{f}")
    return "\n".join(lines) if lines else "(Directory is empty)"


def _tool_read_file(args: dict[str, Any]) -> str:
    raw_path = str(args.get("path", "")).strip()
    target = resolve_workspace_path(raw_path)
    if not target or not target.exists():
        return f"Error: File '{raw_path}' not found inside ~/."
    if target.is_dir():
        return f"Error: '{raw_path}' is a directory. Use 'list_files' instead."

    start_line = max(1, int(args.get("start_line", 1)))
    raw_end = args.get("end_line")
    req_end_line = int(raw_end) if raw_end is not None else None

    sliced, total = _stream_read_lines(target, start_line=start_line, end_line=req_end_line)
    rel = target.relative_to(get_workspace_dir().resolve())
    if total == 0:
        return f"=== FILE: ~/{rel} (0 lines) ===\n(Empty file)"

    actual_end = start_line + len(sliced) - 1 if sliced else start_line
    header = f"=== FILE: ~/{rel} (lines {start_line}-{actual_end} of {total}) ==="
    body = _format_with_line_numbers(sliced, start_line=start_line)
    return f"{header}\n{body}"


def _write_single_file_entry(item: dict[str, Any]) -> str:
    raw_path = str(item.get("path", "")).strip()
    content = str(item.get("content", ""))
    target = resolve_workspace_path(raw_path)
    if not target or target.is_dir():
        return f"Error: Invalid file path '{raw_path}'."

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    rel = target.relative_to(get_workspace_dir().resolve())
    line_cnt = len(content.splitlines())
    return f"Successfully wrote {len(content)} chars ({line_cnt} lines) to ~/{rel}."


def _tool_write_files(args: dict[str, Any]) -> str:
    """単一または複数のファイルを一括で新規作成・上書きする"""
    files_list = args.get("files")
    if isinstance(files_list, list) and files_list:
        entries = files_list
    elif "path" in args and "content" in args:
        entries = [args]
    else:
        return "Error: 'files' must be a non-empty list of {'path': ..., 'content': ...}."

    results: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        results.append(_write_single_file_entry(item))
    return "\n".join(results) if results else "Error: No valid file entries provided."


def _tool_replace_lines(args: dict[str, Any]) -> str:
    """
    指定した行番号範囲 (start_line 〜 end_line, 1始まり・両端含む) の行を削除し、
    その位置に new_text（複数行可）を挿入して置換する。
    同一ファイル内の複数箇所置換は行番号の降順（下から上）で自動適用し、行ズレを防止する。
    """
    raw_replacements = args.get("replacements") or args.get("edits")
    if isinstance(raw_replacements, list) and raw_replacements:
        replace_items = raw_replacements
    elif "path" in args and "start_line" in args:
        replace_items = [args]
    else:
        return "Error: Provide 'replacements' list or ('path', 'start_line', 'end_line', 'new_text')."

    ws = get_workspace_dir().resolve()
    grouped: dict[Path, list[tuple[int, int, str]]] = {}
    errors: list[str] = []

    for idx, item in enumerate(replace_items, 1):
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path", "")).strip()
        target = resolve_workspace_path(raw_path)
        if not target or not target.exists() or target.is_dir():
            errors.append(f"Replacement #{idx}: File '{raw_path}' not found.")
            continue
        try:
            s_line = int(item["start_line"])
            e_line = int(item.get("end_line", s_line))
        except (KeyError, ValueError, TypeError):
            errors.append(f"Replacement #{idx} ({raw_path}): 'start_line'/'end_line' must be integers.")
            continue

        if s_line < 1 or e_line < s_line:
            errors.append(
                f"Replacement #{idx} ({raw_path}): Invalid range {s_line}-{e_line} (must be 1 <= start_line <= end_line)."
            )
            continue

        new_text = str(item.get("new_text", item.get("new_content", "")))
        grouped.setdefault(target, []).append((s_line, e_line, new_text))

    if errors:
        return "Error(s) in replace_lines:\n" + "\n".join(errors)

    reports: list[str] = []

    for target, ops in grouped.items():
        rel = target.relative_to(ws)
        orig_text = target.read_text(encoding="utf-8", errors="replace")
        lines = orig_text.splitlines()
        total = len(lines)

        ops_sorted = sorted(ops, key=lambda x: (x[0], x[1]), reverse=True)

        file_err = False
        for i, (s_line, e_line, _) in enumerate(ops_sorted):
            if s_line > total + 1 or e_line > max(total, s_line):
                reports.append(f"Error (~/{rel}): Range {s_line}-{e_line} exceeds file length ({total} lines).")
                file_err = True
                break
            if i > 0:
                prev_s, prev_e, _ = ops_sorted[i - 1]
                if e_line >= prev_s:
                    reports.append(
                        f"Error (~/{rel}): Overlapping line ranges {s_line}-{e_line} and {prev_s}-{prev_e}."
                    )
                    file_err = True
                    break
        if file_err:
            continue

        for s_line, e_line, new_text in ops_sorted:
            replacement_lines = new_text.splitlines() if new_text != "" else []
            lines[s_line - 1 : e_line] = replacement_lines

        new_full_text = "\n".join(lines) + ("\n" if lines else "")
        target.write_text(new_full_text, encoding="utf-8")

        min_s = min(op[0] for op in ops_sorted)
        max_e = max(op[1] for op in ops_sorted)
        preview_start = max(1, min_s - 2)
        preview_end = min(len(lines), max_e + 5)
        preview_body = _format_with_line_numbers(lines[preview_start - 1 : preview_end], start_line=preview_start)

        reports.append(
            f"Updated ~/{rel} ({len(ops_sorted)} replacement(s) applied, now {len(lines)} lines).\n"
            f"Preview (lines {preview_start}-{preview_end}):\n{preview_body}"
        )

    return "\n\n".join(reports)


def _tool_add_lines_after(args: dict[str, Any]) -> str:
    """
    指定した行番号 (after_line, 0=ファイル先頭, 1〜総行数=該当行の直後) の後ろに新しい行を挿入する。
    同一ファイル内の複数箇所への挿入は行番号の降順（下から上）で自動適用し、行ズレを防止する。
    """
    raw_insertions = args.get("insertions") or args.get("replacements") or args.get("edits")
    if isinstance(raw_insertions, list) and raw_insertions:
        insert_items = raw_insertions
    elif "path" in args and "after_line" in args:
        insert_items = [args]
    else:
        return "Error: Provide 'insertions' list or ('path', 'after_line', 'new_text')."

    ws = get_workspace_dir().resolve()
    grouped: dict[Path, list[tuple[int, str]]] = {}
    errors: list[str] = []

    for idx, item in enumerate(insert_items, 1):
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path", "")).strip()
        target = resolve_workspace_path(raw_path)
        if not target or not target.exists() or target.is_dir():
            errors.append(f"Insertion #{idx}: File '{raw_path}' not found.")
            continue
        try:
            after_line = int(item["after_line"])
        except (KeyError, ValueError, TypeError):
            errors.append(f"Insertion #{idx} ({raw_path}): 'after_line' must be an integer (>= 0).")
            continue

        if after_line < 0:
            errors.append(f"Insertion #{idx} ({raw_path}): Invalid after_line={after_line} (must be >= 0).")
            continue

        new_text = str(item.get("new_text", item.get("new_content", "")))
        grouped.setdefault(target, []).append((after_line, new_text))

    if errors:
        return "Error(s) in add_lines_after:\n" + "\n".join(errors)

    reports: list[str] = []

    for target, ops in grouped.items():
        rel = target.relative_to(ws)
        orig_text = target.read_text(encoding="utf-8", errors="replace")
        lines = orig_text.splitlines()
        total = len(lines)

        file_err = False
        for after_line, _ in ops:
            if after_line > total:
                reports.append(
                    f"Error (~/{rel}): after_line={after_line} exceeds file length ({total} lines)."
                )
                file_err = True
                break
        if file_err:
            continue

        indexed_ops = list(enumerate(ops))
        ops_sorted = sorted(indexed_ops, key=lambda x: (x[1][0], -x[0]), reverse=True)

        total_added = 0
        for _, (after_line, new_text) in ops_sorted:
            addition_lines = new_text.splitlines() if new_text != "" else [""]
            lines[after_line:after_line] = addition_lines
            total_added += len(addition_lines)

        new_full_text = "\n".join(lines) + ("\n" if lines else "")
        target.write_text(new_full_text, encoding="utf-8")

        min_line = min(op[0] for op in ops)
        max_line = max(op[0] for op in ops) + total_added
        preview_start = max(1, min_line - 1)
        preview_end = min(len(lines), max_line + 5)
        preview_body = _format_with_line_numbers(lines[preview_start - 1 : preview_end], start_line=preview_start)

        reports.append(
            f"Updated ~/{rel} ({len(ops)} insertion(s) applied, +{total_added} lines, now {len(lines)} lines).\n"
            f"Preview (lines {preview_start}-{preview_end}):\n{preview_body}"
        )

    return "\n\n".join(reports)


def _tool_delete_lines(args: dict[str, Any]) -> str:
    """
    指定した行番号範囲 (start_line 〜 end_line, 1始まり・両端含む) の行をファイルから完全削除する。
    同一ファイル内の複数箇所削除は降順（下から上）で適用し、行ズレを防ぐ。
    """
    raw_deletions = args.get("deletions") or args.get("replacements") or args.get("edits")
    if isinstance(raw_deletions, list) and raw_deletions:
        del_items = raw_deletions
    elif "path" in args and "start_line" in args:
        del_items = [args]
    else:
        return "Error: Provide 'deletions' list or ('path', 'start_line', 'end_line')."

    normalized: list[dict[str, Any]] = []
    for item in del_items:
        if isinstance(item, dict):
            normalized.append({
                "path": item.get("path", ""),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line", item.get("start_line")),
                "new_text": "",
            })
    return _tool_replace_lines({"replacements": normalized})


def _tool_delete_files(args: dict[str, Any]) -> str:
    """指定されたワークスペース内の不要ファイルまたはディレクトリを安全に一括削除する"""
    raw_paths = args.get("paths") or []
    if isinstance(raw_paths, str):
        raw_paths = [p.strip() for p in raw_paths.split(",") if p.strip()]
    if not raw_paths and "path" in args:
        raw_paths = [str(args["path"]).strip()]
    if not raw_paths:
        return "Error: Provide 'paths' (list of file/directory paths inside ~/ to delete)."

    ws = get_workspace_dir().resolve()
    protected = {(ws / name).resolve() for name in PROTECTED_DOC_NAMES}
    results: list[str] = []

    for rp in raw_paths:
        target = resolve_workspace_path(str(rp))
        if not target or target == ws:
            results.append(f"Skipped invalid or root path: '{rp}'")
            continue
        if target in protected:
            results.append(f"Skipped protected SSOT document: '~/{target.relative_to(ws)}'")
            continue
        if not target.exists():
            results.append(f"Not found (already deleted): '{rp}'")
            continue
        rel = target.relative_to(ws)
        if target.is_dir():
            shutil.rmtree(target)
            results.append(f"Deleted directory: ~/{rel}/")
        else:
            target.unlink()
            results.append(f"Deleted file: ~/{rel}")

    return "\n".join(results)