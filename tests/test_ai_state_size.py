"""The state block is sent on every step, so what it weighs and how often it changes are costs.

ROAD_TO_10 1.9. Two measurements, both on the real builder:

* **Size.** A realistic mid-design CATIA conversation, and the worst case each part of
  the block can reach. A cap on the first catches the block growing; a ceiling on the
  second catches a component that has no bound at all.
* **Churn.** The block sits before the newest user message, so everything after it -- every
  tool exchange of the turn in progress -- is re-billed at the full price on any step where
  the block's text moved. How often it moves is therefore a cost, and the test counts it.

The block is built against the database (it reads the project, runs, documents, the
operation log and the design), so this file needs the schema.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai import resume
from app.ai.state import build_state_block
from app.ai.tokens import estimate
from app.core import designs
from app.core.security import hash_password
from app.design.spec import DesignSpec, FeatureSpec, Parameter, Unit
from app.models import (
    Conversation,
    ConversationMessage,
    GeometryVersion,
    JobStatus,
    Media,
    MediaKind,
    MessageRole,
    Project,
    SimulationJob,
    User,
)
from app.models.catia import CatiaDocument, CatiaOperation


@pytest.fixture(autouse=True)
def _seat_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deterministic seat: connected, no interface language. The block's biggest prose
    section is the CATIA one, so the measurement is of the case that matters."""
    monkeypatch.setattr("app.ai.state._catia_available", lambda *_: True)
    monkeypatch.setattr("app.ai.state._catia_ui_language", lambda *_: None)


