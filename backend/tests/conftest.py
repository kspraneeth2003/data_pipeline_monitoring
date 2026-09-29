"""Shared test setup.

The models are written against Postgres and use JSONB, which SQLite cannot
render. Rather than weaken the production models to a portable JSON type -
JSONB is the right column and the app is Postgres-only - the compiler is
taught to emit plain JSON for SQLite. The semantics the tests depend on
(store a dict, read a dict back) are identical.
"""

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json(type_, compiler, **kw):
    return "JSON"
