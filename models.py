from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class User(BaseModel):
    id: Optional[int] = None
    tg_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_blocked: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Profile(BaseModel):
    id: Optional[int] = None
    user_id: int
    name: str
    gender: str = Field(pattern=r"^(male|female|other)$")
    age: int = Field(ge=18, le=100)
    city: Optional[str] = None
    photos: List[str] = Field(default_factory=list)
    bio: Optional[str] = None
    is_active: bool = True
    boosted_until: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Like(BaseModel):
    id: Optional[int] = None
    from_user_id: int
    to_user_id: int
    message: Optional[str] = None
    video_url: Optional[str] = None
    created_at: Optional[datetime] = None


class Match(BaseModel):
    id: Optional[int] = None
    user1_id: int
    user2_id: int
    is_active: bool = True
    created_at: Optional[datetime] = None


class Chat(BaseModel):
    id: Optional[int] = None
    match_id: int
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Complaint(BaseModel):
    id: Optional[int] = None
    from_user_id: Optional[int] = None
    against_user_id: Optional[int] = None
    reason: Optional[str] = None
    created_at: Optional[datetime] = None
    resolved: bool = False


class ViewedProfile(BaseModel):
    id: Optional[int] = None
    user_id: int
    profile_id: int
    viewed_at: Optional[datetime] = None
