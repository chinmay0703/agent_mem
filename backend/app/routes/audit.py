"""Read-only access to the audit log for the user-self or admin views."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.security import require_api_key, validate_user_id
from app.services.storage import postgres as pg


router = APIRouter(
    prefix="/audit", tags=["audit"], dependencies=[Depends(require_api_key)]
)


class AuditEntry(BaseModel):
    id: int
    user_id: Optional[str] = None
    action: str
    target_type: str
    target_id: Optional[str] = None
    details: Optional[dict] = None
    request_id: Optional[str] = None
    created_at: datetime


@router.get("/{user_id}", response_model=list[AuditEntry])
async def list_user_audit(user_id: str, limit: int = Query(default=100, ge=1, le=500)):
    user_id = validate_user_id(user_id)
    rows = await pg.list_audit(user_id, limit=limit)
    return [AuditEntry(**r) for r in rows]
