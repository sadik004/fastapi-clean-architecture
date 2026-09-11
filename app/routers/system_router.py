"""System and Graduation Telemetry Router."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status

from app.schemas.system import GraduationSummaryResponseDTO
from app.services.system_service import SystemService

router = APIRouter(prefix="/system", tags=["System"])


@router.get(
    "/graduation-summary",
    response_model=GraduationSummaryResponseDTO,
    status_code=status.HTTP_200_OK,
    summary="Executive Graduation Telemetry & Flight Readiness Attestation",
    description="Returns full enterprise system consolidation metrics, module compliance, and curriculum completion attestation.",
)
async def get_graduation_summary(request: Request) -> Any:
    """Retrieve master executive graduation telemetry and architecture attestation."""
    routes_count = len(request.app.routes)
    return SystemService.get_graduation_summary(routes_count=routes_count)
