"""Models package initialization and declarative registry exports."""

from app.models.order import OrderModel
from app.models.outbox import OutboxEventModel
from app.models.post import PostModel
from app.models.product import ProductModel
from app.models.user import UserModel

__all__ = ["OrderModel", "OutboxEventModel", "PostModel", "ProductModel", "UserModel"]
