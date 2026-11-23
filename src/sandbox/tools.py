import asyncio
import json
import os
import re
import shutil
import urllib.request
from pathlib import Path
from typing import Any

from ddgs import DDGS

from core.config import get_io_chunk_bytes, get_max_ctx_chars_budget, get_workspace_dir

from .container import (
    IGNORE_DIRS,
    export_workspace_zip,
    resolve_workspace_path,
    run_in_sandbox,
)

INITIAL_DOCUMENTS: dict[str, str] = {
    "README.md": (
        "# Workspace Master Ledger & SSOT Registry (~/README.md)\n\n"
        "## 1. 単一真実源 (Single Source of Truth: SSOT) 絶対規約\n"
        "- 本ワークスペース (`~/`) における唯一の設計仕様書は `~/DESIGN.md`、唯一のタスク管理台帳は `~/TASK.md`、唯一の監査台帳は `~/REPORT.md` である。\n"
        "- サブディレクトリ内に別個の `DESIGN.md` 等を分散作成することを固く禁ずる。\n"
        "- プロジェクトのルートディレクトリは `~/projects/<project_name>/` に統一し、名称の揺れや重複フォルダの作成を一切禁ずる。\n\n"
        "## 2. 登録プロジェクト・ディレクトリ構成台帳\n"
        "- (現在登録されているプロジェクトはありません。新規起票時にPMまたはSEがここに正式パスを刻印すること)\n"
    ),
    "TASK.md": (
        "# Mission-Critical WBS & Extreme Quality Gate Queue (~/TASK.md)\n\n"
        "<!-- Status tags: [TODO] (SE設計待ち), [IMPL] (PG実装待ち), [QA] (QA極限検証待ち), [FIX] (品質未達・差戻し), [DONE] (完全合格) -->\n\n"
        "## 0. 全タスク共通・絶対品質ゲート (Extreme Definition of Done)\n"
        "以下の基準を1つでも満たさない成果物は、QAおよびAUDITORにより即座に `[FIX]` へ差し戻される：\n"
        "1. **完全無欠の実装 (Zero Placeholder Policy)**: `TODO`, `FIXME`, `// ...`, `pass`, ダミー定数返却、モックのままの放置は理由を問わず即時不合格。\n"
        "2. **C/C++ コンパイラ極限厳格化 (NASA JPL / MISRA / DO-178C Level A 準拠)**:\n"
        "   - コンパイルフラグ `-Wall -Wextra -Werror -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Wundef -Wstrict-prototypes -fno-common` を全て有効にし、警告ゼロ（0 warnings, 0 errors）でビルドを通すこと。\n"
        "   - 暗黙の型変換、未初期化変数、境界外アクセス、浮動小数点の `NaN`/`Inf` 伝播、動的メモリリークを完全に排除すること。\n"
        "3. **Python 型安全性・数値厳密性**:\n"
        "   - 全関数・メソッドに厳密な型ヒント (Type Annotations) と Docstring を記述すること。\n"
        "   - `python3 -c` によるワンライナー検証は全面禁止。必ず `test/` または `tests/` 配下に独立した自動検証スクリプトを構築すること。\n"
        "4. **決定論的検証・ファジング・ストレス試験**:\n"
        "   - 正常系だけでなく、境界値、異常入力、ビット反転・破損パケット注入（Fuzzing）、10,000ステップ以上の連続負荷試験を実施し、アサーション100%パスを実証すること。\n\n"
        "## 1. Active Task Queue\n"
        "- 現在未完了のタスクはありません。\n"
    ),
    "DESIGN.md": (
        "# Master Architecture & Formal Engineering Specification (~/DESIGN.md)\n\n"
        "## 1. システム設計原則 (Engineering Mandates)\n"
        "- **決定論的安全性 (Deterministic Safety)**: すべての状態遷移、制御ループ、通信プロトコルは数学的に定義された不変条件 (Invariants) と明確なタイムアウト・フェイルセーフ境界を持つこと。\n"
        "- **インターフェースの厳密性**: すべてのモジュール間API、バイナリ構造体（エンディアン・アライメント・パディング明示）、物理単位（SI単位系）、エラーコードを省略なく定義すること。\n"
        "- **自己検証アーキテクチャ**: すべての実装モジュールに対応する単体テスト・結合テスト・カオス/ファジング検証ハーネスの設計を同時に定義すること。\n\n"
        "## 2. 詳細設計仕様\n"
        "- (SE部門は `invoke_heavy_brain` を用いて、ここに妥協なき完全なアーキテクチャ設計・数理モデル・ディレクトリ構造・API仕様を記述すること)\n"
    ),
    "REPORT.md": (
        "# PMO Internal Audit & Formal Verification Report (~/REPORT.md)\n\n"
        "## 1. 常設監査チェックリスト (Audit Invariants)\n"
        "- **SSOT一貫性**: `~/README.md`, `~/TASK.md`, `~/DESIGN.md`, `Makefile` 間でプロジェクト名やファイルパスの不一致が1文字たりとも存在しないか。\n"
        "- **形骸化テストの摘発**: テストコードが単なる `print` や緩い条件になっていないか。エッジケース・異常系・境界値・破損データ注入を本当にテストしているか。\n"
        "- **ワンライナー禁止の遵守**: `python3 -c` 等のアドホックなコマンドで済ませず、再現可能なテストスイートおよび `Makefile` / CIターゲットとして恒久化されているか。\n\n"
        "## 2. 最新監査・品質改善フィードバック\n"
        "- ワークスペースは初期状態（クリーン）です。\n"
    ),
}


SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".mp3", ".mp4", ".wav",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".pdf", ".woff", ".woff2", ".ttf", ".eot",
    ".db", ".sqlite", ".sqlite3", ".pkl", ".pt", ".pth", ".onnx", ".safetensors", ".gguf", ".lock",
}


def _is_readable_text_file(path: Path, allowed_exts: set[str] | None = None) -> bool:
    """バイナリ・対象外拡張子のみを除外し、巨大テキストファイルもエラーにせず通す"""
    if not path.is_file():
        return False
    ext = path.suffix.lower()
    if allowed_exts is not None:
        return ext in allowed_exts
    if ext in SKIP_EXTENSIONS:
        return False
    try:
        with path.open("rb") as f:
            chunk = f.read(512)
            if b"\x00" in chunk:
                return False
    except OSError:
        return False
    return True


def _stream_read_lines(
    path: Path,
    start_line: int = 1,
    end_line: int | None = None,
    max_chars: int | None = None,
) -> tuple[list[str], int, bool]:
    """
    粒度可変のバッファサイズ (IO_CHUNK_BYTES) でファイルをストリーミング走査し、
    指定行範囲 (start_line〜end_line) かつ文字数予算内の行リスト・総行数・予算到達有無を返す。
    """
    buf_size = get_io_chunk_bytes()
    char_limit = max_chars or int(get_max_ctx_chars_budget() * 0.7)

    collected: list[str] = []
    total_lines = 0
    acc_chars = 0
    budget_reached = False

    with path.open("r", encoding="utf-8", errors="replace", buffering=buf_size) as f:
        for idx, raw_line in enumerate(f, start=1):
            total_lines = idx
            if idx < start_line:
                continue
            if end_line is not None and idx > end_line:
                continue
            if not budget_reached:
                line = raw_line.rstrip("\r\n")
                if acc_chars + len(line) + 12 > char_limit and collected:
                    budget_reached = True
                else:
                    collected.append(line)
                    acc_chars += len(line) + 12

    return collected, total_lines, budget_reached


def _format_with_line_numbers(lines: list[str], start_line: int = 1) -> str:
    """各行の左側に1始まりの行番号 ('   1 | ...') を付与する"""
    if not lines:
        return "(Empty file)"
    width = max(4, len(str(start_line + len(lines) - 1)))
    return "\n".join(f"{idx:>{width}} | {line}" for idx, line in enumerate(lines, start=start_line))


def init_workspace_files(reset_on_restart: bool = False) -> None:
    """
    起動時に ~/ 内の社内標準ドキュメント4点を整備する。
    reset_on_restart=True の場合は ~/ 配下の既存プロジェクトや散乱ファイルをすべて完全消去し、
    4つの標準 .md のみが存在する純粋な初期状態に戻す。
    """
    ws = get_workspace_dir().resolve()
    ws.mkdir(parents=True, exist_ok=True)

    if reset_on_restart:
        for item in ws.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)

    for name, template in INITIAL_DOCUMENTS.items():
        path = ws / name
        if reset_on_restart or not path.exists():
            path.write_text(template, encoding="utf-8")



