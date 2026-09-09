"""Two authors on one product — master plan 14.5.

`app/assembly/locking.py` is what stands between "two agents decomposed the machine
in parallel" and "the second one silently erased the first". Every test here is named
after the loss it prevents.

Needs no database, no kernel and no clock: time is an argument everywhere, so a lease
expiring is a number in a test rather than a `sleep`. That is the whole reason the
module takes `now` — see its docstring.
"""

import pytest

from app.assembly.errors import LockError, MergeConflict
from app.assembly.locking import (
    Change,
    LeaseBook,
    ProductRepository,
    Workspace,
    changes_between,
    merged_view,
)
from app.assembly.placement import at
from app.assembly.structure import Component, Instance, ProductStructure

MINUTE = 60.0


def press() -> ProductStructure:
    """A three-component machine: a frame carrying two legs and a table."""
    return ProductStructure(
        root="press",
        components=[
            Component(
                name="press",
                instances=(
                    Instance(component="frame", tag="frame", index=1, placement=at(0, 0, 0)),
                    Instance(component="table", tag="table", index=1, placement=at(0, 0, 500)),
                ),
            ),
            Component(name="frame", design="frame-weldment", material="steel"),
            Component(name="table", design="table-plate", material="steel"),
        ],
    )


def revised(structure: ProductStructure, name: str, **fields) -> ProductStructure:
    """The structure with one component's fields changed — one author's edit."""
    current = structure.component(name)
    replacement = Component(
        name=current.name,
        instances=current.instances,
        design=fields.get("design", current.design),
        material=fields.get("material", current.material),
        revision=fields.get("revision", current.revision),
        description=fields.get("description", current.description),
    )
    return merged_view(structure, {name: replacement})


class TestASecondAuthorCannotSilentlyEraseTheFirst:
    """The lost update, which no frozen data structure can see happening."""

    def test_a_commit_against_a_stale_base_is_refused(self) -> None:
        repo = ProductRepository(press())
        base = repo.head.digest
        repo.commit(
            revised(repo.structure, "frame", revision="B"), author="ada", base=base
        )
        with pytest.raises(LockError, match="the head is now"):
            repo.commit(
                revised(repo.structure, "table", revision="C"), author="grace", base=base
            )

    def test_the_refusal_names_who_moved_it_and_what_they_did(self) -> None:
        repo = ProductRepository(press())
        base = repo.head.digest
        repo.commit(revised(repo.structure, "frame", revision="B"), author="ada", base=base)
        with pytest.raises(LockError) as raised:
            repo.commit(
                revised(repo.structure, "table", revision="C"), author="grace", base=base
            )
        message = str(raised.value)
        assert "'ada'" in message
        assert "changed frame" in message
        assert "Nothing has been lost and nothing has been written" in message

    def test_the_first_author_keeps_their_revision(self) -> None:
        repo = ProductRepository(press())
        base = repo.head.digest
        first = repo.commit(
            revised(repo.structure, "frame", revision="B"), author="ada", base=base
        )
        with pytest.raises(LockError):
            repo.commit(
                revised(repo.structure, "table", revision="C"), author="grace", base=base
            )
        assert repo.head.digest == first.digest
        assert repo.structure.component("frame").revision == "B"

    def test_committing_against_the_current_head_succeeds(self) -> None:
        repo = ProductRepository(press())
        first = repo.commit(
            revised(repo.structure, "frame", revision="B"), author="ada", base=repo.head.digest
        )
        second = repo.commit(
            revised(repo.structure, "table", revision="C"),
            author="grace",
            base=first.digest,
        )
        assert second.number == 3
        assert second.parent == first.digest
        assert repo.structure.component("frame").revision == "B"
        assert repo.structure.component("table").revision == "C"

    def test_a_base_from_another_product_is_named_as_such(self) -> None:
        repo = ProductRepository(press())
        with pytest.raises(LockError, match="never had"):
            repo.commit(
                revised(repo.structure, "frame", revision="B"),
                author="ada",
                base="0" * 32,
            )

    def test_a_commit_that_changes_nothing_is_refused(self) -> None:
        repo = ProductRepository(press())
        with pytest.raises(LockError, match="committed no change"):
            repo.commit(repo.structure, author="ada", base=repo.head.digest)


