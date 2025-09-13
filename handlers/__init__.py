from aiogram import Dispatcher

from . import registration
from . import feed
from . import chat
from . import profile
from . import common


def register_routers(dp: Dispatcher) -> None:
    # Handlers are optional at bootstrap; routers may be empty and filled later
    for router in (
        common.router,
        registration.router,
        profile.router,
        feed.router,
        chat.router,
    ):
        if router is not None:
            dp.include_router(router)