def read_workspace_doc(name: str) -> str:
    path = resolve_workspace_path(name)
    if path and path.exists() and path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


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
        return f"Error: '{raw_path}' is a directory. Use 'read_files' or 'list_files' instead."

    start_line = max(1, int(args.get("start_line", 1)))
    max_lines = int(args.get("max_lines", 2000))
    req_end_line = int(args.get("end_line", start_line + max_lines - 1))
    char_budget = int(get_max_ctx_chars_budget() * 0.7)

    sliced, total, budget_hit = _stream_read_lines(
        target, start_line=start_line, end_line=req_end_line, max_chars=char_budget
    )
    rel = target.relative_to(get_workspace_dir().resolve())
    if total == 0:
        return f"=== FILE: ~/{rel} (0 lines) ===\n(Empty file)"

    actual_end = start_line + len(sliced) - 1 if sliced else start_line
    header = f"=== FILE: ~/{rel} (lines {start_line}-{actual_end} of {total}) ==="
    body = _format_with_line_numbers(sliced, start_line=start_line)
    if actual_end < total:
        reason = "256k context safety budget reached" if budget_hit else f"{total - actual_end} more lines"
        body += f"\n...({reason}; call read_file with start_line={actual_end + 1} to read further)..."
    return f"{header}\n{body}"


def _tool_write_file(args: dict[str, Any]) -> str:
    raw_path = str(args.get("path", "")).strip()
    content = str(args.get("content", ""))
    target = resolve_workspace_path(raw_path)
    if not target or target.is_dir():
        return f"Error: Invalid file path '{raw_path}'."

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    rel = target.relative_to(get_workspace_dir().resolve())
    line_cnt = len(content.splitlines())
    return f"Successfully wrote {len(content)} chars ({line_cnt} lines) to ~/{rel}."


def _tool_write_files(args: dict[str, Any]) -> str:
    """複数ファイルを一括で新規作成・上書きする"""
    files_list = args.get("files") or []
    if not isinstance(files_list, list) or not files_list:
        return "Error: 'files' must be a non-empty list of {'path': ..., 'content': ...}."

    results: list[str] = []
    for item in files_list:
        if not isinstance(item, dict):
            continue
        results.append(_tool_write_file(item))
    return "\n".join(results) if results else "Error: No valid file entries provided."


def _tool_edit_lines(args: dict[str, Any]) -> str:
    """
    行番号範囲 (start_line 〜 end_line, 1始まり・両端含む) を指定して改行付き文字列に一括置換する。
    同一ファイル内の複数箇所編集は行番号の降順（下から上）で自動適用し、行ズレを防止する。
    """
    raw_edits = args.get("edits")
    if isinstance(raw_edits, list) and raw_edits:
        edit_items = raw_edits
    elif "path" in args and "start_line" in args:
        edit_items = [args]
    else:
        return "Error: Provide 'edits' list or ('path', 'start_line', 'end_line', 'new_text')."

    ws = get_workspace_dir().resolve()
    grouped: dict[Path, list[tuple[int, int, str]]] = {}
    errors: list[str] = []

    for idx, item in enumerate(edit_items, 1):
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path", "")).strip()
        target = resolve_workspace_path(raw_path)
        if not target or not target.exists() or target.is_dir():
            errors.append(f"Edit #{idx}: File '{raw_path}' not found.")
            continue
        try:
            s_line = int(item["start_line"])
            e_line = int(item.get("end_line", s_line))
        except (KeyError, ValueError, TypeError):
            errors.append(f"Edit #{idx} ({raw_path}): 'start_line'/'end_line' must be integers.")
            continue

        if s_line < 1 or e_line < s_line:
            errors.append(f"Edit #{idx} ({raw_path}): Invalid range {s_line}-{e_line} (must be 1 <= start_line <= end_line).")
            continue

        new_text = str(item.get("new_text", item.get("new_content", "")))
        grouped.setdefault(target, []).append((s_line, e_line, new_text))

    if errors:
        return "Error(s) in edit_lines:\n" + "\n".join(errors)

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
            f"Updated ~/{rel} ({len(ops_sorted)} edit(s) applied, now {len(lines)} lines). "
            f"Preview (lines {preview_start}-{preview_end}):\n{preview_body}"
        )

    return "\n\n".join(reports)


