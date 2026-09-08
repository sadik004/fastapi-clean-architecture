"""Models package initialization and declarative registry exports."""

from app.models.post import PostModel
from app.models.user import UserModel

__all__ = ["PostModel", "UserModel"]

