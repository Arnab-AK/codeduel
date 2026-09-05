"""
Seeds the local database with sample problems so there's something to
exercise the submission endpoint (and, from phase 2 on, matchmaking's random
problem selection) against.

Idempotent by slug -- safe to run more than once instead of throwing a
unique-constraint error on a rerun, which is the more likely way this script
actually gets used in practice (e.g. after a fresh `alembic upgrade head`).

Run from backend/ with the venv active:
    python -m scripts.seed
"""
import asyncio

from sqlalchemy import select

from app.db.models import Difficulty, Problem, TestCase
from app.db.session import async_session_factory

PROBLEMS = [
    dict(
        slug="sum-two-integers",
        title="Sum of Two Integers",
        description=(
            "Read two space-separated integers `a` and `b` from stdin and "
            "print their sum.\n\nExample:\nInput: `2 3`\nOutput: `5`"
        ),
        difficulty=Difficulty.EASY,
        test_cases=[
            TestCase(input="2 3\n", expected_output="5", is_hidden=False, order_index=0),
            TestCase(input="10 -4\n", expected_output="6", is_hidden=False, order_index=1),
            # Hidden: same problem, but zero and large-magnitude inputs --
            # a solution that special-cases the visible examples won't
            # necessarily handle these.
            TestCase(input="0 0\n", expected_output="0", is_hidden=True, order_index=2),
            TestCase(
                input="1000000 999999\n",
                expected_output="1999999",
                is_hidden=True,
                order_index=3,
            ),
        ],
    ),
    dict(
        slug="fizzbuzz",
        title="FizzBuzz",
        description=(
            "Read an integer `n` from stdin. For each integer `i` from 1 to "
            "`n` inclusive, print `Fizz` if `i` is divisible by 3, `Buzz` if "
            "divisible by 5, `FizzBuzz` if divisible by both, otherwise `i` "
            "-- one per line.\n\nExample:\nInput: `5`\nOutput:\n```\n1\n2\n"
            "Fizz\n4\nBuzz\n```"
        ),
        difficulty=Difficulty.EASY,
        test_cases=[
            TestCase(input="5\n", expected_output="1\n2\nFizz\n4\nBuzz", is_hidden=False, order_index=0),
            TestCase(input="1\n", expected_output="1", is_hidden=False, order_index=1),
            # Hidden: exercises the FizzBuzz (divisible by both 3 and 5) case.
            TestCase(
                input="15\n",
                expected_output="1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n11\nFizz\n13\n14\nFizzBuzz",
                is_hidden=True,
                order_index=2,
            ),
        ],
    ),
]


async def main():
    async with async_session_factory() as db:
        for spec in PROBLEMS:
            existing = (
                await db.execute(select(Problem).where(Problem.slug == spec["slug"]))
            ).scalar_one_or_none()
            if existing is not None:
                print(f"Skipping (already seeded): {spec['slug']}")
                continue
            problem = Problem(**spec)
            db.add(problem)
            await db.flush()
            print(f"Seeded problem: {problem.slug} ({problem.id})")
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
