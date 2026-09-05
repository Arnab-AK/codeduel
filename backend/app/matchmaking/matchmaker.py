"""
Background pairing loop. Started once as an asyncio task at app startup
(see main.py's lifespan) and run for the life of the process -- no separate
worker process, no custom job-queue infra, just one coroutine and Redis.
This is the intentional scope: a single-instance deployment (Render/Fly.io
free tier) doesn't need more than that, and adding a distributed job queue
here would be solving a scaling problem this project doesn't have.

Two-phase pop-then-pair, for crash safety:
  1. BLPOP off QUEUE_KEY (blocking, atomic -- Redis guarantees no two
     callers, even concurrent ones, can ever pop the same player).
  2. Immediately record the popped player in PENDING_SET_KEY *before* doing
     anything that could fail or take a while (a DB write; a second BLPOP
     that might block for a long time waiting for another player to show
     up). If this process is killed anywhere after step 1, the player would
     otherwise simply vanish -- popped off the queue, never matched, and
     with no record that they were ever in flight. PENDING_SET_KEY is
     exactly that record.
  3. On success, both players are cleared from PENDING_SET_KEY (and from
     QUEUED_SET_KEY -- see the comment at that call site for why that
     matters). On a handled failure (e.g. the DB insert raises), both
     players are pushed back onto the queue immediately rather than waiting
     for a restart.

`reconcile_pending_on_startup()` is the other half of the crash-safety
story: anything still sitting in PENDING_SET_KEY when the process boots
means a *previous* instance died mid-pairing, and gets put back in line.
"""
import asyncio
import logging
import uuid

from sqlalchemy import func, select

from app.core.redis_client import redis_client
from app.db.models import Match, Problem
from app.db.session import async_session_factory
from app.matchmaking.queue import (
    PENDING_SET_KEY,
    QUEUE_KEY,
    QUEUED_SET_KEY,
    assign_match,
)

logger = logging.getLogger(__name__)


async def _pick_random_problem(db) -> Problem:
    # ORDER BY random() does a full table scan/sort and would not scale to
    # a large problem bank -- fine here since the whole point of this
    # project's problem set is a handful of hand-written problems, not
    # thousands. Revisit (e.g. sampling by id range) if that ever changes.
    result = await db.execute(select(Problem).order_by(func.random()).limit(1))
    problem = result.scalar_one_or_none()
    if problem is None:
        raise RuntimeError("No problems available to assign to a match")
    return problem


async def _create_match(player_a: str, player_b: str) -> uuid.UUID:
    async with async_session_factory() as db:
        problem = await _pick_random_problem(db)
        match = Match(
            player_one_id=uuid.UUID(player_a),
            player_two_id=uuid.UUID(player_b),
            problem_id=problem.id,
        )
        db.add(match)
        await db.commit()
        await db.refresh(match)
        return match.id


async def _pair_once() -> None:
    _, player_a = await redis_client.blpop(QUEUE_KEY)
    await redis_client.sadd(PENDING_SET_KEY, player_a)
    try:
        _, player_b = await redis_client.blpop(QUEUE_KEY)
    except BaseException:
        # Never got a second player (including on task cancellation during
        # shutdown) -- put the first one back rather than stranding them.
        await redis_client.srem(PENDING_SET_KEY, player_a)
        await redis_client.rpush(QUEUE_KEY, player_a)
        raise
    await redis_client.sadd(PENDING_SET_KEY, player_b)

    try:
        match_id = await _create_match(player_a, player_b)
        # Only removed from QUEUED_SET_KEY on success, not before: while a
        # pairing attempt is in flight the player is still, correctly,
        # "queued" as far as get_status() is concerned. Removing them here
        # (rather than never, or too early) is also what lets a player who
        # already finished one match join the queue again for another --
        # without this, enqueue()'s idempotency check would see them as
        # still a member forever and silently refuse to re-queue them.
        await redis_client.srem(QUEUED_SET_KEY, player_a, player_b)
        await assign_match(uuid.UUID(player_a), match_id)
        await assign_match(uuid.UUID(player_b), match_id)
        logger.info("Matched %s vs %s -> match %s", player_a, player_b, match_id)
    except Exception:
        logger.exception(
            "Failed to create match for %s/%s -- requeueing both", player_a, player_b
        )
        await redis_client.rpush(QUEUE_KEY, player_a, player_b)
        raise
    finally:
        await redis_client.srem(PENDING_SET_KEY, player_a, player_b)


async def reconcile_pending_on_startup() -> None:
    stragglers = await redis_client.smembers(PENDING_SET_KEY)
    if not stragglers:
        return
    logger.warning("Reconciling %d player(s) stranded by a previous crash", len(stragglers))
    for player_id in stragglers:
        await redis_client.rpush(QUEUE_KEY, player_id)
        await redis_client.sadd(QUEUED_SET_KEY, player_id)
    await redis_client.srem(PENDING_SET_KEY, *stragglers)


async def matchmaker_loop() -> None:
    await reconcile_pending_on_startup()
    while True:
        try:
            await _pair_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A single failed pairing attempt (e.g. a transient DB error)
            # should not kill the whole matchmaking subsystem for the rest
            # of the process's life -- log it and keep serving the queue.
            # The brief sleep avoids spinning in a tight loop if the
            # failure is persistent (e.g. the DB is actually down).
            await asyncio.sleep(1)
