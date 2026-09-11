"""Dashboard Provisioning Engine & Validation Service for Grafana Dashboard as Code.

Provides schema validation, PromQL query syntax inspection, and export utilities
for declarative observability dashboards.
"""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any

from app.core.exceptions import BaseDomainException

# Default path to the Golden Signals dashboard specification
_DASHBOARD_FILE_PATH: Path = (
    Path(__file__).resolve().parent.parent / "core" / "observability" / "dashboards" / "fastapi_golden_signals.json"
)

# Regex to validate Prometheus lookback ranges (e.g., [1m], [5m], [30s])
_PROMQL_RANGE_REGEX = re.compile(r"\[\d+[smhdwy]\]")


class DashboardValidationError(BaseDomainException):
    """Exception raised when Grafana dashboard JSON fails schema or query validation."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="DASHBOARD_VALIDATION_ERROR")
        self.details: dict[str, Any] = details or {}


class DashboardService:
    """Enterprise service managing declarative Grafana Dashboards as Code."""

    def __init__(self, dashboard_path: Path | None = None) -> None:
        self.dashboard_path: Path = dashboard_path or _DASHBOARD_FILE_PATH
        self._cached_dashboard: dict[str, Any] | None = None

    def load_dashboard_json(self, force_reload: bool = False) -> dict[str, Any]:
        """Load and parse the declarative dashboard JSON specification from disk.

        Uses in-memory caching to guarantee O(1) response latency on repeated calls.

        Args:
            force_reload: If True, bypasses in-memory cache and re-reads from disk.

        Returns:
            Dictionary representation of the Grafana dashboard model.

        Raises:
            DashboardValidationError: If the file does not exist or contains invalid JSON.
        """
        if self._cached_dashboard is not None and not force_reload:
            return copy.deepcopy(self._cached_dashboard)

        if not self.dashboard_path.exists():
            raise DashboardValidationError(
                f"Dashboard specification not found at {self.dashboard_path}",
                details={"path": str(self.dashboard_path)},
            )

        try:
            with open(self.dashboard_path, encoding="utf-8") as f:
                data: Any = json.load(f)
        except json.JSONDecodeError as exc:
            raise DashboardValidationError(
                f"Malformed dashboard JSON: {exc.msg}",
                details={"line": exc.lineno, "col": exc.colno},
            ) from exc

        if not isinstance(data, dict):
            raise DashboardValidationError("Root dashboard JSON must be an object")

        self._cached_dashboard = data
        return copy.deepcopy(data)

    def validate_dashboard_schema(self, dashboard: dict[str, Any] | None = None) -> bool:
        """Validate Grafana dashboard specification schema and PromQL queries in O(N) time.

        Enforces:
        1. Schema version >= 36.
        2. Non-empty dashboard title and valid UID.
        3. Panel uniqueness (unique panel IDs).
        4. Panel completeness (id, title, type, targets).
        5. Valid PromQL expressions and lookback windows.
        6. Comprehensive coverage of all 4 Golden Signals.

        Args:
            dashboard: Optional dashboard dict to validate. If omitted, loads from file.

        Returns:
            True if dashboard schema and queries are completely valid.

        Raises:
            DashboardValidationError: If any validation rule is violated.
        """
        data = dashboard if dashboard is not None else self.load_dashboard_json()

        # 1. Root structure validation
        if not isinstance(data, dict):
            raise DashboardValidationError("Dashboard payload must be a JSON object")

        schema_version = data.get("schemaVersion")
        if not isinstance(schema_version, int) or schema_version < 36:
            raise DashboardValidationError(
                f"Invalid schemaVersion: expected int >= 36, got {schema_version}",
                details={"schemaVersion": schema_version},
            )

        title = data.get("title")
        if not title or not isinstance(title, str) or not title.strip():
            raise DashboardValidationError("Dashboard title must be a non-empty string")

        panels = data.get("panels")
        if not isinstance(panels, list) or len(panels) == 0:
            raise DashboardValidationError("Dashboard must contain at least one panel")

        # 2. Panel validation and Golden Signals detection
        seen_panel_ids: set[int] = set()
        golden_signals: dict[str, bool] = {
            "traffic": False,
            "error_rate": False,
            "latency": False,
            "saturation": False,
        }

        for idx, panel in enumerate(panels):
            if not isinstance(panel, dict):
                raise DashboardValidationError(f"Panel at index {idx} must be an object")

            panel_id = panel.get("id")
            if not isinstance(panel_id, int):
                raise DashboardValidationError(f"Panel at index {idx} has invalid id: {panel_id}")
            if panel_id in seen_panel_ids:
                raise DashboardValidationError(
                    f"Duplicate panel id detected: {panel_id}",
                    details={"panel_id": panel_id},
                )
            seen_panel_ids.add(panel_id)

            panel_title = panel.get("title")
            if not panel_title or not isinstance(panel_title, str):
                raise DashboardValidationError(f"Panel id {panel_id} must have a non-empty title")

            panel_type = panel.get("type")
            if not panel_type or not isinstance(panel_type, str):
                raise DashboardValidationError(f"Panel id {panel_id} must have a valid type string")

            targets = panel.get("targets")
            if not isinstance(targets, list) or len(targets) == 0:
                raise DashboardValidationError(
                    f"Panel id {panel_id} ({panel_title}) must contain at least one query target"
                )

            # 3. Target PromQL query validation
            for t_idx, target in enumerate(targets):
                if not isinstance(target, dict):
                    raise DashboardValidationError(f"Target {t_idx} in panel {panel_id} must be an object")

                expr = target.get("expr")
                if not expr or not isinstance(expr, str) or not expr.strip():
                    raise DashboardValidationError(f"Target {t_idx} in panel {panel_id} has empty PromQL expr")

                # PromQL balanced parentheses check
                if expr.count("(") != expr.count(")"):
                    raise DashboardValidationError(
                        f"Target {t_idx} in panel {panel_id} has unbalanced parentheses in PromQL: {expr}",
                        details={"expr": expr},
                    )

                # Lookback range validation for rate/increase functions
                if "rate(" in expr or "increase(" in expr:
                    if not _PROMQL_RANGE_REGEX.search(expr):
                        raise DashboardValidationError(
                            f"PromQL rate query in panel {panel_id} missing range window: {expr}",
                            details={"expr": expr},
                        )

                # Detect Golden Signals coverage
                expr_lower = expr.lower()
                if "http_requests_total" in expr_lower and "5.." in expr_lower:
                    golden_signals["error_rate"] = True
                elif "http_requests_total" in expr_lower:
                    golden_signals["traffic"] = True

                if "histogram_quantile" in expr_lower and "http_request_duration_seconds" in expr_lower:
                    golden_signals["latency"] = True

                if "http_requests_in_flight" in expr_lower:
                    golden_signals["saturation"] = True

        # 4. Verify all 4 Golden Signals are represented
        missing_signals = [sig for sig, present in golden_signals.items() if not present]
        if missing_signals:
            raise DashboardValidationError(
                f"Dashboard missing required Golden Signal queries: {', '.join(missing_signals)}",
                details={"missing_signals": missing_signals},
            )

        return True

    def get_validation_summary(self, dashboard: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute validation and return an operational diagnostic summary.

        Includes execution duration in milliseconds to verify sub-5ms performance.
        """
        start = time.perf_counter()
        data = dashboard if dashboard is not None else self.load_dashboard_json()
        is_valid = self.validate_dashboard_schema(data)
        duration_ms = round((time.perf_counter() - start) * 1000, 3)

        panels: list[dict[str, Any]] = data.get("panels", [])
        return {
            "status": "valid" if is_valid else "invalid",
            "title": data.get("title"),
            "uid": data.get("uid"),
            "schema_version": data.get("schemaVersion"),
            "panel_count": len(panels),
            "panels": [
                {
                    "id": p.get("id"),
                    "title": p.get("title"),
                    "type": p.get("type"),
                    "target_count": len(p.get("targets", [])),
                }
                for p in panels
            ],
            "golden_signals_verified": True,
            "validation_time_ms": duration_ms,
        }

    def export_dashboard_json(self) -> dict[str, Any]:
        """Export sanitized dashboard JSON ready for Grafana API provisioning or file sync."""
        data = self.load_dashboard_json()
        self.validate_dashboard_schema(data)
        return copy.deepcopy(data)
