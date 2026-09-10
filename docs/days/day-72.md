# Day 72: Distributed Tracing Architecture with OpenTelemetry (Spans, Tracer Providers & W3C Trace Context Propagation)

## 1. Architectural Overview & Context

In microservices and distributed asynchronous backend systems, a single user transaction traverses diverse services: API gateways, ASGI applications, background workers, databases, and message brokers. While structured JSON logging with correlation IDs (Day 71) provides searchable log streams, it fails to quantify **where time is spent across call graphs** or provide a visual, structured causal relationship between concurrent operations.

**OpenTelemetry (OTel)** solves this by establishing an industry-standard telemetry specification. Distributed Tracing constructs a Directed Acyclic Graph (DAG) of **Spans**, where each span represents a timed unit of contiguous execution bounded by metadata attributes and W3C standard trace propagation headers.

```
                              [Incoming HTTP Request]
                                         │
                   ┌─────────────────────▼─────────────────────┐
                   │  Root Server Span: HTTP POST /order-flow  │
                   │  W3C traceparent: 00-{trace_id}-{span_id} │
                   └─────────────────────┬─────────────────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────────┐
        │ (child 1)                      │ (child 2)                      │ (child 3)
┌───────▼───────────────┐        ┌───────▼───────────────┐        ┌───────▼───────────────┐
│   inventory.verify    │        │    payment.charge     │        │    kafka.dispatch     │
│  parent_id: root_span │        │  parent_id: root_span │        │  parent_id: root_span │
│  db.system: postgres  │        │  payment.gateway: str │        │  messaging: kafka     │
└───────────────────────┘        └───────────────────────┘        └───────────────────────┘
```

---

## 2. Core Components Built

### 2.1 OpenTelemetry TracerProvider & In-Memory Exporter (`app/core/tracing.py`)
- Configures `TracerProvider` with standardized `Resource` attributes (`service.name`, `service.version`, `deployment.environment`).
- Registers `SimpleSpanProcessor` with `InMemorySpanExporter` for 100% self-contained local testing and diagnostics without external network dependencies (Jaeger/Zipkin).
- Provides `@trace_span(name: str, attributes: dict[str, Any] | None)` decorator supporting both async coroutines and sync functions with automatic exception capture (`span.record_exception(exc)`) and status marking (`StatusCode.ERROR`).

### 2.2 W3C Trace Context Middleware & Trace-to-Log Correlation (`app/core/middleware.py`)
- **W3C `traceparent` Extraction**: Reads incoming `traceparent` headers via `TraceContextTextMapPropagator().extract(carrier)`.
- **Root Server Span**: Automatically wraps each incoming ASGI HTTP request within a root `SERVER` span.
- **Trace-to-Log Correlation**: Dynamically extracts the active 32-character hex `trace_id` and 16-character hex `span_id`, injecting them directly into `structlog.contextvars`. Every structured JSON log line automatically carries `trace_id` and `span_id`.
- **Downstream Header Injection**: Emits `traceparent` in the outgoing HTTP response (`00-{trace_id}-{span_id}-01`).

### 2.3 Traced Service Workflow (`app/services/traced_order_service.py`)
- Simulates an end-to-end multi-step checkout workflow:
  1. Root / Parent Span: `order.checkout`
  2. Child Span 1: `inventory.verify`
  3. Child Span 2: `payment.charge`
  4. Child Span 3: `kafka.dispatch`
- Ensures every child span explicitly references the parent's `span_id` as its `parent_span_id`.

### 2.4 Diagnostics Telemetry Endpoints (`app/routers/tracing_router.py`)
- `POST /observability/tracing/order-flow`: Dispatches the multi-span checkout workflow and returns generated trace telemetry.
- `GET /observability/tracing/spans/{trace_id}`: Queries the in-memory exporter for all recorded spans belonging to that trace ID, rendering execution durations in milliseconds, span statuses, and custom attributes.
- `DELETE /observability/tracing/spans`: Clears the in-memory exporter buffer.

---

## 3. Verification & Test Architecture (`tests/test_distributed_tracing.py`)

The test suite validates 7 architectural properties:
1. **DAG Hierarchy**: Asserts parent span (`order.checkout`) has `parent is None`, and all 3 child spans have `parent_span_id == parent.span_id` with identical `trace_id`.
2. **W3C Propagation (New Trace)**: Verifies response contains valid format `00-{32hex}-{16hex}-01`.
3. **W3C Propagation (Existing Trace)**: Verifies incoming `traceparent` preserves client trace ID across the service boundary.
4. **Trace-to-Log Correlation**: Verifies structlog JSON logs contain the matching `trace_id` and `span_id`.
5. **Error Recording**: Verifies exceptions set `StatusCode.ERROR` and record exception stack traces onto the span.
6. **Decorator Flexibility**: Tests `@trace_span` on both synchronous functions and async coroutines.
7. **Concurrency Isolation**: Verifies concurrent asyncio tasks maintain independent active span contexts without crosstalk.
