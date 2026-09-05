"""
Redis-backed matchmaking queue.

Two data structures are kept in sync, on purpose (not one, see below):

- QUEUE_KEY (a Redis LIST): FIFO order of waiting player ids. This is the
  source of truth for pairing order, popped by the matchmaker loop via
  BLPOP (matchmaker.py).
- QUEUED_SET_KEY (a Redis SET): the *current* membership of the queue.
  Redis lists have no efficient "is X already in here" check, so this set
  exists purely to make enqueue() idempotent in O(1) -- see enqueue()'s
  docstring for why that matters.

PENDING_SET_KEY is not touched by this module at all -- it belongs to
matchmaker.py's crash-safety design (see that module's docstring). It's
documented here because all three keys together form one state machine:
QUEUED -> (BLPOP) -> PENDING -> (matched) -> gone, or PENDING -> (crash,
then reconciled) -> back to QUEUED.
"""
import uuid

from app.core.redis_client import redis_client

QUEUE_KEY = "matchmaking:queue"
QUEUED_SET_KEY = "matchmaking:queued_players"
PENDING_SET_KEY = "matchmaking:pending_players"
MATCH_FOR_PLAYER_KEY = "matchmaking:match_for_player:{player_id}"
PLAYER_META_KEY = "matchmaking:player:{player_id}"

PLAYER_META_TTL_SECONDS = 3600
MATCH_ASSIGNMENT_TTL_SECONDS = 3600


async def enqueue(player_id: uuid.UUID, display_name: str | None) -> bool:
    """
    Adds player_id to the queue. Returns False (a no-op) if they were
    already queued.

    SADD's return value (1 if newly added, 0 if already a member) doubles
    as an atomic check-and-set: two concurrent join calls carrying the same
    player_id can never both proceed to RPUSH, because Redis executes each
    command atomically and only one SADD can be the one that actually
    changes membership. This is what makes /queue/join idempotent for a
    client that retries a join (e.g. after a network hiccup) using the
    player_id it was already given, instead of accidentally double-entering
    the queue.
    """
    added = await redis_client.sadd(QUEUED_SET_KEY, str(player_id))
    if not added:
        return False
    # A brand-new queue entry always starts clean: clear any match
    # assignment left over from a PREVIOUS time this player_id was matched.
    # Without this, get_status() checks the match key before queue
    # membership (see get_status() below) and would keep reporting that old
    # match_id -- "you're matched!" -- even though this join hasn't been
    # paired yet. Found by manually walking through join -> match -> leave
    # -> rejoin -> status and seeing the stale match survive.
    await redis_client.delete(MATCH_FOR_PLAYER_KEY.format(player_id=player_id))
    await redis_client.rpush(QUEUE_KEY, str(player_id))
    if display_name:
        key = PLAYER_META_KEY.format(player_id=player_id)
        await redis_client.hset(key, mapping={"display_name": display_name})
        await redis_client.expire(key, PLAYER_META_TTL_SECONDS)
    return True


async def leave(player_id: uuid.UUID) -> None:
    """
    Best-effort cancel.

    NOTE, deliberately not papered over: there's an unclosed race between
    this and the matchmaker's BLPOP. If the matchmaker loop pops this
    player from QUEUE_KEY in the instant before this call runs, leave()
    will still remove them from QUEUED_SET_KEY, but they'll be matched
    anyway -- LREM will simply find nothing left to remove from the list.
    Closing this fully would need a Lua script making "check membership and
    pop" one atomic server-side operation; not worth the added complexity
    for a portfolio-scoped queue where the failure mode is "rarely, a
    cancel arrives a few milliseconds too late," not data corruption.
    """
    await redis_client.srem(QUEUED_SET_KEY, str(player_id))
    await redis_client.lrem(QUEUE_KEY, 0, str(player_id))


async def get_status(player_id: uuid.UUID) -> dict:
    match_key = MATCH_FOR_PLAYER_KEY.format(player_id=player_id)
    match_id = await redis_client.get(match_key)
    if match_id:
        return {"status": "matched", "match_id": match_id}
    if await redis_client.sismember(QUEUED_SET_KEY, str(player_id)):
        return {"status": "waiting", "match_id": None}
    return {"status": "not_found", "match_id": None}


async def assign_match(player_id: uuid.UUID, match_id: uuid.UUID) -> None:
    key = MATCH_FOR_PLAYER_KEY.format(player_id=player_id)
    await redis_client.set(key, str(match_id), ex=MATCH_ASSIGNMENT_TTL_SECONDS)
