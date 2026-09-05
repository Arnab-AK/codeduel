from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import get_current_user_id
from app.db.models import Match, MatchStatus, Problem, Submission, SubmissionStatus
from app.db.session import get_db
from app.execution.judge import Judge, to_public_results
from app.matches.completion import complete_match_if_winner
from app.matches.rating import apply_elo_update
from app.realtime.broadcaster import publish_match_complete, publish_progress
from app.schemas.submissions import SubmissionCreate, SubmissionResult

router = APIRouter(prefix="/submissions", tags=["submissions"])

# One Judge per process is fine -- it's stateless aside from holding a
# SandboxRunner instance (itself just a docker.DockerClient handle). The
# actual concurrency bound lives in judge.py's module-level semaphore, not
# here.
_judge = Judge()


@router.post("", response_model=SubmissionResult)
async def create_submission(
    payload: SubmissionCreate,
    db: AsyncSession = Depends(get_db),
    player_id=Depends(get_current_user_id),
):
    # player_id is always the authenticated caller now -- phase 1-5's
    # "client supplies whatever player_id it wants" placeholder is gone,
    # closing a real spoofing gap (anyone could previously submit code, or
    # claim a match win, as any player_id they liked). Every submission,
    # standalone practice or match-attached, is now made by a real logged-in
    # user; there's no reason to keep an unauthenticated path for one and
    # not the other.
    match: Match | None = None
    if payload.match_id is not None:
        match = await db.get(Match, payload.match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found")
        if player_id not in (match.player_one_id, match.player_two_id):
            raise HTTPException(status_code=403, detail="Not a participant in this match")
        if match.problem_id != payload.problem_id:
            raise HTTPException(
                status_code=400, detail="problem_id does not match this match's assigned problem"
            )
        if match.status != MatchStatus.IN_PROGRESS:
            # Best-effort, NOT the correctness guarantee: this is a plain
            # read, so it's possible (if rare) for the match to complete in
            # the gap between this check and the grading below -- that's
            # fine, because the actual guarantee against two winners lives
            # entirely in complete_match_if_winner()'s atomic UPDATE, not
            # here. This check exists purely so an obviously-late
            # submission gets rejected immediately instead of burning a
            # sandbox run on a match that's already decided.
            raise HTTPException(status_code=409, detail="Match is no longer in progress")

    problem = (
        await db.execute(
            select(Problem)
            .where(Problem.id == payload.problem_id)
            .options(selectinload(Problem.test_cases))
        )
    ).scalar_one_or_none()
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")

    # Graded against every test case, visible and hidden alike -- only the
    # response redaction below (to_public_results) knows the difference.
    test_cases = problem.test_cases

    submission = Submission(
        problem_id=payload.problem_id,
        code=payload.code,
        language=payload.language,
        match_id=payload.match_id,
        player_id=player_id,
    )
    db.add(submission)
    await db.flush()

    # Grading is synchronous: the request blocks until the sandbox run
    # finishes and returns the final verdict. That's fine here -- our runs
    # take tens of milliseconds (see phase 1's design decisions) -- and it
    # keeps this endpoint the single place a submission gets graded,
    # whether it's a standalone practice run or part of a live duel. What
    # phase 3 adds on top is the line below: once a match-attached
    # submission is graded, its result gets pushed to the opponent's
    # WebSocket connection instead of only being visible to whoever
    # submitted it.
    await _judge.grade(db, submission, test_cases)
    await db.commit()
    await db.refresh(submission)

    won_match = False
    new_rating = None
    if match is not None:
        await publish_progress(
            match_id=match.id,
            player_id=submission.player_id,
            passed_count=submission.passed_count,
            total_count=submission.total_count,
            status=submission.status.value,
        )
        if submission.status == SubmissionStatus.ACCEPTED:
            # See matches/completion.py for the atomicity this relies on.
            # If two players' ACCEPTED submissions land here within
            # milliseconds of each other, exactly one of these two calls
            # (across the two concurrent requests) gets won_match = True --
            # guaranteed by Postgres's row-level locking on the UPDATE
            # inside complete_match_if_winner, not by anything sequenced
            # in this Python function.
            won_match = await complete_match_if_winner(db, match.id, submission.player_id)
            if won_match:
                loser_id = (
                    match.player_two_id
                    if submission.player_id == match.player_one_id
                    else match.player_one_id
                )
                rating_result = await apply_elo_update(db, submission.player_id, loser_id)
                new_rating = rating_result.winner_after
                # Same lightweight audit-trail write for every match --
                # see the Match model's rating columns.
                match.winner_rating_before = rating_result.winner_before
                match.winner_rating_after = rating_result.winner_after
                match.loser_rating_before = rating_result.loser_before
                match.loser_rating_after = rating_result.loser_after
                await db.commit()
                await publish_match_complete(
                    match_id=match.id,
                    winner_id=submission.player_id,
                    loser_id=loser_id,
                    winner_rating_before=rating_result.winner_before,
                    winner_rating_after=rating_result.winner_after,
                    loser_rating_before=rating_result.loser_before,
                    loser_rating_after=rating_result.loser_after,
                )

    return SubmissionResult(
        id=submission.id,
        problem_id=submission.problem_id,
        status=submission.status,
        passed_count=submission.passed_count,
        total_count=submission.total_count,
        results=to_public_results(submission.results or []),
        won_match=won_match,
        new_rating=new_rating,
    )
