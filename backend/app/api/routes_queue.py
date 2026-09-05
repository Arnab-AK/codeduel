import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import get_current_user_id
from app.matchmaking import queue

router = APIRouter(prefix="/queue", tags=["matchmaking"])


class QueueJoinRequest(BaseModel):
    display_name: str | None = None


class QueueJoinResponse(BaseModel):
    player_id: uuid.UUID
    already_queued: bool


class QueueStatusResponse(BaseModel):
    status: str  # "waiting" | "matched" | "not_found"
    match_id: uuid.UUID | None = None


@router.post("/join", response_model=QueueJoinResponse)
async def join_queue(
    payload: QueueJoinRequest, player_id: uuid.UUID = Depends(get_current_user_id)
):
    # player_id is now always the authenticated user's own id -- phase 2's
    # "client supplies whatever player_id it wants" placeholder is gone.
    # This also makes enqueue()'s idempotency check meaningfully correct
    # for the first time: it's idempotent against a STABLE identity now,
    # not a fresh random guest id a client might regenerate every call.
    newly_added = await queue.enqueue(player_id, payload.display_name)
    return QueueJoinResponse(player_id=player_id, already_queued=not newly_added)


@router.delete("/leave", status_code=204)
async def leave_queue(player_id: uuid.UUID = Depends(get_current_user_id)):
    # No path param anymore -- you can only ever cancel your OWN queue
    # entry, not supply an arbitrary player_id and cancel someone else's.
    await queue.leave(player_id)


@router.get("/status", response_model=QueueStatusResponse)
async def queue_status(player_id: uuid.UUID = Depends(get_current_user_id)):
    result = await queue.get_status(player_id)
    return QueueStatusResponse(**result)
