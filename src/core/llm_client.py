import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
)
from .llm_parser import (
    _has_repetition_loop,
    _is_thinking_enabled,
    _prepare_messages_for_ollama,
    extract_fallback_tool_calls,
    extract_thinking_and_clean,
)
from .logger import error_logger, system_logger
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


async def _diagnose_ollama_state() -> dict[str, Any]:
    """エラー発生時に Ollama /api/ps のロード状況を取得して診断情報として返す"""
    from .config import get_ollama_ps_url

    ps_url = get_ollama_ps_url()
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session,
            session.get(ps_url) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                loaded = [
                    {
                        "name": m.get("name"),
                        "size_gb": round(m.get("size", 0) / (1024**3), 2),
                        "vram_gb": round(m.get("size_vram", 0) / (1024**3), 2),
                    }
                    for m in data.get("models", [])
                ]
                return {"ollama_ps_loaded": loaded}
            return {"ollama_ps_http_status": resp.status}
    except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
        return {"ollama_ps_error": f"{type(exc).__name__}: {exc}"}


def _recover_payload_from_ollama_error(
    role_label: str,
    model_name: str,
    status: int,
    err_body: str,
    payload: dict[str, Any],
) -> bool:
    """
    OllamaのHTTP 400/500エラーからパラメータ由来の問題を検知してpayloadを自動修正する。
    修正してリトライ可能な場合は True を返し、WARNログに記録する。
    """
    err_low = err_body.lower()
    opts: dict[str, Any] = payload.get("options") or {}

    # 1. think パラメータ由来のエラー (文字列非対応 or thinking機能非対応)
    if "think" in payload and (status == 400 or "think" in err_low):
        cur_think = payload.get("think")
        if isinstance(cur_think, str) and "does not support thinking" not in err_low:
            payload["think"] = True
            system_logger.log(
                "LLM_CLIENT",
                f"[{role_label}] Model '{model_name}' rejected think={cur_think!r} (HTTP {status}). Auto-adjusted to think=True.",
                level="WARN",
            )
            return True
        payload.pop("think", None)
        system_logger.log(
            "LLM_CLIENT",
            f"[{role_label}] Model '{model_name}' does not support 'think' parameter (HTTP {status}). Auto-removed 'think'.",
            level="WARN",
        )
        return True

    # 2. CUDA / VRAM OOM またはコンテキスト初期化失敗 (num_gpu / num_ctx / num_batch の自動縮退)
    is_oom = any(
        kw in err_low
        for kw in (
            "out of memory",
            "cudamalloc failed",
            "failed to allocate",
            "failed to initialize the context",
            "ggml_gallocr",
        )
    )
    if is_oom and opts:
        if "num_gpu" in opts:
            removed_gpu = opts.pop("num_gpu")
            system_logger.log(
                "LLM_CLIENT",
                f"[{role_label}] VRAM OOM detected on '{model_name}' with num_gpu={removed_gpu}. "
                "Auto-removed 'num_gpu' to enable Ollama automatic VRAM/RAM offloading.",
                level="WARN",
            )
            return True

        cur_ctx = int(opts.get("num_ctx") or 0)
        if cur_ctx > 8192:
            new_ctx = max(8192, cur_ctx // 2)
            opts["num_ctx"] = new_ctx
            if int(opts.get("num_batch") or 0) > 256:
                opts["num_batch"] = 256
            system_logger.log(
                "LLM_CLIENT",
                f"[{role_label}] VRAM/RAM OOM detected on '{model_name}'. "
                f"Auto-reduced num_ctx ({cur_ctx} -> {new_ctx}) and capped num_batch.",
                level="WARN",
            )
            return True

    # 3. 個別の無効・非対応 options キーの指定エラー
    if opts:
        for opt_key in list(opts.keys()):
            if opt_key.lower() in err_low:
                removed_val = opts.pop(opt_key)
                system_logger.log(
                    "LLM_CLIENT",
                    f"[{role_label}] Invalid option '{opt_key}={removed_val}' rejected by Ollama (HTTP {status}). Auto-removed '{opt_key}'.",
                    level="WARN",
                )
                return True

    # 4. keep_alive フォーマットエラー
    if "keep_alive" in payload and "keep_alive" in err_low:
        bad_ka = payload.pop("keep_alive", None)
        system_logger.log(
            "LLM_CLIENT",
            f"[{role_label}] Invalid keep_alive={bad_ka!r} rejected by Ollama. Auto-removed 'keep_alive'.",
            level="WARN",
        )
        return True

    # 5. tools 非対応モデルのフォールバック (テキスト上のFallback Tool Call抽出へ委譲)
    if "tools" in payload and status == 400 and ("does not support tools" in err_low or "tool" in err_low):
        payload.pop("tools", None)
        system_logger.log(
            "LLM_CLIENT",
            f"[{role_label}] Model '{model_name}' does not support native 'tools' API (HTTP 400). "
            "Auto-removed 'tools' and falling back to text tool-call extraction.",
            level="WARN",
        )
        return True

    return False


async def _execute_ollama_stream(
    role: str,
    model_name: str,
    payload: dict[str, Any],
    est_input_tok: int,
) -> tuple[dict[str, Any], int, int, int, float]:
    """Ollama /api/chat をストリーミング受信し、パラメータやVRAM由来のエラー時は自動補正してリトライする"""
    is_heavy = role.upper() in telemetry.heavy_active_roles
    role_label = f"{role.upper()}:HEAVY" if is_heavy else role.upper()

    base_timeout_sec = get_llm_timeout()
    read_timeout_sec = max(base_timeout_sec, 1800) if is_heavy else base_timeout_sec
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=read_timeout_sec)

    granularity = get_stream_granularity_tokens()
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
        f"[{role_label}] Start {model_name} (in_tok~{est_input_tok}, ctx={payload.get('options', {}).get('num_ctx')})",
    )

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            resp = await session.post(chat_url, json=payload)

            retry_attempts = 0
            while resp.status in (400, 422, 500) and retry_attempts < 5:
                err_body = await resp.text()
                status_code = resp.status
                if _recover_payload_from_ollama_error(role_label, model_name, status_code, err_body, payload):
                    await resp.release()
                    retry_attempts += 1
                    await asyncio.sleep(1.0)
                    req_start_t = time.perf_counter()
                    resp = await session.post(chat_url, json=payload)
                else:
                    break

            async with resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    diag = await _diagnose_ollama_state()
                    diag.update({
                        "http_status": resp.status,
                        "ollama_response": err_text[:500],
                        "options": payload.get("options"),
                        "est_input_tok": est_input_tok,
                    })
                    if is_heavy:
                        error_logger.log_heavy_failure(
                            role, model_name, "HTTP_RESPONSE",
                            f"Ollama returned HTTP {resp.status}: {err_text[:300]}",
                            diagnostics=diag,
                        )
                    else:
                        error_logger.log_error("LLM_CLIENT", f"[{role_label}] HTTP {resp.status}: {err_text[:300]}", context=diag)
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
                        err_msg = str(data["error"])
                        diag = await _diagnose_ollama_state()
                        diag.update({"options": payload.get("options"), "chunk_count": chunk_count})
                        if is_heavy:
                            error_logger.log_heavy_failure(
                                role, model_name, "STREAM_ERROR", f"Ollama stream error: {err_msg}", diagnostics=diag
                            )
                        else:
                            error_logger.log_error("LLM_CLIENT", f"[{role_label}] Stream error: {err_msg}", context=diag)
                        raise RuntimeError(f"Ollama Stream Error ({model_name}): {err_msg}")

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
                        if chunk_count % 8 == 0:
                            tail_c = "".join(acc_content[-60:])
                            tail_t = "".join(acc_thinking[-60:])
                            if _has_repetition_loop(tail_c) or _has_repetition_loop(tail_t):
                                system_logger.log(
                                    "LLM_CLIENT",
                                    f"[{role_label}] Repetition loop aborted at chunk={chunk_count}.",
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
    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
        elapsed_err = time.perf_counter() - req_start_t
        diag = await _diagnose_ollama_state()
        diag.update({
            "elapsed_sec": round(elapsed_err, 1),
            "read_timeout_sec": read_timeout_sec,
            "chunks_received": chunk_count,
            "options": payload.get("options"),
        })
        if is_heavy:
            error_logger.log_heavy_failure(
                role, model_name, "CONNECTION_OR_TIMEOUT",
                f"Connection/Timeout after {elapsed_err:.1f}s (chunks={chunk_count}): {type(exc).__name__}: {exc}",
                diagnostics=diag,
                exc=exc,
            )
        else:
            error_logger.log_error(
                "LLM_CLIENT",
                f"[{role_label}] Network/Timeout error after {elapsed_err:.1f}s: {exc}",
                exc=exc,
                context=diag,
            )
        raise

    elapsed_req = max(0.001, time.perf_counter() - req_start_t)
    system_logger.log(
        "LLM_CLIENT",
        f"[{role_label}] Done in {elapsed_req:.1f}s (in={prompt_tok}, out={eval_tok}, tools={len(acc_tool_calls)})",
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
    """PM優先枠付きの並列制御と粒度可変ストリーミングで Ollama /api/chat を実行する"""
    model_name = get_model_for_role(role)
    think_setting = get_ollama_think()
    prepared_msgs = _prepare_messages_for_ollama(messages, think_setting=think_setting)
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": prepared_msgs,
        "stream": True,
    }

    if _is_thinking_enabled(think_setting):
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
        async with total_sem:
            return await _execute_ollama_stream(role, model_name, payload, est_input_tok)

    if is_pm:
        msg, prompt_tok, eval_tok, eval_duration_ns, elapsed_req = await _run_locked()
    else:
        async with bg_sem:
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
        fb_calls = (
            extract_fallback_tool_calls(clean_content)
            or extract_fallback_tool_calls(raw_content)
            or extract_fallback_tool_calls(thinking_text)
        )
        if fb_calls:
            msg["tool_calls"] = fb_calls

    msg["_clean_content"] = clean_content
    if thinking_text and clean_content:
        msg["content"] = f"[Thinking]\n{thinking_text[:1500]}\n\n[Response]\n{clean_content}"
    elif thinking_text:
        msg["content"] = thinking_text[:1500]
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
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(chat_url, json=payload) as resp,
            ):
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


