import asyncio
import re
from typing import Any

import aiohttp

from core.config import (
    get_max_ctx_chars_budget,
    get_max_steps_per_phase,
    get_sandbox_env_hint,
    get_server_ctx_size,
)
from core.llm_client import chat_step, generate_heavy_brain
from core.logger import RoleLogger, error_logger, system_logger
from core.telemetry import telemetry
from sandbox.tools import (
    auto_apply_heavy_markdown,
    build_heavy_context_bundle,
    execute_tool,
    parse_tool_args,
    read_workspace_doc,
)

from .roles import (
    HEAVY_BRAIN_PROMPTS,
    ROLE_ALLOWED_TOOLS,
    ROLE_PROMPTS,
    get_tools_for_role,
)


def build_fresh_workspace_snapshot(max_chars_per_doc: int = 25000) -> str:
    """毎回最新の社内標準ドキュメント4点を読み込んでプロンプトに注入する（肥大化防止上限付き）"""
    def _read_capped(doc_path: str) -> str:
        text = read_workspace_doc(doc_path)
        if len(text) > max_chars_per_doc:
            return (
                text[:max_chars_per_doc]
                + f"\n...(truncated at {max_chars_per_doc:,} chars in snapshot; use read_file('{doc_path}') if full text is needed)..."
            )
        return text

    readme = _read_capped("~/README.md")
    task = _read_capped("~/TASK.md")
    design = _read_capped("~/DESIGN.md")
    report = _read_capped("~/REPORT.md")

    return (
        "=== 現在の社内標準ドキュメント (Single Source of Truth) ===\n"
        f"[~/README.md]\n{readme}\n\n"
        f"[~/TASK.md]\n{task}\n\n"
        f"[~/DESIGN.md]\n{design}\n\n"
        f"[~/REPORT.md]\n{report}\n"
        "=========================================================="
    )


def _enforce_context_budget(messages: list[dict[str, Any]], budget_chars: int) -> None:
    """
    SERVER_CTX_SIZE に連動した実効文字数上限を強制する。
    1. 古い tool 出力の圧縮
    2. それでも超過する場合は先頭3件 (System, Snapshot, 初期User指示) を絶対保護したまま古い中間履歴を削除
    """
    srv_ctx = get_server_ctx_size() or 65536
    effective_budget = min(budget_chars, int(srv_ctx * 2.0))

    def _total_chars() -> int:
        return sum(len(str(m.get("_clean_content", m.get("content") or ""))) for m in messages)

    if _total_chars() <= effective_budget:
        return

    # Step 1: 直近4件を除き、古い tool メッセージから圧縮する
    for msg in messages[:-4]:
        if _total_chars() <= effective_budget:
            break
        if msg.get("role") == "tool":
            content = str(msg.get("content") or "")
            if len(content) > 2000:
                msg["content"] = (
                    content[:1000]
                    + f"\n...(compressed from {len(content)} chars to fit context window)...\n"
                    + content[-800:]
                )
                msg["_clean_content"] = msg["content"]

    # Step 2: まだ上限を超える場合、先頭3件と末尾6件を保護しつつ古い中間ターンを削除する
    while _total_chars() > effective_budget and len(messages) > 9:
        del messages[3]


def _is_task_md_only_edit(fn_name: str, parsed_args: dict[str, Any]) -> bool:
    """edit_lines / replace_in_file の対象が ~/TASK.md のみかどうか判定する"""
    if fn_name == "replace_in_file":
        return "TASK.md" in str(parsed_args.get("path", ""))
    if fn_name == "edit_lines":
        edits = parsed_args.get("edits")
        if isinstance(edits, list) and edits:
            return all("TASK.md" in str(item.get("path", "")) for item in edits if isinstance(item, dict))
        return "TASK.md" in str(parsed_args.get("path", ""))
    return False


