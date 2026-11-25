import ast
import json
import re
from typing import Any

from .config import get_think_prompt_boost

HIGH_THINKING_DIRECTIVE = (
    "\n\n【思考とツール呼び出しの鉄則】\n"
    "思考（Thinking）は3〜5行以内で簡潔に要点のみ整理し、長文の自己確認は行わず即座にツール（Function Calling）を呼び出してください。"
)

NO_THINKING_DIRECTIVE = (
    "\n\n【Direct Tool Execution Mode (Thinking OFF / 非思考モデル専用鉄則)】\n"
    "現在 Thinking 機能は無効（非対応モデル）です。思考タグ（`<think>` 等）、事前の推論テキスト、計画の説明、"
    "修正コード全文のプレビュー等を本文（content）に一切出力してはいけません。\n"
    "前置きや独り言を完全に省き、1トークン目から直ちにツール呼び出し（Function Calling）のみを実行してください。"
)


def _is_thinking_enabled(think_setting: bool | str | None) -> bool:
    if think_setting is None or think_setting is False:
        return False
    if isinstance(think_setting, str):
        return think_setting.lower() not in ("false", "0", "no", "off", "none", "")
    return True


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


def _prepare_messages_for_ollama(
    messages: list[dict[str, Any]],
    *,
    think_setting: bool | str | None = None,
) -> list[dict[str, Any]]:
    """
    Ollama /api/chat 向けにメッセージ配列を正規化する。
    Thinking OFF（非対応モデル）の場合は NO_THINKING_DIRECTIVE を注入し、
    余計なテキスト生成による時間浪費やツール不発を防ぐ。
    """
    prepared: list[dict[str, Any]] = []
    boost = get_think_prompt_boost()
    thinking_on = _is_thinking_enabled(think_setting)

    for idx, m in enumerate(messages):
        role = m.get("role", "user")
        clean_c = m.get("_clean_content") if "_clean_content" in m else (m.get("content") or "")
        msg_copy: dict[str, Any] = {
            "role": role,
            "content": clean_c,
        }
        if idx == 0 and role == "system":
            if thinking_on and boost:
                msg_copy["content"] += HIGH_THINKING_DIRECTIVE
            elif not thinking_on:
                msg_copy["content"] += NO_THINKING_DIRECTIVE

        if role == "tool":
            tool_name = m.get("tool_name") or m.get("name")
            if tool_name:
                msg_copy["tool_name"] = tool_name
                msg_copy["name"] = tool_name
            if "tool_call_id" in m:
                msg_copy["tool_call_id"] = m["tool_call_id"]

        if tool_calls := m.get("tool_calls"):
            norm_calls = []
            for tc in tool_calls:
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


def extract_fallback_tool_calls(content: str) -> list[dict[str, Any]]:
    """テキスト内に直接出力されたGemma4/Qwen3独自形式 (call:fn{...}) や関数呼び出し形式のツール呼び出しを抽出する"""
    if not content:
        return []

    calls: list[dict[str, Any]] = []

    # 1. ネイティブ構文: call:tool_name{...} または tool_name({...})
    for m in re.finditer(
        r"(?:call:)?\b(invoke_professional|invoke_heavy_brain|complete_phase|list_files|read_file|write_files|replace_lines|add_lines_after|delete_lines|delete_files|run_command|web_search|fetch_url)\s*\(?\s*\{",
        content,
    ):
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