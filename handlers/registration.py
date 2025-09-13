from __future__ import annotations

from io import BytesIO
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from loguru import logger

from utils.supabase_client import (
    table,
    PROFILES,
    get_user_by_tg_id,
    upsert_user_basic,
)
from utils.cloudinary_client import upload_image, upload_video


router = Router(name="registration")


# FSM States for profile creation
class Registration(StatesGroup):
    name = State()
    gender = State()
    age = State()
    city = State()
    photo = State()
    bio = State()


class MyProfileMenu(StatesGroup):
    waiting_choice = State()


class ChangePhoto(StatesGroup):
    waiting_photo = State()


class ChangeBio(StatesGroup):
    waiting_bio = State()


GENDER_MAP = {
    "мужской": "male",
    "женский": "female",
    "другое": "other",
    # english shortcuts
    "male": "male",
    "female": "female",
    "other": "other",
}


# Menu button labels
MENU_BTN_FEED = "1. Смотреть анкеты"
MENU_BTN_REGISTER = "2. Заполнить анкету заново"
MENU_BTN_MEDIA = "3. Изменить фото/видео"
MENU_BTN_BIO = "4. Изменить текст анкеты"
CREATE_BTN = "Создать анкету"
BACK_BTN = "⬅️ Назад"


def back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BACK_BTN)]],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=True,
        input_field_placeholder="Нажмите 'Назад' для отмены",
    )


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_BTN_FEED), KeyboardButton(text=MENU_BTN_REGISTER)],
            [KeyboardButton(text=MENU_BTN_MEDIA), KeyboardButton(text=MENU_BTN_BIO)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=True,
        input_field_placeholder="Выберите пункт меню",
    )


def start_create_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CREATE_BTN)]],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=True,
        input_field_placeholder="Создать анкету",
    )


def gender_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Мужской"), KeyboardButton(text="Женский")],
            [KeyboardButton(text="Другое")],
            [KeyboardButton(text=BACK_BTN)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=True,
        input_field_placeholder="Выберите пол",
    )


# disabled: Command("register")
async def cmd_register(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id if message.from_user else None
    prev_state = await state.get_state()
    logger.debug(f"cmd_register: user_id={user_id} prev_state={prev_state} data={await state.get_data()}")
    """Start profile registration flow."""
    user = message.from_user
    if not user:
        logger.debug("cmd_register: no from_user")
        return

    # Ensure user exists in Users table
    upsert_user_basic(user.id, user.username, user.first_name, user.last_name)

    # Check if profile exists to inform user about update
    db_user = get_user_by_tg_id(user.id)
    existing = None
    if db_user:
        resp = table(PROFILES).select("*").eq("user_id", db_user["id"]).limit(1).execute()
        rows: List[Dict[str, Any]] = resp.data or []
        existing = rows[0] if rows else None

    logger.debug(f"cmd_register: existing_profile={bool(existing)}")

    if existing:
        await message.answer(
            "Обновим вашу анкету. Отправьте имя (как вы хотите, чтобы оно отображалось).\n"
            "Для отмены нажмите '⬅️ Назад'.",
            reply_markup=back_kb(),
        )
    else:
        await message.answer(
            "Создадим вашу анкету. Отправьте имя (как вы хотите, чтобы оно отображалось).\n"
            "Для отмены нажмите '⬅️ Назад'.",
            reply_markup=back_kb(),
        )
    data = await state.get_data()
    if "entry" not in data:
        await state.update_data(entry="start")
    await state.set_state(Registration.name)
    logger.debug(f"cmd_register: set_state -> Registration.name, data={await state.get_data()}")


@router.message(F.text == CREATE_BTN)
async def create_profile_button(message: Message, state: FSMContext) -> None:
    logger.debug(f"create_profile_button: click by user_id={message.from_user.id if message.from_user else None}")
    await state.update_data(entry="start")
    await cmd_register(message, state)


# disabled: Command("cancel")
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    logger.debug(f"cmd_cancel: user_id={message.from_user.id if message.from_user else None}")
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())


