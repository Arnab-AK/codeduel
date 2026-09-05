import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_auth,
    routes_matches,
    routes_players,
    routes_problems,
    routes_queue,
    routes_submissions,
    routes_ws,
)
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
# local development. Still fine now that auth exists (phase 6): sessions
# are bearer tokens in an Authorization header set explicitly by client JS,
# not cookies -- the browser CORS restrictions this wildcard would actually
# be dangerous for (allow_origins="*" combined with allow_credentials=True,
# i.e. cookie-based auth) don't apply here. Revisit if cookie-based auth
# ever replaces this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_problems.router)
app.include_router(routes_submissions.router)
app.include_router(routes_queue.router)
app.include_router(routes_ws.router)
app.include_router(routes_matches.router)
app.include_router(routes_players.router)
app.include_router(routes_auth.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
