import asyncio
from typing import Any

import aiohttp

from core.config import (
    get_max_steps_per_phase,
    get_professional_max_steps,
    get_sandbox_env_hint,
)
from core.llm_client import (
    chat_step,
    professional_chat_step,
    professional_session,
)
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
    PROFESSIONAL_ALLOWED_TOOLS,
    PROFESSIONAL_PROMPTS,
    ROLE_ALLOWED_TOOLS,
    ROLE_PROMPTS,
    get_tools_for_role,
)


def build_fresh_workspace_snapshot() -> str:
    """毎回最新の社内標準ドキュメント4点を一切の切り詰めなしで読み込んでプロンプトに注入する"""
    readme = read_workspace_doc("~/README.md")
    task = read_workspace_doc("~/TASK.md")
    design = read_workspace_doc("~/DESIGN.md")
    report = read_workspace_doc("~/REPORT.md")

    return (
        "=== 現在の社内標準ドキュメント (Single Source of Truth) ===\n"
        f"[~/README.md]\n{readme}\n\n"
        f"[~/TASK.md]\n{task}\n\n"
        f"[~/DESIGN.md]\n{design}\n\n"
        f"[~/REPORT.md]\n{report}\n"
        "=========================================================="
    )


def _extract_recent_command_logs(messages: list[dict[str, Any]], max_logs: int = 2) -> str:
    """会話履歴から直近の run_command 実行結果（コンパイルエラーやテスト出力）を抽出する"""
    logs: list[str] = []
    for m in reversed(messages):
        if m.get("role") == "tool" and (m.get("name") == "run_command" or m.get("tool_name") == "run_command"):
            content = str(m.get("content") or "").strip()
            if content:
                logs.append(content)
                if len(logs) >= max_logs:
                    break
    if not logs:
        return "(まだコマンド実行履歴はありません)"
    return "\n\n---\n\n".join(reversed(logs))


async def run_role_phase(
    role: str,
    instruction: str,
    max_steps: int | None = None,
    require_heavy: bool | None = None,
) -> tuple[str, list[str]]:
    """
    各ロール (PM, SE, PG, QA, AUDITOR) のメインループ。
    コンテキスト消費制限や履歴削除を一切行わず、全文・全履歴を保持して推論する。
    """
    _ = require_heavy
    role = role.upper()
    telemetry.start_role(role)

    steps_limit = max_steps or get_max_steps_per_phase()
    logger = RoleLogger(role)
    tools = get_tools_for_role(ROLE_ALLOWED_TOOLS[role])

    system_logger.log("RUNNER", f"▶ Start role={role} (max_steps={steps_limit})")

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
    pro_invoked_count = 0
    actual_steps = 0

    try:
        for step in range(1, steps_limit + 1):
            actual_steps = step
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

                if no_tool_streak >= 4:
                    error_logger.log_error(
                        f"RUNNER:{role}",
                        f"Aborted after {no_tool_streak} consecutive steps with no tool calls (total_steps={actual_steps}).",
                        context={"last_thought": thought[:300], "pro_invoked_count": pro_invoked_count},
                    )
                    break

                messages.append({
                    "role": "user",
                    "content": (
                        "ツールが呼び出されませんでした。"
                        "今すぐ自分でツール（`read_files`, `get_file_outline`, `edit_lines`, `add_lines_after`, `delete_lines`, `write_file`, `run_command` 等）を呼び出すか、"
                        "難題であれば `invoke_professional` で子タスクを外注するか、完了していれば `complete_phase` を呼び出してください。"
                    ),
                })
                continue

            no_tool_streak = 0
            phase_done = False
            for call in tool_calls:
                fn_name = call["function"]["name"]
                raw_args = call["function"].get("arguments", {})
                parsed_args = parse_tool_args(raw_args)

                if fn_name in ("invoke_professional", "invoke_heavy_brain"):
                    pro_invoked_count += 1
                    output = await _execute_professional_subtask(
                        role, step, parsed_args, logger, messages
                    )
                    messages[1] = {"role": "system", "content": build_fresh_workspace_snapshot()}
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

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", f"call_{step}"),
                    "name": fn_name,
                    "content": output,
                })

                if fn_name == "complete_phase":
                    final_summary = output
                    phase_done = True
                    break

            if phase_done:
                break

        system_logger.log(
            "RUNNER",
            f"■ End role={role} (steps={actual_steps}, pro_outsourced={pro_invoked_count}, summary_len={len(final_summary)})",
        )
        return final_summary, created_zips
    finally:
        telemetry.end_role(role)


