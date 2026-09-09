"""Background tasks for reporting and communications."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Any

from celery import Task

from app.core.celery_app import celery_app


@celery_app.task(name="app.tasks.report_tasks.generate_pdf_report")
def generate_pdf_report(user_id: int, report_type: str) -> dict[str, Any]:
    """Simulate and generate a structured PDF report asynchronously.

    Computes deterministic SHA-256 payload checksum and produces
    structured metadata without blocking FastAPI event loop.
    """
    start_time = time.time()
    timestamp = datetime.now(UTC).isoformat()
    raw_content = f"PDF_REPORT_DATA|user_id={user_id}|type={report_type}|ts={timestamp}"
    checksum = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
    elapsed_seconds = round(time.time() - start_time, 4)

    return {
        "status": "COMPLETED",
        "user_id": user_id,
        "report_type": report_type,
        "filename": f"report_{user_id}_{report_type}_{int(time.time())}.pdf",
        "checksum_sha256": checksum,
        "size_bytes": 1024 * 42,  # 42 KB simulated report
        "generated_at": timestamp,
        "execution_time_seconds": elapsed_seconds,
    }


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    name="app.tasks.report_tasks.send_transactional_email",
)
def send_transactional_email(
    self: Task,
    recipient: str,
    subject: str,
    template: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Background email dispatch task with automatic exponential backoff retry.

    If simulate_error is passed in context, raises an error and invokes self.retry()
    up to max_retries with exponential backoff delay.
    """
    try:
        if context.get("simulate_error"):
            raise ConnectionError("Simulated SMTP gateway timeout error")

        return {
            "status": "SENT",
            "recipient": recipient,
            "subject": subject,
            "template": template,
            "delivered_at": datetime.now(UTC).isoformat(),
            "attempt": self.request.retries + 1,
        }
    except Exception as exc:
        if self.request.retries < self.max_retries:
            # Exponential backoff: 5s, 10s, 20s...
            countdown = int(self.default_retry_delay * (2**self.request.retries))
            raise self.retry(exc=exc, countdown=countdown) from exc
        raise
