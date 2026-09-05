"""
Central app configuration, loaded once from environment variables / .env.
Using pydantic-settings (rather than scattering os.getenv() calls through the
codebase) gives us validation at process startup — a missing/malformed env
var fails fast when the app boots, not three requests into production.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str

    # --- Sandbox execution limits ---
    # These bound what a single submitted program can do to the host. They're
    # env-driven (not hardcoded in execution/limits.py) so they can be tuned
    # per-environment (e.g. tighter on a free-tier deploy box) without a code
    # change. See execution/docker_runner.py for how each one is applied.
    sandbox_memory_limit: str = "256m"
    sandbox_cpu_limit: float = 0.5
    sandbox_pids_limit: int = 64
    # Wall-clock ceiling for a SINGLE test case's program run, enforced by the
    # in-container harness (execution/images/python/harness.py) via
    # subprocess timeout. This is the primary timeout.
    sandbox_timeout_seconds: int = 5
    # How many submissions may be executing in sandboxes at once, process-wide.
    # Each one is a real container consuming host CPU/memory, so this is the
    # backpressure valve that keeps a burst of submissions from starving the
    # host. See execution/judge.py.
    sandbox_max_concurrent: int = 4

    sandbox_image: str = "codeduel-python-runner:latest"


settings = Settings()