def _build_auto_heavy_args(role: str) -> dict[str, Any]:
    """
    軽量モデル(Gemma4)が思考ループ等で invoke_heavy_brain を呼べなかった際、
    ~/TASK.md と ~/DESIGN.md から対象パスを自動抽出して Heavy Brain 引数を構築するフェイルセーフ関数。
    """
    if role == "SE":
        return {
            "context_paths": ["~/"],
            "target_files": ["~/DESIGN.md"],
            "instruction": (
                "`~/TASK.md` の未処理 `[TODO]` タスクの要件を満たすための基本設計・詳細設計・"
                "作成すべきファイル構成と完全なインターフェース仕様を `~/DESIGN.md` に執筆してください。"
            ),
        }

    task_md = read_workspace_doc("~/TASK.md")
    design_md = read_workspace_doc("~/DESIGN.md")
    found_paths = re.findall(r"~/[a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+", f"{task_md}\n{design_md}")
    ignore_docs = {"~/README.md", "~/TASK.md", "~/DESIGN.md", "~/REPORT.md"}
    targets = list(dict.fromkeys(p for p in found_paths if p not in ignore_docs))

    return {
        "context_paths": ["~/"],
        "target_files": targets or ["~/main.py"],
        "instruction": (
            "`~/TASK.md` の `[IMPL]` / `[FIX]` タスクおよび `~/DESIGN.md` の設計仕様に基づき、"
            "必要な全ソースコード・テストコードを省略なしで完全実装してください。"
            "各ファイルは必ず `### FILE: ~/path/to/file` ヘッダを付けて出力してください。"
        ),
    }