def _tool_replace_in_file(args: dict[str, Any]) -> str:
    raw_path = str(args.get("path", "")).strip()
    old_text = str(args.get("old_text", ""))
    new_text = str(args.get("new_text", ""))
    target = resolve_workspace_path(raw_path)
    if not target or not target.exists() or target.is_dir():
        return f"Error: File '{raw_path}' not found."

    text = target.read_text(encoding="utf-8")
    if old_text not in text:
        return f"Error: 'old_text' not found in {raw_path}. Use 'read_file' and 'edit_lines' with line numbers instead."
    target.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
    rel = target.relative_to(get_workspace_dir().resolve())
    return f"Successfully updated ~/{rel}."


def _tool_search_files(args: dict[str, Any]) -> str:
    """IGNORE_DIRS・バイナリを除外し、ストリーミング走査でキーワード／正規表現を横断検索する"""
    query = str(args.get("query", "")).strip()
    if not query:
        return "Error: Empty 'query'."

    raw_dir = str(args.get("directory", "~/"))
    target_dir = resolve_workspace_path(raw_dir)
    if not target_dir or not target_dir.exists():
        return f"Error: Directory '{raw_dir}' does not exist."

    raw_exts = args.get("extensions")
    allowed_exts: set[str] | None = None
    if raw_exts:
        if isinstance(raw_exts, str):
            raw_exts = [e.strip() for e in raw_exts.split(",") if e.strip()]
        allowed_exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in raw_exts}

    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    ws = get_workspace_dir().resolve()
    buf_size = get_io_chunk_bytes()
    matches: list[str] = []
    match_count = 0

    for root, dirs, files in os.walk(target_dir):
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS and not d.startswith("."))
        for fname in sorted(files):
            fpath = Path(root) / fname
            if not _is_readable_text_file(fpath, allowed_exts):
                continue
            hit_lines: list[str] = []
            try:
                with fpath.open("r", encoding="utf-8", errors="replace", buffering=buf_size) as f:
                    for idx, raw_line in enumerate(f, 1):
                        line = raw_line.rstrip("\r\n")
                        if pattern.search(line):
                            hit_lines.append(f"{idx:>4} | {line}")
                            match_count += 1
                            if len(hit_lines) >= 30:
                                hit_lines.append("  ...(more matches in this file)...")
                                break
            except OSError:
                continue
            if hit_lines:
                rel = fpath.relative_to(ws)
                matches.append(f"=== ~/{rel} ===\n" + "\n".join(hit_lines))
            if match_count >= 200:
                break
        if match_count >= 200:
            matches.append("...(search limit of 200 matches reached)...")
            break

    return "\n\n".join(matches) if matches else f"No matches found for '{query}'."


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
        text = " ".join(re.sub(r"<[^>]+>", " ", text).split())
        return text[:4000]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return f"Fetch error: {exc}"


def _tool_export_zip(args: dict[str, Any]) -> str:
    sub = str(args.get("directory", "~/"))
    zip_path = export_workspace_zip(sub)
    return f"ZIP_CREATED:{zip_path}"


def _collect_workspace_files(
    raw_paths: list[str],
    raw_dir: str | None = None,
    allowed_exts: set[str] | None = None,
    skip_files: set[Path] | None = None,
) -> list[Path]:
    """指定されたパスリスト・ディレクトリから読み取り可能なテキストファイルを重複なく再帰収集する"""
    collected: list[Path] = []
    seen: set[Path] = set(skip_files or ())

    def _add(p: Path) -> None:
        resolved = p.resolve()
        if resolved not in seen and _is_readable_text_file(resolved, allowed_exts):
            seen.add(resolved)
            collected.append(resolved)

    def _walk(d: Path) -> None:
        for root, dirs, files in os.walk(d):
            dirs[:] = sorted(x for x in dirs if x not in IGNORE_DIRS and not x.startswith("."))
            for f in sorted(files):
                _add(Path(root) / f)

    targets = [*raw_paths, *([raw_dir] if raw_dir else [])]
    for rp in targets:
        target = resolve_workspace_path(str(rp))
        if not target or not target.exists():
            continue
        if target.is_dir():
            _walk(target)
        else:
            _add(target)

    return collected


