from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Problem, Submission
from app.db.session import get_db
from app.execution.judge import Judge, to_public_results
from app.schemas.submissions import SubmissionCreate, SubmissionResult

router = APIRouter(prefix="/submissions", tags=["submissions"])

# One Judge per process is fine -- it's stateless aside from holding a
# SandboxRunner instance (itself just a docker.DockerClient handle). The
# actual concurrency bound lives in judge.py's module-level semaphore, not
# here.
_judge = Judge()


@router.post("", response_model=SubmissionResult)
async def create_submission(payload: SubmissionCreate, db: AsyncSession = Depends(get_db)):
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
        problem_id=payload.problem_id, code=payload.code, language=payload.language
    )
    db.add(submission)
    await db.flush()

    # Phase 1 is intentionally synchronous: the request blocks until grading
    # finishes and returns the final verdict. This is fine for a standalone
    # "submit and see if it passes" endpoint. It stops being fine once a live
    # duel needs to broadcast incremental progress to an opponent over a
    # WebSocket while grading runs -- that's precisely why phase 3
    # (WebSocket layer) exists as its own step rather than being bolted on
    # here.
    await _judge.grade(db, submission, test_cases)
    await db.commit()
    await db.refresh(submission)

    return SubmissionResult(
        id=submission.id,
        problem_id=submission.problem_id,
        status=submission.status,
        passed_count=submission.passed_count,
        total_count=submission.total_count,
        results=to_public_results(submission.results or []),
    )
