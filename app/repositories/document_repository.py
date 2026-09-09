"""Document Repository Abstraction and In-Memory Storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class DocumentEntity:
    """Domain entity representing a managed document."""

    id: int
    title: str
    content: str
    owner_id: int
    tenant_id: str
    department: str
    status: str = "active"
    amount: float = 0.0


class DocumentRepositoryProtocol(Protocol):
    """Protocol contract for Document persistence."""

    async def get_by_id(self, doc_id: int) -> DocumentEntity | None:
        """Retrieve a document by primary key."""
        ...

    async def create(self, doc: DocumentEntity) -> DocumentEntity:
        """Persist a new document entity."""
        ...

    async def update(self, doc: DocumentEntity) -> DocumentEntity:
        """Update an existing document entity."""
        ...

    async def delete(self, doc_id: int) -> bool:
        """Remove a document entity by primary key."""
        ...


class InMemoryDocumentRepository:
    """Thread-safe in-memory document repository providing O(1) lookups."""

    def __init__(self) -> None:
        self._store: dict[int, DocumentEntity] = {}
        self._sequence: int = 0

    async def get_by_id(self, doc_id: int) -> DocumentEntity | None:
        """Retrieve document by ID in strictly O(1) time."""
        return self._store.get(doc_id)

    async def create(self, doc: DocumentEntity) -> DocumentEntity:
        """Persist document with auto-incremented primary key."""
        if doc.id <= 0:
            self._sequence += 1
            doc.id = self._sequence
        self._store[doc.id] = doc
        return doc

    async def update(self, doc: DocumentEntity) -> DocumentEntity:
        """Update document in storage."""
        self._store[doc.id] = doc
        return doc

    async def delete(self, doc_id: int) -> bool:
        """Delete document from storage in O(1) time."""
        if doc_id in self._store:
            del self._store[doc_id]
            return True
        return False

    def clear(self) -> None:
        """Reset repository store (used in test fixtures)."""
        self._store.clear()
        self._sequence = 0
