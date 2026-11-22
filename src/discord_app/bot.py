import asyncio
import re
from pathlib import Path

import discord

from agents.auditor_agent import continuous_auditor_loop
from agents.dev_pipeline import continuous_dev_pipeline_loop
from agents.pm_agent import handle_client_message
from core.config import (
    ENV_PATH,
    get_desktop_metrics_url,
    get_desktop_ram_gb,
    get_desktop_vram_gb,
    get_monitored_channel_ids,
    get_ollama_base_url,
    get_server_ctx_size,
)
from core.llm_client import warmup_models
from core.logger import system_logger
from core.nas_server import nas_server

from .status_monitor import LiveStatusMonitor

MAX_MSG_LEN = 1900

class SIerBotClient(discord.Client):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.status_monitor = LiveStatusMonitor(self)

    async def close(self) -> None:
        print("[Shutdown] Stopping background tasks, NAS, and removing live status boxes...")
        for task in _background_tasks:
            if not task.done():
                task.cancel()
        if _background_tasks:
            await asyncio.gather(*_background_tasks, return_exceptions=True)
            _background_tasks.clear()
        await nas_server.stop()
        await self.status_monitor.remove_box()
        await super().close()

intents = discord.Intents.default()
intents.message_content = True
bot = SIerBotClient(intents=intents)

_background_tasks: list[asyncio.Task] = []


async def send_chunked(channel: discord.abc.Messageable, text: str, reply_to: discord.Message | None = None) -> None:
    chunks = [text[i : i + MAX_MSG_LEN] for i in range(0, len(text), MAX_MSG_LEN)] or ["(Empty response)"]
    for idx, chunk in enumerate(chunks):
        if idx == 0 and reply_to is not None:
            await reply_to.reply(chunk)
        else:
            await channel.send(chunk)


def is_monitored_channel(channel: discord.abc.Messageable) -> bool:
    """テキストチャンネルID、またはフォーラム／スレッドの親チャンネルIDが .env に含まれるか判定"""
    monitored_ids = get_monitored_channel_ids()
    ch_id = getattr(channel, "id", None)
    parent_id = getattr(channel, "parent_id", None)
    return (ch_id in monitored_ids) or (parent_id in monitored_ids)


async def broadcast_to_primary_channel(message_text: str) -> None:
    """開発ライン（SE/PG/QA）の完了報告を監視対象の先頭テキストチャンネルへ通知する"""
    for ch_id in get_monitored_channel_ids():
        ch = bot.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            await send_chunked(ch, message_text)
            break

async def on_pipeline_phase_done(message_text: str) -> None:
    """開発ラインの1フェーズ完了時にレポート送信＆ステータスBoxを更新する"""
    await broadcast_to_primary_channel(message_text)
    await bot.status_monitor.refresh_box()


async def _warmup_and_refresh() -> None:
    """起動時にモデルをVRAMへプリロードし、完了直後にステータスBoxを更新する"""
    await warmup_models()
    await bot.status_monitor.refresh_box()


@bot.event
async def on_ready() -> None:
    monitored = get_monitored_channel_ids()
    print(f"Logged in as {bot.user} | Monitored Channels: {monitored}")
    system_logger.log(
        "DISCORD_BOT",
        f"on_ready: user={bot.user} | env_exists={ENV_PATH.exists()} ({ENV_PATH}) | "
        f"ollama={get_ollama_base_url()} | ctx={get_server_ctx_size()} | "
        f"vram_cfg={get_desktop_vram_gb()} | ram_cfg={get_desktop_ram_gb()} | "
        f"metrics_url={get_desktop_metrics_url()} | monitored={monitored}",
    )
    if not _background_tasks:
        await nas_server.start()
        _background_tasks.append(
            asyncio.create_task(_warmup_and_refresh())
        )
        _background_tasks.append(
            asyncio.create_task(bot.status_monitor.periodic_update_loop())
        )
        _background_tasks.append(
            asyncio.create_task(continuous_dev_pipeline_loop(on_phase_complete=on_pipeline_phase_done))
        )
        _background_tasks.append(
            asyncio.create_task(continuous_auditor_loop())
        )
        system_logger.log("DISCORD_BOT", "Background tasks (Warmup, StatusMonitor, DevPipeline, Auditor) launched.")


