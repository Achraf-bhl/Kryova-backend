"""The product repository, kept in the database so two workers cannot each hold a head
(ROAD_TO_10 9.4).

`ProductRepository` is the rule set (optimistic base check, leases, empty-commit refusal);
`ProductStore` is where its state lives. Every use goes through one door:

    with store.transaction() as repository:
        repository.commit(structure, author=..., base=head_digest, now=time.time())

On entry it takes `pg_advisory_xact_lock` on the product, so a second worker's transaction
**waits** until the first commits or rolls back and then reads the head the first one wrote --
the lost update that an in-process object cannot see is not possible, and the
`(project, key, number)` unique index is the second wall if the lock were ever skipped. On a
clean exit the revisions the repository gained and its whole lease book are written back in the
caller's transaction; if the block raises (a `LockError` is the normal refusal), **nothing is
written**, so a refused commit leaves no half-state.

The lock dies at COMMIT or ROLLBACK, which is what makes it safe behind a transaction-pooling
PgBouncer (CLAUDE.md, *Database*). On a database without advisory locks (the offline SQLite
suite) it is a no-op, and the unique index is then the only guard.

Not done: a route. Nothing in `app/api` exposes a product yet, so this is the shared state a
route will sit on, not a feature a user can reach.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.assembly.errors import LockError
from app.assembly.locking import Lease, ProductRepository, Revision
from app.assembly.structure import ProductStructure
from app.models import Project
from app.models.product import ProductLeaseRow, ProductRevisionRow


class ProductStore:
    """One product of one project, as rows."""

    def __init__(self, db: Session, project: Project, key: str) -> None:
        if not key.strip():
            raise LockError("A product needs a key: it names the product within its project.")
        self._db = db
        self._project = project
        self._key = key

    # -- state ----------------------------------------------------------------

    def _lock(self) -> None:
        if self._db.get_bind().dialect.name != "postgresql":
            return
        self._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"product:{self._project.id}:{self._key}"},
        )

    def _revision_rows(self) -> list[ProductRevisionRow]:
        return list(
            self._db.scalars(
                select(ProductRevisionRow)
                .where(
                    ProductRevisionRow.project_id == self._project.id,
                    ProductRevisionRow.product_key == self._key,
                )
                .order_by(ProductRevisionRow.number)
            )
        )

    def exists(self) -> bool:
        return (
            self._db.scalar(
                select(ProductRevisionRow.id)
                .where(
                    ProductRevisionRow.project_id == self._project.id,
                    ProductRevisionRow.product_key == self._key,
                )
                .limit(1)
            )
            is not None
        )

    def create(
        self,
        structure: ProductStructure,
        *,
        author: str,
        note: str = "initial revision",
        at: float | None = None,
    ) -> ProductRepository:
        """Store revision 1. Refused if the product already exists."""
        self._lock()
        if self.exists():
            raise LockError(
                f"The product {self._key!r} already exists in this project. Open it and commit "
                "to it; creating it again would put two histories under one name."
            )
        repository = ProductRepository(structure, author=author, note=note, at=at)
        self._write(repository, already=0)
        return repository

    # -- the one door ---------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[ProductRepository]:
        """Lock the product, load it, yield it, and write it back if the block finishes."""
        self._lock()
        rows = self._revision_rows()
        if not rows:
            raise LockError(
                f"There is no product {self._key!r} in this project. Create it first."
            )
        history = [
            Revision.from_parts(
                number=row.number,
                structure=row.structure,
                author=row.author,
                change=row.change,
                parent=row.parent,
                note=row.note,
                at=row.at,
                digest=row.digest,
            )
            for row in rows
        ]
        leases = [
            Lease(
                component=row.component,
                holder=row.holder,
                taken_at=row.taken_at,
                expires_at=row.expires_at,
                note=row.note,
            )
            for row in self._db.scalars(
                select(ProductLeaseRow).where(
                    ProductLeaseRow.project_id == self._project.id,
                    ProductLeaseRow.product_key == self._key,
                )
            )
        ]
        repository = ProductRepository.restore(history, leases)
        yield repository
        self._write(repository, already=len(history))

    def _write(self, repository: ProductRepository, *, already: int) -> None:
        for revision in repository.history()[already:]:
            self._db.add(
                ProductRevisionRow(
                    organisation_id=self._project.organisation_id,
                    project_id=self._project.id,
                    product_key=self._key,
                    number=revision.number,
                    digest=revision.digest,
                    parent=revision.parent,
                    author=revision.author,
                    note=revision.note,
                    at=revision.at,
                    structure=revision.structure.to_dict(),
                    change=revision.change.to_dict(),
                )
            )
        # The whole book, replaced: a lease released or renewed is a row removed or changed,
        # and diffing would be a second place to get that wrong. Delete first, flush, then add,
        # so the unique index never sees a component twice.
        self._db.execute(
            delete(ProductLeaseRow).where(
                ProductLeaseRow.project_id == self._project.id,
                ProductLeaseRow.product_key == self._key,
            )
        )
        self._db.flush()
        for lease in repository.leases.all():
            self._db.add(
                ProductLeaseRow(
                    organisation_id=self._project.organisation_id,
                    project_id=self._project.id,
                    product_key=self._key,
                    component=lease.component,
                    holder=lease.holder,
                    taken_at=lease.taken_at,
                    expires_at=lease.expires_at,
                    note=lease.note,
                )
            )
        self._db.flush()


__all__ = ["ProductStore"]
