from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from loguru import logger

from utils.supabase_client import (
    table,
    USERS,
    PROFILES,
    MATCHES,
    CHATS,
    COMPLAINTS,
    get_user_by_tg_id,
)

router = Router(name="chat")

CB_PREFIX = "chat"


def _cb(action: str, match_id: int) -> str:
    return f"{CB_PREFIX}:{action}:{match_id}"


def _parse_cb(data: str) -> Tuple[str, int]:
    try:
        prefix, action, mid = data.split(":", 3)
        if prefix != CB_PREFIX:
            raise ValueError
        return action, int(mid)
    except Exception as e:
        raise ValueError("bad callback data") from e


async def _db_user_from_message(message: Message) -> Optional[Dict[str, Any]]:
    u = message.from_user
    if not u:
        return None
    return get_user_by_tg_id(u.id)


def _other_user_id(match: Dict[str, Any], my_user_id: int) -> Optional[int]:
    a, b = match.get("user1_id"), match.get("user2_id")
    if a == my_user_id:
        return b
    if b == my_user_id:
        return a
    return None


def _get_matches_for_user(user_id: int, active_only: bool = True, limit: int = 50) -> List[Dict[str, Any]]:
    q = table(MATCHES).select("*")
    if active_only:
        q = q.eq("is_active", True)
    q = q.or_(f"user1_id.eq.{user_id},user2_id.eq.{user_id}").limit(limit)
    r = q.execute()
    return r.data or []


def _get_active_chats_for_matches(match_ids: List[int]) -> List[Dict[str, Any]]:
    if not match_ids:
        return []
    r = table(CHATS).select("*").eq("is_active", True).in_("match_id", match_ids).execute()
    return r.data or []


def _get_match_by_id(mid: int) -> Optional[Dict[str, Any]]:
    r = table(MATCHES).select("*").eq("id", mid).limit(1).execute()
    rows = r.data or []
    return rows[0] if rows else None


