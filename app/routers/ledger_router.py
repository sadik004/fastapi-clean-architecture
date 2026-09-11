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
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.core.dependencies import (
    get_distributed_lock,
    get_fx_conversion_service,
    get_ledger_service,
    get_ledger_transfer_service,
)
from app.core.distributed_lock import AsyncDistributedLock
from app.schemas.ledger import (
    AccountBalanceResponse,
    ActiveLockInfo,
    FundTransferRequestDTO,
    FundTransferResponseDTO,
    JournalEntryCreateDTO,
    JournalEntryResponseDTO,
    LedgerAccountCreate,
    LedgerAccountResponse,
    LedgerLockStatusResponse,
)
from app.services.fx_conversion_service import FXConversionService
from app.services.ledger_domain_service import LedgerDomainService
from app.services.ledger_transfer_service import LedgerTransferService

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


@router.post(
    "/transfers",
    response_model=FundTransferResponseDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Execute an atomic multi-leg fund transfer",
)
async def transfer_funds_endpoint(
    payload: FundTransferRequestDTO,
    service: Annotated[LedgerTransferService, Depends(get_ledger_transfer_service)],
) -> FundTransferResponseDTO:
    """Execute an atomic multi-leg fund transfer satisfying the non-negative balance invariant."""
    return await service.transfer_funds(
        source_account_id=payload.source_account_id,
        destination_account_id=payload.destination_account_id,
        amount=payload.amount,
        fee_amount=payload.fee_amount,
        fee_account_id=payload.fee_account_id,
        reference_id=payload.reference_id,
        description=payload.description,
        exchange_rate=payload.exchange_rate,
    )


@router.get(
    "/fx/rates",
    response_model=dict[str, Decimal],
    status_code=status.HTTP_200_OK,
    summary="Retrieve supported currency pairs and simulation FX rates",
)
async def get_fx_rates_endpoint(
    service: Annotated[FXConversionService, Depends(get_fx_conversion_service)],
) -> dict[str, Decimal]:
    """Retrieve supported currency pairs and reference foreign exchange rates."""
    return service.get_supported_rates()


@router.get(
    "/locks/status",
    response_model=LedgerLockStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Inspect active distributed ledger locks",
)
async def get_ledger_locks_status_endpoint(
    lock_manager: Annotated[AsyncDistributedLock, Depends(get_distributed_lock)],
) -> LedgerLockStatusResponse:
    """Retrieve operational telemetry and inspect active Redis locks on ledger accounts."""
    raw_locks = await lock_manager.get_active_locks("lock:account:*")
    locks = [
        ActiveLockInfo(
            key=item["key"],
            resource=item["resource"],
            ttl_ms=item["ttl_ms"],
        )
        for item in raw_locks
    ]
    return LedgerLockStatusResponse(
        status="active",
        active_locks_count=len(locks),
        locks=locks,
    )
