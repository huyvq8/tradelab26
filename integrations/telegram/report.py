import asyncio
from telegram import Bot


def send_report(token: str, chat_id: str | int, text: str) -> None:
    """Send a text message to Telegram. chat_id: string or int (e.g. -5292958714 for group)."""
    cid = int(chat_id) if isinstance(chat_id, str) and chat_id.lstrip("-").isdigit() else chat_id

    async def _send() -> None:
        bot = Bot(token=token.strip())
        await bot.send_message(chat_id=cid, text=text[:4096])

    try:
        asyncio.run(_send())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_send())
        loop.close()
