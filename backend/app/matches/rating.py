"""
Elo rating update, applied exactly once per completed match -- called only
by the request whose call to complete_match_if_winner() actually won the
race (see completion.py), so this code path never runs twice for the same
match. That guarantee is inherited from phase 4, not re-derived here.

Win determination could be expressed as a single atomic UPDATE with no
read step at all (the WHERE clause *was* the check). Elo update can't be:
computing either player's new rating requires knowing BOTH players'
CURRENT ratings first, so this is inherently read-then-write. `SELECT ...
FOR UPDATE` locks both PlayerRating rows before computing anything,
making the whole read-compute-write cycle atomic against any other
concurrent rating update touching either player.

Both rows are always locked in a fixed order -- sorted by player_id,
never "winner first, then loser" -- specifically to avoid deadlock. If two
concurrent match completions ever touch an overlapping pair of players
(player Y finishing one match while also being one of the two players in
another match resolving at the same instant), locking "winner then loser"
in each transaction could have one transaction lock Y-then-X while the
other locks X-then-Y, and the two would deadlock waiting on each other.
Sorting first means every transaction that ever touches these two rows
requests their locks in the same order, so that deadlock is structurally
impossible, not just unlikely.

A second, separate race lives in "get or create": `SELECT ... FOR UPDATE`
only locks a row that already exists -- it locks nothing for a player_id
that has never played before, because there's no row yet to lock. Two
concurrent first-ever-match calls for the same brand-new player_id can
both observe "no row", and both attempt to INSERT one, racing into a
unique-constraint violation. `_get_or_create_locked` closes this with an
`INSERT ... ON CONFLICT DO NOTHING` first (atomic: at most one of two
concurrent inserts for the same key actually inserts, the other silently
no-ops) to guarantee the row exists, and only then does `SELECT ... FOR
UPDATE` to fetch and lock it. This was caught by deliberately breaking the
lock-ordering fix above to verify the deadlock test actually failed
without it (see the "Proven..." design decision in README.md) --
`test_concurrent_updates_on_an_overlapping_pair_never_deadlock` uses two
players with no prior rating, which is exactly what exposed this.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlayerRating

DEFAULT_RATING = 1200
# A single fixed K-factor for every player, rather than the higher-K for
# provisional/newer players that real rating systems (chess.com, USCF)
# use. That variability is a reasonable next step, not a correctness
# issue -- "simple Elo" was the explicit scope for this phase.
K_FACTOR = 32


@dataclass(frozen=True)
class RatingResult:
    winner_before: int
    winner_after: int
    loser_before: int
    loser_after: int


def _expected_score(rating: int, opponent_rating: int) -> float:
    return 1 / (1 + 10 ** ((opponent_rating - rating) / 400))


async def _get_or_create_locked(db: AsyncSession, player_id: uuid.UUID) -> PlayerRating:
    # Ensure the row exists first, atomically -- see the module docstring
    # for why SELECT ... FOR UPDATE alone isn't enough to make "get or
    # create" safe under concurrency. ON CONFLICT DO NOTHING means at most
    # one of two simultaneous first-match inserts for the same player_id
    # actually inserts; the other is a no-op instead of an error.
    await db.execute(
        pg_insert(PlayerRating)
        .values(player_id=player_id, rating=DEFAULT_RATING, matches_played=0)
        .on_conflict_do_nothing(index_elements=["player_id"])
    )
    return (
        await db.execute(
            select(PlayerRating).where(PlayerRating.player_id == player_id).with_for_update()
        )
    ).scalar_one()


async def apply_elo_update(
    db: AsyncSession, winner_id: uuid.UUID, loser_id: uuid.UUID
) -> RatingResult:
    first_id, second_id = sorted((winner_id, loser_id))
    first = await _get_or_create_locked(db, first_id)
    second = await _get_or_create_locked(db, second_id)
    winner_row, loser_row = (first, second) if first_id == winner_id else (second, first)

    winner_before, loser_before = winner_row.rating, loser_row.rating
    expected_winner = _expected_score(winner_before, loser_before)

    # Actual score is 1 for the winner, 0 for the loser -- there are no
    # draws in this format (exactly one player is ever declared the
    # winner of a match, see completion.py).
    winner_after = round(winner_before + K_FACTOR * (1 - expected_winner))
    loser_after = round(loser_before + K_FACTOR * (0 - (1 - expected_winner)))

    winner_row.rating = winner_after
    winner_row.matches_played += 1
    loser_row.rating = loser_after
    loser_row.matches_played += 1

    await db.commit()
    return RatingResult(
        winner_before=winner_before,
        winner_after=winner_after,
        loser_before=loser_before,
        loser_after=loser_after,
    )
