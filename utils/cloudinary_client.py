from typing import Any, Dict, Optional

import cloudinary
import cloudinary.uploader
from loguru import logger

from config import get_settings


class CloudinaryNotConfigured(RuntimeError):
    pass


def _init_cloudinary() -> None:
    settings = get_settings()
    if not (settings.cloudinary_cloud_name and settings.cloudinary_api_key and settings.cloudinary_api_secret):
        raise CloudinaryNotConfigured(
            "Cloudinary credentials are missing. Ensure CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET are set."
        )
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True,
    )


_initialized = False


def ensure_initialized() -> None:
    global _initialized
    if not _initialized:
        logger.info("Initializing Cloudinary client")
        _init_cloudinary()
        _initialized = True


def upload_image(file_bytes: bytes, folder: str = "tinderbot/photos") -> Dict[str, Any]:
    ensure_initialized()
    result = cloudinary.uploader.upload(file_bytes, folder=folder, resource_type="image")
    return {
        "public_id": result.get("public_id"),
        "url": result.get("secure_url") or result.get("url"),
    }


def upload_video(file_bytes: bytes, folder: str = "tinderbot/videos") -> Dict[str, Any]:
    ensure_initialized()
    result = cloudinary.uploader.upload(file_bytes, folder=folder, resource_type="video")
    return {
        "public_id": result.get("public_id"),
        "url": result.get("secure_url") or result.get("url"),
    }


def delete_asset(public_id: str, resource_type: str = "image") -> bool:
    ensure_initialized()
    try:
        res = cloudinary.uploader.destroy(public_id, resource_type=resource_type)
        return res.get("result") in {"ok", "not found"}
    except Exception as e:
        logger.warning(f"Failed to delete Cloudinary asset {public_id}: {e}")
        return False
