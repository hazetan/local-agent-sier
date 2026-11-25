from collections import deque
from dataclasses import dataclass, field
from typing import Any

ROLES = ("PM", "SE", "PG", "QA", "AUDITOR")



@dataclass
class BranchEvent:
    role: str
    action: str
    result: str


@dataclass
class SystemTelemetry:
    active_roles: set[str] = field(default_factory=set)
    heavy_active_roles: set[str] = field(default_factory=set)
    role_ctx_tokens: dict[str, int] = field(default_factory=lambda: {r: 0 for r in ROLES})
    role_tps: dict[str, float] = field(default_factory=lambda: {r: 0.0 for r in ROLES})
    role_branches: dict[str, deque[BranchEvent]] = field(
        default_factory=lambda: {r: deque(maxlen=3) for r in ROLES}
    )

    # log-raw 用の最新ステップ生データ
    raw_role: str = "IDLE"
    raw_model: str = "-"
    raw_step: int = 0
    raw_max_steps: int = 0
    raw_latency: float = 0.0
    raw_tps: float = 0.0
    raw_thought: str = "(待機中...)"
    raw_tool_name: str = "-"
    raw_tool_args: str = "-"
    raw_tool_output: str = "(まだ出力はありません)"
    on_raw_update: Any = None

    @property
    def active_role_label(self) -> str:
        if not self.active_roles:
            return "IDLE"
        labels = [
            f"{r}:PRO" if r in self.heavy_active_roles else r
            for r in sorted(self.active_roles)
        ]
        return ",".join(labels)

    def set_heavy_mode(self, role: str, active: bool) -> None:
        r = role.upper()
        if active:
            self.heavy_active_roles.add(r)
        else:
            self.heavy_active_roles.discard(r)

    def start_role(self, role: str) -> None:
        r = role.upper()
        self.active_roles.add(r)
        if r in self.role_branches:
            self.role_branches[r].clear()

    def end_role(self, role: str) -> None:
        self.active_roles.discard(role.upper())

    def set_active_role(self, role: str) -> None:
        if role.upper() == "IDLE":
            self.active_roles.clear()
        else:
            self.start_role(role)

    def update_role_tokens(self, role: str, used_tokens: int, tps: float | None = None) -> None:
        r = role.upper()
        if used_tokens > 0:
            self.role_ctx_tokens[r] = used_tokens
        if tps is not None:
            self.role_tps[r] = tps

    def add_branch_event(self, role: str, tool_name: str, args: dict[str, Any], output: str) -> None:
        r = role.upper()
        if tool_name == "run_command":
            target = args.get("command", "")
        elif "path" in args:
            target = args.get("path", "")
        elif "directory" in args:
            target = args.get("directory", "")
        else:
            target = str(args)[:40]

        first_line = output.strip().splitlines()[0] if output.strip() else "(ok)"
        self.role_branches.setdefault(r, deque(maxlen=3)).append(
            BranchEvent(
                role=r,
                action=f"{tool_name}: {target}"[:52],
                result=first_line[:52],
            )
        )

    def record_raw_step(
        self,
        role: str,
        model: str,
        step: int,
        max_steps: int,
        latency: float,
        thought: str,
        tool_name: str = "-",
        tool_args: dict[str, Any] | None = None,
        tool_output: str = "",
        tps: float = 0.0,
    ) -> None:
        import json

        self.raw_role = role.upper()
        self.raw_model = model
        self.raw_step = step
        self.raw_max_steps = max_steps
        self.raw_latency = latency
        self.raw_tps = tps
        self.raw_thought = thought or "(思考テキストなし・直接ツール呼び出し)"
        self.raw_tool_name = tool_name
        self.raw_tool_args = json.dumps(tool_args or {}, ensure_ascii=False)
        self.raw_tool_output = tool_output or "(出力なし)"


telemetry = SystemTelemetry()