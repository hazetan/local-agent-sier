import json
import traceback
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import get_log_dir

JST = timezone(timedelta(hours=9))
MAX_LOG_BYTES = 1 * 1024 * 1024 # 1 MB


def now_jst_str() -> str:
    return datetime.now(UTC).astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")


def _rotate_if_needed(path: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size >= max_bytes:
        backup = path.with_suffix(".old.log")
        path.replace(backup)


class ErrorLogger:
    """エラーおよび重量級モデル失敗原因を明示的に記録する専用ロガー (logs/error.log)"""

    def __init__(self) -> None:
        self.log_path: Path = get_log_dir() / "error.log"

    def log_error(
        self,
        component: str,
        message: str,
        exc: BaseException | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        _rotate_if_needed(self.log_path)
        lines = [
            f"{'=' * 72}",
            f"[{now_jst_str()}] ❌ ERROR in [{component}]",
            f"REASON  : {message}",
        ]
        if context:
            lines.append(f"CONTEXT : {json.dumps(context, ensure_ascii=False)}")
        if exc:
            lines.append(f"EXC_TYPE: {type(exc).__name__}: {exc}")
            tb = traceback.format_exc().strip()
            if tb and tb != "NoneType: None":
                lines.append(f"TRACE   :\n{tb}")
        lines.append(f"{'=' * 72}\n")
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
            f.flush()

    def log_heavy_failure(
        self,
        role: str,
        model: str,
        stage: str,
        reason: str,
        diagnostics: dict[str, Any] | None = None,
        exc: BaseException | None = None,
    ) -> None:
        """重量級モデル (Qwen3-coder-next等) が失敗・不発となった具体的な原因を明示的に出力する"""
        ctx = {"role": role, "heavy_model": model, "stage": stage, **(diagnostics or {})}
        self.log_error(
            component=f"HEAVY_BRAIN:{role}",
            message=f"Heavy model '{model}' failed at [{stage}] -> {reason}",
            exc=exc,
            context=ctx,
        )


error_logger = ErrorLogger()


class RoleLogger:
    """役職（PM, SE, PG, QA, AUDITOR）ごとの最小限・高密度ログ出力クラス"""

    def __init__(self, role: str) -> None:
        self.role = role.upper()
        self.log_path: Path = get_log_dir() / f"{role.lower()}.log"

    def _write(self, block: str) -> None:
        _rotate_if_needed(self.log_path)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(block + "\n")
            f.flush()

    def log_phase_start(self, model: str, objective: str) -> None:
        obj_short = objective.replace("\n", " ")[:120]
        self._write(f"\n[{now_jst_str()}] ▶ START {self.role} ({model}) | {obj_short}")

    def log_step(
        self,
        step: int,
        thought: str,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        tool_output: str | None = None,
        latency_sec: float = 0.0,
        tps: float = 0.0,
    ) -> None:
        compact_args = {}
        if tool_args:
            for k, v in tool_args.items():
                s = str(v).replace("\n", " ")
                compact_args[k] = s[:80] + ".." if len(s) > 80 else s

        tps_str = f" {tps:.1f}t/s" if tps > 0 else ""
        lines = [f"[Step {step} | {latency_sec:.1f}s{tps_str}]"]
        if thought:
            clean_t = thought.replace("\n", " ").strip()
            lines.append(f"  Thought: {clean_t[:180]}{'..' if len(clean_t) > 180 else ''}")
        if tool_name:
            lines.append(f"  Tool   : {tool_name}({json.dumps(compact_args, ensure_ascii=False)})")
        if tool_output:
            clean_o = tool_output.replace("\n", " ").strip()
            lines.append(f"  Result : {clean_o[:220]}{'..' if len(clean_o) > 220 else ''}")

        self._write("\n".join(lines))

    def log_error(self, error_msg: str, exc: BaseException | None = None) -> None:
        self._write(f"[{now_jst_str()}] ❌ ERROR ({self.role}): {error_msg}")
        error_logger.log_error(f"ROLE:{self.role}", error_msg, exc=exc)


def read_recent_logs(roles: list[str], max_chars_per_role: int = 2500) -> str:
    """Auditor用に各ロールの最新ログ末尾を収集する"""
    sections: list[str] = []
    log_dir = get_log_dir()
    for role in roles:
        path = log_dir / f"{role.lower()}.log"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        tail = text[-max_chars_per_role:] if len(text) > max_chars_per_role else text
        sections.append(f"=== RECENT LOG: {role.upper()} ===\n{tail}")
    return "\n\n".join(sections) if sections else "(No logs recorded yet)"


class SystemLogger:
    """起動ごとにリセットされるシステム全体の最小限イベントロガー (logs/system_debug.log)"""

    def __init__(self) -> None:
        self.log_path: Path = get_log_dir() / "system_debug.log"

    def reset(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        header = f"[{now_jst_str()}] 🚀 SYSTEM DEBUG LOG INITIALIZED\n"
        self.log_path.write_text(header, encoding="utf-8")

    def log(self, component: str, message: str, level: str = "INFO") -> None:
        _rotate_if_needed(self.log_path)
        line = f"[{now_jst_str()}] [{level:<5}] [{component:<12}] {message}\n"
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

    def error(
        self,
        component: str,
        message: str,
        exc: BaseException | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.log(component, message, level="ERROR")
        error_logger.log_error(component, message, exc=exc, context=context)


system_logger = SystemLogger()