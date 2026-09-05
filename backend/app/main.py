import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_problems, routes_queue, routes_submissions
from app.matchmaking.matchmaker import matchmaker_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One background task for the life of the process -- see
    # matchmaking/matchmaker.py for why this doesn't need (and shouldn't
    # have) a separate worker process for a single-instance deployment.
    task = asyncio.create_task(matchmaker_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="CodeDuel", lifespan=lifespan)

# Dev-only convenience: the frontend runs on a different origin/port during
# local development. This is intentionally permissive (`*`) because there
# are no cookies/credentialed requests yet (auth is phase 6) -- revisit this
# to a real allowlist once auth introduces session cookies or bearer tokens
# whose exposure actually matters.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_problems.router)
app.include_router(routes_submissions.router)
app.include_router(routes_queue.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
