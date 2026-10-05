"""The product repository's state in the database (ROAD_TO_10 9.4).

Two halves. The first needs nothing: `ProductRepository.restore` and the `from_*` readers are
pure, and a stored history is *checked*, not believed. The second uses `db_session`: a commit
made through one `ProductStore` is the head the next one reads, which is the thing an in-process
repository cannot do across two workers. (The advisory lock itself is Postgres-only and is not
asserted here: a test that holds it would need a second connection; the unique index is what
the second test relies on.)
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.assembly.errors import LockError
from app.assembly.locking import (
    Change,
    Lease,
    LeaseBook,
    ProductRepository,
    Revision,
)
from app.assembly.store import ProductStore
from app.models import Project
from app.models.product import ProductLeaseRow, ProductRevisionRow
from tests.test_assembly_locking import press, revised

MINUTE = 60.0


def _two_revisions() -> ProductRepository:
    repo = ProductRepository(press(), author="ana", at=1.0)
    repo.commit(
        revised(press(), "frame", material="aluminium"),
        author="bo",
        base=repo.head.digest,
        at=2.0,
    )
    return repo


class TestARepositoryIsRebuiltFromItsParts:
    def test_a_revision_survives_a_round_trip_through_its_dicts(self) -> None:
        head = _two_revisions().head
        back = Revision.from_parts(
            number=head.number,
            structure=head.structure.to_dict(),
            author=head.author,
            change=head.change.to_dict(),
            parent=head.parent,
            note=head.note,
            at=head.at,
            digest=head.digest,
        )
        # `ProductStructure` has no `__eq__` (it is not a dataclass), so compare what it says.
        assert back.structure.to_dict() == head.structure.to_dict()
        assert (back.number, back.digest, back.author, back.change, back.parent, back.at) == (
            head.number,
            head.digest,
            head.author,
            head.change,
            head.parent,
            head.at,
        )

    def test_a_change_and_a_lease_round_trip(self) -> None:
        change = Change(added=("a",), removed=("b",), modified=("c",), root_changed=True)
        assert Change.from_dict(change.to_dict()) == change
        lease = Lease("frame", "ana", 1.0, 61.0, "welding")
        assert Lease.from_dict(lease.to_dict()) == lease

    def test_a_restored_repository_enforces_the_same_rules(self) -> None:
        original = _two_revisions()
        original.leases.take("frame", "ana", now=10.0, seconds=MINUTE)
        again = ProductRepository.restore(original.history(), original.leases.all())

        assert again.head.digest == original.head.digest
        with pytest.raises(LockError, match="held by 'ana'"):
            again.commit(
                revised(again.structure, "frame", material="titanium"),
                author="bo",
                base=again.head.digest,
                now=20.0,
            )

    def test_a_stale_base_is_still_refused_after_restore(self) -> None:
        original = _two_revisions()
        again = ProductRepository.restore(original.history())
        with pytest.raises(LockError, match="wrote against"):
            again.commit(
                revised(press(), "table", material="brass"),
                author="cy",
                base=original.history()[0].digest,
            )

    def test_a_history_with_a_gap_is_refused_by_name(self) -> None:
        history = _two_revisions().history()
        with pytest.raises(LockError, match="broken at position 1"):
            ProductRepository.restore(history[1:])

    def test_a_history_whose_parent_link_is_wrong_is_refused(self) -> None:
        first, second = _two_revisions().history()
        forged = Revision(
            number=2,
            digest=second.digest,
            structure=second.structure,
            author=second.author,
            change=second.change,
            parent="not-the-first-digest",
        )
        with pytest.raises(LockError, match="parent"):
            ProductRepository.restore([first, forged])

    def test_an_empty_history_is_refused(self) -> None:
        with pytest.raises(LockError, match="at least one revision"):
            ProductRepository.restore([])

    def test_a_stored_document_that_no_longer_matches_its_digest_is_refused(self) -> None:
        head = _two_revisions().head
        with pytest.raises(LockError, match="digest"):
            Revision.from_parts(
                number=1,
                structure=head.structure.to_dict(),
                author="x",
                change={},
                parent=None,
                note="",
                at=None,
                digest="0" * 64,
            )

    def test_an_expired_lease_is_kept_as_history_in_the_book(self) -> None:
        book = LeaseBook.restore([Lease("frame", "ana", 0.0, 5.0)])
        assert book.all()[0].component == "frame"
        assert book.live(now=10.0) == ()


@pytest.fixture
def project(db_session: Session, project_id: str) -> Project:
    found = db_session.get(Project, project_id)
    assert found is not None
    return found


class TestTheStoreSharesOneHead:
    def test_a_commit_through_one_store_is_the_head_the_next_one_reads(
        self, db_session: Session, project: Project
    ) -> None:
        ProductStore(db_session, project, "press").create(press(), author="ana", at=1.0)

        with ProductStore(db_session, project, "press").transaction() as first:
            first.commit(
                revised(first.structure, "frame", material="aluminium"),
                author="ana",
                base=first.head.digest,
                at=2.0,
            )
        with ProductStore(db_session, project, "press").transaction() as second:
            assert second.head.number == 2
            assert second.structure.component("frame").material == "aluminium"

    def test_the_second_worker_writing_against_the_old_head_is_refused(
        self, db_session: Session, project: Project
    ) -> None:
        ProductStore(db_session, project, "press").create(press(), author="ana")
        with ProductStore(db_session, project, "press").transaction() as one:
            stale = one.head.digest
            one.commit(
                revised(one.structure, "frame", material="aluminium"), author="ana", base=stale
            )
        with pytest.raises(LockError, match="wrote against"):
            with ProductStore(db_session, project, "press").transaction() as two:
                two.commit(
                    revised(press(), "table", material="brass"), author="bo", base=stale
                )

    def test_a_refused_commit_writes_nothing(self, db_session: Session, project: Project) -> None:
        ProductStore(db_session, project, "press").create(press(), author="ana")
        with pytest.raises(LockError):
            with ProductStore(db_session, project, "press").transaction() as repo:
                repo.leases.take("frame", "ana", now=1.0, seconds=MINUTE)
                repo.commit(press(), author="ana", base=repo.head.digest)  # changes nothing
        assert db_session.query(ProductLeaseRow).count() == 0
        assert db_session.query(ProductRevisionRow).count() == 1

    def test_a_lease_taken_in_one_worker_blocks_another_workers_commit(
        self, db_session: Session, project: Project
    ) -> None:
        ProductStore(db_session, project, "press").create(press(), author="ana")
        with ProductStore(db_session, project, "press").transaction() as repo:
            repo.leases.take("frame", "ana", now=100.0, seconds=MINUTE)
        with pytest.raises(LockError, match="held by 'ana'"):
            with ProductStore(db_session, project, "press").transaction() as other:
                other.commit(
                    revised(other.structure, "frame", material="titanium"),
                    author="bo",
                    base=other.head.digest,
                    now=110.0,
                )

    def test_a_released_lease_is_gone_from_the_rows(
        self, db_session: Session, project: Project
    ) -> None:
        ProductStore(db_session, project, "press").create(press(), author="ana")
        with ProductStore(db_session, project, "press").transaction() as repo:
            repo.leases.take("frame", "ana", now=1.0, seconds=MINUTE)
        assert db_session.query(ProductLeaseRow).count() == 1
        with ProductStore(db_session, project, "press").transaction() as repo:
            repo.leases.release("frame", "ana", now=2.0)
        assert db_session.query(ProductLeaseRow).count() == 0

    def test_creating_a_product_twice_is_refused(
        self, db_session: Session, project: Project
    ) -> None:
        ProductStore(db_session, project, "press").create(press(), author="ana")
        with pytest.raises(LockError, match="already exists"):
            ProductStore(db_session, project, "press").create(press(), author="bo")

    def test_opening_a_product_that_was_never_created_is_refused(
        self, db_session: Session, project: Project
    ) -> None:
        with pytest.raises(LockError, match="no product"):
            with ProductStore(db_session, project, "ghost").transaction():
                pass

    def test_two_products_in_one_project_have_separate_histories(
        self, db_session: Session, project: Project
    ) -> None:
        ProductStore(db_session, project, "press").create(press(), author="ana")
        ProductStore(db_session, project, "other").create(press(), author="ana")
        with ProductStore(db_session, project, "press").transaction() as repo:
            repo.commit(
                revised(repo.structure, "frame", material="aluminium"),
                author="ana",
                base=repo.head.digest,
            )
        with ProductStore(db_session, project, "other").transaction() as repo:
            assert repo.head.number == 1

    def test_a_blank_key_is_refused(self, db_session: Session, project: Project) -> None:
        with pytest.raises(LockError, match="needs a key"):
            ProductStore(db_session, project, "  ")
