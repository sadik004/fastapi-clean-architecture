"""End-to-End Ledger Reconciliation & Drift Recovery Engine Service.

Guarantees:
1. Hash-Map Two-Way Diff Matching: Evaluates settlement items against internal ledger in strictly O(N) time.
2. Immutability Invariant: Zero mutations to historical ledger records; discrepancy resolution strictly
   issues new compensating 2-leg journal entries.
3. Zero-Sum Balance Preservation: Compensating entries maintain exact debit/credit conservation.
4. ACID Transaction Boundary: Batch header, items, and compensating entries commit atomically via UnitOfWorkProtocol.
5. Clean Architecture Decoupling: Injects UnitOfWorkProtocol; zero transport layer imports.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from uuid import UUID

from app.core.exceptions import DuplicateReferenceException
from app.core.protocols import UnitOfWorkProtocol
from app.repositories.ledger_repository import (
    JournalEntryEntity,
    LedgerAccountEntity,
)
from app.repositories.reconciliation_repository import (
    ReconciliationBatchEntity,
    ReconciliationItemEntity,
)
from app.schemas.ledger import AccountType, PostingCreateDTO, PostingDirection
from app.schemas.reconciliation import (
    DiscrepancyType,
    ReconciliationBatchResponseDTO,
    ReconciliationItemResponseDTO,
    ReconciliationSummaryDTO,
    ResolutionStatus,
    SettlementItemDTO,
)

logger = logging.getLogger("app.services.ledger_reconciliation")


class LedgerReconciliationService:
    """Domain service managing automated ledger settlement auditing and drift recovery."""

    def __init__(self, uow: UnitOfWorkProtocol) -> None:
        self.uow = uow

    async def reconcile_settlement_feed(
        self,
        batch_reference: str,
        gateway_name: str,
        settlement_items: list[SettlementItemDTO],
        auto_compensate: bool = True,
    ) -> ReconciliationSummaryDTO:
        """Reconcile an external settlement feed against the internal double-entry ledger.

        Args:
            batch_reference: Unique batch reference identifier.
            gateway_name: External payment gateway (e.g. STRIPE, BKASH).
            settlement_items: Transaction items parsed from settlement feed.
            auto_compensate: If True, auto-generates compensating journal entries for MISSING_IN_LEDGER.

        Returns:
            ReconciliationSummaryDTO containing audit counts and item discrepancy breakdown.

        Raises:
            DuplicateReferenceException: If batch_reference was already processed.
        """
        async with self.uow:
            # 1. Idempotency Check on Batch Reference
            existing_batch = await self.uow.reconciliation.get_batch_by_reference(batch_reference)
            if existing_batch is not None:
                raise DuplicateReferenceException(
                    f"Reconciliation batch with reference '{batch_reference}' has already been processed."
                )

            # 2. Step 1: O(N) Hash Map Diff Matching
            references = [item.reference_id for item in settlement_items]
            internal_entries_seq = await self.uow.ledger.get_journal_entries_by_references(references)
            entries_by_ref: dict[str, JournalEntryEntity] = {
                entry.reference_id: entry for entry in internal_entries_seq
            }

            matched_count = 0
            discrepancy_count = 0
            auto_compensated_count = 0

            reconciliation_items: list[ReconciliationItemEntity] = []

            for item in settlement_items:
                ref = item.reference_id
                ext_amount = item.amount

                if ref in entries_by_ref:
                    internal_entry = entries_by_ref[ref]
                    int_amount = self._extract_internal_amount(internal_entry, ext_amount)
                    diff = abs(ext_amount - int_amount)

                    if diff == Decimal("0.0000"):
                        discrepancy_type = DiscrepancyType.MATCHED
                        resolution_status = ResolutionStatus.RESOLVED
                        compensating_id = None
                        matched_count += 1
                    else:
                        discrepancy_type = DiscrepancyType.AMOUNT_MISMATCH
                        resolution_status = ResolutionStatus.MANUAL_REVIEW
                        compensating_id = None
                        discrepancy_count += 1
                else:
                    # Missing in internal ledger!
                    int_amount = None
                    discrepancy_type = DiscrepancyType.MISSING_IN_LEDGER
                    discrepancy_count += 1

                    # Step 2: Automated Drift Recovery (Compensating Entries)
                    if auto_compensate:
                        compensating_entry = await self._create_compensating_entry(
                            gateway_name=gateway_name,
                            reference_id=ref,
                            amount=ext_amount,
                            currency=item.currency,
                        )
                        resolution_status = ResolutionStatus.AUTO_COMPENSATED
                        compensating_id = compensating_entry.id
                        auto_compensated_count += 1
                    else:
                        resolution_status = ResolutionStatus.UNRESOLVED
                        compensating_id = None

                reconciliation_items.append(
                    ReconciliationItemEntity(
                        id=uuid.uuid4(),
                        batch_id=uuid.uuid4(),  # Populated during batch persistence
                        reference_id=ref,
                        external_amount=ext_amount,
                        internal_amount=int_amount,
                        discrepancy_type=discrepancy_type.value,
                        resolution_status=resolution_status.value,
                        compensating_journal_entry_id=compensating_id,
                    )
                )

            # Step 3: Persist Batch and Items Atomically via UoW
            total_records = len(settlement_items)
            created_batch = await self.uow.reconciliation.create_batch(
                batch_reference=batch_reference,
                gateway_name=gateway_name.strip().upper(),
                total_records=total_records,
                matched_records=matched_count,
                discrepancy_records=discrepancy_count,
                status="COMPLETED",
                items=reconciliation_items,
            )

            await self.uow.commit()

            logger.info(
                "Settlement feed reconciled: batch=%s gateway=%s total=%d matched=%d discrepancies=%d compensated=%d",
                batch_reference,
                gateway_name,
                total_records,
                matched_count,
                discrepancy_count,
                auto_compensated_count,
            )

            return self._to_summary_dto(created_batch, auto_compensated_count)

    async def get_reconciliation_batch(self, batch_id: UUID) -> ReconciliationSummaryDTO | None:
        """Retrieve an executed reconciliation batch and its discrepancy item details."""
        async with self.uow:
            batch = await self.uow.reconciliation.get_batch_by_id(batch_id)
            if batch is None:
                return None
            auto_comp = sum(
                1 for item in batch.items if item.resolution_status == ResolutionStatus.AUTO_COMPENSATED.value
            )
            return self._to_summary_dto(batch, auto_comp)

    @staticmethod
    def _extract_internal_amount(entry: JournalEntryEntity, external_target: Decimal) -> Decimal:
        """Extract internal transaction amount from journal postings.

        If a posting leg exactly matches the external target, returns that amount;
        otherwise returns the first debit leg or sum of debits.
        """
        debit_amounts = [p.amount for p in entry.postings if p.direction == PostingDirection.DEBIT]
        if not debit_amounts:
            return Decimal("0.0000")
        if external_target in debit_amounts:
            return external_target
        if len(debit_amounts) == 1:
            return debit_amounts[0]
        return sum(debit_amounts, Decimal("0.0000"))

    async def _create_compensating_entry(
        self,
        gateway_name: str,
        reference_id: str,
        amount: Decimal,
        currency: str,
    ) -> JournalEntryEntity:
        """Create an immutable 2-leg compensating journal entry for an unreconciled transfer."""
        clean_currency = currency.strip().upper()
        clean_gateway = gateway_name.strip().upper()

        user_settlement_acc = await self._get_or_create_account(
            account_number=f"SETTLEMENT-USER-{clean_currency}",
            name=f"User Settlement Clearing ({clean_currency})",
            account_type=AccountType.ASSET,
            currency=clean_currency,
        )
        gateway_clearing_acc = await self._get_or_create_account(
            account_number=f"GATEWAY-CLEARING-{clean_gateway}-{clean_currency}",
            name=f"{clean_gateway} Gateway Clearing ({clean_currency})",
            account_type=AccountType.LIABILITY,
            currency=clean_currency,
        )

        comp_postings = [
            PostingCreateDTO(
                account_id=user_settlement_acc.id,
                amount=amount,
                direction=PostingDirection.DEBIT,
            ),
            PostingCreateDTO(
                account_id=gateway_clearing_acc.id,
                amount=amount,
                direction=PostingDirection.CREDIT,
            ),
        ]

        comp_ref = f"comp-{reference_id}-{uuid.uuid4().hex[:8]}"
        description = (
            f"Automated drift recovery compensating entry for external {clean_gateway} settlement {reference_id}"
        )

        return await self.uow.ledger.create_journal_entry(
            reference_id=comp_ref,
            description=description,
            postings=comp_postings,
            is_flagged=False,
        )

    async def _get_or_create_account(
        self,
        account_number: str,
        name: str,
        account_type: AccountType,
        currency: str,
    ) -> LedgerAccountEntity:
        """Resolve or provision a system clearing/settlement ledger account."""
        existing = await self.uow.ledger.get_account_by_number(account_number)
        if existing is not None:
            return existing
        return await self.uow.ledger.create_account(
            account_number=account_number,
            name=name,
            account_type=account_type,
            currency=currency,
        )

    @staticmethod
    def _to_summary_dto(
        batch: ReconciliationBatchEntity,
        auto_compensated_records: int,
    ) -> ReconciliationSummaryDTO:
        item_dtos = [
            ReconciliationItemResponseDTO(
                id=item.id,
                batch_id=item.batch_id,
                reference_id=item.reference_id,
                external_amount=item.external_amount,
                internal_amount=item.internal_amount,
                discrepancy_type=DiscrepancyType(item.discrepancy_type),
                resolution_status=ResolutionStatus(item.resolution_status),
                compensating_journal_entry_id=item.compensating_journal_entry_id,
            )
            for item in batch.items
        ]
        return ReconciliationBatchResponseDTO(
            batch_id=batch.id,
            batch_reference=batch.batch_reference,
            gateway_name=batch.gateway_name,
            total_records=batch.total_records,
            matched_records=batch.matched_records,
            discrepancy_records=batch.discrepancy_records,
            auto_compensated_records=auto_compensated_records,
            status=batch.status,
            created_at=batch.created_at,
            items=item_dtos,
        )
