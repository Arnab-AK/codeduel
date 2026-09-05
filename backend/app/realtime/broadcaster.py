"""
Thin pub/sub layer connecting the judge (HTTP side, routes_submissions.py)
to match WebSocket connections (api/routes_ws.py).

Redis pub/sub -- not an in-process dict of open connections -- is the
fan-out mechanism. That's what makes "player A's submission just got
graded" correctly reach player B's WebSocket connection even if the two
players' sockets happen to be held by different app instances behind a
load balancer. On a single instance today this is one extra network hop
for no visible benefit; the moment there's more than one instance, an
in-memory registry of connections would silently stop delivering to half
of all matches. Paying the cost now, while it's cheap, is deliberate.

Only aggregate counts ever go over this channel -- never code, never
per-test-case detail, never which specific hidden test failed. That
redaction boundary is enforced here (this is the only place that
constructs the message), not left to the WebSocket handler to remember.
"""
import json
import uuid

from app.core.redis_client import redis_client


def channel_for_match(match_id: uuid.UUID) -> str:
    return f"match:{match_id}:progress"


async def publish_progress(
    match_id: uuid.UUID,
    player_id: uuid.UUID,
    passed_count: int,
    total_count: int,
    status: str,
) -> None:
    payload = {
        "type": "progress",
        "player_id": str(player_id),
        "passed_count": passed_count,
        "total_count": total_count,
        "status": status,
    }
    await redis_client.publish(channel_for_match(match_id), json.dumps(payload))


async def publish_match_complete(match_id: uuid.UUID, winner_id: uuid.UUID) -> None:
    """Published exactly once per match, by whichever request's call to
    complete_match_if_winner() actually won the race (see
    matches/completion.py) -- never speculatively, and never by the losing
    side of a race."""
    payload = {
        "type": "match_complete",
        "match_id": str(match_id),
        "winner_id": str(winner_id),
    }
    await redis_client.publish(channel_for_match(match_id), json.dumps(payload))
