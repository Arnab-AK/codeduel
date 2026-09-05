import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.config import settings
from app.db.base import Base
from app.db import models  # noqa: F401 -- import registers models onto Base.metadata

config = context.config

# Pull the URL from our own Settings (env-driven) rather than duplicating it
# as a static value in alembic.ini -- one source of truth for "where's the
# database", used by both the app and its migrations.
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # The app uses an async engine (asyncpg) everywhere else, so migrations
    # do too -- this way there's only ever one DB driver in the project to
    # reason about, instead of a sync driver (psycopg2) that exists purely
    # for Alembic. `run_sync` bridges Alembic's inherently synchronous
    # migration API onto our async connection.
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
