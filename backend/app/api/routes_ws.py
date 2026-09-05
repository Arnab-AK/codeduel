"""
Match room WebSocket: both players in a Match connect here and receive live
progress updates about EACH OTHER's submissions -- aggregate counts only
("3/5 tests passing"), never code, never which hidden test failed. The
judge (routes_submissions.py, via realtime/broadcaster.py) is what actually
produces these updates; this module only relays them, over the same
Redis-pub/sub-backed channel a connection on any app instance can subscribe
to (see broadcaster.py's docstring for why that matters).

No auth yet (phase 6), so a connecting client identifies itself with the
player_id it already holds from matchmaking -- this endpoint's only
"authorization" check is confirming that id is actually one of the two
players in the match it's asking to join.
"""
import asyncio
import uuid

from fastapi import APIRouter, WebSocket
from sqlalchemy import select

from app.core.redis_client import redis_client
from app.db.models import Match, Submission
from app.db.session import async_session_factory
from app.realtime.broadcaster import channel_for_match

router = APIRouter()


async def _match_snapshot(match: Match) -> dict:
    """Current state for both players, sent once right after connecting so
    a client (or a reconnecting client, mid-duel) doesn't have to wait for
    the next submission to know where things stand."""
    async with async_session_factory() as db:
        players: dict[str, dict | None] = {}
        for player_id in (match.player_one_id, match.player_two_id):
            latest = (
                await db.execute(
                    select(Submission)
                    .where(Submission.match_id == match.id, Submission.player_id == player_id)
                    .order_by(Submission.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            players[str(player_id)] = (
                {
                    "passed_count": latest.passed_count,
                    "total_count": latest.total_count,
                    "status": latest.status.value,
                }
                if latest
                else None
            )
    return {"type": "snapshot", "match_id": str(match.id), "players": players}


@router.websocket("/ws/matches/{match_id}")
async def match_room(websocket: WebSocket, match_id: uuid.UUID, player_id: uuid.UUID):
    # Accept first, then validate: closing with a specific code/reason is
    # more reliably observable to a browser WebSocket client after a
    # completed handshake than rejecting the handshake outright.
    await websocket.accept()

    async with async_session_factory() as db:
        match = await db.get(Match, match_id)

    if match is None:
        await websocket.send_json({"type": "error", "detail": "Match not found"})
        await websocket.close(code=4404)
        return
    if player_id not in (match.player_one_id, match.player_two_id):
        await websocket.send_json({"type": "error", "detail": "Not a participant in this match"})
        await websocket.close(code=4403)
        return

    await websocket.send_json(await _match_snapshot(match))

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_for_match(match_id))

    async def relay_redis_to_client() -> None:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            await websocket.send_text(message["data"])

    async def watch_for_disconnect() -> None:
        # The client isn't expected to send anything meaningful over this
        # socket -- this loop exists purely to notice a disconnect
        # (receive_text raises) so the subscription below gets torn down
        # instead of leaking for the rest of the process's life.
        while True:
            await websocket.receive_text()

    relay_task = asyncio.create_task(relay_redis_to_client())
    watch_task = asyncio.create_task(watch_for_disconnect())
    try:
        await asyncio.wait({relay_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        relay_task.cancel()
        watch_task.cancel()
        # Both tasks are expected to end via cancellation or
        # WebSocketDisconnect once we get here -- gather with
        # return_exceptions collects those instead of raising, which is
        # what actually retrieves the exception on whichever task ended
        # the wait() above (otherwise asyncio logs an "exception was never
        # retrieved" warning when it's garbage collected).
        await asyncio.gather(relay_task, watch_task, return_exceptions=True)
        await pubsub.unsubscribe(channel_for_match(match_id))
        await pubsub.close()