class TestALeaseSaysWhoIsAlreadyOnIt:
    def test_a_second_author_cannot_take_a_held_component(self) -> None:
        book = LeaseBook()
        book.take("frame", "ada", now=0.0, seconds=10 * MINUTE, note="re-sizing the uprights")
        with pytest.raises(LockError, match="re-sizing the uprights"):
            book.take("frame", "grace", now=60.0, seconds=MINUTE)

    def test_the_holder_may_extend_their_own_lease(self) -> None:
        book = LeaseBook()
        book.take("frame", "ada", now=0.0, seconds=MINUTE)
        again = book.take("frame", "ada", now=30.0, seconds=MINUTE)
        assert again.expires_at == pytest.approx(90.0)
        assert again.taken_at == pytest.approx(0.0)

    def test_a_lease_stops_holding_when_it_expires(self) -> None:
        book = LeaseBook()
        book.take("frame", "ada", now=0.0, seconds=MINUTE)
        assert book.holder_of("frame", now=59.0) == "ada"
        assert book.holder_of("frame", now=61.0) is None
        book.take("frame", "grace", now=61.0, seconds=MINUTE)

    def test_a_lease_that_expires_on_the_second_it_is_asked_about_is_over(self) -> None:
        """Strictly `now < expires_at`: a lease held *to* 60 s is not held *at* 60 s."""
        book = LeaseBook()
        book.take("frame", "ada", now=0.0, seconds=MINUTE)
        assert book.holder_of("frame", now=MINUTE) is None

    def test_a_lease_nobody_could_hold_is_refused(self) -> None:
        book = LeaseBook()
        with pytest.raises(LockError, match="expired before it was taken"):
            book.take("frame", "ada", now=0.0, seconds=0.0)

    def test_only_the_holder_releases_it(self) -> None:
        book = LeaseBook()
        book.take("frame", "ada", now=0.0, seconds=MINUTE)
        with pytest.raises(LockError, match="held by 'ada'"):
            book.release("frame", "grace", now=1.0)
        book.release("frame", "ada", now=1.0)
        assert book.holder_of("frame", now=1.0) is None

    def test_releasing_what_nobody_holds_is_refused_rather_than_ignored(self) -> None:
        book = LeaseBook()
        with pytest.raises(LockError, match="which nobody is holding"):
            book.release("frame", "ada", now=0.0)

    def test_a_lease_needs_a_holder_who_can_be_named_in_a_refusal(self) -> None:
        book = LeaseBook()
        with pytest.raises(LockError, match="needs a holder"):
            book.take("frame", "   ", now=0.0, seconds=MINUTE)


class TestALeaseIsEnforcedWhereItMattersWhichIsTheCommit:
    def test_a_commit_touching_someone_elses_component_is_refused(self) -> None:
        repo = ProductRepository(press())
        repo.leases.take("frame", "ada", now=0.0, seconds=10 * MINUTE)
        with pytest.raises(LockError, match="held by 'ada'"):
            repo.commit(
                revised(repo.structure, "frame", revision="B"),
                author="grace",
                base=repo.head.digest,
                now=60.0,
            )

    def test_a_commit_elsewhere_in_the_product_is_not_blocked(self) -> None:
        repo = ProductRepository(press())
        repo.leases.take("frame", "ada", now=0.0, seconds=10 * MINUTE)
        entry = repo.commit(
            revised(repo.structure, "table", revision="C"),
            author="grace",
            base=repo.head.digest,
            now=60.0,
        )
        assert entry.change.modified == ("table",)

    def test_the_holder_may_commit_their_own_component(self) -> None:
        repo = ProductRepository(press())
        repo.leases.take("frame", "ada", now=0.0, seconds=10 * MINUTE)
        entry = repo.commit(
            revised(repo.structure, "frame", revision="B"),
            author="ada",
            base=repo.head.digest,
            now=60.0,
        )
        assert entry.author == "ada"

    def test_an_expired_lease_no_longer_blocks_a_commit(self) -> None:
        repo = ProductRepository(press())
        repo.leases.take("frame", "ada", now=0.0, seconds=MINUTE)
        entry = repo.commit(
            revised(repo.structure, "frame", revision="B"),
            author="grace",
            base=repo.head.digest,
            now=2 * MINUTE,
        )
        assert entry.author == "grace"

    def test_holding_a_lease_does_not_excuse_a_stale_base(self) -> None:
        """The two mechanisms compose; neither replaces the other."""
        repo = ProductRepository(press())
        base = repo.head.digest
        repo.leases.take("table", "grace", now=0.0, seconds=10 * MINUTE)
        repo.commit(revised(repo.structure, "frame", revision="B"), author="ada", base=base)
        with pytest.raises(LockError, match="the head is now"):
            repo.commit(
                revised(repo.revision(base).structure, "table", revision="C"),
                author="grace",
                base=base,
                now=60.0,
            )


