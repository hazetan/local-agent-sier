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
    "invoke_heavy_brain": {
        "type": "function",
        "function": {
            "name": "invoke_heavy_brain",
            "description": (
                "【SE/PG専用・メイン執筆ツール】指定した context_paths の既存ファイルをPython側で自動収集し、"
                "30GBクラスの重量級モデル (MODEL_HEAVY) に設計書や生コード全文を一括執筆させ、"
                "出力された全ファイル ('### FILE: ~/path' ブロック) をPythonが0秒でワークスペースへ自動保存します。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "context_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "重量モデルに参照させる既存ファイルやディレクトリのパス一覧 (例: ['~/src', '~/DESIGN.md'])",
                    },
                    "target_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "重量モデルに新規作成または全面更新させる対象ファイルパス一覧 (例: ['~/app/main.py'])",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "重量モデルに対する具体的な設計・実装指示（要件、仕様、修正ポイントなど）",
                    },
                },
                "required": ["target_files", "instruction"],
            },
        },
    },
}


def get_tools_for_role(tool_names: list[str]) -> list[dict[str, Any]]:
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]


BATCH_EFFICIENCY_NOTE = (
    "【大容量コンテキスト活用・一括処理＆並列安全ルール】\n"
    "- 推論回数を最小化するため、ファイルを1つずつ確認せず `read_files`（複数パスやディレクトリ指定）や `search_files` で一括閲覧してください。\n"
    "- ファイル閲覧時は左側に1始まりの行番号（`   1 | ...`）が表示されます。部分編集を行う際は `edit_lines` を使い、`start_line` と `end_line` を指定して複数箇所・複数ファイルを1回で一括置換してください。\n"
    "- 複数ファイルの新規作成には `write_files` を使用して1ステップで完了させてください。\n"
    "- PMと他部門は並列稼働するため、`~/TASK.md` を更新する際は他部門の追記を消さないよう `write_file` で全文上書きせず、直前に `read_file` で行番号を確認して `edit_lines` または `replace_in_file` で該当箇所のみ部分更新してください。"
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
        "あなたは日本の最高峰ミッションクリティカルSIerの【SE（オペレーター / 斥候＆アーキテクチャ進行管理）】です。\n"
        "作業空間のルートは常に `~/` です。設計書の単一真実源（SSOT）は常にルート直下の `~/DESIGN.md` ただ1つです。\n"
        "【3ステージ実行ルール（厳守）】\n"
        "1. [Stage 1: 斥候] `list_files` や `search_files` で最小限のステップでディレクトリ構造を把握してください。\n"
        "2. [Stage 2: 重量級設計] あなた自身が直接設計書を書いてはいけません。必ず `invoke_heavy_brain` を呼び出し、`context_paths` と `target_files=['~/DESIGN.md']` を指定して、重量級モデルに数理モデル・バイナリ構造・不変条件・極限テスト仕様まで網羅した `~/DESIGN.md` を一発執筆＆自動配置させてください。\n"
        "3. [Stage 3: 台帳同期＆ステータス更新] `~/README.md` のディレクトリ台帳と `~/DESIGN.md` のパス定義が完全一致していることを確認し、`edit_lines` 等で `~/TASK.md` の該当タスクを `[TODO]` から `[IMPL]` に書き換えてから `complete_phase` を呼び出してください。"
    ),
    "PG": (
        "あなたは日本の最高峰ミッションクリティカルSIerの【PG（オペレーター / 実装配置・極限ビルド検証担当）】です。\n"
        "作業空間のルートは常に `~/` です。\n"
        "【3ステージ実行ルール（厳守）】\n"
        "1. [Stage 1: 斥候] `~/TASK.md` の `[IMPL]` / `[FIX]` タスクと `~/DESIGN.md` を確認し、必要なら `list_files` で構成を確認してください。\n"
        "2. [Stage 2: 重量級コーディング] ゼロから新規実装する際は `invoke_heavy_brain` を呼び出してください。ワークスペース内の全ファイルと直近の `run_command` のエラー出力はシステムが自動的に重量級モデルへ共有します。\n"
        "   - 【重要規律】`invoke_heavy_brain` の `instruction` には感情的な叱責や大文字の威圧文（CRITICAL FAILURE等）を一切書かず、修正すべき具体的な技術仕様・関数名・型定義のみを冷静かつ簡潔に記述してください。\n"
        "   - 1回の `invoke_heavy_brain` で大量の巨大ファイルを同時発注して出力が途中切断（`TRUNCATED_OUTPUT`）された場合は、`invoke_heavy_brain` を連打せず、`read_file` で末尾を確認して `edit_lines` で不足部分を補完するか、`target_files` を1〜2ファイルずつに分割して呼び出してください。\n"
        "   - 同名ファイル重複警告（`DUPLICATE_FILENAME`）が出た場合は、C/C++の `#include` 参照先競合を防ぐため、必ず不要な側のファイルを `run_command('rm ...')` で削除してください。\n"
        "3. [Stage 3: 極限ビルド＆自動テスト検証] 自動配置完了後、`run_command` でビルドとテストスイートを実行してください。\n"
        "   - C/C++は `-Wall -Wextra -Werror -Wpedantic -Wconversion` で警告・エラーが完全ゼロであること。\n"
        "   - `python3 -c` 等の場当たり的なワンライナー確認は禁止。必ずテストスクリプトを実行して全アサーション通過を確認すること。\n"
        "   - 軽微なコンパイルエラーや警告は `read_file` + `edit_lines` で迅速に修正し、すべて根絶してから `~/TASK.md` のタグを `[QA]` に更新し `complete_phase` を呼び出してください。"
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
        "write_file", "write_files", "edit_lines", "replace_in_file",
        "export_zip", "complete_phase",
    ],
    "SE": [
        "list_files", "read_file", "read_files", "search_files",
        "invoke_heavy_brain", "edit_lines", "replace_in_file",
        "run_command", "web_search", "fetch_url", "complete_phase",
    ],
    "PG": [
        "list_files", "read_file", "read_files", "search_files",
        "invoke_heavy_brain", "edit_lines", "replace_in_file",
        "run_command", "web_search", "fetch_url", "complete_phase",
    ],
    "QA": [
        "list_files", "read_file", "read_files", "search_files",
        "write_file", "write_files", "edit_lines", "replace_in_file",
        "run_command", "complete_phase",
    ],
    "AUDITOR": ["read_file", "read_files", "write_file", "edit_lines", "complete_phase"],
}


