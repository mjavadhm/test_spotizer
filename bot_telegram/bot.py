import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()  # must run before importing handlers (they read env at import)

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.aiohttp import AiohttpSession  # noqa: E402
from aiogram.client.telegram import TelegramAPIServer  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

import handlers  # noqa: E402


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is not set")

    # optional local Bot API server (for files > 50MB), e.g. http://localhost:8081
    session = None
    api_server = os.getenv("TELEGRAM_API_SERVER")
    if api_server:
        session = AiohttpSession(api=TelegramAPIServer.from_base(api_server))

    bot = Bot(token=token, session=session, default=DefaultBotProperties(parse_mode=None))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(handlers.router)

    # background task: notify followers about new releases
    checker = asyncio.create_task(handlers.subscription_checker(bot))

    try:
        await dp.start_polling(bot)
    finally:
        checker.cancel()
        await handlers.api.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())