class TestWhatAChangeSetSays:
    def test_it_names_added_removed_and_modified_components(self) -> None:
        before = press()
        after = merged_view(before, {"frame": Component(name="frame", design="new")})
        change = changes_between(before, after)
        assert change.modified == ("frame",)
        assert change.added == ()
        assert change.removed == ()
        assert change.touched == ("frame",)

    def test_a_component_rebuilt_identically_is_not_a_change(self) -> None:
        """Compared by value, so two authors adding the same bolt do not conflict."""
        before = press()
        same = Component(
            name="frame",
            design=before.component("frame").design,
            material=before.component("frame").material,
        )
        assert changes_between(before, merged_view(before, {"frame": same})).is_empty

    def test_an_added_component_is_reported_as_added(self) -> None:
        before = press()
        after = ProductStructure(
            root="press",
            components=[
                *[before.component(n) for n in before.component_names()],
                Component(name="ram", design="ram-slide"),
            ],
        )
        assert changes_between(before, after).added == ("ram",)

    def test_it_describes_itself_for_a_refusal_to_print(self) -> None:
        change = Change(added=("ram",), modified=("frame",))
        assert change.describe() == "added ram; changed frame"
        assert Change().describe() == "changed nothing"


class TestTwoAuthorsOnDifferentComponentsMerge:
    def test_disjoint_edits_both_survive(self) -> None:
        repo = ProductRepository(press())
        base = repo.head.digest
        ours = revised(repo.structure, "frame", revision="B")
        theirs = revised(repo.structure, "table", revision="C")
        merged = repo.merge(base, ours, theirs, our_author="ada", their_author="grace")
        assert merged.component("frame").revision == "B"
        assert merged.component("table").revision == "C"

    def test_the_merge_is_a_product_that_can_be_committed(self) -> None:
        repo = ProductRepository(press())
        base = repo.head.digest
        first = repo.commit(
            revised(repo.structure, "frame", revision="B"), author="ada", base=base
        )
        theirs = revised(repo.revision(base).structure, "table", revision="C")
        merged = repo.merge(base, repo.structure, theirs, our_author="ada", their_author="grace")
        entry = repo.commit(merged, author="grace", base=first.digest, note="merge")
        assert entry.change.modified == ("table",)
        assert repo.structure.component("frame").revision == "B"
        assert repo.structure.component("table").revision == "C"

    def test_an_addition_on_one_side_survives_a_change_on_the_other(self) -> None:
        repo = ProductRepository(press())
        base = repo.head.digest
        ours = ProductStructure(
            root="press",
            components=[
                *[repo.structure.component(n) for n in repo.structure.component_names()],
                Component(name="ram", design="ram-slide"),
            ],
        )
        theirs = revised(repo.structure, "table", revision="C")
        merged = repo.merge(base, ours, theirs)
        assert "ram" in merged
        assert merged.component("table").revision == "C"


class TestTwoAuthorsOnOneComponentAreRefusedByName:
    def test_the_same_component_changed_two_ways_is_a_conflict(self) -> None:
        repo = ProductRepository(press())
        base = repo.head.digest
        ours = revised(repo.structure, "frame", revision="B")
        theirs = revised(repo.structure, "frame", revision="C")
        with pytest.raises(MergeConflict) as raised:
            repo.merge(base, ours, theirs, our_author="ada", their_author="grace")
        assert "'ada'" in str(raised.value)
        assert "'grace'" in str(raised.value)
        assert "frame" in str(raised.value)

    def test_the_same_component_changed_the_same_way_is_not_a_conflict(self) -> None:
        repo = ProductRepository(press())
        base = repo.head.digest
        ours = revised(repo.structure, "frame", revision="B")
        theirs = revised(repo.structure, "frame", revision="B")
        merged = repo.merge(base, ours, theirs)
        assert merged.component("frame").revision == "B"

    def test_two_different_roots_are_refused(self) -> None:
        repo = ProductRepository(press())
        base = repo.head.digest
        ours = ProductStructure(
            root="frame", components=[repo.structure.component(n) for n in ("press", "frame", "table")]
        )
        theirs = ProductStructure(
            root="table", components=[repo.structure.component(n) for n in ("press", "frame", "table")]
        )
        with pytest.raises(MergeConflict, match="has one root"):
            repo.merge(base, ours, theirs)


