import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from transmission_rpc.error import TransmissionError

from remotor.auth import AuthMiddleware
from remotor.config import Settings
from remotor.db import Database
from remotor.handlers import setup_routers
from remotor.reporter import run_reporter
from remotor.transmission import Transmission, safe_error

log = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("transmission-rpc").setLevel(logging.WARNING)
    settings = Settings()
    database = Database(settings.db_path)
    await database.connect()
    transmission = Transmission(settings)
    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(AuthMiddleware(set(settings.allowed_user_ids)))
    setup_routers(dispatcher)
    tasks: list[asyncio.Task[None]] = []

    async def on_startup() -> None:
        profile = await database.get_profile()
        try:
            await transmission.apply_session(profile)
        except TransmissionError as exc:
            log.warning("Transmission недоступен при старте: %s", safe_error(exc))
        tasks.append(
            asyncio.create_task(
                run_reporter(bot, database, transmission, settings),
                name="reporter",
            )
        )

    async def on_shutdown() -> None:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await database.close()

    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)
    await dispatcher.start_polling(
        bot,
        db=database,
        transmission=transmission,
        settings=settings,
    )
