from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.models.schemas import GraphPayload, NodeType, Triple
from app.security import require_api_key, truncate_entity, validate_user_id
from app.services.graph.neo4j_client import get_graph_client
from app.services.storage import postgres as pg


router = APIRouter(
    prefix="/memory", tags=["memory"], dependencies=[Depends(require_api_key)]
)


class DeleteTripleRequest(BaseModel):
    subject: str = Field(..., min_length=1)
    relation: str = Field(..., min_length=1)
    object: str = Field(..., min_length=1)
    subject_type: NodeType = "other"
    object_type: NodeType = "other"


class GraphStats(BaseModel):
    nodes: int
    edges: int
    relation_types: int
    per_label: dict[str, int]


@router.get("/graph/{user_id}", response_model=GraphPayload)
async def get_user_graph(user_id: str) -> GraphPayload:
    user_id = validate_user_id(user_id)
    try:
        return await get_graph_client().get_user_graph(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Graph fetch failed: {e}") from e


@router.get("/stats/{user_id}", response_model=GraphStats)
async def get_user_graph_stats(user_id: str) -> GraphStats:
    user_id = validate_user_id(user_id)
    try:
        return GraphStats(**(await get_graph_client().stats(user_id)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stats fetch failed: {e}") from e


@router.delete("/triple/{user_id}")
async def delete_triple(user_id: str, body: DeleteTripleRequest, request: Request):
    """Hard-delete a specific triple from a user's graph. Used by the UI
    when the user clicks a per-triple delete button in the node panel."""
    user_id = validate_user_id(user_id)
    triple = Triple(
        subject=truncate_entity(body.subject),
        relation=body.relation,
        object=truncate_entity(body.object),
        subject_type=body.subject_type,
        object_type=body.object_type,
        confidence=1.0,
    )
    if not triple.subject or not triple.object:
        raise HTTPException(status_code=400, detail="subject/object required")
    try:
        ok = await get_graph_client().delete_triple(user_id, triple)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"delete failed: {e}") from e
    if not ok:
        raise HTTPException(status_code=404, detail="triple not found")
    await pg.write_audit(
        user_id=user_id,
        action="delete_triple",
        target_type="triple",
        target_id=f"{triple.subject}|{triple.relation}|{triple.object}",
        details={"subject": triple.subject, "relation": triple.relation, "object": triple.object},
        request_id=getattr(request.state, "request_id", None),
    )
    return {"status": "deleted"}
