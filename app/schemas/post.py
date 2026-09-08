"""Pydantic schemas for Post entities and atomic multi-entity payloads."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import UserCreate, UserResponse


class PostResponse(BaseModel):
    """Schema representing an authored post response."""

    id: int
    title: str
    content: str
    user_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserWithInitialPostCreate(BaseModel):
    """Atomic request payload for creating a user and their initial post together."""

    user: UserCreate
    post_title: str = Field(..., min_length=1, max_length=255, description="Initial post title")
    post_content: str = Field(..., min_length=1, description="Initial post content")


class UserWithInitialPostResponse(BaseModel):
    """Response returned upon successful atomic creation of user and initial post."""

    user: UserResponse
    post: PostResponse

    model_config = ConfigDict(from_attributes=True)
