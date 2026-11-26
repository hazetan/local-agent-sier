import asyncio

import uvicorn
from core.config import (
    get_discord_enable,
    get_discord_token,
    get_reset_workspace_on_startup,
    get_web_enable,
    get_web_host,
    get_web_port,
)
from core.logger import system_logger
from fastapi import FastAPI
from sandbox.container import ensure_sandbox_container
from sandbox.tools import init_workspace_files
from web_app.router import sier_lifespan, sier_router


def create_web_app() -> FastAPI:
    app = FastAPI(title="SIer Multi-Agent Web API", lifespan=sier_lifespan)
    app.include_router(sier_router)
    return app


async def _run_both_discord_and_web() -> None:
    """DISCORD_ENABLE=true かつ WEB_ENABLE=true の場合に同一イベントループで両方起動する"""
    from discord_app.bot import bot

    app = create_web_app()
    config = uvicorn.Config(app, host=get_web_host(), port=get_web_port(), log_level="info")
    server = uvicorn.Server(config)

    web_task = asyncio.create_task(server.serve(), name="uvicorn_web_server")
    discord_task = asyncio.create_task(bot.start(get_discord_token()), name="discord_bot_client")
    try:
        await asyncio.gather(web_task, discord_task)
    finally:
        if not bot.is_closed():
            await bot.close()


def main() -> None:
    system_logger.reset()
    discord_enabled = get_discord_enable()
    web_enabled = get_web_enable()

    if not discord_enabled and not web_enabled:
        raise RuntimeError("Both DISCORD_ENABLE and WEB_ENABLE are false in .env. Enable at least one.")

    system_logger.log(
        "MAIN",
        f"Starting system initialization (discord={discord_enabled}, web={web_enabled})...",
    )

    reset_ws = get_reset_workspace_on_startup()
    print(f"[Init] Preparing ~/ (workspace) standard SIer documents (reset={reset_ws})...")
    init_workspace_files(reset_on_restart=reset_ws)
    system_logger.log("MAIN", f"Workspace standard documents checked (reset={reset_ws}).")

    print("[Init] Starting clean Docker sandbox container mounted at ~/ (/root)...")
    c_name = ensure_sandbox_container(recreate=True)
    system_logger.log("MAIN", f"Docker sandbox container ready: {c_name}")

    if web_enabled and not discord_enabled:
        host = get_web_host()
        port = get_web_port()
        print(f"[Init] Launching Web API Server only on http://{host}:{port} ...")
        system_logger.log("MAIN", f"Starting Uvicorn Web API on {host}:{port}")
        uvicorn.run(create_web_app(), host=host, port=port)
    elif discord_enabled and not web_enabled:
        from discord_app.bot import bot

        print("[Init] Launching Discord Bot only...")
        system_logger.log("MAIN", "Calling bot.run()...")
        bot.run(get_discord_token())
    else:
        host = get_web_host()
        port = get_web_port()
        print(f"[Init] Launching BOTH Discord Bot and Web API Server (http://{host}:{port})...")
        system_logger.log("MAIN", f"Starting hybrid mode (Discord + Web API on {host}:{port})")
        asyncio.run(_run_both_discord_and_web())


if __name__ == "__main__":
    main()