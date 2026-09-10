# Day 75: Kubernetes Probes Architecture (Liveness, Readiness, Startup Probes & Self-Healing Service Lifecycles)

## 1. Architectural Overview

In containerized cloud environments managed by Kubernetes (EKS, GKE, AKS), orchestrators require automated signals to detect process deadlocks, manage zero-downtime rolling deployments, and protect applications from cascading infrastructure failures.

Kubernetes provides three specialized probe types:
1. **Startup Probe (`startupProbe`)**: Shields the container from premature termination during slow cold-boots (Alembic schema migrations, cache seeding, Bloom filter priming).
2. **Liveness Probe (`livenessProbe`)**: Detects deadlocks and infinite loops within the ASGI application process. Remediation: `SIGKILL` and container restart.
3. **Readiness Probe (`readinessProbe`)**: Checks if downstream dependencies (PostgreSQL, Redis) are available. Remediation: temporarily remove pod from Service/Ingress load balancer endpoints **without restarting the container**.

```
+-----------------------------------------------------------------------------------------+
|                                KUBERNETES NODE ARCHITECTURE                             |
|                                                                                         |
|   +---------------------------------------------------------------------------------+   |
|   | KUBELET DAEMON                                                                  |   |
|   |                                                                                 |   |
|   | 1. Startup Probe Check   ---> GET /health/startup   ---> Initialization OK?     |   |
|   | 2. Liveness Probe Check  ---> GET /health/liveness  ---> ASGI Event Loop Alive? |   |
|   | 3. Readiness Probe Check ---> GET /health/readiness ---> Postgres & Redis OK?   |   |
|   +---------------------------------------|-----------------------------------------+   |
|                                           |                                             |
|                                           v                                             |
|   +---------------------------------------------------------------------------------+   |
|   | FASTAPI CONTAINER (Pod Replica)                                                 |   |
|   |                                                                                 |   |
|   |  GET /health/startup   --> Checks cold-boot flag (60s grace budget)             |   |
|   |  GET /health/liveness  --> Strictly O(1) in-memory process probe (< 0.5ms)       |   |
|   |                            [ZERO DOWNSTREAM DB / REDIS CALLS!]                  |   |
|   |  GET /health/readiness --> Timeout-bounded downstream probes (SELECT 1, Ping)   |   |
|   +---------------------------------------|-----------------------------------------+   |
|                                           |                                             |
+-------------------------------------------|---------------------------------------------+
                                            |
                         If Readiness fails | (HTTP 503)
                                            v
                +-------------------------------------------------------+
                |  Kubernetes Service & Ingress Load Balancer Endpoints  |
                |  --> POD IS TEMPORARILY DROPPED FROM TRAFFIC ROUTING  |
                |  --> ZERO CONTAINER RESTARTS! ZERO CRASHLOOPBACKOFF!  |
                +-------------------------------------------------------+
```

---

## 2. The Liveness Trap: Catastrophic Anti-Pattern & Elimination

### The Anti-Pattern:
Checking databases, caches, or external third-party APIs inside the `livenessProbe`:
```python
# CATASTROPHIC:
@app.get("/health/liveness")
async def bad_liveness(session = Depends(get_db)):
    await session.execute(select(1)) # DB down -> Pod killed -> Cascading failure
```

### Why it causes Global Outages:
If the primary database experiences a 10-second failover or CPU saturation, all 50 replica pods fail their liveness probes simultaneously. Kubernetes executes `SIGKILL` on every container, triggering a cluster-wide **CrashLoopBackOff restart storm**. As 50 newly started containers cold-boot together, they slam the recovering database with connection handshakes, immediately knocking it down again in a permanent death spiral.

### The Solution:
- **Liveness** only checks if the local Python process is alive and processing coroutines (`pid`, `active_threads`).
- **Readiness** handles downstream dependencies. When the database blips, Readiness returns HTTP 503, removing the pod from traffic while keeping the container warm and alive.

---

## 3. Production Manifest Specifications

Configured in `deployments/kubernetes/fastapi-probes.yaml`:
```yaml
startupProbe:
  httpGet:
    path: /health/startup
    port: 8000
  periodSeconds: 2
  timeoutSeconds: 2
  failureThreshold: 30 # 30 * 2s = 60s initialization budget

livenessProbe:
  httpGet:
    path: /health/liveness
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 10
  timeoutSeconds: 2
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health/readiness
    port: 8000
  initialDelaySeconds: 2
  periodSeconds: 5
  timeoutSeconds: 2
  failureThreshold: 2
```

---

## 4. Verification & Benchmarking

- **Liveness Latency**: Strictly $\mathcal{O}(1)$ in-memory execution ($< 0.5\text{ ms}$).
- **Readiness Timeouts**: Bounded at $1.0\text{ s}$ via `asyncio.wait_for()`, preventing probe worker thread exhaustion.
- **Backward Compatibility**: Consolidated `/health`, `/health/db`, `/health/db/pool`, `/health/redis` maintained without regression.
