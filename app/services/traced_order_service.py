"""Traced Order Service simulating an enterprise multi-step distributed workflow with OpenTelemetry spans.

Demonstrates Directed Acyclic Graph (DAG) parent-child span hierarchies, contextual span attributes,
and exception recording across asynchronous microservice boundaries.
"""

from __future__ import annotations

import asyncio
from typing import Any

from opentelemetry.trace import StatusCode

from app.core.logging import get_logger
from app.core.tracing import format_span_id, format_trace_id, get_tracer, trace_span

logger = get_logger("app.services.traced_order")


class TracedOrderService:
    """Service simulating a multi-step distributed checkout workflow instrumented with OpenTelemetry."""

    def __init__(self) -> None:
        self.tracer = get_tracer("app.services.traced_order")

    @trace_span("inventory.verify", attributes={"db.system": "postgresql", "inventory.warehouse": "zone-1"})
    async def verify_inventory(self, item_id: str, quantity: int) -> bool:
        """Child Span 1: Verify inventory availability."""
        logger.info("verifying_inventory", item_id=item_id, quantity=quantity)
        await asyncio.sleep(0.001)  # Simulate microsecond db/cache lookup
        return True

    @trace_span("payment.charge", attributes={"payment.gateway": "stripe", "payment.currency": "USD"})
    async def charge_payment(self, user_id: str, amount: float, should_fail: bool = False) -> str:
        """Child Span 2: Process payment transaction."""
        logger.info("charging_payment", user_id=user_id, amount=amount)
        await asyncio.sleep(0.001)  # Simulate gateway roundtrip
        if should_fail:
            raise ValueError("Payment gateway declined: insufficient funds")
        return f"tx_ch_{user_id[:8]}"

    @trace_span("kafka.dispatch", attributes={"messaging.system": "kafka", "messaging.destination": "order.events"})
    async def dispatch_order_event(self, order_id: str, status: str) -> None:
        """Child Span 3: Publish domain event to message broker."""
        logger.info("dispatching_event", order_id=order_id, status=status)
        await asyncio.sleep(0.001)  # Simulate producer dispatch

    async def execute_checkout_flow(
        self,
        order_id: str,
        user_id: str,
        item_id: str,
        quantity: int,
        amount: float,
        fail_at_step: str | None = None,
    ) -> dict[str, Any]:
        """Execute the full end-to-end checkout workflow under parent span 'order.checkout'.

        Args:
            order_id: Unique order identifier.
            user_id: Customer user identifier.
            item_id: Inventory product ID.
            quantity: Purchased item count.
            amount: Total monetary value.
            fail_at_step: Optional step name ('inventory', 'payment', 'kafka') to trigger a failure.

        Returns:
            Dictionary containing execution metadata, trace_id, parent_span_id, and child span IDs.
        """
        with self.tracer.start_as_current_span(
            "order.checkout",
            attributes={
                "order.id": order_id,
                "user.id": user_id,
                "order.total_amount": amount,
            },
        ) as parent_span:
            parent_ctx = parent_span.get_span_context()
            trace_id = format_trace_id(parent_ctx.trace_id)
            parent_span_id = format_span_id(parent_ctx.span_id)

            logger.info("order_checkout_started", order_id=order_id, user_id=user_id, amount=amount)

            # Step 1: Inventory verification
            if fail_at_step == "inventory":
                with self.tracer.start_as_current_span("inventory.verify") as inv_span:
                    err_inv = RuntimeError("Inventory service unavailable")
                    inv_span.record_exception(err_inv)
                    inv_span.set_status(StatusCode.ERROR, str(err_inv))
                    parent_span.record_exception(err_inv)
                    parent_span.set_status(StatusCode.ERROR, str(err_inv))
                    raise err_inv

            await self.verify_inventory(item_id=item_id, quantity=quantity)

            # Step 2: Payment processing
            should_fail_payment = fail_at_step == "payment"
            try:
                tx_id = await self.charge_payment(user_id=user_id, amount=amount, should_fail=should_fail_payment)
            except Exception as exc:
                parent_span.record_exception(exc)
                parent_span.set_status(StatusCode.ERROR, str(exc))
                raise

            # Step 3: Kafka event dispatch
            if fail_at_step == "kafka":
                with self.tracer.start_as_current_span("kafka.dispatch") as k_span:
                    err_kafka = RuntimeError("Kafka cluster unreachable")
                    k_span.record_exception(err_kafka)
                    k_span.set_status(StatusCode.ERROR, str(err_kafka))
                    parent_span.record_exception(err_kafka)
                    parent_span.set_status(StatusCode.ERROR, str(err_kafka))
                    raise err_kafka

            await self.dispatch_order_event(order_id=order_id, status="PAID")

            parent_span.set_status(StatusCode.OK)
            logger.info("order_checkout_completed", order_id=order_id, tx_id=tx_id)

            return {
                "status": "success",
                "order_id": order_id,
                "trace_id": trace_id,
                "parent_span_id": parent_span_id,
                "transaction_id": tx_id,
                "spans_created": 4,  # parent + 3 children
            }