async def run_role_phase(
    role: str,
    instruction: str,
    max_steps: int | None = None,
    require_heavy: bool | None = None,
) -> tuple[str, list[str]]:
    role = role.upper()
    telemetry.start_role(role)

    if require_heavy is None:
        require_heavy = role in ("SE", "PG")

    steps_limit = max_steps or get_max_steps_per_phase()
    srv_ctx = get_server_ctx_size() or 65536
    ctx_char_budget = min(get_max_ctx_chars_budget(is_heavy=False), int(srv_ctx * 2.0))
    max_tool_output_chars = int(ctx_char_budget * 0.5)
    logger = RoleLogger(role)
    tools = get_tools_for_role(ROLE_ALLOWED_TOOLS[role])

    system_logger.log("RUNNER", f"▶ Start role={role} (max_steps={steps_limit}, require_heavy={require_heavy})")

    system_prompt = ROLE_PROMPTS[role]
    if role in ("SE", "PG", "QA"):
        env_hint = get_sandbox_env_hint()
        if env_hint:
            system_prompt += f"\n\n{env_hint}"

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": build_fresh_workspace_snapshot()},
        {"role": "user", "content": instruction},
    ]

    created_zips: list[str] = []
    final_summary = ""
    header_logged = False
    no_tool_streak = 0
    heavy_invoked = False
    actual_steps = 0

    try:
        for step in range(1, steps_limit + 1):
            actual_steps = step
            _enforce_context_budget(messages, ctx_char_budget)
            try:
                response_msg, latency, model_name, tps = await chat_step(role, messages, tools=tools)
                if not header_logged:
                    logger.log_phase_start(model_name, instruction[:120])
                    header_logged = True
            except (RuntimeError, aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
                logger.log_error(str(exc), exc=exc)
                system_logger.error("RUNNER", f"[{role}] Step {step} chat_step failed: {exc}", exc)
                raise

            thought = (response_msg.get("content") or "").strip()
            tool_calls = response_msg.get("tool_calls") or []
            messages.append(response_msg)

            if not tool_calls:
                no_tool_streak += 1
                logger.log_step(step, thought, latency_sec=latency, tps=tps)
                telemetry.record_raw_step(role, model_name, step, steps_limit, latency, thought, tps=tps)
                if telemetry.on_raw_update:
                    await telemetry.on_raw_update()

                # --- フェイルセーフ: SE/PGで2回連続ツール不発ならランナーが自動的にHeavy Brainを起動 ---
                if require_heavy and not heavy_invoked and no_tool_streak >= 2:
                    system_logger.log(
                        "RUNNER",
                        f"[{role}] Light model failed to call tools {no_tool_streak} times. Auto-triggering invoke_heavy_brain!",
                        level="WARN",
                    )
                    heavy_invoked = True
                    no_tool_streak = 0
                    auto_args = _build_auto_heavy_args(role)
                    output = await _execute_heavy_brain_stage(role, step, steps_limit, auto_args, logger)
                    telemetry.add_branch_event(role, "invoke_heavy_brain(auto)", auto_args, output)

                    messages[1] = {"role": "system", "content": build_fresh_workspace_snapshot()}
                    del messages[3:]
                    next_tag = "[IMPL]" if role == "SE" else "[QA]"
                    messages.append({
                        "role": "user",
                        "content": (
                            f"システムが自動的に `invoke_heavy_brain` を実行し、ファイルを生成・配置しました：\n\n"
                            f"{output[:max_tool_output_chars]}\n\n"
                            f"続けて `edit_lines`（または `replace_in_file`）で `~/TASK.md` のステータスタグを `{next_tag}` に更新し、"
                            "`complete_phase` を呼び出してください。"
                        ),
                    })
                    continue

                if no_tool_streak >= 4:
                    error_logger.log_error(
                        f"RUNNER:{role}",
                        f"Aborted after {no_tool_streak} consecutive steps with no tool calls (total_steps={actual_steps}).",
                        context={"last_thought": thought[:300], "heavy_invoked": heavy_invoked},
                    )
                    break

                if no_tool_streak >= 2:
                    messages[1] = {"role": "system", "content": build_fresh_workspace_snapshot()}
                    del messages[3:]

                if require_heavy and not heavy_invoked:
                    default_target = "['~/DESIGN.md']" if role == "SE" else "['~/対象のファイルパス']"
                    messages.append({
                        "role": "user",
                        "content": (
                            "【必須指示】思考やテキスト回答は不要です。今すぐ `invoke_heavy_brain` ツールを直接呼び出し、"
                            f"`target_files={default_target}` と `instruction` を指定してください。"
                        ),
                    })
                else:
                    messages.append({
                        "role": "user",
                        "content": "思考は不要です。今すぐツールを直接呼び出すか、`complete_phase` を呼び出して完了してください。",
                    })
                continue

            no_tool_streak = 0
            phase_done = False
            for call in tool_calls:
                fn_name = call["function"]["name"]
                raw_args = call["function"].get("arguments", {})
                parsed_args = parse_tool_args(raw_args)

                if require_heavy and not heavy_invoked:
                    if fn_name == "complete_phase":
                        output = (
                            "Error: まだ `invoke_heavy_brain` が実行されていません。"
                            f"必ず `invoke_heavy_brain` を呼び出して重量級モデル (Qwen3-Coder-Next) に執筆させてから完了してください。"
                        )
                        logger.log_step(step, thought, fn_name, parsed_args, output, latency, tps=tps)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.get("id", f"call_{step}"),
                            "name": fn_name,
                            "content": output,
                        })
                        continue

                    if fn_name in ("edit_lines", "replace_in_file", "write_file", "write_files") and not _is_task_md_only_edit(fn_name, parsed_args):
                        output = (
                            f"Error: `{fn_name}` による直接執筆は禁止されています。"
                            "まずは `invoke_heavy_brain` を呼び出して重量級モデル (Qwen3-Coder-Next) にファイル全文を生成させてください。"
                        )
                        logger.log_step(step, thought, fn_name, parsed_args, output, latency, tps=tps)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.get("id", f"call_{step}"),
                            "name": fn_name,
                            "content": output,
                        })
                        continue

                if fn_name == "invoke_heavy_brain":
                    heavy_invoked = True
                    output = await _execute_heavy_brain_stage(role, step, steps_limit, parsed_args, logger)
                else:
                    output = await execute_tool(fn_name, parsed_args)
                    logger.log_step(step, thought, fn_name, parsed_args, output, latency, tps=tps)
                    telemetry.record_raw_step(
                        role, model_name, step, steps_limit, latency, thought, fn_name, parsed_args, output, tps=tps
                    )
                    if telemetry.on_raw_update:
                        await telemetry.on_raw_update()

                telemetry.add_branch_event(role, fn_name, parsed_args, output)

                if output.startswith("ZIP_CREATED:"):
                    created_zips.append(output.split("ZIP_CREATED:", 1)[1].strip())

                if len(output) > max_tool_output_chars:
                    trimmed_out = (
                        output[:max_tool_output_chars]
                        + f"\n...(truncated at {max_tool_output_chars:,} chars to fit within context budget)..."
                    )
                else:
                    trimmed_out = output

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", f"call_{step}"),
                    "name": fn_name,
                    "content": trimmed_out,
                })

                if fn_name == "complete_phase":
                    final_summary = output
                    phase_done = True
                    break

            if phase_done:
                break

        if require_heavy and not heavy_invoked:
            error_logger.log_heavy_failure(
                role, "NOT_INVOKED", "PHASE_END",
                f"Role {role} ended after {actual_steps} step(s) (limit={steps_limit}) without invoking Heavy Brain.",
                diagnostics={"actual_steps": actual_steps, "steps_limit": steps_limit, "summary_len": len(final_summary)},
            )

        system_logger.log("RUNNER", f"■ End role={role} (steps={actual_steps}, heavy_used={heavy_invoked}, summary_len={len(final_summary)})")
        return final_summary, created_zips
    finally:
        telemetry.end_role(role)


