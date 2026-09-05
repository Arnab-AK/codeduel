"""
Race-condition tests for match win determination -- the piece of this
project built specifically to demonstrate handling a real concurrency bug
correctly, not papering over it. See app/matches/completion.py's docstring
for the full reasoning; this file exists to prove it holds under genuine
concurrent execution, not just to exercise the happy path twice.

These call the route handler / service functions directly rather than
through Starlette's TestClient. Two reasons: (1) it lets two "requests" run
as genuinely concurrent asyncio tasks, each with its own real AsyncSession
and real Postgres transaction, on the SAME event loop the rest of this
suite shares -- TestClient's separate thread+loop would conflict with this
app's process-wide async DB/Redis clients (see the phase 3 note in
README.md); (2) FastAPI route functions are just plain async functions --
nothing stops calling create_submission(payload, db) directly with an
explicit session instead of going through Depends(get_db).
"""
import asyncio
import uuid

import pytest
from sqlalchemy import delete

from app.api.routes_submissions import create_submission
from app.db.models import Difficulty, Match, MatchStatus, Problem, Submission, TestCase
from app.db.session import async_session_factory
from app.matches.completion import complete_match_if_winner
from app.schemas.submissions import SubmissionCreate

ACCEPTED_CODE = "print('ok')"


@pytest.fixture
async def duel_match():
    """A real Match plus a trivial single-test-case Problem, so either
    "player" can trivially produce an ACCEPTED submission on demand."""
    async with async_session_factory() as db:
        problem = Problem(
            slug=f"race-test-{uuid.uuid4().hex[:8]}",
            title="Race test problem",
            description="print ok",
            difficulty=Difficulty.EASY,
            test_cases=[TestCase(input="", expected_output="ok", is_hidden=False, order_index=0)],
        )
        db.add(problem)
        await db.flush()

        player_a, player_b = uuid.uuid4(), uuid.uuid4()
        match = Match(player_one_id=player_a, player_two_id=player_b, problem_id=problem.id)
        db.add(match)
        await db.commit()
        match_id, problem_id = match.id, problem.id

    yield match_id, problem_id, player_a, player_b

    async with async_session_factory() as db:
        await db.execute(delete(Submission).where(Submission.match_id == match_id))
        await db.execute(delete(Match).where(Match.id == match_id))
        await db.execute(delete(Problem).where(Problem.id == problem_id))
        await db.commit()


async def test_concurrent_win_attempts_exactly_one_succeeds(duel_match):
    match_id, _problem_id, player_a, player_b = duel_match
    start = asyncio.Event()

    async def attempt(winner_id: uuid.UUID) -> bool:
        async with async_session_factory() as db:
            # Both tasks block here until released together below, so their
            # UPDATEs reach Postgres as close to simultaneously as this
            # process can arrange -- the point is to actually exercise row
            # contention, not just call the function twice in sequence.
            await start.wait()
            return await complete_match_if_winner(db, match_id, winner_id)

    task_a = asyncio.create_task(attempt(player_a))
    task_b = asyncio.create_task(attempt(player_b))
    await asyncio.sleep(0)  # let both tasks reach `await start.wait()` first
    start.set()
    result_a, result_b = await asyncio.gather(task_a, task_b)

    # The core invariant: never both, never neither -- exactly one caller
    # wins the race, regardless of which.
    assert result_a != result_b

    async with async_session_factory() as db:
        match = await db.get(Match, match_id)
        assert match.status == MatchStatus.COMPLETED
        assert match.winner_id == (player_a if result_a else player_b)


async def test_a_third_attempt_after_completion_always_fails(duel_match):
    match_id, _problem_id, player_a, player_b = duel_match
    async with async_session_factory() as db:
        assert await complete_match_if_winner(db, match_id, player_a) is True
    async with async_session_factory() as db:
        # Someone else (or a retried request) trying to claim victory on an
        # already-decided match must always lose, not just "usually" lose.
        assert await complete_match_if_winner(db, match_id, player_b) is False


async def test_concurrent_accepted_submissions_end_to_end(duel_match):
    """Full path: two real submissions, through the real endpoint function,
    both graded by the real sandbox, racing for the same match -- not just
    the completion primitive in isolation."""
    match_id, problem_id, player_a, player_b = duel_match

    async def submit_as(player_id: uuid.UUID):
        async with async_session_factory() as db:
            payload = SubmissionCreate(
                problem_id=problem_id,
                code=ACCEPTED_CODE,
                language="python",
                match_id=match_id,
                player_id=player_id,
            )
            return await create_submission(payload, db)

    result_a, result_b = await asyncio.gather(submit_as(player_a), submit_as(player_b))

    assert result_a.status.value == "accepted"
    assert result_b.status.value == "accepted"
    # Both solved it -- but only one match win is ever recorded.
    assert result_a.won_match != result_b.won_match

    async with async_session_factory() as db:
        match = await db.get(Match, match_id)
        assert match.status == MatchStatus.COMPLETED
        winner = player_a if result_a.won_match else player_b
        assert match.winner_id == winner
