# RCA: Day 42 - Idempotency Key Payload Tampering, In-Flight Concurrency Races, and Bounded Memory Safety

- **Date**: 2026-09-09
- **Trigger**: Financial double-spend vulnerability analysis, payload tampering risks, and concurrency hazards during Day 42 Idempotency Key architecture implementation:
  1. The "Double-Spend Catastrophe" caused by client retry loops during network timeouts on non-idempotent mutation endpoints.
  2. The "Payload Tampering Hazard" where clients reuse an existing `Idempotency-Key` with altered payload parameters (e.g. changing charge amount from $10 to $500).
  3. The "In-Flight Concurrency Race" where concurrent duplicate requests arrive milliseconds apart and execute the payment gateway simultaneously before either record completes.
  4. The "Unbounded Redis Memory Hazard" if idempotency records are persisted indefinitely without an explicit expiration TTL.

- **Faulty Code / Pattern**:
  ```python
  # FLAW 1: Naive mutation endpoint — client retries cause duplicate credit card debits
  @router.post("/payments/charge")
  async def charge_naive(request: PaymentChargeRequest, service: PaymentService = Depends()):
      # No idempotency check! Every POST charges the customer's card again!
      return await service.charge(request.account_id, request.amount, request.currency)

  # FLAW 2: Blind cache replay without payload hash comparison — silent payload tampering
  record = await redis.get(f"idempotency:{key}")
  if record:
      # If the client sent $500 instead of the original $10, this falsely returns the $10 receipt!
      return JSONResponse(status_code=201, content=record["response_body"])

  # FLAW 3: Storing idempotency records without TTL — permanent RAM growth / Redis OOM crash
  await redis.set(f"idempotency:{key}", json.dumps(record))  # Missing ex / px TTL!
  ```

- **Root Cause**:
  1. **Network Timeouts & Duplicate POSTs**:
     In distributed systems, the network between the client and server is unreliable. If a customer clicks "Pay" and the server successfully charges their bank account, but an upstream proxy or Wi-Fi glitch drops the HTTP 200 response packet, the client's browser or mobile app assumes failure and retries the request. Without server-side idempotency, this second POST executes another independent debit, resulting in double-billing and severe financial compliance penalties.
  2. **Payload Tampering via Reused Keys**:
     If an application simply matches on the `Idempotency-Key` string without validating the request payload, an attacker or buggy client can reuse a previously completed key with modified parameters (different amount, recipient account, or currency). Replaying the previous response gives a false confirmation for an action that never occurred, while updating the record introduces non-deterministic mutation state.
  3. **In-Flight Concurrency Races**:
     If a user double-clicks the "Pay" button or an automated payment bot dispatches duplicate requests concurrently, both requests arrive at the server before either completes. Without an atomic acquisition lock (`SET NX`), both requests check the cache, observe a "miss", and concurrently call the external payment gateway in parallel, defeating the idempotency guarantee.
  4. **Redis OOM via Indefinite Key Persistence**:
     Production payment APIs process millions of transactions per day. Persisting idempotency records without an expiration TTL causes Redis heap usage to climb monotonically until Redis runs out of memory and halts write operations with `OOM command not allowed`.

- **Resolution**:
  1. **Deterministic Cryptographic SHA-256 Hashing**:
     Implemented `compute_request_hash` which normalizes the HTTP method, URL path, and serializes the JSON payload with `sort_keys=True` before computing a SHA-256 hexadecimal digest:
     ```python
     def compute_request_hash(method: str, path: str, body: dict[str, Any] | None) -> str:
         serialized = json.dumps(body or {}, sort_keys=True, separators=(",", ":"))
         raw_payload = f"{method.upper()}:{path}:{serialized}"
         return hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
     ```
  2. **Atomic State Machine & In-Flight Concurrency Guard**:
     Implemented `IdempotencyManager` in `app/core/idempotency.py` using Redis:
     - On request arrival, atomically attempts `SET idempotency:{key} IN_PROGRESS NX PX {ttl_ms}`.
     - If key does not exist: worker acquires the lock, transitions state to `IN_PROGRESS`, and proceeds to execute the domain service.
     - If key exists and state is `IN_PROGRESS`:
       - If payload hash differs: raises **HTTP 422 Unprocessable Content** (`IDEMPOTENCY_PAYLOAD_MISMATCH`).
       - If payload hash matches: raises **HTTP 409 Conflict** (`CONCURRENT_REQUEST_IN_FLIGHT`), halting concurrent duplicate execution.
     - If key exists and state is `COMPLETED`:
       - If payload hash differs: raises **HTTP 422 Unprocessable Content** (`IDEMPOTENCY_PAYLOAD_MISMATCH`).
       - If payload hash matches: immediately replays cached response with header `X-Cache-Lookup: HIT-IDEMPOTENT` and status `201 Created`, completely bypassing the payment service.
  3. **Bounded Redis Memory with Mandatory 24h Expiration**:
     Configured an explicit TTL of 86,400,000 ms (24 hours) on all idempotency keys. On successful completion, `record_success` preserves the remaining TTL, preventing Redis heap memory bloat while providing full idempotency protection throughout the active business cycle.

- **Permanent Prevention Rules**:
  1. **Mandate `Idempotency-Key` on State-Mutating Endpoints**: Any endpoint creating financial charges, debits, orders, or external notifications MUST enforce an idempotency key.
  2. **Always Validate Request Hash**: Never look up an idempotency key without comparing the cryptographic hash of the current request against the stored hash. Discrepancies MUST be rejected with HTTP 422.
  3. **Enforce Atomic `IN_PROGRESS` Locking**: Guard against concurrent duplicate requests by setting an in-flight status atomically via `SET NX` before executing domain business logic.
  4. **Always Enforce Finite TTL on Idempotency Records**: Never create an idempotency key in Redis without an explicit expiration (e.g. 24 hours), guaranteeing bounded memory and zero OOM crashes.
