"""Task coordination service managing Celery dispatch and result polling."""

from __future__ import annotations

from typing import Any

from celery.result import AsyncResult

from app.core.celery_app import celery_app
from app.tasks.report_tasks import generate_pdf_report, send_transactional_email


class TaskService:
    """Service layer orchestrating asynchronous background task lifecycle."""

    @staticmethod
    def dispatch_report_generation(user_id: int, report_type: str) -> str:
        """Dispatch PDF report generation asynchronously.

        Returns unique task_id immediately without blocking.
        """
        async_result = generate_pdf_report.delay(user_id, report_type)
        return str(async_result.id)

    @staticmethod
    def dispatch_transactional_email(
        recipient: str,
        subject: str,
        template: str,
        context: dict[str, Any],
    ) -> str:
        """Dispatch transactional email sending task asynchronously."""
        async_result = send_transactional_email.delay(recipient, subject, template, context)
        return str(async_result.id)

    @staticmethod
    def get_task_status(task_id: str) -> dict[str, Any]:
        """Poll Celery backend for task execution state and result payload."""
        result = AsyncResult(task_id, app=celery_app)
        state = result.state

        if state == "SUCCESS":
            return {
                "task_id": task_id,
                "status": state,
                "result": result.result if isinstance(result.result, dict) else {"data": result.result},
                "error": None,
            }
        elif state == "FAILURE":
            return {
                "task_id": task_id,
                "status": state,
                "result": None,
                "error": str(result.result) if result.result else "Task execution failed",
            }
        else:
            # PENDING, STARTED, RETRY, etc.
            return {
                "task_id": task_id,
                "status": state,
                "result": None,
                "error": None,
            }
