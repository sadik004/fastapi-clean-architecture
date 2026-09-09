"""Payment Router exposing idempotent financial charge endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse

from app.core.idempotency import (
    IdempotencyManager,
    compute_request_hash,
    get_idempotency_manager,
)
from app.schemas.payment import PaymentChargeRequest, PaymentChargeResponse
from app.services.payment_service import PaymentService, get_payment_service

router = APIRouter(prefix="/payments", tags=["Payments & Idempotency"])


@router.post(
    "/charge",
    response_model=PaymentChargeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Process a financial payment charge with idempotency protection",
)
async def process_payment_charge_endpoint(
    request: Request,
    payload: PaymentChargeRequest,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=64,
            description="Unique client idempotency token",
        ),
    ],
    payment_service: Annotated[PaymentService, Depends(get_payment_service)],
    idempotency_manager: Annotated[IdempotencyManager, Depends(get_idempotency_manager)],
) -> Response:
    """Execute payment charge with zero double-spending guarantee.

    Guarantees:
    - Mathematical Mutation Idempotency: f(f(x)) = f(x).
    - Payload Tampering Guard: Modified payloads reusing the key trigger HTTP 422.
    - In-Flight Collision Guard: Concurrent overlapping requests trigger HTTP 409.
    - O(1) Replay: Completed charges return cached response (HTTP 201) with 'X-Cache-Lookup: HIT-IDEMPOTENT'.
    """
    raw_body = await request.body()
    request_hash = compute_request_hash(request.method, request.url.path, raw_body)

    is_cached, cached_record = await idempotency_manager.check_or_acquire(
        key=idempotency_key,
        request_hash=request_hash,
    )

    if is_cached and cached_record:
        return JSONResponse(
            status_code=cached_record.get("status_code", status.HTTP_201_CREATED),
            content=cached_record.get("response_body"),
            headers={"X-Cache-Lookup": "HIT-IDEMPOTENT"},
        )

    try:
        charge = await payment_service.process_charge(
            order_id=payload.order_id,
            amount=payload.amount,
            currency=payload.currency,
        )
        response_data = charge.model_dump(mode="json")
        await idempotency_manager.record_success(
            key=idempotency_key,
            request_hash=request_hash,
            status_code=status.HTTP_201_CREATED,
            response_body=response_data,
        )
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content=response_data,
            headers={"X-Cache-Lookup": "MISS"},
        )
    except Exception as exc:
        await idempotency_manager.record_failure(key=idempotency_key, error_detail=str(exc))
        raise


__all__ = ["router"]
