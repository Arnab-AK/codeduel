"""
Tests for Elo rating updates -- correctness of the formula itself, that
rating persists and accumulates correctly across multiple matches for the
same player_id, and specifically that the consistent-lock-ordering design
in apply_elo_update() actually prevents deadlock rather than just being a
plausible-sounding comment.

Like test_match_completion.py, the deadlock test drives genuinely
concurrent asyncio tasks against real Postgres transactions rather than
sequential calls -- see that file's docstring for why (TestClient's
separate thread/loop doesn't mix with this app's process-wide async
clients; calling service functions directly on the shared test event loop
does).
"""
import asyncio
import uuid

import pytest
from sqlalchemy import delete

from app.db.models import PlayerRating
from app.db.session import async_session_factory
from app.matches.rating import DEFAULT_RATING, K_FACTOR, apply_elo_update


@pytest.fixture
def two_players():
    return uuid.uuid4(), uuid.uuid4()


async def _cleanup(*player_ids: uuid.UUID):
    async with async_session_factory() as db:
        await db.execute(delete(PlayerRating).where(PlayerRating.player_id.in_(player_ids)))
        await db.commit()


async def test_equal_rated_players_move_by_half_k(two_players):
    winner, loser = two_players
    try:
        async with async_session_factory() as db:
            result = await apply_elo_update(db, winner, loser)

        # Both start at DEFAULT_RATING, so expected score is exactly 0.5
        # for each -- the textbook case, easy to hand-verify:
        # winner: 1200 + 32*(1 - 0.5) = 1216, loser: 1200 + 32*(0 - 0.5) = 1184
        assert result.winner_before == DEFAULT_RATING
        assert result.loser_before == DEFAULT_RATING
        assert result.winner_after == DEFAULT_RATING + K_FACTOR // 2
        assert result.loser_after == DEFAULT_RATING - K_FACTOR // 2
    finally:
        await _cleanup(winner, loser)


async def test_beating_a_higher_rated_opponent_gains_more(two_players):
    underdog, favorite = two_players
    try:
        async with async_session_factory() as db:
            db.add(PlayerRating(player_id=underdog, rating=1000))
            db.add(PlayerRating(player_id=favorite, rating=1400))
            await db.commit()

        async with async_session_factory() as db:
            result = await apply_elo_update(db, winner_id=underdog, loser_id=favorite)

        # A big upset should move the underdog's rating by close to the
        # full K-factor (expected score was close to 0 going in).
        gained = result.winner_after - result.winner_before
        assert gained > K_FACTOR * 0.9
    finally:
        await _cleanup(underdog, favorite)


async def test_rating_persists_and_accumulates_across_matches(two_players):
    player_a, player_b = two_players
    try:
        async with async_session_factory() as db:
            first = await apply_elo_update(db, winner_id=player_a, loser_id=player_b)
        async with async_session_factory() as db:
            second = await apply_elo_update(db, winner_id=player_a, loser_id=player_b)

        # Second match starts from where the first left off, not back at
        # DEFAULT_RATING -- proves the row is being updated in place, not
        # recreated.
        assert second.winner_before == first.winner_after
        assert second.loser_before == first.loser_after

        async with async_session_factory() as db:
            row = await db.get(PlayerRating, player_a)
            assert row.matches_played == 2
    finally:
        await _cleanup(player_a, player_b)


async def test_concurrent_updates_on_an_overlapping_pair_never_deadlock(two_players):
    """
    The scenario the sorted-lock-order design in apply_elo_update() exists
    for: two concurrent match completions on the SAME two players, but
    with opposite winner/loser roles -- (X beat Y) racing (Y beat X). Locked
    "winner first, then loser" naively, one transaction would lock X-then-Y
    while the other locks Y-then-X: a textbook deadlock. Locked in a fixed
    order regardless of role (as implemented), both transactions request
    the same two locks in the same order, so they only ever serialize,
    never deadlock. This test proves both calls complete without either
    raising -- a real deadlock would surface as Postgres aborting one side
    with a database error, not a hang (Postgres's own deadlock detector
    would eventually kill one transaction), so a passing test here is a
    real, meaningful assertion, not just "it didn't hang forever".
    """
    x, y = two_players
    start = asyncio.Event()

    async def race(winner_id, loser_id):
        async with async_session_factory() as db:
            await start.wait()
            return await apply_elo_update(db, winner_id, loser_id)

    try:
        task_1 = asyncio.create_task(race(x, y))  # "X beat Y"
        task_2 = asyncio.create_task(race(y, x))  # "Y beat X", racing the same pair
        await asyncio.sleep(0)
        start.set()

        await asyncio.wait_for(asyncio.gather(task_1, task_2), timeout=10)

        # Both updates were applied -- each player played 2 "matches" here
        # (each was a winner in one call and a loser in the other), and
        # neither update was lost to the other.
        async with async_session_factory() as db:
            row_x = await db.get(PlayerRating, x)
            row_y = await db.get(PlayerRating, y)
        assert row_x.matches_played == 2
        assert row_y.matches_played == 2
    finally:
        await _cleanup(x, y)
