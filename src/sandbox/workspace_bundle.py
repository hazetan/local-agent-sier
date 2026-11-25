import re
import shutil
from pathlib import Path

from core.config import get_workspace_dir

from .container import resolve_workspace_path
from .file_ops import _tool_list_files, _tool_read_file

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
        "- (SE部門はここに妥協なき完全なアーキテクチャ設計・数理モデル・ディレクトリ構造・API仕様を記述すること)\n"
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

HEAVY_FILE_HEADER_PATTERN = re.compile(
    r"(?:^|\n)[ \t]*(?:#+|\*\*|-)?[ \t]*FILE[ \t]*:[ \t]*([^\n]+)",
)


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


def build_heavy_context_bundle(target_files: list[str] | None = None) -> str:
    """
    Professional 用に、ワークスペース (~/) 全体のディレクトリツリーと、
    明示的に指定された target_files のみを行番号付きでバンドルする（無差別なゴミファイル混入を防止）。
    """
    ws = get_workspace_dir().resolve()
    standard_root_docs = {(ws / name).resolve() for name in INITIAL_DOCUMENTS}
    tree_listing = _tool_list_files({"directory": "~/"})
    sections: list[str] = [f"=== WORKSPACE FULL DIRECTORY TREE ===\n{tree_listing}"]

    seen: set[Path] = set(standard_root_docs)
    for rp in target_files or []:
        target = resolve_workspace_path(str(rp))
        if not target or not target.exists() or target.is_dir():
            continue
        resolved = target.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        sections.append(_tool_read_file({"path": str(rp)}))

    return "\n\n".join(sections)


def auto_apply_heavy_markdown(
    markdown_text: str,
    default_targets: list[str] | None = None,
) -> list[str]:
    """
    重量モデルが Markdown で '### FILE: ~/path/to/file' セクションを出力した場合に自動保存する。
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
            report += " [WARNING: TRUNCATED_OUTPUT - 出力が途中で切れています。replace_lines で末尾を補完してください]"
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

    return applied_reports