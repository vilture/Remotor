from aiogram import Dispatcher

from remotor.handlers import fallback, files, folder, jobs, settings, start, torrent


def setup_routers(dispatcher: Dispatcher) -> None:
    dispatcher.include_router(start.router)
    dispatcher.include_router(jobs.router)
    dispatcher.include_router(settings.router)
    dispatcher.include_router(folder.router)
    dispatcher.include_router(torrent.router)
    dispatcher.include_router(files.router)
    dispatcher.include_router(fallback.router)
