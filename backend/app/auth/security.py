"""
Password hashing, using the `bcrypt` package directly rather than the more
commonly-recommended `passlib`. That's a deliberate simplification for a
single-purpose need: passlib is a multi-algorithm abstraction layer, and
its bcrypt backend has a real, documented compatibility rough edge with
recent bcrypt releases (passlib reads a `__about__.__version__` attribute
that newer bcrypt versions removed). Since this project only ever wants
one hashing scheme, calling bcrypt's own small, stable API directly avoids
that friction entirely instead of pinning around it.
"""
import bcrypt

# bcrypt silently truncates input beyond 72 bytes rather than hashing the
# full password -- enforced at the schema layer (schemas/auth.py) with a
# max_length so this is a documented, deliberate limit, not a silent one.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