MAX_ATTACHMENT_BYTES = 512 * 1024  # 1ファイルあたり最大 512 KB
TEXT_ATTACHMENT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".py", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh",
    ".rs", ".go", ".js", ".ts", ".jsx", ".tsx", ".java", ".cs",
    ".sh", ".bash", ".zsh", ".bat", ".ps1",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".html", ".htm", ".css", ".xml", ".sql",
    ".makefile", ".mk", ".cmake", ".dockerfile", ".scad",
}


async def _extract_text_attachments(message: discord.Message) -> list[str]:
    """Discordメッセージに添付されたテキスト系ファイルを読み取って整形リストとして返す"""
    sections: list[str] = []
    for att in message.attachments:
        fname = att.filename or "attachment.txt"
        ext = Path(fname).suffix.lower()
        ctype = (att.content_type or "").lower()
        is_text_type = (
            ext in TEXT_ATTACHMENT_EXTENSIONS
            or fname.lower() in ("makefile", "dockerfile", "cmakelists.txt")
            or ctype.startswith("text/")
            or "json" in ctype
            or "xml" in ctype
            or "yaml" in ctype
            or "javascript" in ctype
        )
        if not is_text_type:
            system_logger.log("DISCORD_BOT", f"Skipped non-text attachment: {fname} (content_type={ctype})")
            continue

        if att.size > MAX_ATTACHMENT_BYTES:
            sections.append(
                f"=== 添付ファイル: {fname} (サイズ超過: {att.size:,} bytes > 上限 {MAX_ATTACHMENT_BYTES:,} bytes のため省略) ==="
            )
            system_logger.log("DISCORD_BOT", f"Skipped oversized attachment: {fname} ({att.size} bytes)", level="WARN")
            continue

        try:
            raw_bytes = await att.read()
        except (discord.DiscordException, OSError) as exc:
            system_logger.error("DISCORD_BOT", f"Failed to read attachment {fname}: {exc}", exc)
            continue

        if b"\x00" in raw_bytes[:1024]:
            system_logger.log("DISCORD_BOT", f"Skipped binary attachment: {fname}")
            continue

        try:
            text_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text_content = raw_bytes.decode("cp932", errors="replace")

        sections.append(f"=== 添付ファイル: {fname} ===\n{text_content.strip()}")
        system_logger.log("DISCORD_BOT", f"Loaded text attachment: {fname} ({len(text_content)} chars)")

    return sections


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    ch_id = getattr(message.channel, "id", None)
    parent_id = getattr(message.channel, "parent_id", None)
    system_logger.log(
        "DISCORD_BOT",
        f"on_message event: author={message.author} | ch_id={ch_id} | parent_id={parent_id} | "
        f"content={message.content[:60]!r} | attachments={len(message.attachments)}",
    )

    if not is_monitored_channel(message.channel):
        system_logger.log("DISCORD_BOT", f"Ignored: channel {ch_id} (parent={parent_id}) is not in monitored_channels.")
        return

    other_mentions = [u for u in message.mentions if bot.user and u.id != bot.user.id]
    if other_mentions or message.mention_everyone:
        system_logger.log("DISCORD_BOT", f"Ignored: message contains other mentions ({other_mentions}).")
        return

    clean_text = re.sub(r"<@!?\d+>", "", message.content).strip()
    attachment_sections = await _extract_text_attachments(message) if message.attachments else []

    if attachment_sections:
        attachments_block = "\n\n".join(attachment_sections)
        clean_text = f"{clean_text}\n\n{attachments_block}".strip() if clean_text else attachments_block

    if not clean_text:
        system_logger.log("DISCORD_BOT", "Ignored: clean_text and text attachments are both empty.")
        return

    system_logger.log(
        "DISCORD_BOT",
        f"Accepted client message in ch={ch_id} (total_chars={len(clean_text)}, attachments={len(attachment_sections)}): "
        f"{clean_text[:80]!r}",
    )
    async with message.channel.typing():
        reply_text, zip_files = await handle_client_message(clean_text)
        await send_chunked(message.channel, reply_text, reply_to=message)

        for zp in zip_files:
            if zp.stat().st_size < 25 * 1024 * 1024:
                await message.channel.send(file=discord.File(str(zp)))
    system_logger.log("DISCORD_BOT", f"Finished replying to client message in ch={ch_id}.")