@pytest.fixture
def user(db_session: Session) -> User:
    account = User(email="state@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
    db_session.add(account)
    db_session.flush()
    return account


def _spec(parameters: int, *, features: int = 6) -> DesignSpec:
    return DesignSpec.of(
        "Mounting plate",
        material="steel-s355",
        parameters=[
            Parameter(f"dimension_{index:02d}_mm", Unit.MM, value=10.0 + index)
            for index in range(parameters)
        ],
        features=[
            FeatureSpec(f"f{index}", "catia_sketch_create", {"support": "XY"})
            for index in range(features)
        ],
    )


def build_session(
    db: Session,
    user: User,
    *,
    catia_features: int = 14,
    catia_parameters: int = 10,
    design_parameters: int = 12,
    documents: int = 1,
    plan_tasks: int = 6,
    failures: int = 2,
    operations: int = 40,
    requirements: int = 5,
    text: int = 24,
    active_document: int | None = None,
) -> Conversation:
    """A conversation as far into a design as the arguments say."""
    # Column widths are 255; names are as long as the arguments say, up to that.
    name = "N" * min(text, 200)
    project = Project(name=f"{name} plate", description="D" * min(text * 5, 1_000), owner_id=user.id)
    db.add(project)
    db.flush()
    media = Media(
        owner_id=user.id, kind=MediaKind.CAD, filename="m.step", size_bytes=1, sha256="0" * 64, meta={}
    )
    db.add(media)
    db.flush()
    geometry = GeometryVersion(
        project_id=project.id, media_id=media.id, version_number=3,
        filename=f"{name}.step", file_format="step",
        stats={"bounding_box": [[0.0, 0.0, 0.0], [200.0, 120.0, 6.0]]},
    )
    db.add(geometry)
    db.flush()
    db.add(
        SimulationJob(
            project_id=project.id, geometry_version_id=geometry.id, status=JobStatus.SUCCEEDED,
            solver="internal", load_case={}, result={"factor_of_safety": 2.31, "max_von_mises_mpa": 118.4},
        )
    )
    conversation = Conversation(owner_id=user.id, project_id=project.id, title="t")
    db.add(conversation)
    db.flush()
    sentence = " ".join(
        f"Make it {120 + index} mm wide and carry {index + 1}.5 kN with a factor of safety of {index + 2}."
        for index in range(requirements)
    )
    db.add(ConversationMessage(conversation_id=conversation.id, sequence=0, role=MessageRole.USER, content=sentence))
    conversation.catia_state = {
        "material": "steel-s355", "density_kg_m3": 7850,
        "features": [f"{name[:20]}.{index}" for index in range(catia_features)],
        "parameters": {f"param_{index}": f"{10 + index}mm" for index in range(catia_parameters)},
        "mass_kg": 0.42, "volume_mm3": 53500.0, "bounding_box_mm": [200, 120, 6],
    }
    for index in range(documents):
        db.add(
            CatiaDocument(
                conversation_id=conversation.id,
                is_active=index == (documents - 1 if active_document is None else active_document),
                doc_type="part", doc_name=f"{name}_{index}.CATPart",
            )
        )
    for index in range(operations):
        failed = index < failures
        db.add(
            CatiaOperation(
                conversation_id=conversation.id, user_id=user.id, tool=f"catia_tool_{index}",
                tier="write", arguments={}, ok=not failed,
                error=("E" * 160) if failed else None,
            )
        )
    db.flush()
    designs.save(db, conversation, _spec(design_parameters))
    if plan_tasks:
        conversation.task_graph = {
            "format_version": 1,
            "tasks": [
                {
                    "id": f"t{index}", "title": f"{'T' * text} step {index}",
                    "state": "pending", "note": "n" * text,
                    **({"depends_on": [f"t{index - 1}"]} if index else {}),
                }
                for index in range(plan_tasks)
            ],
        }
    db.flush()
    db.refresh(conversation)
    return conversation


def block(db: Session, user: User, **kwargs: Any) -> str:
    return build_state_block(db, user, build_session(db, user, **kwargs))


#: Measured 2026-10-04 on `build_session()`: 4,663 characters, ~1,300 tokens, against ~66,000
#: for the tool registry. The cap is that plus a third: it exists to notice the block
#: *growing* (a new line, a longer sentence of advice), and a figure with no headroom would be
#: raised by whoever hit it without reading why. Raise it deliberately, in the commit that
#: adds the line, and say what the line is worth.
REALISTIC_CAP_CHARS = 6_000

#: Every part of the block at its largest at once. Not a realistic conversation: the
#: ceiling that proves each part has a bound at all (before 2026-10-04 the documents, the
#: design's parameters and the plan's titles and notes had none).
WORST_CASE_CAP_CHARS = 32_000


class TestHowMuchItWeighs:
    def test_a_realistic_mid_design_conversation_stays_under_its_cap(
        self, db_session: Session, user: User
    ) -> None:
        size = len(block(db_session, user))
        assert size <= REALISTIC_CAP_CHARS, (
            f"the state block for a realistic conversation is {size:,} characters "
            f"(~{estimate(size):,} tokens), over the {REALISTIC_CAP_CHARS:,} cap. It is resent on "
            "every step: if the growth is worth it, raise the cap in the same change and say why."
        )

    def test_the_cap_is_not_so_loose_that_it_stops_meaning_anything(
        self, db_session: Session, user: User
    ) -> None:
        # A cap three times the measurement would let the block triple unnoticed.
        assert len(block(db_session, user)) > REALISTIC_CAP_CHARS * 0.6

    def test_every_part_at_its_largest_at_once_is_still_bounded(
        self, db_session: Session, user: User
    ) -> None:
        size = len(
            block(
                db_session, user, catia_features=100, catia_parameters=100, design_parameters=300,
                documents=200, plan_tasks=40, failures=40, operations=120, requirements=30, text=300,
            )
        )
        assert size <= WORST_CASE_CAP_CHARS

    def test_a_machine_with_many_parts_names_them_and_counts_the_rest(
        self, db_session: Session, user: User
    ) -> None:
        text = block(db_session, user, documents=80)
        assert "owns 80 documents" in text
        assert "and 50 more" in text
        assert text.count(".CATPart (part") == 30

    def test_the_active_document_is_named_even_when_it_is_the_oldest_of_many(
        self, db_session: Session, user: User
    ) -> None:
        text = block(db_session, user, documents=80, active_document=0)
        assert "_0.CATPart (part, active)" in text

    def test_a_design_with_many_parameters_names_sixty_and_counts_the_rest(
        self, db_session: Session, user: User
    ) -> None:
        text = block(db_session, user, design_parameters=100)
        assert "dimension_59_mm" in text and "dimension_60_mm" not in text
        assert "and 40 more" in text

    def test_a_plan_line_is_clipped_with_a_mark_never_cut_silently(
        self, db_session: Session, user: User
    ) -> None:
        text = block(db_session, user, plan_tasks=3, text=300)
        assert "\u2026" in text
        assert "n" * 200 not in text

    def test_clipping_the_rendering_does_not_clip_the_stored_plan(
        self, db_session: Session, user: User
    ) -> None:
        conversation = build_session(db_session, user, plan_tasks=2, text=300)
        build_state_block(db_session, user, conversation)
        assert conversation.task_graph["tasks"][0]["note"] == "n" * 300


class TestHowOftenItMoves:
    """What the block says changes the prompt from the block onward, and the block sits before
    the newest user message -- so every tool exchange of the turn in progress is re-billed at
    the full price on a step where it moved. Measured on the real replay: a block that changes
    every step makes the turn cost 5.7-6.4x what a stable one does (90 % cache discount)."""

    def test_two_builds_with_nothing_changed_are_byte_identical(
        self, db_session: Session, user: User
    ) -> None:
        conversation = build_session(db_session, user)
        assert build_state_block(db_session, user, conversation) == build_state_block(
            db_session, user, conversation
        )

    @staticmethod
    def _one_more_operation(db: Session, conversation: Conversation, user: User, index: int) -> None:
        db.add(
            CatiaOperation(
                conversation_id=conversation.id, user_id=user.id, tool=f"catia_extra_{index}",
                tier="write", arguments={}, ok=True,
            )
        )
        db.flush()

    def test_a_forty_step_catia_turn_changes_the_block_a_handful_of_times_not_forty(
        self, db_session: Session, user: User
    ) -> None:
        conversation = build_session(db_session, user, operations=0, failures=0)
        seen: list[str] = []
        for index in range(40):
            self._one_more_operation(db_session, conversation, user, index)
            seen.append(build_state_block(db_session, user, conversation))
        changes = sum(1 for before, after in zip(seen, seen[1:]) if before != after)
        assert changes <= 6, f"the block changed {changes} times in 40 steps"

    def test_the_block_holds_still_across_a_whole_band_of_operations(
        self, db_session: Session, user: User
    ) -> None:
        conversation = build_session(db_session, user, operations=10, failures=0)
        at_ten = build_state_block(db_session, user, conversation)
        for index in range(14):  # 11 .. 24
            self._one_more_operation(db_session, conversation, user, index)
        assert build_state_block(db_session, user, conversation) == at_ten
        self._one_more_operation(db_session, conversation, user, 99)  # 25: the next band
        assert build_state_block(db_session, user, conversation) != at_ten

    def test_a_handful_of_operations_are_still_counted_exactly(
        self, db_session: Session, user: User
    ) -> None:
        assert "2 operation(s)" in block(db_session, user, operations=2, failures=0)

    def test_what_matters_still_moves_it(self, db_session: Session, user: User) -> None:
        """Holding still must not mean going stale: a measurement, a failure that is not
        fixed, and a design revision are exactly what the block exists to carry."""
        conversation = build_session(db_session, user, operations=12, failures=0)
        before = build_state_block(db_session, user, conversation)

        conversation.catia_state = {**(conversation.catia_state or {}), "mass_kg": 9.99}
        assert build_state_block(db_session, user, conversation) != before

        conversation.catia_state = {**(conversation.catia_state or {}), "mass_kg": 0.42}
        assert build_state_block(db_session, user, conversation) == before
        db_session.add(
            CatiaOperation(
                conversation_id=conversation.id, user_id=user.id, tool="catia_pad", tier="write",
                arguments={}, ok=False, error="The sketch is not closed.",
            )
        )
        db_session.flush()
        assert "The sketch is not closed." in build_state_block(db_session, user, conversation)

    @pytest.mark.parametrize(
        ("count", "expected"),
        [(0, "0 operation(s)"), (3, "3 operation(s)"), (4, "between 4 and 9 operations"),
         (9, "between 4 and 9 operations"), (10, "between 10 and 24 operations"),
         (24, "between 10 and 24 operations"), (25, "between 25 and 49 operations"),
         (99, "between 50 and 99 operations"), (249, "between 100 and 249 operations"),
         (499, "between 250 and 499 operations"), (500, "500 or more operations"),
         (5_000, "500 or more operations")],
    )
    def test_the_bands(self, count: int, expected: str) -> None:
        assert resume._how_many(count) == expected


class TestTheTurnInProgressStaysCached:
    """The claim the banding exists for, on the real builder rather than on the block alone:
    build the whole prompt at every step of a CATIA turn and measure how much of the previous
    request is still the front of the next one. Only that front is billed at the cache price."""

    STEPS = 30

    def _walk(self, db: Session, user: User) -> list[float]:
        import json
        import os

        from app.ai.context import build_messages

        conversation = build_session(db, user)  # 40 operations: the 25-49 band
        previous = ""
        kept: list[float] = []
        sequence = 1
        for step in range(self.STEPS):
            call = f"call-{step}"
            db.add(
                ConversationMessage(
                    conversation_id=conversation.id, sequence=sequence, role=MessageRole.ASSISTANT,
                    content="", tool_calls=[{"id": call, "type": "function",
                                             "function": {"name": "catia_pad", "arguments": {"n": step}}}],
                )
            )
            db.add(
                ConversationMessage(
                    conversation_id=conversation.id, sequence=sequence + 1, role=MessageRole.TOOL,
                    content="r" * 3_000, tool_call_id=call, tool_name="catia_pad",
                )
            )
            # The dispatcher logs every operation it runs; that is what moves the count.
            db.add(
                CatiaOperation(
                    conversation_id=conversation.id, user_id=user.id, tool="catia_pad",
                    tier="write", arguments={}, ok=True,
                )
            )
            sequence += 2
            db.flush()
            db.expire(conversation, ["messages"])
            text = json.dumps(build_messages(db, user, conversation), sort_keys=True)
            if previous:
                kept.append(len(os.path.commonprefix([previous, text])) / len(previous))
            previous = text
        return kept

    def test_almost_every_step_of_a_catia_turn_keeps_the_front_of_the_last_request(
        self, db_session: Session, user: User
    ) -> None:
        kept = self._walk(db_session, user)
        broken = [fraction for fraction in kept if fraction < 0.85]
        # Thirty operations cross one band edge (49 -> 50). An exact count broke every one.
        assert len(broken) <= 3, f"{len(broken)} of {len(kept)} steps rebuilt the prompt: {kept}"

    def test_an_exact_count_would_have_broken_nearly_all_of_them(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The measurement the banding is justified by: the same walk with the count exact."""
        monkeypatch.setattr(resume, "_how_many", lambda count: f"{count} operation(s)")
        kept = self._walk(db_session, user)
        assert len([fraction for fraction in kept if fraction < 0.85]) >= 20