@router.message(Registration.name)
async def reg_name(message: Message, state: FSMContext) -> None:
    back_text = (message.text or "").strip()
    logger.debug(f"reg_name: text={back_text!r} user_id={message.from_user.id if message.from_user else None} state={await state.get_state()} data={await state.get_data()}")
    if back_text == BACK_BTN:
        data = await state.get_data()
        entry = data.get("entry", "start")
        await state.clear()
        user = message.from_user
        p = _get_profile_for_user(user.id) if user else None
        logger.debug(f"reg_name: BACK entry={entry} has_profile={bool(p)}")
        if entry == "menu" and p:
            text = _format_profile(p) + "\n\nВыберите:\n1. Смотреть анкеты\n2. Заполнить анкету заново\n3. Изменить фото/видео\n4. Изменить текст анкеты\n(ответьте цифрой)"
            await message.answer(text, reply_markup=main_menu_kb())
            await state.set_state(MyProfileMenu.waiting_choice)
        else:
            await message.answer("Нажмите кнопку, чтобы создать анкету.", reply_markup=start_create_kb())
        return
    name = back_text
    if not name:
        await message.answer("Введите корректное имя.")
        return
    await state.update_data(name=name)
    await state.set_state(Registration.gender)
    logger.debug(f"reg_name: set_state -> Registration.gender, data={await state.get_data()}")
    await message.answer("Выберите пол:", reply_markup=gender_keyboard())


