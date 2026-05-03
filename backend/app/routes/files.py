"""File upload + listing + retrieval routes."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import get_settings
from app.security import require_api_key, validate_user_id
from app.services.files.manager import delete_upload, store_upload
from app.services.storage import postgres as pg


router = APIRouter(
    prefix="/files", tags=["files"], dependencies=[Depends(require_api_key)]
)


# Hard cap on uploaded file size — defense-in-depth on top of the 1 MB
# request middleware. Files are larger than chat payloads, so we use a
# higher per-file cap here.
MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB


class FileSummary(BaseModel):
    id: str
    filename: str
    kind: str
    mime: str
    size_bytes: int
    summary: Optional[str] = None
    metadata: Optional[dict] = None
    created_at: datetime


@router.post("/{user_id}", response_model=FileSummary)
async def upload_file(
    user_id: str,
    file: UploadFile = File(...),
    thread_id: Optional[str] = Form(default=None),
):
    user_id = validate_user_id(user_id)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds {MAX_FILE_BYTES // (1024 * 1024)} MB",
        )

    rec = await store_upload(
        user_id=user_id,
        thread_id=thread_id,
        filename=file.filename or "upload",
        mime=file.content_type or "application/octet-stream",
        raw=raw,
    )
    full = await pg.get_file(user_id, rec["id"])
    if not full:
        raise HTTPException(status_code=500, detail="file persist failed")
    return FileSummary(**{
        "id": full["id"],
        "filename": full["filename"],
        "kind": full["kind"],
        "mime": full["mime"],
        "size_bytes": full["size_bytes"],
        "summary": full.get("summary"),
        "metadata": full.get("metadata"),
        "created_at": full["created_at"],
    })


@router.get("/{user_id}", response_model=list[FileSummary])
async def list_user_files(user_id: str):
    user_id = validate_user_id(user_id)
    rows = await pg.list_files(user_id)
    return [FileSummary(**r) for r in rows]


@router.get("/{user_id}/{file_id}/download")
async def download_file(user_id: str, file_id: str):
    user_id = validate_user_id(user_id)
    f = await pg.get_file(user_id, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(
        path=f["storage_path"], media_type=f["mime"], filename=f["filename"]
    )


@router.delete("/{user_id}/{file_id}")
async def delete_user_file(user_id: str, file_id: str, request: Request):
    user_id = validate_user_id(user_id)
    result = await delete_upload(user_id, file_id)
    if not result:
        raise HTTPException(status_code=404, detail="file not found")
    await pg.write_audit(
        user_id=user_id,
        action="delete_file",
        target_type="file",
        target_id=file_id,
        details=result,
        request_id=getattr(request.state, "request_id", None),
    )
    return {"status": "deleted", **result}
