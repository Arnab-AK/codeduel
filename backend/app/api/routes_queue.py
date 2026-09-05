import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.matchmaking import queue

router = APIRouter(prefix="/queue", tags=["matchmaking"])


class QueueJoinRequest(BaseModel):
    # A client that already has a player_id (from an earlier join) should
    # send it back rather than omitting it -- that's what makes retrying a
    # join call safe. See queue.enqueue()'s docstring.
    player_id: uuid.UUID | None = None
    display_name: str | None = None


class QueueJoinResponse(BaseModel):
    player_id: uuid.UUID
    already_queued: bool


class QueueStatusResponse(BaseModel):
    status: str  # "waiting" | "matched" | "not_found"
    match_id: uuid.UUID | None = None


@router.post("/join", response_model=QueueJoinResponse)
async def join_queue(payload: QueueJoinRequest):
    player_id = payload.player_id or uuid.uuid4()
    newly_added = await queue.enqueue(player_id, payload.display_name)
    return QueueJoinResponse(player_id=player_id, already_queued=not newly_added)


@router.delete("/leave/{player_id}", status_code=204)
async def leave_queue(player_id: uuid.UUID):
    await queue.leave(player_id)


@router.get("/status/{player_id}", response_model=QueueStatusResponse)
async def queue_status(player_id: uuid.UUID):
    result = await queue.get_status(player_id)
    return QueueStatusResponse(**result)
