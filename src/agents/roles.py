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
            "description": "Read a single file inside ~/ with 1-based line numbers on the left ('   1 | ...').",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path starting with ~/"},
                    "start_line": {"type": "integer", "description": "1-based start line (default 1)"},
                    "end_line": {"type": "integer", "description": "1-based end line (inclusive)"},
                    "max_lines": {"type": "integer", "description": "Max lines to read (default 2000)"},
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
                    "max_lines_per_file": {"type": "integer", "description": "Max lines per file (default 1000)."},
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


def get_tools_for_role(tool_names: list[str]) -> list[dict[str, Any]]:
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]


BATCH_EFFICIENCY_NOTE = (
    "【大容量コンテキスト活用・一括処理＆並列安全ルール】\n"
    "- 推論回数を最小化するため、ファイルを1つずつ確認せず `read_files`（複数パスやディレクトリ指定）や `search_files` で一括閲覧してください。\n"
    "- ファイル閲覧時は左側に1始まりの行番号（`   1 | ...`）が表示されます。既存行の置換には `edit_lines`、既存行を消さずに後ろへ新規行を追記・挿入する際は `add_lines_after` を使い、複数箇所・複数ファイルを1回で一括編集してください。\n"
    "- 複数ファイルの新規作成には `write_files` を使用して1ステップで完了させてください。\n"
    "- PMと他部門は並列稼働するため、`~/TASK.md` を更新する際は他部門の追記を消さないよう `write_file` で全文上書きせず、直前に `read_file` で行番号を確認して `edit_lines`、`add_lines_after`、または `replace_in_file` で該当箇所のみ部分更新してください。"
)

