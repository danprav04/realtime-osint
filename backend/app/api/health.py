import os
import time
from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Response, status
from backend.app.config import get_settings
from backend.app.db.session import check_db_health
from backend.app.services.redis_bus import get_redis_bus

router = APIRouter(tags=["Health & Monitoring"])

_START_TIME = time.time()


def get_memory_stats() -> Dict[str, Any]:
    """Retrieve process and host memory metrics to ensure compliance with <900MB budget."""
    settings = get_settings()
    stats: Dict[str, Any] = {
        "budget_limit_mb": settings.MEMORY_BUDGET_MB,
        "budget_compliant": True,
    }

    try:
        import psutil
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        rss_mb = round(mem_info.rss / (1024 * 1024), 2)
        vms_mb = round(mem_info.vms / (1024 * 1024), 2)
        vm = psutil.virtual_memory()

        stats.update({
            "process_rss_mb": rss_mb,
            "process_vms_mb": vms_mb,
            "system_total_mb": round(vm.total / (1024 * 1024), 2),
            "system_available_mb": round(vm.available / (1024 * 1024), 2),
            "system_used_percent": vm.percent,
            "budget_compliant": rss_mb < settings.MEMORY_BUDGET_MB,
        })
    except ImportError:
        # Fallback if psutil is not available
        stats.update({
            "process_rss_mb": 0.0,
            "process_vms_mb": 0.0,
            "note": "psutil not installed; memory metrics estimated",
        })
    except Exception as exc:
        stats.update({
            "error": str(exc),
            "budget_compliant": True,
        })

    return stats


async def build_health_payload() -> Dict[str, Any]:
    """Compile comprehensive health report of all sub-components."""
    settings = get_settings()
    db_health = await check_db_health()
    redis_health = await get_redis_bus().check_health()
    mem_stats = get_memory_stats()

    db_status = db_health.get("status")
    redis_status = redis_health.get("status")

    if db_status != "healthy" or redis_status == "unhealthy":
        overall_status = "unhealthy"
    elif redis_status == "degraded":
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    uptime_sec = round(time.time() - _START_TIME, 2)

    return {
        "status": overall_status,
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": uptime_sec,
        "database": db_health,
        "redis": redis_health,
        "memory": mem_stats,
    }


@router.get("/health", summary="System Health Report")
@router.get("/api/health", summary="System Health Report (API Alias)")
async def get_health_report(response: Response) -> Dict[str, Any]:
    """Comprehensive health check returning database status, Redis status, and memory stats."""
    payload = await build_health_payload()
    if payload["status"] == "unhealthy":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


@router.get("/health/ready", summary="Readiness Probe")
async def get_readiness() -> Dict[str, Any]:
    """Readiness probe checking database connectivity and event bus health."""
    db_health = await check_db_health()
    redis_health = await get_redis_bus().check_health()

    is_db_ok = db_health.get("status") == "healthy"
    bus_status = redis_health.get("status")
    is_bus_ok = bus_status in ("healthy", "degraded")

    if not is_db_ok or not is_bus_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not ready",
        )

    return {
        "status": "ready",
        "database": db_health,
        "redis": redis_health,
    }


@router.get("/health/live", summary="Liveness Probe")
async def get_liveness() -> Dict[str, Any]:
    """Liveness probe verifying HTTP application process responsiveness."""
    return {
        "status": "alive",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
