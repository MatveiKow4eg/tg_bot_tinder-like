import os
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env if present (useful for local development)
load_dotenv()


def _parse_admin_ids(value: Optional[str]) -> List[int]:
    """Parse comma/semicolon separated list of admin IDs into integers."""
    ids: List[int] = []
    if value:
        parts = value.replace(";", ",").split(",")
        for p in parts:
            s = p.strip()
            if not s:
                continue
            try:
                ids.append(int(s))
            except ValueError:
                # Ignore invalid entries silently
                continue
    return ids


def _privacy_default() -> str:
    return (
        "Политика конфиденциальности\n\n"
        "Используя этого бота, вы соглашаетесь с нашей Политикой конфиденциальности.\n"
        "Данные, которые вы предоставляете (имя, пол, возраст, город, фото и описание),\n"
        "используются для функционирования сервиса знакомств, подбора анкет,\n"
        "обработки лайков/матчей и ведения анонимных чатов. Медиа-файлы хранятся в Cloudinary,\n"
        "а данные аккаунта — в Supabase (PostgreSQL). Мы не передаем ваши данные третьим лицам,\n"
        "за исключением случаев, предусмотренных законом. Команда /privacy покажет актуальную\n"
        "версию этой политики прямо в боте.\n"
    )


class Settings(BaseModel):
    # Telegram
    bot_token: str = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))

    # Supabase (PostgreSQL)
    supabase_url: str = Field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_anon_key: str = Field(default_factory=lambda: os.getenv("SUPABASE_ANON_KEY", ""))

    # Cloudinary
    cloudinary_cloud_name: str = Field(default_factory=lambda: os.getenv("CLOUDINARY_CLOUD_NAME", ""))
    cloudinary_api_key: str = Field(default_factory=lambda: os.getenv("CLOUDINARY_API_KEY", ""))
    cloudinary_api_secret: str = Field(default_factory=lambda: os.getenv("CLOUDINARY_API_SECRET", ""))

    # Admins (comma or semicolon separated Telegram user IDs)
    admin_ids: List[int] = Field(default_factory=lambda: _parse_admin_ids(os.getenv("ADMIN_IDS")))

    # Misc
    environment: str = Field(default_factory=lambda: os.getenv("ENV", "development"))
    debug: bool = Field(default_factory=lambda: os.getenv("DEBUG", "false").lower() in {"1", "true", "yes", "on"})

    # App limits and defaults
    privacy_text: str = Field(default_factory=_privacy_default)
    message_limit_per_day: int = 200
    media_limit_per_day: int = 50
    search_radius_km: int = 200

    class Config:
        arbitrary_types_allowed = True

    def validate_required(self) -> None:
        missing = []
        if not self.bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.supabase_url:
            missing.append("SUPABASE_URL")
        if not self.supabase_anon_key:
            missing.append("SUPABASE_ANON_KEY")
        if not self.cloudinary_cloud_name:
            missing.append("CLOUDINARY_CLOUD_NAME")
        if not self.cloudinary_api_key:
            missing.append("CLOUDINARY_API_KEY")
        if not self.cloudinary_api_secret:
            missing.append("CLOUDINARY_API_SECRET")
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}


_settings_cache: Optional[Settings] = None


def get_settings() -> Settings:
    """Lazy settings accessor with module-level cache."""
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = Settings()
    return _settings_cache
