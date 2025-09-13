import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from loguru import logger

from config import get_settings
from utils.supabase_client import upsert_user_basic
from handlers import register_routers
from admin import router as admin_router
from handlers.registration import _get_profile_for_user, MyProfileMenu, cmd_register, main_menu_kb, start_create_kb


async def on_startup(bot: Bot) -> None:
    logger.info("Bot startup")


async def on_shutdown(bot: Bot) -> None:
    logger.info("Bot shutdown")


async def start_handler(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user:
        upsert_user_basic(
            tg_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
        )
    # Check if profile exists
    p = _get_profile_for_user(user.id) if user else None
    if not p:
        # greet and start registration flow
        await message.answer(
            "👋 Добро пожаловать!\n"
            "Используя этого бота, вы соглашаетесь с нашей Политикой конфиденциальности.\n"
            "Ознакомиться можно командой /privacy.\n\n"
            "Нажмите кнопку, чтобы создать анкету.",
            reply_markup=start_create_kb()
        )
        return

    # Show profile and numeric menu
    text = (
        f"Имя: {p.get('name')}\n"
        f"Пол: {p.get('gender')}\n"
        f"Возраст: {p.get('age')}\n"
        f"Город: {p.get('city')}\n"
        f"Описание: {p.get('bio')}\n"
        f"Активна: {'да' if p.get('is_active') else 'нет'}\n\n"
        "Выберите:\n"
        "1. Смотреть анкеты\n"
        "2. Заполнить анкету заново\n"
        "3. Изменить фото/видео\n"
        "4. Изменить текст анкеты\n"
        "(ответьте цифрой)"
    )
    await message.answer(text, reply_markup=main_menu_kb())
    await state.set_state(MyProfileMenu.waiting_choice)


async def privacy_handler(message: Message) -> None:
    settings = get_settings()
    await message.answer(settings.privacy_text)


async def main() -> None:
    settings = get_settings()
    settings.validate_required()

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # base commands
    dp.message.register(start_handler, CommandStart())
    dp.message.register(privacy_handler, Command("privacy"))

    # include routers
    register_routers(dp)
    dp.include_router(admin_router)

    # register lifecycle callbacks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    logger.info("Starting polling")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