@router.message(Registration.gender)
async def reg_gender(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    logger.debug(f"reg_gender: text={raw!r} user_id={message.from_user.id if message.from_user else None} state={await state.get_state()}")
    if raw == BACK_BTN:
        await state.set_state(Registration.name)
        logger.debug("reg_gender: BACK -> Registration.name")
        await message.answer("Отправьте имя (как вы хотите, чтобы оно отображалось).", reply_markup=back_kb())
        return
    text = raw.lower()
    gender = GENDER_MAP.get(text)
    if not gender:
        await message.answer("Пожалуйста, выберите пол с клавиатуры: Мужской, Женский или Другое.", reply_markup=gender_keyboard())
        return
    await state.update_data(gender=gender)
    await state.set_state(Registration.age)
    logger.debug(f"reg_gender: set_state -> Registration.age, data={await state.get_data()}")
    await message.answer("Укажите возраст (число от 18 до 100).", reply_markup=back_kb())


@router.message(Registration.age)
async def reg_age(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    logger.debug(f"reg_age: text={raw!r} user_id={message.from_user.id if message.from_user else None} state={await state.get_state()}")
    if raw == BACK_BTN:
        await state.set_state(Registration.gender)
        logger.debug("reg_age: BACK -> Registration.gender")
        await message.answer("Выберите пол:", reply_markup=gender_keyboard())
        return
    text = raw
    try:
        age = int(text)
        if age < 18 or age > 100:
            raise ValueError
    except Exception:
        await message.answer("Возраст должен быть числом в диапазоне 18–100. Введите возраст ещё раз.")
        return
    await state.update_data(age=age)
    await state.set_state(Registration.city)
    logger.debug(f"reg_age: set_state -> Registration.city, data={await state.get_data()}")
    await message.answer("Укажите город (текстом).", reply_markup=back_kb())


@router.message(Registration.city)
async def reg_city(message: Message, state: FSMContext) -> None:
    txt = (message.text or "").strip()
    logger.debug(f"reg_city: text={txt!r} user_id={message.from_user.id if message.from_user else None} state={await state.get_state()}")
    if txt == BACK_BTN:
        await state.set_state(Registration.age)
        logger.debug("reg_city: BACK -> Registration.age")
        await message.answer("Укажите возраст (число от 18 до 100).", reply_markup=back_kb())
        return
    city = txt
    if not city:
        await message.answer("Введите корректное название города.")
        return
    await state.update_data(city=city)
    await state.set_state(Registration.photo)
    logger.debug(f"reg_city: set_state -> Registration.photo, data={await state.get_data()}")
    await message.answer("Отправьте фото для анкеты (как фотографию, не файлом).", reply_markup=back_kb())


async def _download_photo_bytes(message: Message) -> Optional[bytes]:
    if not message.photo:
        return None
    photo = message.photo[-1]
    buf = BytesIO()
    try:
        await message.bot.download(photo, destination=buf)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.warning(f"Failed to download photo: {e}")
        return None


@router.message(Registration.photo, F.photo)
async def reg_photo(message: Message, state: FSMContext) -> None:
    logger.debug(f"reg_photo: got photo user_id={message.from_user.id if message.from_user else None} state={await state.get_state()}")
    content = await _download_photo_bytes(message)
    if not content:
        await message.answer("Не удалось получить фото. Отправьте фото ещё раз.")
        return
    try:
        res = upload_image(content)
        url = res.get("url")
    except Exception as e:
        logger.error(f"Cloudinary upload error: {e}")
        await message.answer("Ошибка загрузки фото. Попробуйте другое изображение.")
        return

    await state.update_data(photos=[url])
    await state.set_state(Registration.bio)
    logger.debug(f"reg_photo: set_state -> Registration.bio, data={await state.get_data()}")
    await message.answer("Расскажите о себе (краткое описание).", reply_markup=back_kb())


@router.message(Registration.photo)
async def reg_photo_invalid(message: Message, state: FSMContext) -> None:
    logger.debug(f"reg_photo_invalid: text={message.text!r} user_id={message.from_user.id if message.from_user else None} state={await state.get_state()}")
    if (message.text or "").strip() == BACK_BTN:
        await state.set_state(Registration.city)
        logger.debug("reg_photo_invalid: BACK -> Registration.city")
        await message.answer("Укажите город (текстом).", reply_markup=back_kb())
        return
    await message.answer("Пожалуйста, отправьте фото (как фотографию, не файлом).", reply_markup=back_kb())


@router.message(Registration.bio)
async def reg_bio(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    logger.debug(f"reg_bio: text={raw!r} user_id={message.from_user.id if message.from_user else None} state={await state.get_state()}")
    if raw == BACK_BTN:
        await state.set_state(Registration.photo)
        logger.debug("reg_bio: BACK -> Registration.photo")
        await message.answer("Отправьте фото для анкеты (как фотографию, не файло��).", reply_markup=back_kb())
        return
    bio = raw
    if not bio:
        await message.answer("Описание не должно быть пустым. Введите описание.")
        return

    data = await state.get_data()
    data.update(bio=bio)

    # Persist profile
    await _create_or_update_profile(message, data)

    await state.clear()
    logger.debug("reg_bio: profile saved, state cleared")
    await message.answer(
        "Анкета сохранена.\n"
        "Откройте /start для управления:\n"
        "1. Смотреть анкеты\n"
        "2. Заполнить анкету заново\n"
        "3. Изменить фото/видео\n"
        "4. Изменить текст анкеты"
    )


async def _create_or_update_profile(message: Message, data: Dict[str, Any]) -> None:
    logger.debug(f"_create_or_update_profile: user_id={message.from_user.id if message.from_user else None} payload_keys={list(data.keys())}")
    user = message.from_user
    if not user:
        return

    # Ensure user present in Users
    upsert_user_basic(user.id, user.username, user.first_name, user.last_name)
    db_user = get_user_by_tg_id(user.id)
    if not db_user:
        await message.answer("Ошибка: не удалось идент��фицировать пользователя в базе.")
        return

    name = data.get("name")
    gender = data.get("gender")
    age = data.get("age")
    city = data.get("city")
    photos: List[str] = data.get("photos", [])
    bio = data.get("bio")

    # Check existing profile
    resp = table(PROFILES).select("*").eq("user_id", db_user["id"]).limit(1).execute()
    rows: List[Dict[str, Any]] = resp.data or []
    existing = rows[0] if rows else None

    payload = {
        "user_id": db_user["id"],
        "name": name,
        "gender": gender,
        "age": age,
        "city": city,
        "photos": photos,
        "bio": bio,
        "is_active": True,
    }

    try:
        if existing:
            resp = table(PROFILES).update(payload).eq("id", existing["id"]).execute()
            logger.info(f"Profile updated for user_id={db_user['id']}")
        else:
            boosted_until = datetime.now(timezone.utc) + timedelta(hours=24)
            payload["boosted_until"] = boosted_until.isoformat()
            resp = table(PROFILES).insert(payload).execute()
            logger.info(f"Profile created for user_id={db_user['id']}, boosted 24h")
    except Exception as e:
        logger.error(f"Failed to persist profile: {e}")
        await message.answer("Ошибка сохранения анкеты. Попробуйте позже.")


def _format_profile(p: Dict[str, Any]) -> str:
    lines = [
        f"Имя: {p.get('name')}",
        f"Пол: {p.get('gender')}",
        f"Возраст: {p.get('age')}",
        f"Город: {p.get('city')}",
        f"Описание: {p.get('bio')}",
        f"Активна: {'да' if p.get('is_active') else 'нет'}",
    ]
    return "\n".join(lines)


def _get_profile_for_user(tg_id: int) -> Optional[Dict[str, Any]]:
    try:
        resp = table(PROFILES).select("*").eq("user_id", get_user_by_tg_id(tg_id)["id"]).limit(1).execute()  # type: ignore[index]
        rows: List[Dict[str, Any]] = resp.data or []
        prof = rows[0] if rows else None
        logger.debug(f"_get_profile_for_user: tg_id={tg_id} found={bool(prof)}")
        return prof
    except Exception as e:
        logger.warning(f"_get_profile_for_user error: {e}")
        return None


@router.message(StateFilter(
    Registration.name,
    Registration.gender,
    Registration.age,
    Registration.city,
    Registration.photo,
    Registration.bio,
    ChangePhoto.waiting_photo,
    ChangeBio.waiting_bio,
), F.text == BACK_BTN)
async def back_button(message: Message, state: FSMContext) -> None:
    user = message.from_user
    logger.debug(f"back_button: user_id={user.id if user else None} current_state={await state.get_state()} data={await state.get_data()}")
    if not user:
        return
    current = await state.get_state()
    data = await state.get_data()
    entry = data.get("entry", "start")

    # If at first step, return to where we came from
    if current == Registration.name.state:
        await state.clear()
        p = _get_profile_for_user(user.id)
        if entry == "menu" and p:
            text = _format_profile(p) + "\n\nВыберите:\n1. Смотреть анкеты\n2. Заполнить анкету заново\n3. Изменить фото/видео\n4. Изменить текст анкеты\n(ответьте цифрой)"
            await message.answer(text, reply_markup=main_menu_kb())
            await state.set_state(MyProfileMenu.waiting_choice)
        else:
            await message.answer("Нажмите кнопку, чтобы создать анкету.", reply_markup=start_create_kb())
        return

    # Step back inside Registration flow
    if current == Registration.gender.state:
        await state.set_state(Registration.name)
        logger.debug("back_button: -> Registration.name")
        await message.answer("Отправьте имя (как вы хотите, чтобы оно отображалось).", reply_markup=back_kb())
        return
    if current == Registration.age.state:
        await state.set_state(Registration.gender)
        logger.debug("back_button: -> Registration.gender")
        await message.answer("Выберите пол:", reply_markup=gender_keyboard())
        return
    if current == Registration.city.state:
        await state.set_state(Registration.age)
        logger.debug("back_button: -> Registration.age")
        await message.answer("Укажите возраст (число от 18 до 100).", reply_markup=back_kb())
        return
    if current == Registration.photo.state:
        await state.set_state(Registration.city)
        logger.debug("back_button: -> Registration.city")
        await message.answer("Укажите город (текстом).", reply_markup=back_kb())
        return
    if current == Registration.bio.state:
        await state.set_state(Registration.photo)
        logger.debug("back_button: -> Registration.photo")
        await message.answer("Отправьте фото для анкеты (как фотографию, не файлом).", reply_markup=back_kb())
        return

    # Edit flows -> back to menu
    if current == ChangePhoto.waiting_photo.state or current == ChangeBio.waiting_bio.state:
        await state.clear()
        p = _get_profile_for_user(user.id)
        if p:
            text = _format_profile(p) + "\n\nВыберите:\n1. Смотреть анкеты\n2. Заполнить анкету заново\n3. Изменить фото/видео\n4. Изменить текст анкеты\n(ответьте цифрой)"
            await message.answer(text, reply_markup=main_menu_kb())
            await state.set_state(MyProfileMenu.waiting_choice)
        else:
            await message.answer("Нажмите кнопку, чтобы создать анкету.", reply_markup=start_create_kb())
        return

    # Fallback
    await state.clear()
    await message.answer("Нажмите кнопку, чтобы создать анкету.", reply_markup=start_create_kb())


# disabled: Command("myprofile")
async def cmd_myprofile(message: Message, state: FSMContext) -> None:
    logger.debug(f"cmd_myprofile: user_id={message.from_user.id if message.from_user else None}")
    user = message.from_user
    if not user:
        return
    p = _get_profile_for_user(user.id)
    if not p:
        await message.answer("Анкета не создана. Используйте /register для создания.")
        return
    text = _format_profile(p) + "\n\nВыберите:\n1. Смотреть анкеты\n2. Заполнить анкету заново\n3. Изменить фото\n4. Изменить текст анкеты\n(ответьте цифрой)"
    await message.answer(text)
    await state.set_state(MyProfileMenu.waiting_choice)


# disabled: Command("pause_profile")
async def cmd_pause_profile(message: Message) -> None:
    logger.debug(f"cmd_pause_profile: user_id={message.from_user.id if message.from_user else None}")
    user = message.from_user
    if not user:
        return
    p = _get_profile_for_user(user.id)
    if not p:
        await message.answer("Анкета не найдена.")
        return
    try:
        table(PROFILES).update({"is_active": False}).eq("id", p["id"]).execute()
        await message.answer("Анкета приостановлена.")
    except Exception as e:
        logger.error(f"Pause profile error: {e}")
        await message.answer("Ошибка приостановки анкеты.")


# disabled: Command("resume_profile")
async def cmd_resume_profile(message: Message) -> None:
    logger.debug(f"cmd_resume_profile: user_id={message.from_user.id if message.from_user else None}")
    user = message.from_user
    if not user:
        return
    p = _get_profile_for_user(user.id)
    if not p:
        await message.answer("Анкета не найдена.")
        return
    try:
        table(PROFILES).update({"is_active": True}).eq("id", p["id"]).execute()
        await message.answer("Анкета возобновлена.")
    except Exception as e:
        logger.error(f"Resume profile error: {e}")
        await message.answer("Ошибка возобновления анкеты.")


# disabled: Command("delete_profile")
async def cmd_delete_profile(message: Message) -> None:
    logger.debug(f"cmd_delete_profile: user_id={message.from_user.id if message.from_user else None}")
    user = message.from_user
    if not user:
        return
    p = _get_profile_for_user(user.id)
    if not p:
        await message.answer("Анкета не найдена.")
        return
    try:
        table(PROFILES).delete().eq("id", p["id"]).execute()
        await message.answer("Анкета удалена.")
    except Exception as e:
        logger.error(f"Delete profile error: {e}")
        await message.answer("Ошибка удаления анкеты.")


@router.message(MyProfileMenu.waiting_choice)
async def myprofile_choice(message: Message, state: FSMContext) -> None:
    choice = (message.text or "").strip()
    logger.debug(f"myprofile_choice: choice={choice!r} user_id={message.from_user.id if message.from_user else None} state={await state.get_state()} data={await state.get_data()}")

    def is_choice(n: str, label: str) -> bool:
        return choice == n or choice.startswith(n + ".") or choice == label

    if is_choice("1", MENU_BTN_FEED):
        # Keep menu state so subsequent numeric choices still work after viewing feed
        logger.debug("myprofile_choice: -> feed (state kept as MyProfileMenu.waiting_choice)")
        from .feed import cmd_feed
        await cmd_feed(message)
    elif is_choice("2", MENU_BTN_REGISTER):
        await state.update_data(entry="menu")
        logger.debug("myprofile_choice: -> register entry=menu")
        await cmd_register(message, state)
    elif is_choice("3", MENU_BTN_MEDIA):
        await state.set_state(ChangePhoto.waiting_photo)
        logger.debug("myprofile_choice: -> ChangePhoto.waiting_photo")
        await message.answer("Отправьте новое фото/видео для анкеты (как медиа).", reply_markup=back_kb())
    elif is_choice("4", MENU_BTN_BIO):
        await state.set_state(ChangeBio.waiting_bio)
        logger.debug("myprofile_choice: -> ChangeBio.waiting_bio")
        await message.answer("Отправьте новый текст анкеты.", reply_markup=back_kb())
    else:
        await message.answer("Выберите пункт меню кнопкой или введите цифру: 1, 2, 3 или 4.")


@router.message(ChangePhoto.waiting_photo, F.photo | F.video)
async def change_photo_receive(message: Message, state: FSMContext) -> None:
    logger.debug(f"change_photo_receive: content_type={'video' if message.video else 'photo'} user_id={message.from_user.id if message.from_user else None}")
    url = None
    if message.video:
        buf = BytesIO()
        try:
            await message.bot.download(message.video, destination=buf)
            buf.seek(0)
            res = upload_video(buf.read())
            url = res.get("url")
        except Exception as e:
            logger.error(f"Cloudinary video upload error: {e}")
            await message.answer("Ошибка загрузки видео. Попробуйте ещё раз.")
            return
    else:
        content = await _download_photo_bytes(message)
        if not content:
            await message.answer("Не удалось получить медиа, отправьте снова.")
            return
        try:
            res = upload_image(content)
            url = res.get("url")
        except Exception as e:
            logger.error(f"Cloudinary image upload error: {e}")
            await message.answer("Ошибка загрузки фото. Попробуйте другое изображение.")
            return

    user = message.from_user
    if not user:
        return
    p = _get_profile_for_user(user.id)
    if not p:
        await message.answer("Анкета не найдена.")
        await state.clear()
        return
    try:
        table(PROFILES).update({"photos": [url]}).eq("id", p["id"]).execute()
        await message.answer("Медиа обновлено.")
        text = _format_profile(p) + "\n\nВыберите:\n1. Смотреть анкеты\n2. Заполнить анкету заново\n3. Изменить фото/видео\n4. Изменить текст анкеты\n(ответьте цифрой)"
        await message.answer(text, reply_markup=main_menu_kb())
        await state.set_state(MyProfileMenu.waiting_choice)
        return
    except Exception as e:
        logger.error(f"Update media error: {e}")
        await message.answer("Ошибка обновления медиа.")
        await state.clear()
        return


@router.message(ChangePhoto.waiting_photo)
async def change_photo_invalid(message: Message, state: FSMContext) -> None:
    logger.debug(f"change_photo_invalid: text={message.text!r} user_id={message.from_user.id if message.from_user else None}")
    if (message.text or "").strip() == BACK_BTN:
        await state.clear()
        user = message.from_user
        p = _get_profile_for_user(user.id) if user else None
        if p:
            text = _format_profile(p) + "\n\nВыберите:\n1. Смотреть анкеты\n2. Заполнить анкету заново\n3. Изменить фото/видео\n4. Изменить текст анкеты\n(ответьте цифрой)"
            await message.answer(text, reply_markup=main_menu_kb())
            await state.set_state(MyProfileMenu.waiting_choice)
        else:
            await message.answer("Нажмите кнопку, чтобы создать анкету.", reply_markup=start_create_kb())
        return
    await message.answer("Пожалуйста, отправьте фото или видео (как медиа).")


@router.message(ChangeBio.waiting_bio)
async def change_bio_receive(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    logger.debug(f"change_bio_receive: text={raw!r} user_id={message.from_user.id if message.from_user else None}")
    if raw == BACK_BTN:
        await state.clear()
        user = message.from_user
        p = _get_profile_for_user(user.id) if user else None
        if p:
            text = _format_profile(p) + "\n\nВыберите:\n1. Смотреть анкеты\n2. Заполнить анкету заново\n3. Изменить фото/видео\n4. Изменить текст анкеты\n(ответьте цифрой)"
            await message.answer(text, reply_markup=main_menu_kb())
            await state.set_state(MyProfileMenu.waiting_choice)
        else:
            await message.answer("Нажмите кнопку, чтобы создать анкету.", reply_markup=start_create_kb())
        return
    bio = raw
    if not bio:
        await message.answer("Текст не должен быть пустым. Отправьте новый текст анкеты.")
        return
    user = message.from_user
    if not user:
        return
    p = _get_profile_for_user(user.id)
    if not p:
        await message.answer("Анкета не найдена.")
        await state.clear()
        return
    try:
        table(PROFILES).update({"bio": bio}).eq("id", p["id"]).execute()
        await message.answer("Текст анкеты обновлён.")
        # Get updated profile and show menu
        updated_p = _get_profile_for_user(user.id)
        if updated_p:
            text = _format_profile(updated_p) + "\n\nВыберите:\n1. Смотреть анкеты\n2. Заполнить анкету заново\n3. Изменить фото/видео\n4. Изменить текст анкеты\n(ответьте цифрой)"
            await message.answer(text, reply_markup=main_menu_kb())
            await state.set_state(MyProfileMenu.waiting_choice)
        else:
            await state.clear()
        return
    except Exception as e:
        logger.error(f"Update bio error: {e}")
        await message.answer("Ошибка обновления текста анкеты.")
        await state.clear()
        return