class TestAWorkspaceIsOneAuthorsSeat:
    def test_it_commits_against_the_base_it_opened_at(self) -> None:
        repo = ProductRepository(press())
        ada = Workspace.open(repo, "ada")
        entry = ada.commit(revised(ada.structure, "frame", revision="B"))
        assert entry.author == "ada"
        assert ada.base == entry.digest

    def test_a_workspace_opened_before_someone_elses_commit_is_refused(self) -> None:
        repo = ProductRepository(press())
        ada = Workspace.open(repo, "ada")
        grace = Workspace.open(repo, "grace")
        ada.commit(revised(ada.structure, "frame", revision="B"))
        with pytest.raises(LockError, match="the head is now"):
            grace.commit(revised(grace.structure, "table", revision="C"))

    def test_refreshing_moves_it_onto_the_head(self) -> None:
        repo = ProductRepository(press())
        ada = Workspace.open(repo, "ada")
        grace = Workspace.open(repo, "grace")
        ada.commit(revised(ada.structure, "frame", revision="B"))
        grace.refresh()
        entry = grace.commit(revised(grace.structure, "table", revision="C"))
        assert entry.number == 3

    def test_claiming_takes_leases_and_committing_gives_them_back(self) -> None:
        repo = ProductRepository(press())
        ada = Workspace.open(repo, "ada").claim("frame", now=0.0, seconds=10 * MINUTE)
        assert repo.leases.holder_of("frame", now=1.0) == "ada"
        ada.commit(revised(ada.structure, "frame", revision="B"), now=1.0)
        assert repo.leases.holder_of("frame", now=2.0) is None

    def test_a_refused_commit_leaves_the_author_still_holding_their_lease(self) -> None:
        """Otherwise fixing the work means racing whoever took the lease meanwhile."""
        repo = ProductRepository(press())
        ada = Workspace.open(repo, "ada").claim("frame", now=0.0, seconds=10 * MINUTE)
        grace = Workspace.open(repo, "grace")
        grace.commit(revised(grace.structure, "table", revision="C"))
        with pytest.raises(LockError):
            ada.commit(revised(ada.structure, "frame", revision="B"), now=1.0)
        assert repo.leases.holder_of("frame", now=2.0) == "ada"

    def test_two_workspaces_cannot_claim_the_same_component(self) -> None:
        repo = ProductRepository(press())
        Workspace.open(repo, "ada").claim("frame", now=0.0, seconds=MINUTE)
        with pytest.raises(LockError, match="cannot take 'frame'"):
            Workspace.open(repo, "grace").claim("frame", now=1.0, seconds=MINUTE)


class TestTheHistoryIsACheckableChain:
    def test_every_revision_names_the_one_it_was_written_against(self) -> None:
        repo = ProductRepository(press())
        first = repo.commit(
            revised(repo.structure, "frame", revision="B"), author="ada", base=repo.head.digest
        )
        second = repo.commit(
            revised(repo.structure, "table", revision="C"), author="grace", base=first.digest
        )
        history = repo.history()
        assert [entry.number for entry in history] == [1, 2, 3]
        assert history[0].parent is None
        assert history[1].parent == history[0].digest
        assert history[2].parent == second.parent == history[1].digest

    def test_a_revision_can_be_fetched_by_digest_and_an_unknown_one_refused(self) -> None:
        repo = ProductRepository(press())
        assert repo.revision(repo.head.digest).number == 1
        with pytest.raises(LockError, match="No revision"):
            repo.revision("nope")

    def test_the_report_says_where_the_product_is_and_who_is_holding_what(self) -> None:
        repo = ProductRepository(press())
        assert "Nothing is leased" in repo.report(now=0.0)
        repo.leases.take("frame", "ada", now=0.0, seconds=MINUTE)
        line = repo.report(now=0.0)
        assert "frame by ada" in line
        assert "revision 1" in line


class TestReplacingAComponentIsNotAWayToAddOne:
    def test_an_unknown_component_is_refused(self) -> None:
        with pytest.raises(LockError, match="nothing to"):
            merged_view(press(), {"rma": Component(name="rma")})

    def test_the_replacement_keeps_the_rest_of_the_product(self) -> None:
        after = merged_view(press(), {"table": Component(name="table", design="thicker")})
        assert after.component_names() == ("frame", "press", "table")
        assert after.occurrence_count() == 2