def _get_active_chat_for_user(user_id: int) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Return (chat, match) for active chat of user if any (latest by updated_at)."""
    matches = _get_matches_for_user(user_id, active_only=True, limit=100)
    mids = [m["id"] for m in matches]
    chats = _get_active_chats_for_matches(mids)
    if not chats:
        return None
    # choose latest by updated_at (fallback to id)
    chats.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    chat = chats[0]
    match = next((m for m in matches if m["id"] == chat["match_id"]), None)
    if not match:
        return None
    return chat, match


def _has_active_chat_for_match(match_id: int) -> bool:
    r = table(CHATS).select("id").eq("match_id", match_id).eq("is_active", True).limit(1).execute()
    return bool(r.data)


def _deactivate_all_user_chats(user_id: int) -> None:
    matches = _get_matches_for_user(user_id, active_only=True, limit=200)
    mids = [m["id"] for m in matches]
    if not mids:
        return
    try:
        table(CHATS).update({"is_active": False}).in_("match_id", mids).eq("is_active", True).execute()
    except Exception as e:
        logger.warning(f"Deactivate chats failed: {e}")


def _create_chat(match_id: int) -> Optional[Dict[str, Any]]:
    try:
        r = table(CHATS).insert({"match_id": match_id, "is_active": True}).execute()
        rows = r.data or []
        return rows[0] if rows else None
    except Exception as e:
        logger.warning(f"Create chat failed: {e}")
        return None


async def _tg_id(users_id: int) -> Optional[int]:
    r = table(USERS).select("tg_id").eq("id", users_id).limit(1).execute()
    rows = r.data or []
    return rows[0]["tg_id"] if rows else None


def _profile_name(user_id: int) -> str:
    try:
        r = table(PROFILES).select("name").eq("user_id", user_id).limit(1).execute()
        rows = r.data or []
        return rows[0].get("name") if rows else "Пользователь"
    except Exception:
        return "Пользователь"


# disabled: Command("my_matches")
async def cmd_my_matches(message: Message) -> None:
    dbu = await _db_user_from_message(message)
    if not dbu:
        return
    ms = _get_matches_for_user(dbu["id"], active_only=True)
    if not ms:
        await message.answer("У вас пока нет матчей.")
        return
    lines = []
    for m in ms[:20]:
        other = _other_user_id(m, dbu["id"]) or 0
        nm = _profile_name(other)
        active = "да" if _has_active_chat_for_match(m["id"]) else "нет"
        lines.append(f"• {nm} — активный чат: {active}")
    await message.answer("Ваши матчи:\n" + "\n".join(lines))


# disabled: Command("start_chat")
async def cmd_start_chat(message: Message) -> None:
    dbu = await _db_user_from_message(message)
    if not dbu:
        return

    ms = _get_matches_for_user(dbu["id"], active_only=True)
    if not ms:
        await message.answer("Нет активных матчей для начала чата.")
        return

    # pick first without active chat
    candidate = None
    for m in ms:
        if not _has_active_chat_for_match(m["id"]):
            candidate = m
            break
    if not candidate:
        await message.answer("По всем матчам уже есть активные чаты. Используйте /end_chat, чтобы завершить текущий, и затем /start_chat.")
        return

    # ensure only one chat active for this user
    _deactivate_all_user_chats(dbu["id"])

    ch = _create_chat(candidate["id"])
    if not ch:
        await message.answer("Не удалось создать чат. Попробуйте позже.")
        return

    other = _other_user_id(candidate, dbu["id"]) or 0
    my_tg = message.from_user.id if message.from_user else None
    other_tg = await _tg_id(other)

    name_me = _profile_name(dbu["id"])
    name_ot = _profile_name(other)

    if my_tg:
        await message.bot.send_message(my_tg, f"Анонимный чат с ‘{name_ot}’ начат. Сообщения будут пересылаться анонимно. Используйте /end_chat для завершения.")
    if other_tg:
        await message.bot.send_message(other_tg, f"‘{name_me}’ начал(а) с вами анонимный чат. Сообщения будут пересылаться анонимно. Используйте /end_chat для завершения.")


# disabled: Command("end_chat")
async def cmd_end_chat(message: Message) -> None:
    dbu = await _db_user_from_message(message)
    if not dbu:
        return
    active = _get_active_chat_for_user(dbu["id"])
    if not active:
        await message.answer("У вас нет активного чата.")
        return
    chat, match = active
    try:
        table(CHATS).update({"is_active": False}).eq("id", chat["id"]).execute()
    except Exception as e:
        logger.warning(f"End chat update failed: {e}")

    my_tg = message.from_user.id if message.from_user else None
    other = _other_user_id(match, dbu["id"]) or 0
    other_tg = await _tg_id(other)

    if my_tg:
        await message.bot.send_message(my_tg, "Чат завершён.")
    if other_tg:
        await message.bot.send_message(other_tg, "Собеседник завершил чат.")


# disabled: Command("block_user")
async def cmd_block_user(message: Message) -> None:
    dbu = await _db_user_from_message(message)
    if not dbu:
        return
    active = _get_active_chat_for_user(dbu["id"])
    if not active:
        await message.answer("Нет активного чата для блокировки.")
        return
    chat, match = active
    other = _other_user_id(match, dbu["id"]) or 0

    try:
        table(USERS).update({"is_blocked": True}).eq("id", other).execute()
        table(CHATS).update({"is_active": False}).eq("id", chat["id"]).execute()
        await message.answer("Пользователь заблокирован, чат завершён.")
    except Exception as e:
        logger.warning(f"Block user failed: {e}")
        await message.answer("Ошибка блокировки пользователя.")


# disabled: Command("report")
async def cmd_report(message: Message) -> None:
    dbu = await _db_user_from_message(message)
    if not dbu:
        return
    reason = (message.text or "").partition(" ")[2].strip()
    if not reason:
        await message.answer("Использование: /report <описание жалобы>")
        return
    active = _get_active_chat_for_user(dbu["id"]) or (None, None)
    _chat, match = active
    target_id = _other_user_id(match, dbu["id"]) if match else None
    try:
        table(COMPLAINTS).insert({
            "from_user_id": dbu["id"],
            "against_user_id": target_id,
            "reason": reason,
        }).execute()
        await message.answer("Жалоба отправлена.")
    except Exception as e:
        logger.warning(f"Report failed: {e}")
        await message.answer("Ошибка отправки жалобы.")


# disabled: Command("share_contact")
async def cmd_share_contact(message: Message) -> None:
    dbu = await _db_user_from_message(message)
    if not dbu:
        return
    active = _get_active_chat_for_user(dbu["id"]) 
    if not active:
        await message.answer("Нет активного чата для обмена контактами.")
        return
    _chat, match = active
    match_id = match["id"]
    other = _other_user_id(match, dbu["id"]) or 0
    other_tg = await _tg_id(other)
    if not other_tg:
        await message.answer("Не удалось связат��ся с собеседником.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[ 
            InlineKeyboardButton(text="Разрешить обмен контактами", callback_data=_cb("approve", match_id)),
            InlineKeyboardButton(text="Отказать", callback_data=_cb("reject", match_id)),
        ]]
    )
    await message.answer("Запрос отправлен. Ожидайте подтверждения собеседника.")
    try:
        await message.bot.send_message(other_tg, "Собеседник запросил обмен контактами. Разрешить?", reply_markup=kb)
    except Exception as e:
        logger.warning(f"Share contact notify failed: {e}")


@router.callback_query(F.data.startswith(f"{CB_PREFIX}:"))
async def cb_share_contact(call: CallbackQuery) -> None:
    try:
        action, mid = _parse_cb(call.data or "")
    except ValueError:
        await call.answer("Некорректные данные")
        return

    match = _get_match_by_id(mid)
    if not match:
        await call.answer("Матч не найден")
        return

    # identify parties
    user_a, user_b = match.get("user1_id"), match.get("user2_id")
    tg_a, tg_b = await _tg_id(user_a), await _tg_id(user_b)

    if action == "approve":
        # share usernames if available
        def _mention(tg: Optional[int]) -> str:
            if not tg:
                return "контакт недоступен"
            # Attempt to share @username via Users table is not stored; Telegram username might be missing.
            # We'll share plain Telegram profile link by ID.
            return f"tg://user?id={tg}"

        try:
            if tg_a and tg_b:
                await call.bot.send_message(tg_a, "Обмен контактами разрешён. Контакт собеседника: " + _mention(tg_b))
                await call.bot.send_message(tg_b, "Обмен контактами разрешён. Контакт собеседника: " + _mention(tg_a))
            await call.answer("Обмен контактами выполнен")
        except Exception as e:
            logger.warning(f"Approve contact failed: {e}")
            await call.answer("Ошибка обмена контактами")
        return

    if action == "reject":
        try:
            if tg_a:
                await call.bot.send_message(tg_a, "Собеседник отклонил обмен контактами.")
            if tg_b:
                await call.bot.send_message(tg_b, "Вы отклонили обмен контактами.")
            await call.answer("Отклонено")
        except Exception as e:
            logger.warning(f"Reject contact failed: {e}")
            await call.answer("Ошибка")
        return


# Proxy messages inside active anonymous chat
@router.message(StateFilter(None), F.text | F.photo | F.video)
async def proxy_messages(message: Message) -> None:
    dbu = await _db_user_from_message(message)
    if not dbu:
        return
    active = _get_active_chat_for_user(dbu["id"]) 
    if not active:
        return  # let other handlers process
    _chat, match = active
    other = _other_user_id(match, dbu["id"]) or 0
    other_tg = await _tg_id(other)
    if not other_tg:
        return
    try:
        await message.bot.copy_message(chat_id=other_tg, from_chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logger.warning(f"Proxy copy message failed: {e}")
