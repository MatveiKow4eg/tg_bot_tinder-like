from typing import Any, Dict, List, Optional

from loguru import logger
from supabase import Client, create_client

from config import get_settings

_client: Optional[Client] = None


class SupabaseNotConfigured(RuntimeError):
    pass


def get_supabase() -> Client:
    global _client
    if _client is not None:
        return _client

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise SupabaseNotConfigured(
            "Supabase credentials are missing. Ensure SUPABASE_URL and SUPABASE_ANON_KEY are set."
        )

    logger.info("Initializing Supabase client")
    _client = create_client(settings.supabase_url, settings.supabase_anon_key)
    return _client


def table(name: str):
    """Shortcut to access a table."""
    return get_supabase().table(name)


# Common table names (keep in sync with DB schema)
USERS = "Users"
PROFILES = "Profiles"
LIKES = "Likes"
MATCHES = "Matches"
CHATS = "Chats"
COMPLAINTS = "Complaints"
VIEWED_PROFILES = "ViewedProfiles"


# Basic user helpers

def upsert_user_basic(
    tg_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
) -> Dict[str, Any]:
    """Create or update basic telegram user record in Users table."""
    payload = {
        "tg_id": tg_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
    }
    logger.debug(f"Upserting user {tg_id} into {USERS}")
    resp = table(USERS).upsert(payload, on_conflict="tg_id").execute()
    # supabase-py v2 returns dict-like; normalize to first item when present
    if isinstance(resp.data, list) and resp.data:
        return resp.data[0]
    return {"tg_id": tg_id}


def get_user_by_tg_id(tg_id: int) -> Optional[Dict[str, Any]]:
    resp = table(USERS).select("*").eq("tg_id", tg_id).limit(1).execute()
    rows: List[Dict[str, Any]] = resp.data or []
    return rows[0] if rows else None


def mark_profile_viewed(user_id: int, profile_id: int) -> None:
    try:
        table(VIEWED_PROFILES).insert({
            "user_id": user_id,
            "profile_id": profile_id,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to insert ViewedProfiles record: {e}")


def get_schema_sql() -> str:
    """Return SQL for creating required tables and indexes in Supabase (execute once)."""
    return r"""
-- Users table: stores Telegram users
create table if not exists public."Users" (
  id bigserial primary key,
  tg_id bigint unique not null,
  username text,
  first_name text,
  last_name text,
  is_blocked boolean default false,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_users_tg_id on public."Users"(tg_id);

-- Profiles table: dating profile per user
create table if not exists public."Profiles" (
  id bigserial primary key,
  user_id bigint references public."Users"(id) on delete cascade,
  name text not null,
  gender text check (gender in ('male', 'female', 'other')),
  age int check (age >= 18 and age <= 100),
  city text,
  photos text[],
  bio text,
  is_active boolean default true,
  boosted_until timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_profiles_boosted_until on public."Profiles"(boosted_until desc nulls last);
create index if not exists idx_profiles_user_id on public."Profiles"(user_id);

-- Likes
create table if not exists public."Likes" (
  id bigserial primary key,
  from_user_id bigint references public."Users"(id) on delete cascade,
  to_user_id bigint references public."Users"(id) on delete cascade,
  message text,
  video_url text,
  created_at timestamptz default now(),
  unique (from_user_id, to_user_id)
);
create index if not exists idx_likes_from_to on public."Likes"(from_user_id, to_user_id);

-- Matches: canonical order (lower id first) to ensure uniqueness
create table if not exists public."Matches" (
  id bigserial primary key,
  user1_id bigint references public."Users"(id) on delete cascade,
  user2_id bigint references public."Users"(id) on delete cascade,
  is_active boolean default true,
  created_at timestamptz default now(),
  unique (least(user1_id, user2_id), greatest(user1_id, user2_id))
);
create index if not exists idx_matches_users on public."Matches"(user1_id, user2_id);

-- Chats: for anonymous chat sessions
create table if not exists public."Chats" (
  id bigserial primary key,
  match_id bigint references public."Matches"(id) on delete cascade,
  is_active boolean default true,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_chats_match on public."Chats"(match_id);

-- Complaints
create table if not exists public."Complaints" (
  id bigserial primary key,
  from_user_id bigint references public."Users"(id) on delete set null,
  against_user_id bigint references public."Users"(id) on delete set null,
  reason text,
  created_at timestamptz default now(),
  resolved boolean default false
);
create index if not exists idx_complaints_from on public."Complaints"(from_user_id);
create index if not exists idx_complaints_against on public."Complaints"(against_user_id);

-- ViewedProfiles: who viewed which profile and when
create table if not exists public."ViewedProfiles" (
  id bigserial primary key,
  user_id bigint references public."Users"(id) on delete cascade,
  profile_id bigint references public."Profiles"(id) on delete cascade,
  viewed_at timestamptz default now()
);
create index if not exists idx_viewed_user_time on public."ViewedProfiles"(user_id, viewed_at desc);
"""
