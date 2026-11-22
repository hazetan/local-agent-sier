import ast
import asyncio
import json
import re
import time
from typing import Any

import aiohttp

from .config import (
    get_heavy_model_for_role,
    get_heavy_ollama_keep_alive,
    get_heavy_ollama_options,
    get_heavy_ollama_think,
    get_llm_timeout,
    get_model_for_role,
    get_ollama_chat_url,
    get_ollama_keep_alive,
    get_ollama_num_parallel,
    get_ollama_options,
    get_ollama_think,
    get_stream_granularity_tokens,
    get_think_prompt_boost,
)
from .logger import system_logger
from .telemetry import telemetry

_total_llm_sem: asyncio.Semaphore | None = None
_bg_llm_sem: asyncio.Semaphore | None = None
_configured_parallel: int = 0


def _get_llm_semaphores() -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
    """
    全体枠 (OLLAMA_NUM_PARALLEL) と バックグラウンド枠 (OLLAMA_NUM_PARALLEL - 1) を返す。
    並列数2以上のとき、常に1枠をPM用に空けておく。未設定(None)時は1並列。
    """
    global _total_llm_sem, _bg_llm_sem, _configured_parallel
    parallel = get_ollama_num_parallel() or 1
    if _total_llm_sem is None or _bg_llm_sem is None or parallel != _configured_parallel:
        _configured_parallel = parallel
        _total_llm_sem = asyncio.Semaphore(parallel)
        _bg_llm_sem = asyncio.Semaphore(max(1, parallel - 1))
    return _total_llm_sem, _bg_llm_sem