def _tool_read_files(args: dict[str, Any]) -> str:
    """複数ファイルまたはディレクトリ配下を粒度可変ストリーミング＋行番号付きで一括閲覧する"""
    ws = get_workspace_dir().resolve()
    raw_paths = args.get("paths") or []
    if isinstance(raw_paths, str):
        raw_paths = [p.strip() for p in raw_paths.split(",") if p.strip()]

    raw_dir = args.get("directory") or ("~/" if not raw_paths else None)
    raw_exts = args.get("extensions")
    allowed_exts: set[str] | None = None
    if raw_exts:
        if isinstance(raw_exts, str):
            raw_exts = [e.strip() for e in raw_exts.split(",") if e.strip()]
        allowed_exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in raw_exts}

    collected = _collect_workspace_files(raw_paths, raw_dir, allowed_exts)
    if not collected:
        return "No readable text files matched the specified paths/directory."

    max_lines_per_file = int(args.get("max_lines_per_file", 1000))
    batch_char_budget = int(get_max_ctx_chars_budget() * 0.7)
    sections: list[str] = []
    total_chars = 0
    omitted_files: list[str] = []

    for idx, fpath in enumerate(collected):
        remaining_budget = max(4000, batch_char_budget - total_chars)
        if total_chars >= batch_char_budget and sections:
            omitted_files = [f"~/{p.relative_to(ws)}" for p in collected[idx:]]
            break

        rel = fpath.relative_to(ws)
        shown_lines, total_lines, budget_hit = _stream_read_lines(
            fpath, start_line=1, end_line=max_lines_per_file, max_chars=remaining_budget
        )
        body = _format_with_line_numbers(shown_lines, start_line=1)
        if len(shown_lines) < total_lines:
            body += (
                f"\n...(truncated after {len(shown_lines)} of {total_lines} lines; "
                f"use read_file with start_line={len(shown_lines) + 1} if needed)..."
            )

        block = f"=== FILE: ~/{rel} (total {total_lines} lines) ===\n{body}"
        sections.append(block)
        total_chars += len(block)

        if budget_hit and idx + 1 < len(collected):
            omitted_files = [f"~/{p.relative_to(ws)}" for p in collected[idx + 1 :]]
            break

    if omitted_files:
        sections.append(
            f"=== OMITTED FILES (reached context safety budget of {batch_char_budget:,} chars) ===\n"
            + "\n".join(f"- {p}" for p in omitted_files)
        )

    return "\n\n".join(sections)


def _tool_add_lines_after(args: dict[str, Any]) -> str:
    """
    指定した行番号 (after_line, 0=ファイル先頭, 1〜総行数=該当行の直後) の後ろに新しい行を挿入する。
    同一ファイル内の複数箇所への挿入は行番号の降順（下から上）で自動適用し、行ズレを防止する。
    """
    raw_insertions = args.get("insertions") or args.get("edits")
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

        # 同じ after_line に複数指定された場合は指定順を維持しつつ、異なる行番号は降順（下から上）で挿入
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
            f"Updated ~/{rel} ({len(ops)} insertion(s) applied, +{total_added} lines, now {len(lines)} lines). "
            f"Preview (lines {preview_start}-{preview_end}):\n{preview_body}"
        )

    return "\n\n".join(reports)


