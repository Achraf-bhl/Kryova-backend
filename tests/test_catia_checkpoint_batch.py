"""One checkpoint per batch, not one per edit (ROAD_TO_10 5.4, E15 task 1).

Every mutating call paid a COM save and an upload first, so a twenty-feature build made twenty
snapshots and rolled back one feature at a time. `checkpoint_batch` snapshots a run of edits once
at the start and once at the end; rollback granularity becomes the batch, which is also the unit
the user approved.

Driven through `call_catia` against the scripted device (CLAUDE.md *Testing* 8: the dispatcher's
private helpers are not the path the agent is offered). The counts are *calls the seat received*,
which is the cost the change exists to remove.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.catia import dispatch
from app.catia.approval import mint_approval
from app.catia.connection import BridgeCallFailed
from app.catia.dispatch import CatiaError
from app.catia.ops import registry
from app.models.catia import CatiaCheckpoint, CatiaDocument, CatiaOperation
from tests.test_catia_dispatch import run, wired  # noqa: F401 - fixture re-export

PAD = {"sketch": "Sketch.1", "length_mm": 10}


def _batch(wired, **kwargs):  # noqa: F811
    return dispatch.checkpoint_batch(
        wired["db"],
        user_id=wired["user_id"],
        conversation_id=wired["conversation"].id,
        label=kwargs.pop("label", "build"),
        **kwargs,
    )


def _count(wired, tool: str) -> int:  # noqa: F811
    return wired["connection"].tools_called.count(tool)


@pytest.fixture
def part(wired):  # noqa: F811
    """A conversation that already owns a part, with the new-part call forgotten."""
    run(wired, "catia_new_part", {"name": "Bracket"})
    wired["connection"].calls.clear()
    return wired


class TestFiveEditsMakeTwoCheckpoints:
    def test_the_baseline_is_one_checkpoint_per_edit(self, part) -> None:
        # What the batch exists to remove, pinned so the saving is a measurement and not a
        # claim about a number that may already have been small.
        for _ in range(5):
            run(part, "catia_pad", PAD)

        assert _count(part, "catia_checkpoint") == 5

    def test_a_five_step_batch_takes_one_at_the_start_and_one_at_the_end(self, part) -> None:
        with _batch(part) as batch:
            for _ in range(5):
                run(part, "catia_pad", PAD)

        assert _count(part, "catia_checkpoint") == 2
        assert part["connection"].tools_called == (
            ["catia_checkpoint"] + ["catia_pad"] * 5 + ["catia_checkpoint"]
        )
        assert batch.steps == 5 and not batch.rolled_back
        labels = [
            row.label for row in part["db"].scalars(select(CatiaCheckpoint).order_by(CatiaCheckpoint.created_at))
        ]
        assert labels[-2:] == ["before build", "after build"]

    def test_the_latest_checkpoint_is_the_end_of_the_batch(self, part) -> None:
        with _batch(part):
            run(part, "catia_pad", PAD)

        document = part["db"].scalar(
            select(CatiaDocument).where(CatiaDocument.conversation_id == part["conversation"].id)
        )
        latest = part["db"].get(CatiaCheckpoint, document.latest_checkpoint_id)
        assert latest.label == "after build"

    def test_a_batch_that_starts_with_a_new_part_snapshots_once_there_is_something_to_save(
        self, wired
    ) -> None:
        # `catia_new_part` has nothing to snapshot, so the start checkpoint is lazy: it is
        # taken before the first edit, not before a part exists.
        with _batch(wired):
            run(wired, "catia_new_part", {"name": "Bracket"})
            run(wired, "catia_pad", PAD)
            run(wired, "catia_pad", PAD)

        assert wired["connection"].tools_called == [
            "catia_new_part",
            "catia_checkpoint",
            "catia_pad",
            "catia_pad",
            "catia_checkpoint",
        ]

    def test_a_read_inside_a_batch_is_not_a_step(self, part) -> None:
        with _batch(part) as batch:
            run(part, "catia_measure", {})

        assert batch.steps == 0
        assert _count(part, "catia_checkpoint") == 0  # nothing changed, so nothing to bracket

    def test_a_batch_inside_a_batch_joins_the_outer_one(self, part) -> None:
        with _batch(part, label="outer"):
            run(part, "catia_pad", PAD)
            with _batch(part, label="inner"):
                run(part, "catia_pad", PAD)

        assert _count(part, "catia_checkpoint") == 2


class TestAFailureInTheMiddleRestoresTheStart:
    def _fail_third(self, part, **kwargs):
        connection = part["connection"]
        with _batch(part, **kwargs) as batch:
            for index in range(5):
                if index == 2:
                    connection.raises = BridgeCallFailed("the seat refused the third pad")
                try:
                    run(part, "catia_pad", PAD)
                except CatiaError:
                    # The seat is fine again by the time the batch cleans up: the double only
                    # raises for tools other than `catia_checkpoint`, restore included.
                    connection.raises = None
                    raise
        return batch  # pragma: no cover - the block above always raises

    def test_the_start_checkpoint_is_restored_and_nothing_after_it_runs(self, part) -> None:
        with pytest.raises(CatiaError):
            self._fail_third(part)

        assert part["connection"].tools_called == [
            "catia_checkpoint",
            "catia_pad",
            "catia_pad",
            "catia_pad",  # the failing one
            "catia_restore",
        ]
        start = part["db"].scalar(
            select(CatiaCheckpoint).where(CatiaCheckpoint.label == "before build")
        )
        restore = next(c for c in part["connection"].calls if c["tool"] == "catia_restore")
        assert restore["arguments"]["checkpoint"]["checkpoint_id"] == start.id

    def test_the_restore_is_logged_like_any_other(self, part) -> None:
        with pytest.raises(CatiaError):
            self._fail_third(part)

        logged = [
            row
            for row in part["db"].scalars(select(CatiaOperation).order_by(CatiaOperation.created_at))
            if row.tool == "catia_restore"
        ]
        assert len(logged) == 1 and logged[0].ok is True
        assert "build failed" in str(logged[0].arguments)

    def test_without_rollback_the_end_checkpoint_records_what_was_built(self, part) -> None:
        # `build_design` keeps a failed build's features so the agent carries on from there.
        with pytest.raises(CatiaError):
            self._fail_third(part, rollback_on_failure=False)

        assert "catia_restore" not in part["connection"].tools_called
        assert part["connection"].tools_called[-1] == "catia_checkpoint"
        assert _count(part, "catia_checkpoint") == 2

    def test_an_end_checkpoint_that_cannot_be_taken_does_not_undo_the_work(
        self, part, monkeypatch
    ) -> None:
        original = dispatch._auto_checkpoint

        def failing_at_the_end(*args, label: str, **kwargs):
            if label.startswith("after"):
                raise CatiaError("the snapshot failed")
            return original(*args, label=label, **kwargs)

        monkeypatch.setattr(dispatch, "_auto_checkpoint", failing_at_the_end)

        with _batch(part) as batch:  # must not raise
            run(part, "catia_pad", PAD)

        assert batch.steps == 1 and batch.ends == {}

    def test_a_start_checkpoint_that_fails_still_refuses_the_first_edit(
        self, part, monkeypatch
    ) -> None:
        def failing(*args, **kwargs):
            raise CatiaError("the snapshot failed")

        monkeypatch.setattr(dispatch, "_auto_checkpoint", failing)

        with pytest.raises(CatiaError, match="snapshot failed"):
            with _batch(part):
                run(part, "catia_pad", PAD)

        assert "catia_pad" not in part["connection"].tools_called


class TestEveryDocumentTheBatchChangesIsSnapshotted:
    def test_a_second_part_started_inside_the_batch_is_not_left_unprotected(self, part) -> None:
        part["connection"].replies["catia_new_part"] = {
            "doc_name": "Housing",
            "remote_path": "C:\\work\\Housing.CATPart",
            "features": [],
        }
        with _batch(part) as batch:
            run(part, "catia_pad", PAD)
            run(part, "catia_new_part", {"name": "Housing"})
            run(part, "catia_pad", PAD)

        assert len(batch.starts) == 2
        assert _count(part, "catia_checkpoint") == 4  # a start and an end for each part


class TestOneListSaysWhatIsNotCheckpointed:
    def test_dispatch_reads_the_registry_and_keeps_no_list_of_its_own(self) -> None:
        # Two statements of one fact had already disagreed: `catia_restore` was flagged in
        # the registry and checkpointed anyway.
        assert dispatch._NO_AUTO_CHECKPOINT == registry.no_auto_checkpoint_names()

    def test_the_tools_that_cannot_wait_for_a_save_are_exempt(self) -> None:
        # The interactive family runs when a dialog has COM blocked; a checkpoint is a COM save
        # and a failed one refuses the call.
        assert {
            "catia_fill_dialog",
            "catia_dialog_action",
            "catia_press_key",
            "catia_close_document",
            "catia_checkpoint",
            "catia_new_part",
        } <= dispatch._NO_AUTO_CHECKPOINT

    def test_a_restore_is_not_snapshotted_first(self, part) -> None:
        # Behaviour change 2026-10-05: it was flagged and checkpointed anyway. A recovery that
        # is refused because the document is too broken to snapshot is no recovery.
        checkpoint = part["db"].scalar(select(CatiaCheckpoint))
        if checkpoint is None:
            run(part, "catia_pad", PAD)
            checkpoint = part["db"].scalar(select(CatiaCheckpoint))
        part["connection"].calls.clear()
        token = mint_approval(
            user_id=part["user_id"],
            tool="catia_restore",
            conversation_id=part["conversation"].id,
            target=checkpoint.id,
        )

        run(part, "catia_restore", {"checkpoint_id": checkpoint.id, "approval_token": token})

        assert part["connection"].tools_called == ["catia_restore"]
