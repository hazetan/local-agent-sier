from core.config import get_discord_token, get_reset_workspace_on_startup
from core.logger import system_logger
from discord_app.bot import bot
from sandbox.container import ensure_sandbox_container
from sandbox.tools import init_workspace_files


def main() -> None:
    system_logger.reset()
    system_logger.log("MAIN", "Starting system initialization...")

    reset_ws = get_reset_workspace_on_startup()
    print(f"[Init] Preparing ~/ (workspace) standard SIer documents (reset={reset_ws})...")
    init_workspace_files(reset_on_restart=reset_ws)
    system_logger.log("MAIN", f"Workspace standard documents checked (reset={reset_ws}).")

    print("[Init] Starting clean Docker sandbox container mounted at ~/ (/root)...")
    c_name = ensure_sandbox_container(recreate=True)
    system_logger.log("MAIN", f"Docker sandbox container ready: {c_name}")

    print("[Init] Launching Discord Bot & SIer Multi-Agent Pipeline...")
    system_logger.log("MAIN", "Calling bot.run()...")
    bot.run(get_discord_token())


if __name__ == "__main__":
    main()