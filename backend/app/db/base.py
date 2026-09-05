from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base so Alembic's autogenerate can discover all
    models off a single metadata object (see alembic/env.py)."""
    pass
