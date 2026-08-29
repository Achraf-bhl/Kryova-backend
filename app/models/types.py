from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# Postgres -- every real deployment -- gets JSONB. The variant exists so the
# same metadata can also build on SQLite, which is what the test suite runs
# against by default rather than pointing at the production Neon database.
# The Postgres DDL is unchanged, so migrations are unaffected.
JSONB_compat = JSONB().with_variant(JSON(), "sqlite")
