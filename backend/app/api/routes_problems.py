import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Problem
from app.db.session import get_db
from app.schemas.problems import ProblemDetail, ProblemSummary, TestCaseExample

router = APIRouter(prefix="/problems", tags=["problems"])


@router.get("", response_model=list[ProblemSummary])
async def list_problems(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Problem))).scalars().all()
    return rows


@router.get("/{problem_id}", response_model=ProblemDetail)
async def get_problem(problem_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    problem = (
        await db.execute(
            select(Problem).where(Problem.id == problem_id).options(selectinload(Problem.test_cases))
        )
    ).scalar_one_or_none()
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")

    # Only ever expose non-hidden test cases as "examples" -- hidden ones
    # exist specifically so a solution has to generalize, not just satisfy
    # whatever it can see. See db/models.py TestCase.is_hidden.
    examples = [
        TestCaseExample.model_validate(tc) for tc in problem.test_cases if not tc.is_hidden
    ]
    return ProblemDetail(
        id=problem.id,
        slug=problem.slug,
        title=problem.title,
        difficulty=problem.difficulty,
        description=problem.description,
        examples=examples,
    )
