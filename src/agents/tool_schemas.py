from typing import Any

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_files": {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories inside ~/ (e.g. '~/', '~/a'). Ignores build/.git/node_modules.",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string", "description": "Target directory path starting with ~/"}},
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a single file inside ~/ with 1-based line numbers on the left ('   1 | ...'). Reads full file unless start_line/end_line are specified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path starting with ~/"},
                    "start_line": {"type": "integer", "description": "1-based start line (default 1)"},
                    "end_line": {"type": "integer", "description": "1-based end line (inclusive, optional)"},
                },
                "required": ["path"],
            },
        },
    },
    "read_files": {
        "type": "function",
        "function": {
            "name": "read_files",
            "description": (
                "Batch-read multiple files at once with '=== FILE: ... ===' headers and 1-based line numbers on the left. "
                "Automatically skips build/.git/node_modules/binaries. Use this to inspect an entire project or multiple files in 1 step."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file or directory paths to read (e.g. ['~/a/main.py', '~/a/utils.py']).",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Directory to recursively read all text files from (e.g. '~/a').",
                    },
                    "extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional file extensions filter (e.g. ['.py', '.md']).",
                    },
                },
            },
        },
    },
    "get_file_outline": {
        "type": "function",
        "function": {
            "name": "get_file_outline",
            "description": (
                "Extract structural outline (namespaces, classes, structs, functions, methods, and main blocks) "
                "with 1-based line numbers and brace balance check for one or multiple source files (C/C++/Python/Rust/JS/TS)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Single file path (e.g. '~/projects/app/test_all.cpp')"},
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of file paths to inspect at once",
                    },
                },
            },
        },
    },
    "search_files": {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a keyword or regex across all text files in ~/ (with 1-based line numbers).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keyword or regex pattern"},
                    "directory": {"type": "string", "description": "Target directory (default '~/')"},
                    "extensions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["query"],
            },
        },
    },
    "write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a single file inside ~/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path starting with ~/"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "write_files": {
        "type": "function",
        "function": {
            "name": "write_files",
            "description": "Batch-create or overwrite multiple files in a single tool call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["path", "content"],
                        },
                    },
                },
                "required": ["files"],
            },
        },
    },
    "edit_lines": {
        "type": "function",
        "function": {
            "name": "edit_lines",
            "description": (
                "Replace lines from 'start_line' to 'end_line' (1-based, inclusive, as shown by read_file/read_files) "
                "with 'new_text'. Supports batch editing multiple ranges across one or multiple files in a single call. "
                "Multiple edits in the same file are automatically applied bottom-up to prevent line number shifts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "description": "List of line-range edits to apply in batch.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Target file path (e.g. '~/a/main.py')"},
                                "start_line": {"type": "integer", "description": "1-based start line number (inclusive)"},
                                "end_line": {"type": "integer", "description": "1-based end line number (inclusive)"},
                                "new_text": {"type": "string", "description": "Multi-line replacement string (use '' to delete lines)"},
                            },
                            "required": ["path", "start_line", "end_line", "new_text"],
                        },
                    },
                },
                "required": ["edits"],
            },
        },
    },
    "add_lines_after": {
        "type": "function",
        "function": {
            "name": "add_lines_after",
            "description": (
                "Insert new line(s) immediately after 'after_line' (0 = insert at top of file before line 1; "
                "1..N = insert after that 1-based line number) without deleting existing lines. "
                "Supports batch inserting across one or multiple files in a single call (applied bottom-up automatically)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "insertions": {
                        "type": "array",
                        "description": "List of line insertions to apply in batch.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Target file path (e.g. '~/a/main.py')"},
                                "after_line": {
                                    "type": "integer",
                                    "description": "Line number after which to insert (0 for top of file, or 1-based line number)",
                                },
                                "new_text": {"type": "string", "description": "Single-line or multi-line string to insert"},
                            },
                            "required": ["path", "after_line", "new_text"],
                        },
                    },
                },
                "required": ["insertions"],
            },
        },
    },
    "delete_lines": {
        "type": "function",
        "function": {
            "name": "delete_lines",
            "description": (
                "Delete lines from 'start_line' to 'end_line' (1-based, inclusive) from a file. "
                "Supports batch deleting multiple ranges across one or multiple files in a single call "
                "(automatically applied bottom-up to prevent line number shifts)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "deletions": {
                        "type": "array",
                        "description": "List of line-range deletions to apply in batch.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Target file path (e.g. '~/a/main.py')"},
                                "start_line": {"type": "integer", "description": "1-based start line number to delete (inclusive)"},
                                "end_line": {"type": "integer", "description": "1-based end line number to delete (inclusive)"},
                            },
                            "required": ["path", "start_line", "end_line"],
                        },
                    },
                },
                "required": ["deletions"],
            },
        },
    },
    "delete_files": {
        "type": "function",
        "function": {
            "name": "delete_files",
            "description": "Safely delete one or multiple temporary/duplicate files or directories inside ~/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file or directory paths inside ~/ to delete (e.g. ['~/test_debug.cpp', '~/a.bak']).",
                    },
                },
                "required": ["paths"],
            },
        },
    },
    "replace_in_file": {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace an exact snippet in an existing file inside ~/. Prefer 'edit_lines' for faster and safer partial edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    "run_command": {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command inside the Docker sandbox (working directory is ~/ i.e. /root).",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for technical documentation or error solutions.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    "fetch_url": {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch text content from a URL.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    "export_zip": {
        "type": "function",
        "function": {
            "name": "export_zip",
            "description": "Package ~/ or a subdirectory into a ZIP archive in the exports folder.",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string", "description": "Directory to zip, default '~/'"}},
            },
        },
    },
    "complete_phase": {
        "type": "function",
        "function": {
            "name": "complete_phase",
            "description": "Conclude your current role's phase and hand over to the next department.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string", "description": "Concise Japanese summary of work done."}},
                "required": ["summary"],
            },
        },
    },
    "invoke_professional": {
        "type": "function",
        "function": {
            "name": "invoke_professional",
            "description": (
                "【子タスク外注ツール】大規模な設計・新規実装や、難解なバグ・コンパイルエラー・テスト失敗に直面した際、"
                "明確な子タスク (subtask_title) と達成条件 (instruction) を定義してプロフェッショナル (Qwen3-Coder-Next) に作業を外注します。"
                "呼び出された Professional はあなたと同じ全ツール（ファイル読み書き・行編集・コマンド実行）を駆使し、"
                "最大512ステップまで自律的に実装・ビルド・テスト検証を行って子タスクを完遂してから結果を返します。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subtask_title": {
                        "type": "string",
                        "description": "外注する子タスクの明確なタイトル (例: 'SafetyManagerとBlackboxの実装および単体テスト通過')",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "Professionalに対する具体的な仕様・修正対象・達成すべき完了条件（ビルド・テスト通過条件など）",
                    },
                    "target_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "（任意）主に関与・作成・修正してほしい対象ファイルパスのリスト",
                    },
                },
                "required": ["subtask_title", "instruction"],
            },
        },
    },
}