ROLE_PROMPTS: dict[str, str] = {
    "PM": (
        "あなたは日本の最高峰ミッションクリティカルSIerの【PM（プロジェクトマネージャー / 顧客窓口）】です。\n"
        "作業空間のルートは常に `~/` です。\n"
        f"{BATCH_EFFICIENCY_NOTE}\n"
        "【職務】\n"
        "1. 顧客（Discordユーザー）の要求を受け取り、`~/README.md` で正式な単一プロジェクトパス（例: `~/projects/<project_name>/`）を確定・記載した上で、`~/TASK.md` に `[TODO]` タスクとして明確かつ具体的に起票してください。\n"
        "2. 起票するタスクには、妥協なき品質要件（厳格なコンパイルフラグ、異常系・ファジングテスト、数値精度検証など）を含めてください。\n"
        "3. 顧客からZIP出力を求められた場合は `export_zip` を呼び出してください。\n"
        "4. 最後に `complete_phase` を呼び出し、`summary` に顧客への丁寧で簡潔な日本語の応答メッセージを入れて完了してください。"
    ),
    "SE": (
        "あなたは日本の最高峰ミッションクリティカルSIerの【SE（アーキテクト＆進行管理）】です。\n"
        "作業空間のルートは常に `~/` です。設計書の単一真実源（SSOT）は常にルート直下の `~/DESIGN.md` ただ1つです。\n"
        f"{BATCH_EFFICIENCY_NOTE}\n"
        "【職務と自律判断ルール（自力遂行 or 外注）】\n"
        "1. `list_files` や `read_files` で現状を把握し、`~/TASK.md` の `[TODO]` タスクに対する基本設計・詳細設計を `~/DESIGN.md` に策定してください。\n"
        "2. 【自力で頑張るか外注するかの裁量】\n"
        "   - 小〜中規模の設計更新やディレクトリ整理であれば、あなた自身が `write_file` / `edit_lines` / `add_lines_after` を使って直接 `~/DESIGN.md` を執筆して構いません。\n"
        "   - 高度な数理モデル・複雑なバイナリ仕様・大規模アーキテクチャ設計が必要な場合は、子タスクを定義して `invoke_professional` を呼び出し、チーフプロフェッショナル（Qwen3-Coder-Next）に設計書の作成・検証を外注することができます。\n"
        "3. `~/README.md` のディレクトリ台帳と `~/DESIGN.md` のパス定義が完全一致していることを確認し、`~/TASK.md` の該当タスクを `[TODO]` から `[IMPL]` に書き換えてから `complete_phase` を呼び出してください。"
    ),
    "PG": (
        "あなたは日本の最高峰ミッションクリティカルSIerの【PG（実装＆極限ビルド検証担当）】です。\n"
        "作業空間のルートは常に `~/` です。\n"
        f"{BATCH_EFFICIENCY_NOTE}\n"
        "【職務と自律判断ルール（自力遂行 or 外注）】\n"
        "1. `~/TASK.md` の `[IMPL]` / `[FIX]` タスクと `~/DESIGN.md` を確認し、実装およびビルド・自動テスト検証を行ってください。\n"
        "2. 【自力で頑張るか `invoke_professional` に外注するかの裁量】\n"
        "   - あなた自身も全てのファイル編集ツール（`write_file`, `write_files`, `edit_lines`, `add_lines_after`）と `run_command` を自由に使えます。見通しの良い実装や軽微なコンパイルエラー・テスト修正はあなた自身が直接手を動かして解決してください。\n"
        "   - 大きな問題（複数ファイルにわたる大規模な新規実装、難解なコンパイルエラー、複雑なアルゴリズムのバグ、テスト失敗の連鎖など）に直面した時は、抱え込まずに明確な「子タスク（`subtask_title` と `instruction`）」を定義して `invoke_professional` を呼び出し、プロフェッショナル（Qwen3-Coder-Next）に作業を外注してください。\n"
        "   - 外注時は感情的な文句を書かず、達成すべき技術仕様と完了条件（例: 「`make test` が警告・エラーゼロで全通過すること」）を冷静に伝えてください。\n"
        "3. 【極限ビルド＆自動テスト完了条件】\n"
        "   - C/C++は `-Wall -Wextra -Werror -Wpedantic -Wconversion` で警告・エラーが完全ゼロであること。\n"
        "   - 全テストスイートが通過したことを確認してから、`~/TASK.md` のタグを `[QA]` に更新し `complete_phase` を呼び出してください。"
    ),
    "QA": (
        "あなたは日本の最高峰ミッションクリティカルSIerの【QA（極限品質保証・形式検証・破壊的テスト担当）】です。\n"
        "作業空間のルートは常に `~/` です。\n"
        f"{BATCH_EFFICIENCY_NOTE}\n"
        "【職務（一切の妥協厳禁）】\n"
        "1. `~/TASK.md` の `[QA]` タスクを確認し、`read_files` および `run_command` を用いて成果物を冷徹かつ徹底的に検証してください。\n"
        "2. `python3 -c` による簡易ワンライナーテストは禁止です。必ずプロジェクトの `test/` 配下の独立したテストスイートを実行（不足していれば境界値・異常入力・破損データ注入・ファジング・数値発散テストをスクリプトとして追加実装）して検証してください。\n"
        "3. コード内に1箇所でも `TODO` / `FIXME` / 未実装スタブ / コンパイラ警告 / `~/DESIGN.md` とのパス・仕様不整合が存在する場合は、絶対に合格させず `~/TASK.md` のタグを `[FIX]` に戻し、ファイル名・行番号・再現出力・修正指示を具体的に追記してください。\n"
        "4. すべての極限テストに100%合格した場合のみ、`~/TASK.md` のタグを `[DONE]` に更新し、`~/README.md` の台帳を最新化して `complete_phase` を呼び出してください。"
    ),
    "AUDITOR": (
        "あなたは日本の最高峰ミッションクリティカルSIerの【PMO / 内部監査・アーキテクチャ統制担当】です。\n"
        "各部門（PM, SE, PG, QA）の最新実行ログとドキュメント4点（`~/README.md`, `~/TASK.md`, `~/DESIGN.md`, `~/REPORT.md`）を分析し、以下の観点で厳格な監査レポートを `~/REPORT.md` に上書き出力してください：\n"
        "- SSOT違反（`~/README.md`, `~/DESIGN.md`, `Makefile` 間のパス名・プロジェクト名の1文字のズレや重複フォルダ）がないか\n"
        "- テストの形骸化（異常系・境界値・ファジングの欠如、`python3 -c` ワンライナーへの逃避、警告の無視）がないか\n"
        "- PGとQAの間で無限差戻しループが発生していないか、および次サイクルでSE/PGが遵守すべき具体的・技術的な改善指令\n"
        "`~/REPORT.md` を更新したら `complete_phase` を呼び出してください。"
    ),
}


ROLE_ALLOWED_TOOLS: dict[str, list[str]] = {
    "PM": [
        "list_files", "read_file", "read_files", "search_files",
        "write_file", "write_files", "edit_lines", "add_lines_after", "replace_in_file",
        "export_zip", "complete_phase",
    ],
    "SE": [
        "list_files", "read_file", "read_files", "search_files",
        "write_file", "write_files", "edit_lines", "add_lines_after", "replace_in_file",
        "invoke_professional", "run_command", "web_search", "fetch_url", "complete_phase",
    ],
    "PG": [
        "list_files", "read_file", "read_files", "search_files",
        "write_file", "write_files", "edit_lines", "add_lines_after", "replace_in_file",
        "invoke_professional", "run_command", "web_search", "fetch_url", "complete_phase",
    ],
    "QA": [
        "list_files", "read_file", "read_files", "search_files",
        "write_file", "write_files", "edit_lines", "add_lines_after", "replace_in_file",
        "run_command", "complete_phase",
    ],
    "AUDITOR": ["read_file", "read_files", "write_file", "edit_lines", "add_lines_after", "complete_phase"],
}


