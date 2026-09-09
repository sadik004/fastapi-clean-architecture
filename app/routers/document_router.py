"""Document Router demonstrating Attribute-Based Access Control (ABAC)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.core.abac import ResourceContext
from app.core.dependencies import (
    check_abac_permission,
    get_current_authenticated_user,
    get_document_resource,
    get_document_service,
)
from app.schemas.auth import AuthenticatedUserResponse
from app.schemas.document import (
    DocumentApproveResponse,
    DocumentCreateRequest,
    DocumentResponse,
    DocumentUpdateRequest,
)
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["Documents - ABAC Engine"])


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new document",
)
async def create_document(
    payload: DocumentCreateRequest,
    current_user: Annotated[AuthenticatedUserResponse, Depends(get_current_authenticated_user)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    """Create a new document owned by the currently authenticated user."""
    doc = await service.create_document(
        title=payload.title,
        content=payload.content,
        owner_id=current_user.user_id,
        tenant_id=payload.tenant_id,
        department=payload.department,
        amount=payload.amount,
    )
    return DocumentResponse.model_validate(doc)


@router.get(
    "/{doc_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve document by ID (Tenant Isolated)",
)
async def get_document(
    doc_id: Annotated[int, Path(ge=1)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    resource: Annotated[
        ResourceContext,
        Depends(check_abac_permission("read", get_document_resource)),
    ],
) -> DocumentResponse:
    """Retrieve document details if tenant isolation policy passes."""
    doc = await service.get_document(doc_id)
    return DocumentResponse.model_validate(doc)


@router.put(
    "/{doc_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update document (ABAC Protected: Tenant + Ownership + Active State)",
)
async def update_document(
    doc_id: Annotated[int, Path(ge=1)],
    payload: DocumentUpdateRequest,
    service: Annotated[DocumentService, Depends(get_document_service)],
    resource: Annotated[
        ResourceContext,
        Depends(check_abac_permission("update", get_document_resource)),
    ],
) -> DocumentResponse:
    """Update document attributes.

    Protected by ABAC PolicyEngine evaluating:
    1. Tenant Isolation: subject.tenant_id == resource.tenant_id
    2. Ownership / Admin Bypass: subject.user_id == resource.owner_id or subject.role == 'admin'
    3. Lifecycle Guard: resource.status != 'archived' or subject.role == 'admin'
    """
    updated = await service.update_document(
        doc_id=doc_id,
        title=payload.title,
        content=payload.content,
        status=payload.status,
    )
    return DocumentResponse.model_validate(updated)


@router.delete(
    "/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete document (ABAC Protected: Tenant + Owner/Admin + Lifecycle)",
)
async def delete_document(
    doc_id: Annotated[int, Path(ge=1)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    resource: Annotated[
        ResourceContext,
        Depends(check_abac_permission("delete", get_document_resource)),
    ],
) -> Response:
    """Delete document.

    Protected by ABAC PolicyEngine evaluating:
    1. Tenant Isolation
    2. Ownership or Admin Bypass
    3. Lifecycle Guard (archived docs cannot be deleted by non-admins)
    """
    await service.delete_document(doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{doc_id}/approve",
    response_model=DocumentApproveResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve document (ABAC Protected: Contextual & Environmental Gate)",
)
async def approve_document(
    doc_id: Annotated[int, Path(ge=1)],
    current_user: Annotated[AuthenticatedUserResponse, Depends(get_current_authenticated_user)],
    service: Annotated[DocumentService, Depends(get_document_service)],
    resource: Annotated[
        ResourceContext,
        Depends(check_abac_permission("approve", get_document_resource)),
    ],
) -> DocumentApproveResponse:
    """Approve a document based on contextual policies.

    If document amount > 10,000:
      Requires department == 'finance' AND environment.is_business_hours == True.
    """
    doc = await service.approve_document(doc_id)
    return DocumentApproveResponse(
        id=doc.id,
        status=doc.status,
        approved_by=current_user.user_id,
        message="Document successfully approved under ABAC policy verification.",
    )