ROLE_ALLOWED_TOOLS: dict[str, list[str]] = {
    "PM": [
        "list_files", "read_file", "read_files", "get_file_outline", "search_files",
        "write_file", "write_files", "edit_lines", "add_lines_after", "delete_lines", "delete_files", "replace_in_file",
        "export_zip", "complete_phase",
    ],
    "SE": [
        "list_files", "read_file", "read_files", "get_file_outline", "search_files",
        "write_file", "write_files", "edit_lines", "add_lines_after", "delete_lines", "delete_files", "replace_in_file",
        "invoke_professional", "run_command", "web_search", "fetch_url", "complete_phase",
    ],
    "PG": [
        "list_files", "read_file", "read_files", "get_file_outline", "search_files",
        "write_file", "write_files", "edit_lines", "add_lines_after", "delete_lines", "delete_files", "replace_in_file",
        "invoke_professional", "run_command", "web_search", "fetch_url", "complete_phase",
    ],
    "QA": [
        "list_files", "read_file", "read_files", "get_file_outline", "search_files",
        "write_file", "write_files", "edit_lines", "add_lines_after", "delete_lines", "delete_files", "replace_in_file",
        "run_command", "complete_phase",
    ],
    "AUDITOR": [
        "read_file", "read_files", "get_file_outline",
        "write_file", "edit_lines", "add_lines_after", "delete_lines", "complete_phase",
    ],
}


PROFESSIONAL_ALLOWED_TOOLS: list[str] = [
    "list_files", "read_file", "read_files", "get_file_outline", "search_files",
    "write_file", "write_files", "edit_lines", "add_lines_after", "delete_lines", "delete_files", "replace_in_file",
    "run_command", "web_search", "fetch_url", "complete_phase",
]


def get_tools_for_role(tool_names: list[str]) -> list[dict[str, Any]]:
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]