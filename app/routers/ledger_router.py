"""Fintech Double-Entry Ledger API Router.

Endpoints:
- POST /api/v1/ledger/accounts: Create a new ledger account.
- GET  /api/v1/ledger/accounts: List ledger accounts.
- GET  /api/v1/ledger/accounts/{account_id}: Retrieve account details.
- GET  /api/v1/ledger/accounts/{account_id}/balance: Dynamically compute account balance.
- POST /api/v1/ledger/entries: Post a balanced multi-leg journal entry.
- GET  /api/v1/ledger/entries/{entry_id}: Retrieve a journal entry by ID.

Clean Architecture Boundary Invariants:
- Zero imports from app.models.* (Rule 1).
- No inline Pydantic models (Rule 5).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.core.dependencies import get_ledger_service
from app.schemas.ledger import (
    AccountBalanceResponse,
    JournalEntryCreateDTO,
    JournalEntryResponseDTO,
    LedgerAccountCreate,
    LedgerAccountResponse,
)
from app.services.ledger_domain_service import LedgerDomainService

router = APIRouter(prefix="/api/v1/ledger", tags=["Fintech Double-Entry Ledger"])


@router.post(
    "/accounts",
    response_model=LedgerAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new ledger account",
)
async def create_ledger_account_endpoint(
    payload: LedgerAccountCreate,
    service: Annotated[LedgerDomainService, Depends(get_ledger_service)],
) -> LedgerAccountResponse:
    """Create a new ledger account with a specified account number, type, and currency."""
    return await service.create_account(payload)


@router.get(
    "/accounts",
    response_model=list[LedgerAccountResponse],
    status_code=status.HTTP_200_OK,
    summary="List all ledger accounts",
)
async def list_ledger_accounts_endpoint(
    service: Annotated[LedgerDomainService, Depends(get_ledger_service)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LedgerAccountResponse]:
    """List ledger accounts with bounded pagination."""
    return await service.list_accounts(limit=limit, offset=offset)


@router.get(
    "/accounts/{account_id}",
    response_model=LedgerAccountResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve ledger account by ID",
)
async def get_ledger_account_endpoint(
    account_id: Annotated[uuid.UUID, Path(description="The UUIDv7 primary key of the account")],
    service: Annotated[LedgerDomainService, Depends(get_ledger_service)],
) -> LedgerAccountResponse:
    """Retrieve details for a specific ledger account."""
    return await service.get_account(account_id)


@router.get(
    "/accounts/{account_id}/balance",
    response_model=AccountBalanceResponse,
    status_code=status.HTTP_200_OK,
    summary="Compute dynamic ledger account balance",
)
async def get_account_balance_endpoint(
    account_id: Annotated[uuid.UUID, Path(description="The UUIDv7 primary key of the account")],
    service: Annotated[LedgerDomainService, Depends(get_ledger_service)],
) -> AccountBalanceResponse:
    """Dynamically compute the account balance from immutable postings according to normal balance rules."""
    return await service.get_account_balance(account_id)


@router.post(
    "/entries",
    response_model=JournalEntryResponseDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Record an atomic multi-leg journal entry",
)
async def create_journal_entry_endpoint(
    payload: JournalEntryCreateDTO,
    service: Annotated[LedgerDomainService, Depends(get_ledger_service)],
) -> JournalEntryResponseDTO:
    """Record an atomic multi-leg journal entry satisfying the zero-sum balance invariant."""
    return await service.record_journal_entry(payload)


@router.get(
    "/entries/{entry_id}",
    response_model=JournalEntryResponseDTO,
    status_code=status.HTTP_200_OK,
    summary="Retrieve journal entry by ID",
)
async def get_journal_entry_endpoint(
    entry_id: Annotated[uuid.UUID, Path(description="The UUIDv7 primary key of the journal entry")],
    service: Annotated[LedgerDomainService, Depends(get_ledger_service)],
) -> JournalEntryResponseDTO:
    """Retrieve a journal entry and its child posting legs."""
    return await service.get_journal_entry(entry_id)
