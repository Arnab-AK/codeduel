from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Match, Problem, Submission
from app.db.session import get_db
from app.execution.judge import Judge, to_public_results
from app.realtime.broadcaster import publish_progress
from app.schemas.submissions import SubmissionCreate, SubmissionResult

router = APIRouter(prefix="/submissions", tags=["submissions"])

# One Judge per process is fine -- it's stateless aside from holding a
# SandboxRunner instance (itself just a docker.DockerClient handle). The
# actual concurrency bound lives in judge.py's module-level semaphore, not
# here.
_judge = Judge()


@router.post("", response_model=SubmissionResult)
async def create_submission(payload: SubmissionCreate, db: AsyncSession = Depends(get_db)):
    if (payload.match_id is None) != (payload.player_id is None):
        raise HTTPException(
            status_code=400, detail="match_id and player_id must be provided together"
        )

    match: Match | None = None
    if payload.match_id is not None:
        match = await db.get(Match, payload.match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="Match not found")
        if payload.player_id not in (match.player_one_id, match.player_two_id):
            raise HTTPException(status_code=403, detail="Not a participant in this match")
        if match.problem_id != payload.problem_id:
            raise HTTPException(
                status_code=400, detail="problem_id does not match this match's assigned problem"
            )

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
        player_id=payload.player_id,
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

    if match is not None:
        await publish_progress(
            match_id=match.id,
            player_id=submission.player_id,
            passed_count=submission.passed_count,
            total_count=submission.total_count,
            status=submission.status.value,
        )

    return SubmissionResult(
        id=submission.id,
        problem_id=submission.problem_id,
        status=submission.status,
        passed_count=submission.passed_count,
        total_count=submission.total_count,
        results=to_public_results(submission.results or []),
    )
