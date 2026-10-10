"""Recovery and explicit connection-test HTTP routes."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


router = APIRouter(tags=["Recovery"])


class RecoveryConnectionTestRequest(BaseModel):
    integration: str


@router.get("/api/health/recovery")
async def get_recovery_health(request: Request):
    """Return a read-only, secret-free recovery snapshot."""
    handler = getattr(request.app.state, "recovery_health_handler", None)
    if handler is None:
        raise HTTPException(status_code=503, detail="Recovery checks are unavailable")
    return await handler(request)


@router.post("/api/health/recovery/test")
async def test_recovery_connection(payload: RecoveryConnectionTestRequest, request: Request):
    """Explicitly test one configured integration."""
    handler = getattr(request.app.state, "recovery_connection_test_handler", None)
    if handler is None:
        raise HTTPException(status_code=503, detail="Recovery checks are unavailable")
    return await handler(payload, request)
