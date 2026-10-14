---
name: production-environment
description: >-
  Operational guide for communicating with, inspecting, updating, and managing the remote production Linux server
  (ubuntu@production) over Tailscale VPN. Use whenever running commands on the production host, debugging remote
  services, checking hardware constraints (1GB RAM / 1 OCPU), avoiding port clashes, or deploying code updates.
---

# Production Environment & Operations Guide

## 1. Host Identity & Network Access

- **SSH Target**: `ubuntu@production`
- **Tailscale MagicDNS**: `production`
- **Tailscale IP**: `100.64.0.1`
- **Public IP**: `192.0.2.1` (Oracle Cloud Infrastructure, ingress locked to port 22/80/443; internal traffic routed securely via `tailscale0`)
- **Remote Project Directory**: `/home/ubuntu/deployments/realtime-osint`

> [!NOTE]
> All services on `production` are accessed over Tailscale VPN. Always verify Tailscale connectivity before issuing remote management commands.

---

## 2. Hardware Budget & Resource Constraints

The host is an Oracle Cloud `VM.Standard.E2.1.Micro` instance:
- **CPU**: 1 OCPU (AMD EPYC 7742 64-Core Processor)
- **RAM**: ~952 MiB Physical RAM + 2.0 GiB Swap
- **Rule of Thumb**: **NEVER compile or build images directly on the production host!** Building Node/TypeScript/Docker images on this machine will exhaust memory, trigger swap thrashing, and cause OOM locks. Always build `linux/amd64` images on your local workstation and transfer them.

### Realtime OSINT Container Resource Allocations

| Container | Image | Memory Limit | Memory Reservation | Internal Port | Host Port |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `osint-postgres` | `postgres:16-alpine` | `120M` | `45M` | `5432` | *None* (Docker network only) |
| `osint-redis` | `redis:7-alpine` | `50M` | `20M` | `6379` | *None* (Docker network only) |
| `osint-backend` | `realtimeosint-backend:latest` | `450M` | `150M` | `8000` | `24176:8000` |
| `osint-frontend` | `realtimeosint-frontend:latest` | `180M` | `80M` | `3000` | `26816:3000` |

---

## 3. Port Allocation & Collision Prevention

The server also runs host-level services (`macros-counter-service`, PostgreSQL on 5432, Redis on 6379, Uvicorn on 8000/8001, and Caddy on 80/443).

**Port Safety Rules:**
1. **Never expose `osint-postgres` on host port `5432`** (conflicts with host PostgreSQL).
2. **Never expose `osint-redis` on host port `6379`** (conflicts with host Redis).
3. **Never bind `osint-backend` to port `8000` or `8001`** (used by existing Uvicorn apps). It is mapped to `24176:8000` (randomized 2026-10-15; `8080` retired).
4. **`osint-frontend`** is mapped to `26816:3000` (randomized 2026-10-15).

---

## 4. Live Endpoints (Tailscale VPN)

| Service | Tailscale Domain | Tailscale IP |
| :--- | :--- | :--- |
| **Frontend Web UI** | `http://production:26816` | `http://100.64.0.1:26816` |
| **Backend API Health** | `http://production:24176/api/health` | `http://100.64.0.1:24176/api/health` |
| **Backend REST API** | `http://production:24176/api/events` | `http://100.64.0.1:24176/api/events` |
| **WebSocket Gateway** | `http://production:24176/socket.io` | `http://100.64.0.1:24176/socket.io` |

*Default API Key*: `osint_secret_key_2026` (passed via header `X-API-Key: osint_secret_key_2026`).

---

## 5. Deployment & Update Workflow

Use the local automation script `deploy.ps1` from your local terminal. It cross-compiles locally, uploads the image tar, loads it into remote Docker, restarts containers, and validates health checks.

```powershell
# Deploy everything (frontend + backend + config):
.\deploy.ps1

# Deploy only frontend (Next.js UI changes):
.\deploy.ps1 -Service frontend

# Deploy only backend (FastAPI / AI pipeline / ingestion changes):
.\deploy.ps1 -Service backend

# Synchronize compose / config without image rebuilding:
.\deploy.ps1 -Service config
```

---

## 6. Remote Inspection Runbook

Run these commands from your local workstation:

### Check Running Containers
```powershell
ssh ubuntu@production "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
```

### Check Host Memory & Swap Status
```powershell
ssh ubuntu@production "free -h"
```

### Inspect Live Backend Logs
```powershell
ssh ubuntu@production "docker logs osint-backend --tail 50 -f"
```

### Inspect Live Ingestion & AI Pipeline Errors
```powershell
ssh ubuntu@production "docker logs osint-backend --tail 100 | grep -E 'ERROR|WARNING'"
```

### Restart a Specific Container
```powershell
ssh ubuntu@production "cd /home/ubuntu/deployments/realtime-osint && docker compose restart backend"
```

### Test Remote API Health from Local Desktop
```powershell
curl -i "http://100.64.0.1:24176/api/health"
```
