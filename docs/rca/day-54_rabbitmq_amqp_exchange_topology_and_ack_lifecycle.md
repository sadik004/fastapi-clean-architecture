# RCA: RabbitMQ AMQP 0-9-1 Exchange Topology, Pre-Flight Connection Probing & Manual ACK Guarantees

- **Trigger**: Day 54 architectural implementation — Implementing enterprise message broker architecture using RabbitMQ and `aio-pika`, resolving offline standalone test hangs caused by `aiormq` background reconnect loops, and preventing silent message loss via manual acknowledgements.
- **Faulty Code / Pattern**:
  ```python
  # 1. Anti-Pattern: Calling connect_robust on unreachable broker inside asyncio.wait_for without pre-flight check
  try:
      conn = await asyncio.wait_for(
          aio_pika.connect_robust("amqp://guest:guest@localhost:5672/"),
          timeout=1.5,
      )
  except Exception:
      # aiormq spawns background asyncio reconnect tasks that ignore asyncio.wait_for timeout
      # and loop indefinitely trying to reconnect every 5s, stalling test suite execution!
      pass

  # 2. Anti-Pattern: Enabling auto_ack=True in message consumer
  message = await queue.get(no_ack=True)
  await process_order_in_database(message)
  # If process_order_in_database crashes or DB connection drops, message was already deleted by broker!

  # 3. Anti-Pattern: Tightly coupling microservices with synchronous HTTP calls
  response = await httpx_client.post("http://notification-service/notify", json=order)
  # If notification-service is slow or restarting, customer's checkout fails with HTTP 504!
  ```
- **Root Cause**:
  1. **aiormq Background Reconnect Loops**: `aio_pika.connect_robust()` is engineered for persistent resilience in production and delegates connection logic to `aiormq`. When given an invalid or offline host, `aiormq` spawns detached background reconnect tasks that repeatedly attempt TCP handshakes. Even if the caller wraps the call in `asyncio.wait_for()`, cancelling the outer task leaves internal retry timers active, stalling test runners.
  2. **Auto-Ack Data Loss**: Setting `no_ack=True` / `auto_ack=True` instructs the AMQP broker to immediately purge the message from queue memory upon wire transmission. Any transient process failure (OOM kill, network socket drop, unhandled exception in DB commit) leads to irreversible message loss.
  3. **Tight Coupling Latency Inflation**: Synchronous cross-service REST calls inflate endpoint P99 latency and create cascading failure points across the entire system.
- **Resolution**:
  1. **Non-Blocking Pre-Flight Socket Probe**: Before calling `aio_pika.connect_robust`, execute a 100ms non-blocking asynchronous socket probe (`loop.sock_connect`). If the port is closed or unreachable, immediately fall back to the in-memory `MockAMQP` engine without ever invoking `aiormq` reconnect machinery.
  2. **Manual Acknowledgement Protocol (`message.ack()` / `message.nack(requeue=True)`)**: Strictly fetch messages with `no_ack=False`. Execute database operations and domain logic first, and only call `await message.ack()` upon verified completion. On failure, invoke `await message.nack(requeue=True)` to safely return the message to the head of the queue.
  3. **Asynchronous Event Publishing with AMQP Exchanges**: Decouple inter-service communication through Direct, Fanout, and Topic AMQP exchanges, providing HTTP 202 Accepted responses immediately to clients.
  ```python
  # Corrected implementation in app/core/rabbitmq.py:
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  sock.setblocking(False)
  try:
      await asyncio.wait_for(loop.sock_connect(sock, (host, port)), timeout=0.1)
  finally:
      sock.close()

  # Safe consumption with manual ACK:
  msg = await queue.get(no_ack=False)
  try:
      await process_payload(msg.decode_json())
      await msg.ack()
  except Exception:
      await msg.nack(requeue=True)
      raise
  ```
- **Permanent Prevention Rule**:
  - Always perform lightweight pre-flight socket verification before invoking connection-robust AMQP libraries when running in environments where broker availability is optional.
  - Never enable `auto_ack=True` on mission-critical message queues; always execute manual `message.ack()` strictly after downstream persistence succeeds.
  - Enforce `delivery_mode=DeliveryMode.PERSISTENT` on all messages published to RabbitMQ to prevent data loss on broker restarts.