def _quote_unquoted_keys(s: str) -> str:
    """Gemma4固有の {directory:"~/...", extensions:[...]} のようなクォート無しキーを標準JSONへ補正する"""
    pattern = re.compile(
        r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|([\{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)'
    )
    return pattern.sub(
        lambda m: m.group(1) if m.group(1) is not None else f'{m.group(2)}"{m.group(3)}"{m.group(4)}',
        s,
    )


def _parse_json_lenient(raw: str) -> Any | None:
    s = raw.strip()
    quoted = _quote_unquoted_keys(s)
    for candidate in (
        s,
        re.sub(r",\s*([}\]])", r"\1", s),
        quoted,
        re.sub(r",\s*([}\]])", r"\1", quoted),
    ):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    try:
        val = ast.literal_eval(s)
        if isinstance(val, (dict, list)):
            return val
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        pass
    return None

def extract_thinking_and_clean(raw_content: str, explicit_thinking: str = "") -> tuple[str, str]:
    """
    レスポンスから (思考テキスト, クリーンな本文) を分離・抽出する。
    <think>, <thought>, <|channel>thought...<channel|>、文字化けした £thought および未クローズ状態に対応。
    """
    think_parts: list[str] = []
    if explicit_thinking and explicit_thinking.strip():
        think_parts.append(explicit_thinking.strip())

    cleaned = raw_content or ""
    if cleaned:
        cleaned = re.sub(r"[£Ï]thought\b", "<|channel>thought", cleaned)
        tag_patterns = [
            (r"<think>(.*?)</think>", "<think>", "</think>"),
            (r"<thought>(.*?)</thought>", "<thought>", "</thought>"),
            (r"<\|channel>thought\s*(.*?)<channel\|>", "<|channel>thought", "<channel|>"),
        ]
        for regex, open_tag, close_tag in tag_patterns:
            for m in re.findall(regex, cleaned, flags=re.DOTALL):
                m_clean = m.strip()
                if m_clean and m_clean not in think_parts[-1:]:
                    think_parts.append(m_clean)
            cleaned = re.sub(regex, "", cleaned, flags=re.DOTALL)

            if open_tag in cleaned and close_tag not in cleaned:
                prefix, unclosed_think = cleaned.split(open_tag, 1)
                if unclosed_think.strip():
                    think_parts.append(unclosed_think.strip())
                cleaned = prefix

        cleaned = re.sub(
            r"(?:<\|channel>thought|<channel\|>|<\|?tool_call\|?>|<\|?tool_response\|?>)",
            "",
            cleaned,
        ).strip()

    return "\n".join(think_parts).strip(), cleaned


HIGH_THINKING_DIRECTIVE = (
    "\n\n【最高レベル思考プロセス (Deep Thinking) 指示】\n"
    "ツールを呼び出す前に、必ず現在の状況・ディレクトリ構造・問題の本質・影響範囲・エッジケースを"
    "最高レベルの深さで段階的に思考（Thinking）し、論理的な整合性を検証してから行動してください。"
)


def _prepare_messages_for_ollama(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ollama /api/chat 向けにメッセージ配列と tool_calls / tool_name を正規化する"""
    prepared: list[dict[str, Any]] = []
    boost = get_think_prompt_boost()

    for idx, m in enumerate(messages):
        role = m.get("role", "user")
        msg_copy: dict[str, Any] = {
            "role": role,
            "content": m.get("_clean_content", m.get("content") or ""),
        }
        if idx == 0 and role == "system" and boost:
            msg_copy["content"] += HIGH_THINKING_DIRECTIVE

        if role == "tool":
            tool_name = m.get("tool_name") or m.get("name")
            if tool_name:
                msg_copy["tool_name"] = tool_name
                msg_copy["name"] = tool_name
            if "tool_call_id" in m:
                msg_copy["tool_call_id"] = m["tool_call_id"]

        if "tool_calls" in m and m["tool_calls"]:
            norm_calls = []
            for tc in m["tool_calls"]:
                fn = dict(tc.get("function", {}))
                raw_args = fn.get("arguments", {})
                if isinstance(raw_args, str):
                    fn["arguments"] = _parse_json_lenient(raw_args) or {}
                norm_calls.append({"function": fn})
            msg_copy["tool_calls"] = norm_calls

        prepared.append(msg_copy)
    return prepared


def _has_repetition_loop(text: str) -> bool:
    """
    ストリーミング中の末尾テキストを監視し、Gemma4の思考タグ暴走 (£thought / <channel|> / End of Thought)
    または同一フレーズの5回以上の異常反復を検知する。
    """
    if len(text) < 80:
        return False
    tail = text[-800:]
    for marker in ("£thought", "<channel|>", "<|channel>thought", "(End of Thought)"):
        if tail.count(marker) >= 5:
            return True
    for pat_len in (16, 28, 48, 80):
        if len(tail) >= pat_len * 5:
            unit = tail[-pat_len:]
            if unit.strip() and tail.count(unit) >= 5:
                return True
    return False


async def _execute_ollama_stream(
    role: str,
    model_name: str,
    payload: dict[str, Any],
    est_input_tok: int,
) -> tuple[dict[str, Any], int, int, int, float]:
    """Ollama /api/chat をストリーミング受信し、指定粒度ごとにテレメトリと生思考ログを更新する"""
    is_heavy = role.upper() in telemetry.heavy_active_roles
    role_label = f"{role.upper()}:HEAVY" if is_heavy else role.upper()

    base_timeout_sec = get_llm_timeout()
    read_timeout_sec = max(base_timeout_sec, 1800) if is_heavy else base_timeout_sec
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=read_timeout_sec)

    granularity = get_stream_granularity_tokens()
    think_setting = payload.get("think")
    chat_url = get_ollama_chat_url()

    req_start_t = time.perf_counter()
    acc_content: list[str] = []
    acc_thinking: list[str] = []
    acc_tool_calls: list[dict[str, Any]] = []
    chunk_count = 0
    prompt_tok = 0
    eval_tok = 0
    eval_duration_ns = 0

    system_logger.log(
        "LLM_CLIENT",
        f"[{role_label}] POST {chat_url} | model={model_name} | est_in_tok={est_input_tok} | think={think_setting} | options={payload.get('options')}",
    )

    async with aiohttp.ClientSession(timeout=timeout) as session:
        resp = await session.post(chat_url, json=payload)
        system_logger.log("LLM_CLIENT", f"[{role_label}] HTTP status={resp.status} received.")

        if resp.status == 400 and isinstance(think_setting, str):
            err_body = await resp.text()
            system_logger.log("LLM_CLIENT", f"[{role_label}] 400 with think={think_setting} ({err_body[:120]}). Retrying with think=True...", level="WARN")
            await resp.release()
            payload["think"] = True
            req_start_t = time.perf_counter()
            resp = await session.post(chat_url, json=payload)
            system_logger.log("LLM_CLIENT", f"[{role_label}] Retry HTTP status={resp.status} received.")

        async with resp:
            if resp.status != 200:
                err_text = await resp.text()
                system_logger.log("LLM_CLIENT", f"[{role_label}] Non-200 error body: {err_text}", level="ERROR")
                raise RuntimeError(f"Ollama Error ({resp.status}, model={model_name}): {err_text}")

            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if "error" in data:
                    system_logger.log("LLM_CLIENT", f"[{role_label}] Stream JSON error: {data['error']}", level="ERROR")
                    raise RuntimeError(f"Ollama Stream Error ({model_name}): {data['error']}")

                m = data.get("message") or {}
                c_piece = m.get("content") or ""
                t_piece = m.get("thinking") or m.get("reasoning") or ""
                if c_piece:
                    acc_content.append(c_piece)
                if t_piece:
                    acc_thinking.append(t_piece)
                if m.get("tool_calls"):
                    acc_tool_calls.extend(m["tool_calls"])

                if c_piece or t_piece:
                    chunk_count += 1
                    if chunk_count == 1:
                        first_tok_sec = time.perf_counter() - req_start_t
                        system_logger.log("LLM_CLIENT", f"[{role_label}] First token chunk arrived in {first_tok_sec:.2f}s.")

                    if chunk_count % 8 == 0:
                        tail_c = "".join(acc_content[-60:])
                        tail_t = "".join(acc_thinking[-60:])
                        if _has_repetition_loop(tail_c) or _has_repetition_loop(tail_t):
                            system_logger.log(
                                "LLM_CLIENT",
                                f"[{role_label}] Repetition loop detected at chunk={chunk_count}. Aborting stream early!",
                                level="WARN",
                            )
                            eval_tok = chunk_count
                            break

                    if chunk_count % granularity == 0:
                        elapsed = max(0.001, time.perf_counter() - req_start_t)
                        live_tps = chunk_count / elapsed
                        telemetry.update_role_tokens(role, est_input_tok + chunk_count, tps=live_tps)

                        live_think, live_clean = extract_thinking_and_clean(
                            "".join(acc_content), "".join(acc_thinking)
                        )
                        telemetry.raw_role = role_label
                        telemetry.raw_model = model_name
                        telemetry.raw_latency = elapsed
                        telemetry.raw_tps = live_tps
                        if live_think and live_clean:
                            telemetry.raw_thought = f"[Thinking]\n{live_think[-1200:]}\n\n[Drafting]\n{live_clean[-600:]}"
                        elif live_think:
                            telemetry.raw_thought = f"[Thinking...]\n{live_think[-1800:]}"
                        elif live_clean:
                            telemetry.raw_thought = live_clean[-1800:]
                        if telemetry.on_raw_update:
                            await telemetry.on_raw_update()

                if data.get("done"):
                    prompt_tok = int(data.get("prompt_eval_count") or 0)
                    eval_tok = int(data.get("eval_count") or chunk_count)
                    eval_duration_ns = int(data.get("eval_duration") or 0)
                    break

    elapsed_req = max(0.001, time.perf_counter() - req_start_t)
    system_logger.log(
        "LLM_CLIENT",
        f"[{role_label}] Stream done in {elapsed_req:.2f}s | prompt_tok={prompt_tok} | eval_tok={eval_tok} | tool_calls={len(acc_tool_calls)}",
    )
    assembled_msg: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(acc_content),
        "thinking": "".join(acc_thinking),
    }
    if acc_tool_calls:
        assembled_msg["tool_calls"] = acc_tool_calls

    return assembled_msg, prompt_tok, eval_tok, eval_duration_ns, elapsed_req

async def chat_step(
    role: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], float, str, float]:
    """
    PM優先枠付きの並列制御と粒度可変ストリーミングで Ollama /api/chat を実行する。
    """
    model_name = get_model_for_role(role)
    prepared_msgs = _prepare_messages_for_ollama(messages)
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": prepared_msgs,
        "stream": True,
    }

    think_setting = get_ollama_think()
    if think_setting is not None:
        payload["think"] = think_setting

    options = get_ollama_options()
    if options:
        payload["options"] = options

    keep_alive = get_ollama_keep_alive()
    if keep_alive:
        payload["keep_alive"] = keep_alive

    if tools:
        payload["tools"] = tools

    est_chars = len(json.dumps(prepared_msgs, ensure_ascii=False))
    if tools:
        est_chars += len(json.dumps(tools, ensure_ascii=False))
    est_input_tok = max(1, est_chars // 3)
    telemetry.update_role_tokens(role, est_input_tok)

    start_t = time.perf_counter()
    total_sem, bg_sem = _get_llm_semaphores()
    is_pm = role.upper() == "PM"

    async def _run_locked() -> tuple[dict[str, Any], int, int, int, float]:
        system_logger.log("LLM_CLIENT", f"[{role}] Waiting for total_sem...")
        async with total_sem:
            system_logger.log("LLM_CLIENT", f"[{role}] Acquired total_sem. Executing stream...")
            return await _execute_ollama_stream(role, model_name, payload, est_input_tok)

    if is_pm:
        msg, prompt_tok, eval_tok, eval_duration_ns, elapsed_req = await _run_locked()
    else:
        system_logger.log("LLM_CLIENT", f"[{role}] Waiting for bg_sem...")
        async with bg_sem:
            system_logger.log("LLM_CLIENT", f"[{role}] Acquired bg_sem.")
            msg, prompt_tok, eval_tok, eval_duration_ns, elapsed_req = await _run_locked()

    if prompt_tok == 0:
        prompt_tok = est_input_tok

    raw_content = msg.get("content") or ""
    explicit_thinking = msg.get("thinking") or ""
    if eval_tok == 0:
        eval_tok = max(1, len(raw_content + explicit_thinking) // 3)

    if eval_duration_ns > 0:
        tps = eval_tok / (eval_duration_ns / 1e9)
    else:
        tps = eval_tok / elapsed_req

    telemetry.update_role_tokens(role, prompt_tok + eval_tok, tps=tps)

    thinking_text, clean_content = extract_thinking_and_clean(raw_content, explicit_thinking)

    if not msg.get("tool_calls"):
        fb_calls = extract_fallback_tool_calls(clean_content) or extract_fallback_tool_calls(raw_content)
        if fb_calls:
            system_logger.log("LLM_CLIENT", f"[{role}] Extracted {len(fb_calls)} fallback tool call(s) from text.")
            msg["tool_calls"] = fb_calls

    msg["_clean_content"] = clean_content
    if thinking_text and clean_content:
        msg["content"] = f"[Thinking]\n{thinking_text[:4000]}\n\n[Response]\n{clean_content}"
    elif thinking_text:
        msg["content"] = thinking_text[:4000]
    else:
        msg["content"] = clean_content

    latency = time.perf_counter() - start_t
    return msg, latency, model_name, tps


async def warmup_models() -> None:
    """起動時に設定済みモデルを指定options (256k num_ctx等)・keep_aliveでVRAM/RAMへプリロードする"""
    models: list[str] = []
    for role in ("PM", "SE", "PG", "QA", "AUDITOR"):
        try:
            m = get_model_for_role(role)
            if m and m not in models:
                models.append(m)
        except RuntimeError:
            continue

    if not models:
        return

    chat_url = get_ollama_chat_url()
    options = get_ollama_options()
    keep_alive = get_ollama_keep_alive()
    timeout_sec = get_llm_timeout()
    timeout = aiohttp.ClientTimeout(total=timeout_sec, sock_read=timeout_sec)

    for model_name in models:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [],
            "stream": False,
        }
        if options:
            payload["options"] = options
        if keep_alive:
            payload["keep_alive"] = keep_alive

        system_logger.log(
            "LLM_CLIENT",
            f"[WARMUP] Preloading model={model_name} into VRAM/RAM (options={options}, keep_alive={keep_alive})...",
        )
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(chat_url, json=payload) as resp:
                    body = await resp.text()
                    if resp.status == 200:
                        system_logger.log("LLM_CLIENT", f"[WARMUP] Model={model_name} preloaded successfully.")
                    else:
                        system_logger.log(
                            "LLM_CLIENT",
                            f"[WARMUP] Failed to preload model={model_name} (HTTP {resp.status}): {body[:200]}",
                            level="WARN",
                        )
        except (TimeoutError, aiohttp.ClientError, OSError) as exc:
            system_logger.error("LLM_CLIENT", f"[WARMUP] Exception while preloading {model_name}: {exc}", exc)


async def _unload_models_for_heavy(keep_model_name: str) -> None:
    """
    keep_model_name 以外のロード済みモデル（軽量・重量すべて）を keep_alive=0 で確実にアンロードする。
    """
    from .config import get_ollama_ps_url

    models_to_unload: list[str] = []
    for r in ("PM", "SE", "PG", "QA", "AUDITOR"):
        for getter in (get_model_for_role, get_heavy_model_for_role):
            try:
                m = getter(r)
                if m and m != keep_model_name and m not in models_to_unload:
                    models_to_unload.append(m)
            except RuntimeError:
                continue

    chat_url = get_ollama_chat_url()
    ps_url = get_ollama_ps_url()
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(ps_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for m_info in data.get("models", []):
                        for key in ("name", "model"):
                            loaded_name = m_info.get(key)
                            if loaded_name and loaded_name != keep_model_name and loaded_name not in models_to_unload:
                                models_to_unload.append(loaded_name)
        except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError, OSError):
            pass

        for m in models_to_unload:
            try:
                system_logger.log("LLM_CLIENT", f"[HEAVY_PREP] Unloading '{m}' to free VRAM/RAM for '{keep_model_name}'...")
                async with session.post(
                    chat_url,
                    json={"model": m, "messages": [], "stream": False, "keep_alive": 0},
                ) as resp:
                    await resp.read()
            except (TimeoutError, aiohttp.ClientError, OSError) as exc:
                system_logger.log("LLM_CLIENT", f"[HEAVY_PREP] Unload warning for '{m}': {exc}", level="WARN")


_heavy_lock = asyncio.Lock()


async def generate_heavy_brain(
    role: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[str, str, float, str, float]:
    """
    Stage 2 (Pure Brain): 重量モデル (Qwen3-Coder-Next) をツール無しで1回だけ呼び出し、
    (thinking_text, clean_markdown, latency_sec, model_name, tps) を返す。
    実行中は全並列枠 (PM用枠含むすべて) を完全ロックして他モデルの同時ロードによるOOMを防ぎ、
    完了後は重量モデルを即座にアンロードしてロックを解放する。
    """
    model_name = get_heavy_model_for_role(role)
    light_model_name = get_model_for_role(role)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    prepared_msgs = _prepare_messages_for_ollama(messages)

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": prepared_msgs,
        "stream": True,
    }

    think_setting = get_heavy_ollama_think()
    if think_setting is not None:
        payload["think"] = think_setting

    options = get_heavy_ollama_options()
    if options:
        payload["options"] = options

    keep_alive = get_heavy_ollama_keep_alive()
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    elif model_name != light_model_name:
        payload["keep_alive"] = 0

    est_chars = len(json.dumps(prepared_msgs, ensure_ascii=False))
    est_input_tok = max(1, est_chars // 3)
    telemetry.update_role_tokens(role, est_input_tok)

    start_t = time.perf_counter()
    total_sem, bg_sem = _get_llm_semaphores()
    total_slots = max(1, _configured_parallel)
    bg_slots = max(1, _configured_parallel - 1)

    telemetry.set_heavy_mode(role, True)
    try:
        async with _heavy_lock:
            acquired_bg = 0
            acquired_total = 0
            try:
                for _ in range(bg_slots):
                    await bg_sem.acquire()
                    acquired_bg += 1
                for _ in range(total_slots):
                    await total_sem.acquire()
                    acquired_total += 1

                system_logger.log(
                    "LLM_CLIENT",
                    f"[{role}:HEAVY] All {total_slots} parallel slots locked exclusively. Preparing VRAM/RAM for {model_name}...",
                )
                await _unload_models_for_heavy(model_name)
                try:
                    msg, prompt_tok, eval_tok, eval_duration_ns, elapsed_req = await _execute_ollama_stream(
                        role, model_name, payload, est_input_tok
                    )
                finally:
                    if model_name != light_model_name:
                        await _unload_models_for_heavy(light_model_name)
            finally:
                for _ in range(acquired_total):
                    total_sem.release()
                for _ in range(acquired_bg):
                    bg_sem.release()
    finally:
        telemetry.set_heavy_mode(role, False)

    if prompt_tok == 0:
        prompt_tok = est_input_tok

    raw_content = msg.get("content") or ""
    explicit_thinking = msg.get("thinking") or ""
    if eval_tok == 0:
        eval_tok = max(1, len(raw_content + explicit_thinking) // 3)

    if eval_duration_ns > 0:
        tps = eval_tok / (eval_duration_ns / 1e9)
    else:
        tps = eval_tok / elapsed_req

    telemetry.update_role_tokens(role, prompt_tok + eval_tok, tps=tps)
    thinking_text, clean_content = extract_thinking_and_clean(raw_content, explicit_thinking)
    latency = time.perf_counter() - start_t
    return thinking_text, clean_content, latency, model_name, tps


def extract_fallback_tool_calls(content: str) -> list[dict[str, Any]]:
    """テキスト内に直接出力されたGemma4独自形式 (call:fn{...}) やJSON/タグ形式のツール呼び出しを抽出する"""
    if not content:
        return []

    calls: list[dict[str, Any]] = []

    # 1. Gemma4 ネイティブ構文: call:tool_name{key:"value", ...}
    for m in re.finditer(r"call:([a-zA-Z0-9_]+)\s*\{", content):
        fn_name = m.group(1)
        brace_start = m.end() - 1
        depth = 0
        in_str = False
        esc = False
        quote_ch = ""
        end_pos = -1
        for i in range(brace_start, len(content)):
            ch = content[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == quote_ch:
                    in_str = False
            else:
                if ch in ('"', "'"):
                    in_str = True
                    quote_ch = ch
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end_pos = i + 1
                        break
        raw_obj = content[brace_start:end_pos] if end_pos != -1 else content[brace_start:]
        parsed_args = _parse_json_lenient(raw_obj)
        if not isinstance(parsed_args, dict):
            parsed_args = {}
        calls.append({
            "id": f"gemma_call_{len(calls) + 1}",
            "type": "function",
            "function": {"name": fn_name, "arguments": parsed_args},
        })

    if calls:
        return calls

    # 2. <tool_call> タグおよび ```json コードブロック形式
    candidates: list[str] = []
    tag_matches = re.findall(
        r"(?:<tool_call>|<\|tool_call_start\|>)\s*(.*?)\s*(?:</tool_call>|<\|tool_call_end\|>)",
        content,
        flags=re.DOTALL,
    )
    candidates.extend(tag_matches)

    md_matches = re.findall(r"```(?:json|tool_call|python)?\s*(\{.*?\}|\[.*?\])\s*```", content, flags=re.DOTALL)
    candidates.extend(md_matches)

    stripped = content.strip()
    if not candidates and ((stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]"))):
        candidates.append(stripped)

    for idx, cand in enumerate(candidates, 1):
        parsed = _parse_json_lenient(cand)
        if not parsed:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if not isinstance(item, dict):
                continue
            fn_obj = item.get("function") if isinstance(item.get("function"), dict) else item
            fn_name = fn_obj.get("name") or fn_obj.get("tool") or fn_obj.get("tool_name")
            if not fn_name or not isinstance(fn_name, str):
                continue
            args = fn_obj.get("arguments", fn_obj.get("args", fn_obj.get("parameters", {})))
            if isinstance(args, str):
                args = _parse_json_lenient(args) or {}
            elif not isinstance(args, dict):
                args = {}
            calls.append({
                "id": f"fallback_call_{idx}",
                "type": "function",
                "function": {"name": fn_name, "arguments": args},
            })
    return calls