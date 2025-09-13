from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
)
from loguru import logger

from utils.supabase_client import (
    table,
    USERS,
    PROFILES,
    LIKES,
    MATCHES,
    VIEWED_PROFILES,
    get_user_by_tg_id,
    mark_profile_viewed,
)
from utils.cloudinary_client import upload_video

router = Router(name="feed")


class LikeWithMessage(StatesGroup):
    waiting_text = State()


class LikeWithVideo(StatesGroup):
    waiting_video = State()


FEED_PREFIX = "feed"


def _action_cb(action: str, profile_id: int, to_user_id: int) -> str:
    return f"{FEED_PREFIX}:{action}:{profile_id}:{to_user_id}"


def _parse_cb(data: str) -> Tuple[str, int, int]:
    # returns (action, profile_id, to_user_id)
    try:
        prefix, action, pid, uid = data.split(":", 3)
        if prefix != FEED_PREFIX:
            raise ValueError
        return action, int(pid), int(uid)
    except Exception as e:
        raise ValueError("bad callback data") from e


async def _get_db_user(message: Message) -> Optional[Dict[str, Any]]:
    u = message.from_user
    if not u:
        return None
    return get_user_by_tg_id(u.id)


def _format_profile_card(p: Dict[str, Any]) -> str:
    lines = [
        f"Имя: {p.get('name')}",
        f"Пол: {p.get('gender')}",
        f"Возраст: {p.get('age')}",
        f"Город: {p.get('city')}",
        f"Описание: {p.get('bio') or ''}",
    ]
    if p.get("boosted_until"):
        lines.append("🚀 Boosted")
    return "\n".join(lines)


def _profile_keyboard(profile_id: int, to_user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="❤️ Лайк", callback_data=_action_cb("like", profile_id, to_user_id)),
                InlineKeyboardButton(text="💬 Лайк+со��бщение", callback_data=_action_cb("like_msg", profile_id, to_user_id)),
            ],
            [
                InlineKeyboardButton(text="🎬 Лайк+видео", callback_data=_action_cb("like_vid", profile_id, to_user_id)),
                InlineKeyboardButton(text="⏭️ Скип", callback_data=_action_cb("skip", profile_id, to_user_id)),
            ],
        ]
    )


async def _download_video_bytes(message: Message) -> Optional[bytes]:
    if not message.video:
        return None
    buf = BytesIO()
    try:
        await message.bot.download(message.video, destination=buf)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.warning(f"Failed to download video: {e}")
        return None


async def _get_recently_viewed_ids(user_id: int, days: int = 2) -> List[int]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    resp = (
        table(VIEWED_PROFILES)
        .select("profile_id, viewed_at")
        .eq("user_id", user_id)
        .gte("viewed_at", since)
        .execute()
    )
    rows: List[Dict[str, Any]] = resp.data or []
    return [r["profile_id"] for r in rows]


def _opposite_gender(g: Optional[str]) -> Optional[str]:
    if g == "male":
        return "female"
    if g == "female":
        return "male"
    return None  # for 'other' or unknown — no filter by gender


