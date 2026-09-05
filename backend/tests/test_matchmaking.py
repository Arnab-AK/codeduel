"""
Integration tests for the Redis-backed matchmaking queue. These run against
the same local Postgres/Redis (docker-compose) services as the rest of the
app rather than mocks -- the whole point of this module is verifying real
Redis atomicity/ordering guarantees (SADD-as-check-and-set, BLPOP FIFO
order), which a mock would just assume away instead of proving.
"""
import uuid

import pytest
from sqlalchemy import delete

from app.core.redis_client import redis_client
from app.db.models import Match
from app.db.session import async_session_factory
from app.matchmaking import queue
from app.matchmaking.matchmaker import _pair_once, reconcile_pending_on_startup
from app.matchmaking.queue import PENDING_SET_KEY, QUEUE_KEY, QUEUED_SET_KEY


@pytest.fixture(autouse=True)
async def clean_matchmaking_state():
    keys = [QUEUE_KEY, QUEUED_SET_KEY, PENDING_SET_KEY]
    await redis_client.delete(*keys)
    created_match_ids: list[uuid.UUID] = []
    yield created_match_ids
    await redis_client.delete(*keys)
    if created_match_ids:
        async with async_session_factory() as db:
            await db.execute(delete(Match).where(Match.id.in_(created_match_ids)))
            await db.commit()


async def test_enqueue_is_idempotent_per_player_id():
    player_id = uuid.uuid4()
    assert await queue.enqueue(player_id, "alice") is True
    # Same player_id again -- must be a no-op, not a second queue entry.
    assert await queue.enqueue(player_id, "alice") is False


async def test_leave_removes_player_from_queue():
    player_id = uuid.uuid4()
    await queue.enqueue(player_id, "bob")
    await queue.leave(player_id)
    status = await queue.get_status(player_id)
    assert status["status"] == "not_found"


async def test_pairing_creates_a_match_visible_to_both_players(clean_matchmaking_state):
    player_a, player_b = uuid.uuid4(), uuid.uuid4()
    await queue.enqueue(player_a, "alice")
    await queue.enqueue(player_b, "bob")

    await _pair_once()

    status_a = await queue.get_status(player_a)
    status_b = await queue.get_status(player_b)
    assert status_a["status"] == "matched"
    assert status_b["status"] == "matched"
    assert status_a["match_id"] == status_b["match_id"]
    clean_matchmaking_state.append(uuid.UUID(status_a["match_id"]))

    async with async_session_factory() as db:
        match = await db.get(Match, uuid.UUID(status_a["match_id"]))
        assert match is not None
        assert {match.player_one_id, match.player_two_id} == {player_a, player_b}


async def test_rejoin_after_being_matched_is_allowed(clean_matchmaking_state):
    # Regression test: a player who already completed a match must be able
    # to queue again for a new one. Before _pair_once() explicitly removed
    # matched players from QUEUED_SET_KEY, enqueue()'s idempotency check
    # would see them as still "queued" forever and silently refuse to
    # re-add them to the actual queue list.
    player_a, player_b = uuid.uuid4(), uuid.uuid4()
    await queue.enqueue(player_a, "alice")
    await queue.enqueue(player_b, "bob")
    await _pair_once()
    clean_matchmaking_state.append(
        uuid.UUID((await queue.get_status(player_a))["match_id"])
    )

    assert await queue.enqueue(player_a, "alice") is True


async def test_rejoin_does_not_report_a_stale_match(clean_matchmaking_state):
    # Regression test for a bug caught by manual testing: after matching,
    # leaving, and rejoining, get_status() was still returning the OLD
    # match_id (checked before queue membership) even though the player
    # hadn't been re-paired yet -- because nothing ever cleared the old
    # match_for_player key on a fresh join.
    player_a, player_b = uuid.uuid4(), uuid.uuid4()
    await queue.enqueue(player_a, "alice")
    await queue.enqueue(player_b, "bob")
    await _pair_once()
    clean_matchmaking_state.append(
        uuid.UUID((await queue.get_status(player_a))["match_id"])
    )

    await queue.enqueue(player_a, "alice")  # rejoin for a new match
    status = await queue.get_status(player_a)
    assert status["status"] == "waiting"
    assert status["match_id"] is None


async def test_reconcile_requeues_stragglers_from_a_previous_crash():
    # Simulates what PENDING_SET_KEY looks like after a process died between
    # popping a player off the queue and successfully pairing them.
    stranded = uuid.uuid4()
    await redis_client.sadd(PENDING_SET_KEY, str(stranded))

    await reconcile_pending_on_startup()

    status = await queue.get_status(stranded)
    assert status["status"] == "waiting"
    assert await redis_client.scard(PENDING_SET_KEY) == 0