async def _execute_professional_subtask(
    role: str,
    parent_step: int,
    parsed_args: dict[str, Any],
    logger: RoleLogger,
    parent_messages: list[dict[str, Any]] | None = None,
) -> str:
    """
    Gemma4 から外注された子タスクを、Professional (Qwen3-Coder-Next) が
    コンテキスト切り詰めなしで PROFESSIONAL_MAX_STEPS まで自律遂行する。
    """
    pro_max_steps = get_professional_max_steps()
    subtask_title = str(parsed_args.get("subtask_title") or "Outsourced Subtask").strip()
    pro_instruction = str(parsed_args.get("instruction") or "子タスクを完遂してください。").strip()
    raw_targets = parsed_args.get("target_files") or []
    if isinstance(raw_targets, str):
        raw_targets = [p.strip() for p in raw_targets.split(",") if p.strip()]

    system_logger.log(
        "RUNNER",
        f"[{role}:PRO] ▶ Outsourced subtask='{subtask_title}' (targets={raw_targets}, max_steps={pro_max_steps})",
    )
    logger.log_step(
        parent_step,
        f"[Outsourced to Professional] Subtask: {subtask_title}",
        "invoke_professional",
        parsed_args,
        f"Starting Professional {pro_max_steps}-step autonomous session...",
    )

    pro_tools = get_tools_for_role(PROFESSIONAL_ALLOWED_TOOLS)
    pro_sys_prompt = PROFESSIONAL_PROMPTS.get(role, PROFESSIONAL_PROMPTS["PG"])
    env_hint = get_sandbox_env_hint()
    if env_hint:
        pro_sys_prompt += f"\n\n{env_hint}"

    bundle_text = await asyncio.to_thread(build_heavy_context_bundle, ["~/", *raw_targets])
    snapshot = build_fresh_workspace_snapshot()
    recent_cmd_logs = _extract_recent_command_logs(parent_messages or [])
    targets_line = f"\n【主な対象ファイル】: {', '.join(raw_targets)}" if raw_targets else ""

    initial_user_prompt = (
        f"=== 外注された子タスク: {subtask_title} ==={targets_line}\n"
        f"{pro_instruction}\n\n"
        f"=== 直近のビルド・テスト・コマンド実行結果 ===\n{recent_cmd_logs}\n\n"
        f"=== ワークスペース初期スナップショット ===\n{bundle_text}\n\n"
        "上記の状況を踏まえ、ツール（`get_file_outline`, `edit_lines`, `add_lines_after`, `delete_lines`, `delete_files`, `write_file`, `write_files`, `run_command` 等）を直接呼び出して作業を開始し、"
        "ビルドやテストの通過を確認したら `complete_phase` で完了報告を行ってください。"
    )

    pro_messages: list[dict[str, Any]] = [
        {"role": "system", "content": pro_sys_prompt},
        {"role": "system", "content": snapshot},
        {"role": "user", "content": initial_user_prompt},
    ]

    action_history: list[str] = []
    pro_summary = ""
    no_tool_streak = 0
    pro_steps_taken = 0

    try:
        async with professional_session(role) as pro_model_name:
            for p_step in range(1, pro_max_steps + 1):
                pro_steps_taken = p_step

                resp_msg, p_latency, _, p_tps = await professional_chat_step(
                    role, pro_model_name, pro_messages, tools=pro_tools
                )
                p_thought = (resp_msg.get("content") or "").strip()
                p_clean = (resp_msg.get("_clean_content") or "").strip()
                p_calls = resp_msg.get("tool_calls") or []
                pro_messages.append(resp_msg)

                if not p_calls:
                    if "### FILE:" in p_clean:
                        applied = await asyncio.to_thread(auto_apply_heavy_markdown, p_clean, raw_targets)
                        if applied:
                            no_tool_streak = 0
                            auto_msg = "Auto-applied markdown files:\n" + "\n".join(f"  - {a}" for a in applied)
                            action_history.append(f"[Pro Step {p_step}] auto_apply_markdown -> {len(applied)} file(s)")
                            logger.log_step(
                                parent_step, f"[PRO {p_step}/{pro_max_steps}] {p_thought}",
                                "auto_apply_markdown", {"targets": raw_targets}, auto_msg, p_latency, tps=p_tps,
                            )
                            telemetry.record_raw_step(
                                f"{role}:PRO", pro_model_name, p_step, pro_max_steps, p_latency,
                                p_thought, "auto_apply_markdown", {"targets": raw_targets}, auto_msg, tps=p_tps,
                            )
                            if telemetry.on_raw_update:
                                await telemetry.on_raw_update()
                            pro_messages.append({
                                "role": "user",
                                "content": f"{auto_msg}\n続けて `run_command` でビルド・テストを実行するか、完了なら `complete_phase` を呼んでください。",
                            })
                            continue

                    no_tool_streak += 1
                    logger.log_step(
                        parent_step, f"[PRO {p_step}/{pro_max_steps}] {p_thought}",
                        latency_sec=p_latency, tps=p_tps,
                    )
                    telemetry.record_raw_step(
                        f"{role}:PRO", pro_model_name, p_step, pro_max_steps, p_latency, p_thought, tps=p_tps
                    )
                    if telemetry.on_raw_update:
                        await telemetry.on_raw_update()

                    if no_tool_streak >= 4:
                        pro_summary = f"(Professional stopped after {no_tool_streak} steps without tool calls. Last response: {p_clean[:600]})"
                        break

                    pro_messages.append({
                        "role": "user",
                        "content": (
                            "ツールが呼び出されませんでした。本文で説明せず、直ちに `edit_lines`, `add_lines_after`, `delete_lines`, `write_file`, `run_command` 等のツールを呼び出すか、"
                            "子タスクが完了していれば `complete_phase` を呼び出してください。"
                        ),
                    })
                    continue

                no_tool_streak = 0
                subtask_done = False
                for call in p_calls:
                    fn_name = call["function"]["name"]
                    p_args = parse_tool_args(call["function"].get("arguments", {}))

                    if fn_name in ("invoke_professional", "invoke_heavy_brain"):
                        t_out = "Error: あなた自身が既に Professional です。直接ツールを使って作業してください。"
                    else:
                        t_out = await execute_tool(fn_name, p_args)

                    first_line = t_out.strip().splitlines()[0][:100] if t_out.strip() else "(ok)"
                    action_history.append(f"[Pro Step {p_step}] {fn_name} -> {first_line}")

                    logger.log_step(
                        parent_step, f"[PRO {p_step}/{pro_max_steps}] {p_thought}",
                        fn_name, p_args, t_out, p_latency, tps=p_tps,
                    )
                    telemetry.record_raw_step(
                        f"{role}:PRO", pro_model_name, p_step, pro_max_steps, p_latency,
                        p_thought, fn_name, p_args, t_out, tps=p_tps,
                    )
                    telemetry.add_branch_event(role, f"PRO:{fn_name}", p_args, t_out)
                    if telemetry.on_raw_update:
                        await telemetry.on_raw_update()

                    pro_messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", f"pro_call_{p_step}"),
                        "name": fn_name,
                        "content": t_out,
                    })

                    if fn_name == "complete_phase":
                        pro_summary = t_out
                        subtask_done = True
                        break

                if subtask_done:
                    break
    except (RuntimeError, aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
        err_msg = f"Professional Subtask Failed ({type(exc).__name__}): {exc}"
        system_logger.log("RUNNER", f"[{role}:PRO] {err_msg}", level="ERROR")
        return f"Error in invoke_professional: {err_msg} (詳細は logs/error.log を参照)"

    if not pro_summary:
        pro_summary = f"(Reached {pro_steps_taken} steps without explicit complete_phase)"

    history_tail = "\n".join(action_history[-25:]) if action_history else "  (No tool actions recorded)"
    system_logger.log(
        "RUNNER",
        f"[{role}:PRO] ■ Subtask='{subtask_title}' finished in {pro_steps_taken} step(s).",
    )
    return (
        f"=== Professional (Qwen3-Coder-Next) 子タスク完了報告 ===\n"
        f"- 子タスク名: {subtask_title}\n"
        f"- 実行ステップ数: {pro_steps_taken} / {pro_max_steps}\n"
        f"- 完了サマリ:\n{pro_summary}\n\n"
        f"- 直近の実行アクション履歴:\n{history_tail}"
    )