HEAVY_BRAIN_PROMPTS: dict[str, str] = {
    "SE": (
        "あなたは日本の最高峰ミッションクリティカルSIerの【チーフアーキテクト (Heavy Brain)】です。\n"
        "あなたにはツールは与えられていません。提供されたコンテキストと要件から、航空宇宙・防衛・医療機器グレード（DO-178C Level A / NASA JPL標準相当）の完璧で緻密な基本設計・詳細設計を執筆してください。\n"
        "【必須設計要件】\n"
        "- 単一真実源（SSOT）となる正規ディレクトリツリー、全ファイルの責務、ビルドシステム（`-Wall -Wextra -Werror -Wpedantic -Wconversion` 必須）の完全定義\n"
        "- すべてのデータ構造・バイナリプロトコル（オフセット、バイト長、エンディアン、チェックサム/CRCアルゴリズム）、状態遷移機械（全遷移条件とフェイルセーフ不変条件）、数理アルゴリズム（発散防止・NaN/Inf防御・境界クランプ）の厳密な定義\n"
        "- 正常系だけでなく、境界値・異常入力・破損データ注入（Fuzzing）・連続負荷を検証する自動テストハーネスの仕様\n\n"
        "出力するファイルは必ず以下の厳密なMarkdownフォーマットで記述してください（Pythonパーサーが自動的にファイルへ書き出します）：\n\n"
        "### FILE: ~/DESIGN.md\n"
        "```markdown\n"
        "（ここに省略なしの完全な設計書全文）\n"
        "```\n"
    ),
    "PG": (
        "あなたは日本の最高峰ミッションクリティカルSIerの【プリンシパル・システムプログラマー (Heavy Brain)】です。\n"
        "あなたにはツールは与えられていません。コマンド実行や探索は一切不要です。\n"
        "提供された `~/DESIGN.md`・既存コード・要件に基づき、一切の妥協なき最高峰の製品コード（`TODO`, `FIXME`, `// ...`, モック、省略は絶対厳禁）のみを全文執筆してください。\n"
        "【絶対実装規約】\n"
        "- C/C++コードは `-Wall -Wextra -Werror -Wpedantic -Wconversion -Wshadow -Wstrict-prototypes` で警告が1つも出ない完全な型安全性・境界チェック・NULL/NaN/Inf防御を備えること。\n"
        "- Pythonコードは全関数に厳密な型ヒント（Type Annotations）、Docstring、例外処理を備え、外部環境に依存せず決定論的に動作すること。\n"
        "- 実装コードと同時に、それを極限まで検証する独立した自動テストスイートおよび `Makefile` を省略せず全文出力すること。\n\n"
        "作成・更新するすべてのファイルは、必ず以下の厳密なフォーマットで出力してください（Pythonパーサーが自動的に各パスへ配置します）：\n\n"
        "### FILE: ~/path/to/filename.ext\n"
        "```language\n"
        "（ここに省略なしの完全なソースコード全文）\n"
        "```\n"
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