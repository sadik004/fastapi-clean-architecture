"""Observability Dashboard Router for Grafana Dashboards as Code.

Exposes declarative Grafana JSON models and automated schema validation endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/observability/dashboards", tags=["Observability Dashboards"])

_dashboard_service = DashboardService()


def get_dashboard_service() -> DashboardService:
    """Dependency provider returning singleton DashboardService instance."""
    return _dashboard_service


@router.get(
    "/golden-signals",
    summary="Export declarative Grafana Dashboard JSON specification",
    response_description="Complete Grafana 10/11 dashboard model",
)
async def get_golden_signals_dashboard(
    service: DashboardService = Depends(get_dashboard_service),
) -> JSONResponse:
    """Export the production-ready Golden Signals Grafana Dashboard JSON specification.

    This payload can be imported directly into Grafana UI or deployed via
    Grafana Dashboards as Code provisioning (e.g. Terraform or Helm).
    """
    dashboard_data: dict[str, Any] = service.export_dashboard_json()
    return JSONResponse(
        content=dashboard_data,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get(
    "/validate",
    summary="Validate Grafana Dashboard schema and PromQL queries",
    response_model=dict[str, Any],
)
async def validate_dashboard_specification(
    service: DashboardService = Depends(get_dashboard_service),
) -> dict[str, Any]:
    """Execute automated schema and PromQL syntax validation on the dashboard model.

    Validates schemaVersion >= 36, unique panel IDs, lookback windows, and Golden Signals coverage.
    """
    return service.get_validation_summary()