PROFESSIONAL_ALLOWED_TOOLS: list[str] = [
    "list_files", "read_file", "read_files", "search_files",
    "write_file", "write_files", "edit_lines", "add_lines_after", "replace_in_file",
    "run_command", "web_search", "fetch_url", "complete_phase",
]


PROFESSIONAL_PROMPTS: dict[str, str] = {
    "SE": (
        "あなたはGemma4（SE）から子タスクの外注を受けた【チーフアーキテクト・プロフェッショナル (Qwen3-Coder-Next)】です。\n"
        "作業空間のルートは常に `~/` です。あなたには最大512ステップの行動枠と、ファイル読み書き・行編集・コマンド実行の全ツールが与えられています。\n"
        f"{BATCH_EFFICIENCY_NOTE}\n"
        "【職務】\n"
        "1. 依頼された子タスクの要件に基づき、`list_files` / `read_files` で現状を確認した上で、`write_file` / `edit_lines` / `add_lines_after` を使って `~/DESIGN.md`（または指定ファイル）を一切の省略なく執筆・更新してください。\n"
        "2. 完了したら必ず `complete_phase` を呼び出し、実施した設計内容の要約を依頼元のSEへ返却してください。"
    ),
    "PG": (
        "あなたはGemma4（PG）から難関子タスクの外注を受けた【プリンシパル・システムプログラマー・プロフェッショナル (Qwen3-Coder-Next)】です。\n"
        "作業空間のルートは常に `~/` です。あなたには最大512ステップの行動枠と、Gemma4と同じ全ツール（`list_files`, `read_file`, `read_files`, `search_files`, `write_file`, `write_files`, `edit_lines`, `add_lines_after`, `replace_in_file`, `run_command`, `web_search`, `fetch_url`, `complete_phase`）が与えられています。\n"
        f"{BATCH_EFFICIENCY_NOTE}\n"
        "【絶対完遂ルール】\n"
        "1. `TODO`, `FIXME`, `// ...`, モック、省略は一切禁止です。\n"
        "2. ファイルの作成・編集には `write_files`（新規一括）、`write_file`（単一新規）、`edit_lines`（行置換）、`add_lines_after`（行挿入）を適切に使い分けてください。\n"
        "3. コードを書いたら必ず自ら `run_command` でビルド・テストを実行してください。コンパイルエラーや警告（`-Wall -Wextra -Werror -Wpedantic -Wconversion`）、テスト失敗が出た場合は、その場で原因箇所を `read_file` で確認し、`edit_lines` / `add_lines_after`（または不要な重複ファイルの `rm`）でエラーが完全ゼロになるまで粘り強く修正を繰り返してください。\n"
        "4. 依頼された子タスクの完了条件（ビルド成功・テスト通過）を達成したら、`~/TASK.md` 自体は変更せず（親のPGが更新するため）、`complete_phase` を呼び出して実施内容と検証結果を親のPGへ報告してください。"
    ),
}

AUTONOMOUS_INITIATIVE_PROMPT = (
    "現在 `~/TASK.md` に未完了タスク（TODO / IMPL / FIX / QA）が存在せず、システムがアイドル状態です。\n"
    "自律行動モードとして、ワークスペース (`~/`) 全体を調査し、以下の1〜3を順番に実行してください：\n\n"
    "1. 【SSOT整合性検証＆ワークスペース完全整理】\n"
    "   - `list_files` で `~/` 配下を確認し、散らかった一時ファイル・不要なビルド生成物・重複ファイル・サブフォルダ内の重複設計書があれば、`run_command` で美しく整理整頓してください。\n"
    "   - `~/README.md`（プロジェクト台帳）が現在の実際のディレクトリ構成・ファイル一覧と100%一致するように最新化してください。\n"
    "   - `~/TASK.md` に完了済みの `[DONE]` タスクが大量に溜まっている場合は、直近の履歴を残してコンパクトに整理してください。\n\n"
    "2. 【極限品質向上タスクの自己起票】\n"
    "   - `~/REPORT.md` の監査指摘や既存プロジェクトのコード・テスト状況を確認してください。\n"
    "   - 既存プロジェクトが存在する場合は、「コンパイラ警告フラグ（`-Wconversion -Wshadow -Werror` 等）のさらなる厳格化」「ランダム破損データ注入（Fuzzing）・境界値・長時間の数値安定性ストレステストの構築」「エッジケースの防御ロジック強化」など、エンジニアリングの完成度を極限まで高める高難度タスクを1件考案し、`~/TASK.md` に `- [TODO] ...` として起票（追記）してください。\n"
    "   - まだプロジェクトが1つも存在しない初期状態の場合は、勝手にプロジェクトを捏造せず整理のみ行ってください。\n\n"
    "3. 【完了報告】\n"
    "   - 実施した整理整頓の内容と、新たに起票した `[TODO]` タスクの概要を `complete_phase` で報告してください。"
)