@asynccontextmanager
async def professional_session(role: str) -> AsyncIterator[str]:
    """
    Professional (Qwen3-Coder-Next) が最大512ステップ連続稼働する間、
    排他ロックを保持して軽量モデルをアンロードし、VRAMスワップゼロで常駐させるコンテキストマネージャ。
    """
    try:
        model_name = get_heavy_model_for_role(role)
        light_model_name = get_model_for_role(role)
    except RuntimeError as exc:
        error_logger.log_heavy_failure(role, "UNCONFIGURED", "CONFIG_LOAD", str(exc), exc=exc)
        raise

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
                    f"[{role}:PRO] Exclusive lock acquired. Unloading light models for '{model_name}'...",
                )
                await _unload_models_for_heavy(model_name)
                try:
                    yield model_name
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


async def professional_chat_step(
    role: str,
    model_name: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], float, str, float]:
    """
    professional_session 内で呼び出される Qwen3-Coder-Next 用の1ステップ推論関数。
    HEAVY_THINK=false (または未設定) 時は Thinking 非対応プロンプトへ切り替え、think パラメータを送らない。
    """
    think_setting = get_heavy_ollama_think()
    prepared_msgs = _prepare_messages_for_ollama(messages, think_setting=think_setting)
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": prepared_msgs,
        "stream": True,
    }

    if _is_thinking_enabled(think_setting):
        payload["think"] = think_setting

    options = get_heavy_ollama_options()
    if options:
        payload["options"] = options

    keep_alive = get_heavy_ollama_keep_alive() or "30m"
    payload["keep_alive"] = keep_alive

    if tools:
        payload["tools"] = tools

    est_chars = len(json.dumps(prepared_msgs, ensure_ascii=False))
    if tools:
        est_chars += len(json.dumps(tools, ensure_ascii=False))
    est_input_tok = max(1, est_chars // 3)
    telemetry.update_role_tokens(role, est_input_tok)

    start_t = time.perf_counter()
    msg, prompt_tok, eval_tok, eval_duration_ns, elapsed_req = await _execute_ollama_stream(
        role, model_name, payload, est_input_tok
    )

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
        fb_calls = (
            extract_fallback_tool_calls(clean_content)
            or extract_fallback_tool_calls(raw_content)
            or extract_fallback_tool_calls(thinking_text)
        )
        if fb_calls:
            msg["tool_calls"] = fb_calls

    msg["_clean_content"] = clean_content
    if thinking_text and clean_content:
        msg["content"] = f"[Thinking]\n{thinking_text[:1500]}\n\n[Response]\n{clean_content}"
    elif thinking_text:
        msg["content"] = thinking_text[:1500]
    else:
        msg["content"] = clean_content

    latency = time.perf_counter() - start_t
    return msg, latency, model_name, tps