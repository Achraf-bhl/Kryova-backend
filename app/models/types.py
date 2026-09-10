from enum import Enum as PyEnum
from typing import Any, TypeVar

from sqlalchemy import JSON, String, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB

# Postgres -- every real deployment -- gets JSONB. The variant exists so the
# same metadata can also build on SQLite, which is what the test suite runs
# against by default rather than pointing at the production Neon database.
# The Postgres DDL is unchanged, so migrations are unaffected.
JSONB_compat = JSONB().with_variant(JSON(), "sqlite")


E = TypeVar("E", bound=PyEnum)


class EnumText(TypeDecorator):
    """Store an enum as text, and always hand one back.

    **The bug this exists to stop has now been shipped twice.** A bare
    `mapped_column(String(16))` typed as an enum gives you the enum on a row
    *written* in this session and a plain `str` on one *loaded* from the
    database. Every `value is Thing.X` check then changes answer depending on
    whether the object came from the identity map or a SELECT -- so it works in
    a test that writes and reads in one session, and fails on the next request,
    where the whole row loads fresh.

    `StrEnum` makes it doubly easy to miss, because `value == Thing.X` stays
    True either way and only the identity checks break, silently. It was found
    the first time on `ConversationMessage.role` (which grew `MessageRoleType`,
    this class's ancestor) and the second time on `ApprovalGate.state`, where
    `gate.state.is_decided` raised `AttributeError` on any gate read back from
    the database -- which is every gate a reviewer ever opens.

    Text rather than a native PostgreSQL enum: the columns are already VARCHAR
    everywhere, and adding a member to an enum must never need a type migration.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[E], length: int = 32) -> None:
        self.enum_class = enum_class
        super().__init__(length=length)

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return self.enum_class(value).value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        return self.enum_class(value)