async def _execute_heavy_brain_stage(
    role: str,
    step: int,
    steps_limit: int,
    parsed_args: dict[str, Any],
    logger: RoleLogger,
) -> str:
    """
    Stage 2 & Stage 3 (Auto-Apply):
    重量級モデル (MODEL_HEAVY) を呼び出し、出力された '### FILE:' ブロックを自動保存する。
    """
    raw_ctx_paths = parsed_args.get("context_paths") or []
    if isinstance(raw_ctx_paths, str):
        raw_ctx_paths = [p.strip() for p in raw_ctx_paths.split(",") if p.strip()]

    raw_targets = parsed_args.get("target_files") or []
    if isinstance(raw_targets, str):
        raw_targets = [p.strip() for p in raw_targets.split(",") if p.strip()]
    if not raw_targets and role == "SE":
        raw_targets = ["~/DESIGN.md"]

    heavy_instruction = str(parsed_args.get("instruction", "")).strip() or "タスク要件に従い、完全な設計または実装を行ってください。"
    all_read_paths = list(dict.fromkeys([*raw_ctx_paths, *raw_targets]))

    system_logger.log("RUNNER", f"[{role}:HEAVY] Invoking Heavy Brain for targets={raw_targets}")
    bundle_text = await asyncio.to_thread(build_heavy_context_bundle, all_read_paths)
    snapshot = build_fresh_workspace_snapshot()

    heavy_sys_prompt = HEAVY_BRAIN_PROMPTS.get(role, HEAVY_BRAIN_PROMPTS["PG"])
    env_hint = get_sandbox_env_hint()
    if env_hint:
        heavy_sys_prompt += f"\n\n{env_hint}"

    target_fmt_hint = "\n".join(f"- `### FILE: {t}`" for t in raw_targets) or "- `### FILE: ~/path/to/file`"
    heavy_user_prompt = (
        f"{snapshot}\n\n"
        f"=== 参照ファイル群 (Context Files) ===\n{bundle_text}\n\n"
        f"=== 執筆・実装指示 ===\n{heavy_instruction}\n\n"
        f"=== 出力対象ファイル（必ず以下のヘッダとコードブロックで全文を出力すること） ===\n{target_fmt_hint}\n"
    )

    telemetry.record_raw_step(
        f"{role}:HEAVY", "HEAVY_MODEL", step, steps_limit, 0.0,
        "(Heavy Brain 推論開始...)", "invoke_heavy_brain", parsed_args, "(執筆中...)",
    )
    if telemetry.on_raw_update:
        await telemetry.on_raw_update()

    try:
        thinking_text, clean_md, h_latency, h_model, h_tps = await generate_heavy_brain(
            role, heavy_sys_prompt, heavy_user_prompt
        )
    except (RuntimeError, aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
        err_msg = f"Heavy Brain Execution Failed ({type(exc).__name__}): {exc}"
        system_logger.log("RUNNER", f"[{role}:HEAVY] {err_msg}", level="ERROR")
        return f"Error in invoke_heavy_brain: {err_msg} (詳細は logs/error.log を参照)"

    applied_files = await asyncio.to_thread(auto_apply_heavy_markdown, clean_md, raw_targets)
    if not applied_files:
        error_logger.log_heavy_failure(
            role, h_model, "AUTO_APPLY_PARSE",
            "Heavy model succeeded, but no valid '### FILE:' blocks could be applied.",
            diagnostics={"targets": raw_targets, "output_preview": clean_md[:400]},
        )

    summary_header = (
        f"[Heavy Brain ({h_model}) Completed in {h_latency:.1f}s ({h_tps:.2f} tok/s)]\n"
        f"Auto-applied files ({len(applied_files)}):\n"
        + ("\n".join(f"  - {af}" for af in applied_files) if applied_files else "  - (No '### FILE:' blocks detected)")
    )
    tool_output = f"{summary_header}\n\n[Heavy Brain Output Preview]\n{clean_md[:4000]}"

    combined_thought = (
        f"[Heavy Brain Thinking ({h_model})]\n{thinking_text[:600]}"
        if thinking_text
        else f"[Heavy Brain Output ({h_model})]\n{clean_md[:600]}"
    )
    logger.log_step(step, combined_thought, "invoke_heavy_brain", parsed_args, summary_header, h_latency, tps=h_tps)
    telemetry.record_raw_step(
        f"{role}:HEAVY", h_model, step, steps_limit, h_latency, combined_thought,
        "invoke_heavy_brain", parsed_args, summary_header, tps=h_tps,
    )
    if telemetry.on_raw_update:
        await telemetry.on_raw_update()

    return tool_output