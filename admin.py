from typing import Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from config import get_settings

router = Router(name="admin")


def _is_admin(user_id: int, admin_ids: Iterable[int]) -> bool:
    return user_id in admin_ids


@router.message(Command("admin"))
async def admin_help(message: Message) -> None:
    settings = get_settings()
    user = message.from_user
    if not user or not _is_admin(user.id, settings.admin_ids):
        return
    await message.answer(
        "Админка:\n"
        "/users — список пользователей (заглушка)\n"
        "/broadcast <text> — рассылка (заглушка)\n"
    )