TOOL_DISPATCH = {
    "list_files": _tool_list_files,
    "read_file": _tool_read_file,
    "read_files": _tool_read_files,
    "write_file": _tool_write_file,
    "write_files": _tool_write_files,
    "edit_lines": _tool_edit_lines,
    "add_lines_after": _tool_add_lines_after,
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

    if name in ("write_file", "write_files", "edit_lines", "add_lines_after", "replace_in_file"):
        async with _file_write_lock:
            return await asyncio.to_thread(fn, args)

    return await asyncio.to_thread(fn, args)


def _find_duplicate_workspace_files(ws: Path) -> dict[str, list[str]]:
    """ワークスペース内で同一ファイル名が複数パスに存在する重複ファイル群を検出する"""
    by_name: dict[str, list[str]] = {}
    for fpath in _collect_workspace_files(["~/"]):
        rel = f"~/{fpath.relative_to(ws)}"
        by_name.setdefault(fpath.name, []).append(rel)
    return {name: paths for name, paths in by_name.items() if len(paths) > 1}


def build_heavy_context_bundle(context_paths: list[str] | None = None) -> str:
    """
    Stage 2 用に、参照制限を一切設けずワークスペース (~/) 全体のディレクトリツリー、
    同名重複ファイルの警告、および全テキストファイルの全文を一括バンドルする。
    """
    ws = get_workspace_dir().resolve()
    standard_root_docs = {(ws / name).resolve() for name in INITIAL_DOCUMENTS}
    extra_paths = list(context_paths or [])
    all_search_paths = list(dict.fromkeys(["~/", *extra_paths]))

    tree_listing = _tool_list_files({"directory": "~/"})
    duplicates = _find_duplicate_workspace_files(ws)
    dup_lines: list[str] = []
    if duplicates:
        dup_lines.append("⚠️ [CRITICAL WARNING: DUPLICATE FILENAMES DETECTED IN WORKSPACE]")
        for fname, paths in sorted(duplicates.items()):
            dup_lines.append(f"  - '{fname}' exists in multiple locations: {', '.join(paths)}")

    collected = _collect_workspace_files(all_search_paths, skip_files=standard_root_docs)
    sections: list[str] = [f"=== WORKSPACE FULL DIRECTORY TREE ===\n{tree_listing}"]
    if dup_lines:
        sections.append("\n".join(dup_lines))

    if not collected:
        sections.append("(ワークスペース内に追加のソースファイルはまだ存在しません)")
        return "\n\n".join(sections)

    for fpath in collected:
        rel = fpath.relative_to(ws)
        text = fpath.read_text(encoding="utf-8", errors="replace")
        line_cnt = len(text.splitlines())
        sections.append(f"=== FILE: ~/{rel} ({line_cnt} lines, {len(text)} chars) ===\n{text}")

    return "\n\n".join(sections)


HEAVY_FILE_HEADER_PATTERN = re.compile(
    r"(?:^|\n)[ \t]*(?:#+|\*\*|-)?[ \t]*FILE[ \t]*:[ \t]*([^\n]+)",
)


def auto_apply_heavy_markdown(
    markdown_text: str,
    default_targets: list[str] | None = None,
) -> list[str]:
    """
    Stage 3 (Auto-Apply): 重量モデルが出力した Markdown から
    '### FILE: ~/path/to/file' セクションを分割して自動保存する。
    コードブロックの未クローズ（途中切断）および同名ファイルの重複存在を自動検知して警告を付与する。
    """
    ws = get_workspace_dir().resolve()
    applied_reports: list[str] = []

    def _extract_body(section_text: str) -> tuple[str, bool]:
        stripped = section_text.strip()
        first_fence = re.match(r"^```[^\n]*\n", stripped)
        if first_fence:
            content_start = first_fence.end()
            last_fence_pos = stripped.rfind("```")
            if last_fence_pos > first_fence.start():
                body = stripped[content_start:last_fence_pos]
                is_truncated = False
            else:
                body = stripped[content_start:]
                is_truncated = True
            return body, is_truncated
        return stripped + "\n", False

    def _save_to_path(raw_path: str, body: str, is_truncated: bool) -> None:
        target = resolve_workspace_path(raw_path)
        if not target or target.is_dir():
            applied_reports.append(f"[ERROR] Invalid target path skipped: {raw_path}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        rel = target.relative_to(ws)
        line_cnt = len(body.splitlines())
        report = f"~/{rel} ({line_cnt} lines, {len(body)} chars)"
        if is_truncated:
            report += " [WARNING: TRUNCATED_OUTPUT - 出力が途中で切れています。invoke_heavy_brainを再実行せず edit_lines で末尾を補完してください]"
        applied_reports.append(report)

    matches = list(HEAVY_FILE_HEADER_PATTERN.finditer(markdown_text))
    if matches:
        for idx, match in enumerate(matches):
            raw_path = match.group(1).strip().strip("`'\"* ")
            section_start = match.end()
            section_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_text)
            section = markdown_text[section_start:section_end]
            code_body, is_truncated = _extract_body(section)
            if code_body.strip():
                _save_to_path(raw_path, code_body, is_truncated)
    elif default_targets and len(default_targets) == 1 and markdown_text.strip():
        raw_path = str(default_targets[0]).strip().strip("`'\"* ")
        code_body, is_truncated = _extract_body(markdown_text)
        if code_body.strip():
            _save_to_path(raw_path, code_body, is_truncated)

    duplicates = _find_duplicate_workspace_files(ws)
    for fname, paths in sorted(duplicates.items()):
        applied_reports.append(
            f"[WARNING: DUPLICATE_FILENAME] '{fname}' が複数箇所に存在します ({', '.join(paths)})。"
            "C/C++の #include 優先順位バグの原因となるため、不要な旧ファイルを run_command('rm ...') で削除してください。"
        )

    return applied_reports