async def _fetch_next_profile(db_user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Fetch current user's profile to infer filters
    resp = table(PROFILES).select("*").eq("user_id", db_user["id"]).limit(1).execute()
    my_profile_list: List[Dict[str, Any]] = resp.data or []
    my_profile = my_profile_list[0] if my_profile_list else None

    gender_filter: Optional[str] = None
    city_filter: Optional[str] = None
    if my_profile:
        gender_filter = _opposite_gender(my_profile.get("gender"))
        # proximity: same city as a simple proxy for closeness
        city_filter = my_profile.get("city") or None

    viewed_ids = await _get_recently_viewed_ids(db_user["id"], days=2)

    now_iso = datetime.now(timezone.utc).isoformat()

    def _apply_common_filters(q):
        q = q.eq("is_active", True).neq("user_id", db_user["id"])
        if gender_filter:
            q = q.eq("gender", gender_filter)
        if city_filter:
            q = q.eq("city", city_filter)
        if viewed_ids:
            # NOT IN filter
            q = q.not_.in_("id", viewed_ids)  # type: ignore[attr-defined]
        return q

    # 1) boosted first
    q1 = table(PROFILES).select("*")
    q1 = _apply_common_filters(q1)
    q1 = q1.gte("boosted_until", now_iso).order("boosted_until", desc=True).limit(20)
    r1 = q1.execute()
    boosted: List[Dict[str, Any]] = r1.data or []
    if boosted:
        return boosted[0]

    # 2) others by recency
    q2 = table(PROFILES).select("*")
    q2 = _apply_common_filters(q2)
    q2 = q2.order("created_at", desc=True).limit(50)
    r2 = q2.execute()
    others: List[Dict[str, Any]] = r2.data or []
    if others:
        return others[0]

    return None


async def _show_profile(message: Message, db_user: Dict[str, Any], profile: Dict[str, Any]) -> None:
    caption = _format_profile_card(profile)
    kb = _profile_keyboard(profile_id=profile["id"], to_user_id=profile["user_id"])
    photos: List[str] = profile.get("photos") or []
    if photos:
        try:
            await message.answer_photo(photos[0], caption=caption, reply_markup=kb)
        except Exception:
            await message.answer(caption, reply_markup=kb)
    else:
        await message.answer(caption, reply_markup=kb)

    # mark as viewed
    try:
        mark_profile_viewed(db_user["id"], profile["id"])
    except Exception as e:
        logger.warning(f"Failed to mark viewed: {e}")


# disabled: Command("feed")
async def cmd_feed(message: Message) -> None:
    db_user = await _get_db_user(message)
    if not db_user:
        await message.answer("Ошибка идентификации пользователя. Попробуйте /start.")
        return

    profile = await _fetch_next_profile(db_user)
    if not profile:
        await message.answer("Подходящих анкет не найдено. Попробуйте позже.")
        return
    await _show_profile(message, db_user, profile)


async def _insert_like(from_user_id: int, to_user_id: int, message_text: Optional[str] = None, video_url: Optional[str] = None) -> None:
    payload = {
        "from_user_id": from_user_id,
        "to_user_id": to_user_id,
        "message": message_text,
        "video_url": video_url,
    }
    try:
        table(LIKES).upsert(payload, on_conflict="from_user_id,to_user_id").execute()
    except Exception as e:
        logger.warning(f"Like upsert failed: {e}")


def _check_reciprocal_like(a_user_id: int, b_user_id: int) -> bool:
    try:
        resp = (
            table(LIKES)
            .select("id")
            .eq("from_user_id", b_user_id)
            .eq("to_user_id", a_user_id)
            .limit(1)
            .execute()
        )
        rows: List[Dict[str, Any]] = resp.data or []
        return bool(rows)
    except Exception as e:
        logger.warning(f"Reciprocal like check failed: {e}")
        return False


def _get_match_between(a_user_id: int, b_user_id: int) -> Optional[Dict[str, Any]]:
    try:
        r = (
            table(MATCHES)
            .select("*")
            .or_(f"and(user1_id.eq.{a_user_id},user2_id.eq.{b_user_id}),and(user1_id.eq.{b_user_id},user2_id.eq.{a_user_id})")
            .limit(1)
            .execute()
        )
        rows: List[Dict[str, Any]] = r.data or []
        return rows[0] if rows else None
    except Exception as e:
        logger.warning(f"Get match failed: {e}")
        return None


def _create_match(a_user_id: int, b_user_id: int) -> Optional[Dict[str, Any]]:
    if a_user_id == b_user_id:
        return None
    user1 = min(a_user_id, b_user_id)
    user2 = max(a_user_id, b_user_id)
    if _get_match_between(user1, user2):
        return _get_match_between(user1, user2)
    try:
        r = table(MATCHES).insert({"user1_id": user1, "user2_id": user2}).execute()
        rows: List[Dict[str, Any]] = r.data or []
        return rows[0] if rows else None
    except Exception as e:
        logger.warning(f"Create match failed: {e}")
        return _get_match_between(user1, user2)


async def _notify_like(bot, to_tg_id: int, from_profile: Dict[str, Any], like_text: Optional[str] = None) -> None:
    name = from_profile.get("name") or "Пользователь"
    msg = f"Вас лайкнул(а) {name}!"
    if like_text:
        msg += f"\nСообщение: {like_text}"
    try:
        await bot.send_message(to_tg_id, msg)
    except Exception as e:
        logger.warning(f"Notify like failed: {e}")


async def _notify_match(bot, user_a_tg: int, user_b_tg: int) -> None:
    text = "🔥 У вас взаимная симпатия! Мы предложим анонимный чат в ближайшее время."
    for tg in (user_a_tg, user_b_tg):
        try:
            await bot.send_message(tg, text)
        except Exception as e:
            logger.warning(f"Notify match failed to {tg}: {e}")


async def _resolve_tg_id(users_id: int) -> Optional[int]:
    try:
        resp = table(USERS).select("tg_id").eq("id", users_id).limit(1).execute()
        rows: List[Dict[str, Any]] = resp.data or []
        return rows[0]["tg_id"] if rows else None
    except Exception:
        return None


@router.callback_query(F.data.startswith(f"{FEED_PREFIX}:"))
async def feed_actions(call: CallbackQuery, state: FSMContext) -> None:
    if not call.message:
        return
    db_user = await _get_db_user(call.message)
    if not db_user:
        await call.answer("Ошибка пользователя")
        return

    try:
        action, profile_id, to_user_id = _parse_cb(call.data or "")
    except ValueError:
        await call.answer("Некорректные данные")
        return

    if action == "skip":
        await call.answer("Пропущено")
        # show next
        next_profile = await _fetch_next_profile(db_user)
        if not next_profile:
            await call.message.edit_text("Больше анкет нет. Попробуйте позже.")
            return
        await _show_profile(call.message, db_user, next_profile)
        return

    if action == "like":
        await _insert_like(db_user["id"], to_user_id)
        # notify target user
        target_tg = await _resolve_tg_id(to_user_id)
        my_profile_resp = table(PROFILES).select("*").eq("user_id", db_user["id"]).limit(1).execute()
        my_profile = (my_profile_resp.data or [None])[0]
        if target_tg and my_profile:
            await _notify_like(call.message.bot, target_tg, my_profile)
        # check reciprocal and create match
        if _check_reciprocal_like(db_user["id"], to_user_id):
            m = _create_match(db_user["id"], to_user_id)
            if m:
                other_tg = target_tg
                my_tg = call.from_user.id if call.from_user else None
                if other_tg and my_tg:
                    await _notify_match(call.message.bot, my_tg, other_tg)
        await call.answer("Лайк отправлен")
        # show next
        next_profile = await _fetch_next_profile(db_user)
        if not next_profile:
            await call.message.edit_text("Больше анкет нет. Попробуйте позже.")
            return
        await _show_profile(call.message, db_user, next_profile)
        return

    if action == "like_msg":
        # set FSM waiting text
        await state.update_data(target_user_id=to_user_id)
        await state.set_state(LikeWithMessage.waiting_text)
        await call.message.answer("Напишите сообщение, которое отправим вместе с лайком.", reply_markup=ReplyKeyboardRemove())
        await call.answer()
        return

    if action == "like_vid":
        await state.update_data(target_user_id=to_user_id)
        await state.set_state(LikeWithVideo.waiting_video)
        await call.message.answer("Отправьте видео (как видео, не файлом).", reply_markup=ReplyKeyboardRemove())
        await call.answer()
        return


@router.message(LikeWithMessage.waiting_text)
async def like_with_message(message: Message, state: FSMContext) -> None:
    db_user = await _get_db_user(message)
    if not db_user:
        await message.answer("Ошибка пользователя.")
        return
    data = await state.get_data()
    to_user_id = data.get("target_user_id")
    text = (message.text or "").strip()
    if not to_user_id or not text:
        await message.answer("Сообщение не может быть пустым.")
        return

    await _insert_like(db_user["id"], int(to_user_id), message_text=text)

    target_tg = await _resolve_tg_id(int(to_user_id))
    my_profile_resp = table(PROFILES).select("*").eq("user_id", db_user["id"]).limit(1).execute()
    my_profile = (my_profile_resp.data or [None])[0]
    if target_tg and my_profile:
        await _notify_like(message.bot, target_tg, my_profile, like_text=text)

    if _check_reciprocal_like(db_user["id"], int(to_user_id)):
        m = _create_match(db_user["id"], int(to_user_id))
        if m:
            other_tg = target_tg
            my_tg = message.from_user.id if message.from_user else None
            if other_tg and my_tg:
                await _notify_match(message.bot, my_tg, other_tg)

    await state.clear()
    await message.answer("Лайк с сообщением отправлен.")


@router.message(LikeWithVideo.waiting_video, F.video)
async def like_with_video(message: Message, state: FSMContext) -> None:
    db_user = await _get_db_user(message)
    if not db_user:
        await message.answer("Ошибка пользователя.")
        return

    data = await state.get_data()
    to_user_id = data.get("target_user_id")
    if not to_user_id:
        await message.answer("Ошибка цели лайка.")
        return

    content = await _download_video_bytes(message)
    if not content:
        await message.answer("Не удалось получить видео. Попробуйте ещё раз отправить видео.")
        return

    try:
        up = upload_video(content)
        vurl = up.get("url")
    except Exception as e:
        logger.error(f"Cloudinary video upload error: {e}")
        await message.answer("Ошибка загрузки видео. Попробуйте позже.")
        return

    await _insert_like(db_user["id"], int(to_user_id), video_url=vurl)

    target_tg = await _resolve_tg_id(int(to_user_id))
    my_profile_resp = table(PROFILES).select("*").eq("user_id", db_user["id"]).limit(1).execute()
    my_profile = (my_profile_resp.data or [None])[0]
    if target_tg and my_profile:
        await _notify_like(message.bot, target_tg, my_profile, like_text="[видео]")

    if _check_reciprocal_like(db_user["id"], int(to_user_id)):
        m = _create_match(db_user["id"], int(to_user_id))
        if m:
            other_tg = target_tg
            my_tg = message.from_user.id if message.from_user else None
            if other_tg and my_tg:
                await _notify_match(message.bot, my_tg, other_tg)

    await state.clear()
    await message.answer("Лайк с видео отправлен.")


@router.message(LikeWithVideo.waiting_video)
async def like_with_video_invalid(message: Message, state: FSMContext) -> None:
    await message.answer("Пожалуйста, отправьте видео как видео (не файлом).")
