"""Canonical Architecture Protocols and Interface Contracts.

This module unifies and re-exports all decoupled repository and persistence contracts.
Services and business layers depend strictly on these abstract Protocol interfaces,
honoring the Dependency Inversion Principle (DIP) and Clean Architecture boundaries.
"""

from __future__ import annotations

from app.core.unit_of_work import UnitOfWorkProtocol
from app.repositories.catalog_repository import CatalogRepositoryProtocol
from app.repositories.document_repository import DocumentRepositoryProtocol
from app.repositories.ledger_repository import LedgerRepositoryProtocol
from app.repositories.order_repository import OrderRepositoryProtocol
from app.repositories.outbox_repository import OutboxRepositoryProtocol
from app.repositories.post_repository import PostRepositoryProtocol
from app.repositories.product_repository import ProductRepositoryProtocol
from app.repositories.reconciliation_repository import ReconciliationRepositoryProtocol
from app.repositories.user_repository import UserRepositoryProtocol

__all__ = [
    "CatalogRepositoryProtocol",
    "DocumentRepositoryProtocol",
    "LedgerRepositoryProtocol",
    "OrderRepositoryProtocol",
    "OutboxRepositoryProtocol",
    "PostRepositoryProtocol",
    "ProductRepositoryProtocol",
    "ReconciliationRepositoryProtocol",
    "UnitOfWorkProtocol",
    "UserRepositoryProtocol",
]
