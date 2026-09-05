"""
The atomic core of race-safe win determination -- the piece of this
project built specifically to demonstrate handling a real concurrency bug
correctly, not papering over it.

The scenario: two players in the same match can each submit an accepted
(all tests passing) solution within milliseconds of each other. Both
requests independently discover "my submission passed" and both want to
declare their own player_id the winner. Only one of them may actually win.

This is deliberately NOT implemented as "read match.status, check if it's
still in_progress in Python, then write if so" -- that's a classic
check-then-act race: both requests could read status == IN_PROGRESS before
either has written anything back, and both would proceed to declare
themselves the winner. Whichever write lands second would silently
overwrite the first -- a lost update, and the wrong player recorded as
having won.

Instead, a single UPDATE statement carries the whole check-and-act as one
atomic operation, using its own WHERE clause as the guard:

    UPDATE matches
    SET status = 'completed', winner_id = :winner_id, completed_at = now()
    WHERE id = :match_id AND status = 'in_progress'

Postgres serializes concurrent UPDATEs to the same row: the second
transaction to reach this statement blocks (at the database level, not the
application's event loop -- other asyncio tasks keep running while this
one awaits the lock) until the first commits, then re-evaluates its WHERE
clause against the now-committed row. Since status is no longer
'in_progress', the second UPDATE matches zero rows -- it cannot "win" no
matter how close the timing was. Exactly one caller ever sees
`rowcount == 1`, and that caller is the actual, deterministic winner --
decided by the database's own row-level locking, not by application code
racing to read-then-write. There is no such thing as a true tie here: two
truly simultaneous requests still resolve to a strict commit order at the
storage engine, it's just not predictable in advance from the app's side.
"""
import uuid

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Match, MatchStatus


async def complete_match_if_winner(
    db: AsyncSession, match_id: uuid.UUID, winner_id: uuid.UUID
) -> bool:
    """
    Attempts to mark `match_id` completed with `winner_id` as the winner.

    Returns True iff THIS call is the one that actually completed the
    match; False means someone else's call won the race (or the match was
    already completed/abandoned for an unrelated reason). The caller uses
    this to decide whether to broadcast a match-complete event -- only the
    winning call should.

    Commits its own transaction, deliberately separate from whatever
    transaction recorded the Submission itself: the submission is real and
    persisted regardless of who wins the match, and the completion race is
    a logically distinct atomic step that must be its own commit for the
    row lock to be acquired/released at the right moment relative to a
    concurrent competing call.
    """
    result = await db.execute(
        update(Match)
        .where(Match.id == match_id, Match.status == MatchStatus.IN_PROGRESS)
        .values(status=MatchStatus.COMPLETED, winner_id=winner_id, completed_at=func.now())
    )
    await db.commit()
    return result.rowcount == 1
