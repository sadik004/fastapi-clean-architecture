"""Document Service encapsulating domain business logic for document workflows."""

from __future__ import annotations

from app.core.exceptions import DocumentNotFoundException
from app.repositories.document_repository import (
    DocumentEntity,
    DocumentRepositoryProtocol,
    InMemoryDocumentRepository,
)

_document_repository = InMemoryDocumentRepository()


def get_default_document_repository() -> InMemoryDocumentRepository:
    """Return the singleton in-memory document repository."""
    return _document_repository


class DocumentService:
    """Domain service managing document lifecycle and state transitions."""

    def __init__(self, repository: DocumentRepositoryProtocol | None = None) -> None:
        self._repo = repository or _document_repository

    async def get_document(self, doc_id: int) -> DocumentEntity:
        """Retrieve a document by ID or raise DocumentNotFoundException."""
        doc = await self._repo.get_by_id(doc_id)
        if doc is None:
            raise DocumentNotFoundException(doc_id=doc_id)
        return doc

    async def create_document(
        self,
        title: str,
        content: str,
        owner_id: int,
        tenant_id: str,
        department: str,
        amount: float = 0.0,
    ) -> DocumentEntity:
        """Create and persist a new document entity."""
        entity = DocumentEntity(
            id=0,
            title=title,
            content=content,
            owner_id=owner_id,
            tenant_id=tenant_id,
            department=department,
            status="active",
            amount=amount,
        )
        return await self._repo.create(entity)

    async def update_document(
        self,
        doc_id: int,
        title: str | None = None,
        content: str | None = None,
        status: str | None = None,
    ) -> DocumentEntity:
        """Update fields of an existing document."""
        doc = await self.get_document(doc_id)
        new_title = title if title is not None else doc.title
        new_content = content if content is not None else doc.content
        new_status = status if status is not None else doc.status

        updated = DocumentEntity(
            id=doc.id,
            title=new_title,
            content=new_content,
            owner_id=doc.owner_id,
            tenant_id=doc.tenant_id,
            department=doc.department,
            status=new_status,
            amount=doc.amount,
        )
        return await self._repo.update(updated)

    async def delete_document(self, doc_id: int) -> bool:
        """Delete a document by ID."""
        await self.get_document(doc_id)
        return await self._repo.delete(doc_id)

    async def approve_document(self, doc_id: int) -> DocumentEntity:
        """Approve a document and update status to 'approved'."""
        doc = await self.get_document(doc_id)
        approved = DocumentEntity(
            id=doc.id,
            title=doc.title,
            content=doc.content,
            owner_id=doc.owner_id,
            tenant_id=doc.tenant_id,
            department=doc.department,
            status="approved",
            amount=doc.amount,
        )
        return await self._repo.update(approved)
