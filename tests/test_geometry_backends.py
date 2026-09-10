"""The geometry backend seam — Decision 1 made true of the product.

Until this existed, `dispatch.call_catia` went straight to the bridge and
`OcctRunner` was constructed only by tests: 108 working kernel operations needed a
CATIA licence to reach, and the agent could not build a box without a seat. These
tests pin the seam and, more importantly, pin the things it must never do quietly.

Offline: the OCCT half needs the kernel (skipped without it), and the selection,
session and honesty tests need nothing at all.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.core.config import settings
from app.geometry import backends


@pytest.fixture(autouse=True)
def _clean_sessions() -> Any:
    """Sessions are process-global by necessity; a test must not inherit one."""
    for key in list(backends._sessions):
        backends.forget(key)
    yield
    for key in list(backends._sessions):
        backends.forget(key)


@pytest.fixture
def occt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "geometry_backend", "occt")


def _kernel_available() -> bool:
    try:
        from app.kernel.occt.binding import require

        require()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_kernel = pytest.mark.skipif(not _kernel_available(), reason="OCCT not installed")


class TestChoosingABackend:
    def test_the_default_is_catia_so_an_existing_deployment_is_unchanged(self) -> None:
        assert "catia" in backends.BACKENDS
        assert backends.selected_backend() in backends.BACKENDS

    def test_occt_is_selected_by_the_setting(self, occt: None) -> None:
        assert backends.selected_backend() == "occt"
        assert backends.is_local() is True

    def test_an_unknown_backend_falls_back_to_catia_rather_than_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo in an env var must not take the whole geometry surface down."""
        monkeypatch.setattr(settings, "geometry_backend", "opencascade")
        assert backends.selected_backend() == "catia"

    def test_the_choice_is_never_automatic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A silent fallback would hand the user a part built by a different kernel.

        There is deliberately no "use OCCT when no seat answers" path: Decision 3
        binds a result to what produced it, and a backend that switched itself
        would make that unknowable from the outside. This test exists to fail if
        someone adds the convenience later.
        """
        monkeypatch.setattr(settings, "geometry_backend", "catia")
        assert backends.is_local() is False


class TestSessionsAreOnePartEach:
    def test_a_conversation_keeps_one_document_across_calls(self, occt: None) -> None:
        """OCAF labels must persist between calls or feature#selector cannot work."""
        first = backends.session_for("conversation-a")
        assert backends.session_for("conversation-a") is first

    def test_two_conversations_do_not_share_a_part(self, occt: None) -> None:
        assert backends.session_for("a") is not backends.session_for("b")

    def test_peeking_does_not_create_a_document(self, occt: None) -> None:
        """A status poll must not fill the table with empty parts."""
        assert backends.peek_session("never-touched") is None
        assert backends.session_count() == 0

    def test_the_oldest_document_is_evicted_and_the_eviction_is_remembered(
        self, occt: None
    ) -> None:
        """Silence here lets the agent add a pocket to an empty part and report success."""
        for index in range(backends.MAX_SESSIONS + 1):
            backends.session_for(f"conversation-{index}")
        assert backends.session_count() == backends.MAX_SESSIONS
        assert backends.was_evicted("conversation-0") is True
        assert backends.was_evicted("conversation-1") is False

    def test_using_a_conversation_keeps_it_from_being_evicted(self, occt: None) -> None:
        backends.session_for("keep-me")
        for index in range(backends.MAX_SESSIONS):
            backends.session_for(f"filler-{index}")
            backends.session_for("keep-me")  # touching it moves it to the end
        assert backends.was_evicted("keep-me") is False

    def test_an_eviction_can_be_acknowledged_once(self, occt: None) -> None:
        for index in range(backends.MAX_SESSIONS + 1):
            backends.session_for(f"c{index}")
        assert backends.was_evicted("c0") is True
        backends.clear_eviction("c0")
        assert backends.was_evicted("c0") is False

    def test_forget_drops_both_the_session_and_its_eviction(self, occt: None) -> None:
        backends.session_for("gone")
        backends.forget("gone")
        assert backends.peek_session("gone") is None
        assert backends.was_evicted("gone") is False


class TestCoverageIsReadNotDeclared:
    @needs_kernel
    def test_the_offered_tools_come_from_the_handler_table(self) -> None:
        """A declared number drifts from the code; a read one cannot."""
        names = backends.local_tool_names()
        assert names
        assert "catia_pad" in names
        coverage = backends.local_coverage()
        assert coverage["implemented"] == len(names) - len(backends.LOCALLY_SERVED)
        assert coverage["declared"] > coverage["implemented"]

    def test_what_is_offered_and_what_the_kernel_implements_are_different_questions(
        self,
    ) -> None:
        """`catia_export_step` is offered on `occt` and is not a geometry operation.

        The seat does it by asking the bridge to save a file; this process does
        it by writing one. Either way OCCT implements nothing for it, so it must
        not reach the coverage number — that number is read by `catia_status` and
        by the cross-backend conformance harness, and a harness that saw an
        export in the handler table would try to build a part with it.
        """
        assert "catia_export_step" in backends.LOCALLY_SERVED
        assert "catia_export_step" in backends.local_tool_names()

        from app.kernel.occt.operations import HANDLERS

        assert "catia_export_step" not in HANDLERS
        assert not (backends.LOCALLY_SERVED & set(HANDLERS))

    @needs_kernel
    def test_the_kernel_version_is_reported_for_provenance(self) -> None:
        assert "OCCT" in backends.backend_version()

    def test_a_missing_kernel_is_a_state_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Everything must import on a machine with no OCCT — app/kernel's own contract."""
        import builtins

        real = builtins.__import__

        def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("app.kernel"):
                raise ModuleNotFoundError(name)
            return real(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", refuse)
        assert backends.local_tool_names() == frozenset()
        assert backends.local_coverage() == {}
        assert "unavailable" in backends.backend_version()


@needs_kernel
class TestTheAgentCanBuildWithoutASeat:
    """The whole point: 60x40x20 with no CATIA, no licence and no Windows."""

    def test_a_part_builds_and_measures_to_the_closed_form_answer(self, occt: None) -> None:
        runner = backends.session_for("build")
        runner("catia_new_part", {"name": "Bracket"})
        runner("catia_sketch_create", {"support": "XY", "name": "profile"})
        runner("catia_sketch_rectangle", {"sketch": "profile", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"name": "slab", "sketch": "profile", "length_mm": 20.0})

        assert runner("catia_list_faces", {})["count"] == 6
        assert runner("catia_measure", {})["volume_mm3"] == pytest.approx(48_000.0)

    def test_an_unimplemented_operation_says_so_rather_than_failing_the_part(
        self, occt: None
    ) -> None:
        """'Not implemented here' and 'your geometry is wrong' need different answers.

        An agent told the second will try to repair a part that is fine.
        """
        from app.kernel.errors import OperationNotSupported

        runner = backends.session_for("build")
        runner("catia_new_part", {"name": "Bracket"})
        with pytest.raises(OperationNotSupported):
            runner("catia_measure_part", {})  # a declared tool this backend lacks


def _missing_tool() -> str:
    """A declared operation this backend does not implement, found rather than named.

    Hard-coding one would rot the moment it is implemented, and the test would
    then pass for the wrong reason -- it would be asserting about a tool that
    works.
    """
    from app.catia.ops.registry import OPERATIONS_BY_NAME
    from app.geometry import backends

    handled = backends.local_tool_names()
    for name in sorted(OPERATIONS_BY_NAME):
        if name not in handled:
            return name
    raise AssertionError("every declared operation is implemented; pick another probe")


class TestARefusalMustNotDenyATheToolExists:
    """An unsupported *option* is not an unimplemented *tool*, and saying so cost
    ladder Level 2 two runs on 2026-09-09.

    `OperationNotSupported` is raised for both — a whole operation this backend
    lacks, and a capability within one it has — and it carries a `subject` so the
    two can be told apart. `_execute_locally` used to discard the subject and the
    reason and substitute *"<tool> is not implemented in the open kernel yet"* for
    both.

    For a capability that is a **false statement**, and the agent acts on it. It
    called `catia_pad` with `limit='up_to_surface'`, was told catia_pad is not
    implemented — having used `catia_pad` successfully two calls earlier — and
    concluded the kernel had no pad at all: it tried a shaft, tried a surface
    extrude, told the user "the pad operation isn't implemented in this kernel"
    and reached for CATIA's interface. The part was never finished. The kernel's
    own sentence names the limit and says to extrude past and cut, or to use
    `limit='up_to_plane'`.
    """

    def _refusal(self, arguments: dict[str, Any]) -> str:
        from app.catia.dispatch import CatiaError, _execute_locally
        from app.catia.ops.registry import OPERATIONS_BY_NAME

        spec = OPERATIONS_BY_NAME["catia_pad"]
        runner = backends.session_for("refusal")
        runner("catia_new_part", {"name": "Plate"})
        runner("catia_sketch_create", {"support": "XY", "name": "profile"})
        runner("catia_sketch_rectangle", {"sketch": "profile", "width_mm": 60.0, "height_mm": 40.0})
        with pytest.raises(CatiaError) as raised:
            _execute_locally(spec=spec, conversation_id="refusal", arguments=dict(arguments))
        return str(raised.value)

    def test_an_unsupported_limit_does_not_claim_the_tool_is_missing(
        self, occt: None
    ) -> None:
        message = self._refusal(
            {"sketch": "profile", "length_mm": 30.0, "limit": "up_to_surface"}
        )

        assert "is not implemented in the open kernel yet" not in message

    def test_it_names_the_option_that_was_refused(self, occt: None) -> None:
        message = self._refusal(
            {"sketch": "profile", "length_mm": 30.0, "limit": "up_to_surface"}
        )

        assert "up_to_surface" in message

    def test_it_keeps_the_reason_that_says_what_to_do_instead(self, occt: None) -> None:
        """The reason is the whole value of the refusal: without it the agent has
        been told no and given nowhere to go."""
        message = self._refusal(
            {"sketch": "profile", "length_mm": 30.0, "limit": "up_to_surface"}
        )

        assert "up_to_plane" in message or "catia_boolean" in message

    def test_a_genuinely_missing_tool_still_reports_the_coverage(self, occt: None) -> None:
        """The other half must not regress: when the *operation* is absent, the
        coverage number is what makes 'not built yet' checkable rather than a
        shrug."""
        from app.catia.dispatch import CatiaError, _execute_locally
        from app.catia.ops.registry import OPERATIONS_BY_NAME

        runner = backends.session_for("missing")
        runner("catia_new_part", {"name": "Plate"})
        with pytest.raises(CatiaError) as raised:
            _execute_locally(
                spec=OPERATIONS_BY_NAME[_missing_tool()],
                conversation_id="missing",
                arguments={},
            )

        message = str(raised.value)
        assert "is not implemented in the open kernel yet" in message
        assert "operations are" in message


class TestSketchingOnAFaceOfThePart:
    """`support="slab#top"` works, and this module said for months that it did not.

    `elements.plane_frame` is the one resolver every "which plane" argument goes
    through, and its own docstring records that each operation once had a private
    accept-list. `catia_sketch_create` was the last one still holding its own: it
    refused a planar face and blamed Phase 2.2, **a phase that had already
    shipped**. `catia_plane_offset(reference="slab#top")` resolved a face while
    `catia_sketch_create(support="slab#top")` did not, and no message on either
    side hinted at the difference.

    Measured at ladder Level 2 on 2026-09-09, four runs of one prompt: the agent
    asked for `support="top"`, read that face sketching needed an unbuilt phase,
    and never tried the syntax that would have worked — it went to
    `limit="up_to_surface"`, to `catia_shaft`, to a surface extrude, and finally
    told the user the kernel had no pad. A boss on the top face is the most
    ordinary thing a Level 2 part asks for.
    """

    def _plate(self):
        runner = backends.session_for("faces")
        runner("catia_new_part", {"name": "Plate"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner(
            "catia_sketch_rectangle",
            {"sketch": "outline", "width_mm": 120.0, "height_mm": 80.0},
        )
        runner("catia_pad", {"sketch": "outline", "length_mm": 10.0, "name": "slab"})
        return runner

    def test_a_sketch_lands_on_a_named_face(self, occt: None) -> None:
        runner = self._plate()

        created = runner("catia_sketch_create", {"support": "slab#top", "name": "boss"})

        assert created["sketch"] == "boss"

    def test_a_boss_built_on_the_top_face_adds_material(self, occt: None) -> None:
        """The sentence the ladder could not reach, end to end."""
        runner = self._plate()
        runner("catia_sketch_create", {"support": "slab#top", "name": "boss"})
        runner("catia_sketch_circle", {"sketch": "boss", "diameter_mm": 30.0})
        runner("catia_pad", {"sketch": "boss", "length_mm": 30.0})

        volume = runner("catia_measure", {})["volume_mm3"]

        assert volume == pytest.approx(120 * 80 * 10 + math.pi * 225 * 30)

    def test_the_catia_style_name_resolves_too(self, occt: None) -> None:
        """The agent reads feature names out of `catia_list_features`, which
        reports `Pad.1`, not the semantic name the design used."""
        runner = self._plate()

        assert runner("catia_sketch_create", {"support": "Pad.1#top", "name": "s"})

    def test_the_two_resolvers_agree(self, occt: None) -> None:
        """`catia_plane_offset` resolved a face while `catia_sketch_create` did
        not. Whatever one accepts as a plane, the other must."""
        runner = self._plate()

        runner("catia_plane_offset", {"reference": "slab#top", "distance_mm": 0.0, "name": "p"})
        runner("catia_sketch_create", {"support": "slab#top", "name": "s"})

    def test_an_unknown_plane_names_the_syntax_with_a_real_feature(
        self, occt: None
    ) -> None:
        """The refusal the model actually hits, since it writes `support="top"`.

        It must carry the syntax and a feature this part really has — a phase
        number is not something a model can act on, and it read as unbuilt.

        `"top"` itself no longer reaches here: it resolves against the bounding
        box, the way the seat has always resolved it. So this asks with a name
        that really is unknown.
        """
        from app.kernel.errors import GeometryError

        runner = self._plate()
        with pytest.raises(GeometryError) as raised:
            runner("catia_sketch_create", {"support": "nowhere", "name": "boss"})

        message = str(raised.value)
        assert "#top" in message
        assert "Pad.1" in message or "slab" in message
        assert "Phase 2.2" not in message


class TestABareFaceWordMeansTheSameOnBothBackends:
    """`support="top"` built a part on the seat and was refused here.

    `scripts/catia_bridge/com/_context.py::resolve_support` has resolved bare
    face words against the part's bounding box for some time — `FACE_PLANES` and
    `FACE_AXES` — and the open kernel had no equivalent. So the identical call
    succeeded on `GEOMETRY_BACKEND=catia` and failed on `occt`, which is the one
    thing Decision 1's conformance rests on not happening, and no message on
    either side hinted at it.

    Measured at ladder Level 2 on 2026-09-09: `support="top"` is the *first*
    thing the model reaches for, on both backends. The table here is copied from
    the bridge rather than invented, so the two cannot drift apart by taste.
    """

    def _plate(self):
        runner = backends.session_for("bareword")
        runner("catia_new_part", {"name": "Plate"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner(
            "catia_sketch_rectangle",
            {"sketch": "outline", "width_mm": 120.0, "height_mm": 80.0},
        )
        runner("catia_pad", {"sketch": "outline", "length_mm": 10.0, "name": "slab"})
        return runner

    def test_top_puts_the_boss_on_top(self, occt: None) -> None:
        """Not merely accepted — accepted *and* on the right side. A support that
        resolved to the bottom face would build a boss hanging underneath and
        report the same success."""
        runner = self._plate()
        runner("catia_sketch_create", {"support": "top", "name": "boss"})
        runner("catia_sketch_circle", {"sketch": "boss", "diameter_mm": 30.0})
        runner("catia_pad", {"sketch": "boss", "length_mm": 30.0})

        size = runner("catia_measure", {})["bounding_box_mm"]["size"]

        assert size[2] == pytest.approx(40.0, abs=1e-3), "the boss went the wrong way"

    def test_bottom_is_the_other_end(self, occt: None) -> None:
        runner = self._plate()
        runner("catia_sketch_create", {"support": "bottom", "name": "pin"})
        runner("catia_sketch_circle", {"sketch": "pin", "diameter_mm": 20.0})
        runner("catia_pad", {"sketch": "pin", "length_mm": 5.0, "reversed": True})

        low = runner("catia_measure", {})["bounding_box_mm"]["min"]

        assert low[2] == pytest.approx(-5.0, abs=1e-3)

    def test_the_words_are_the_bridge_s_own(self, occt: None) -> None:
        """Copied, not invented — if the bridge grows a word this must too."""
        from app.kernel.occt.operations.sketcher import _BOUNDING_BOX_FACES

        assert set(_BOUNDING_BOX_FACES) == {
            "top", "bottom", "front", "back", "left", "right",
        }

    def test_a_face_word_needs_a_part_to_measure(self, occt: None) -> None:
        """With no solid there is no bounding box, so the word means nothing and
        the ordinary refusal applies rather than a guess at the origin plane."""
        from app.kernel.errors import GeometryError

        runner = backends.session_for("empty")
        runner("catia_new_part", {"name": "Nothing"})
        with pytest.raises(GeometryError):
            runner("catia_sketch_create", {"support": "top", "name": "s"})


class TestThePartCanReachTheSolver:
    """The defect the 2026-09-10 ladder run found, and the seam that closes it.

    On `GEOMETRY_BACKEND=occt` the agent could build a part and then do nothing
    whatever to it. `catia_export_step` and `sync_geometry_from_catia` were the
    only two geometry→solver routes in its whole vocabulary and both were
    CATIA-only, so `run_simulation` had nothing to mesh and ladder Levels 3, 4
    and 5 — plane analyses, conduction, convergence studies — were unreachable
    from a conversation on the open kernel.

    **The offline suite could not see it, and that is the part worth keeping in
    mind.** Every tool worked. `write_step` worked. The kernel document held the
    shape. What was missing lived one layer above `dispatch`, in what the agent
    was *offered* — which is why these tests go through `call_catia` rather than
    through a runner.
    """

    @pytest.fixture
    def project_conversation(self, db_session, current_user_id, media_store, monkeypatch):
        from app.catia import dispatch
        from app.models import Conversation, Project

        monkeypatch.setattr(dispatch, "get_media_store", lambda: media_store)
        project = Project(name="Bracket", owner_id=current_user_id)
        db_session.add(project)
        db_session.flush()
        conversation = Conversation(
            owner_id=current_user_id, title="Bracket", project_id=project.id
        )
        db_session.add(conversation)
        db_session.commit()
        return {"db": db_session, "user_id": current_user_id, "conversation": conversation,
                "project": project}

    def _build_a_plate(self, conversation_id: str) -> None:
        runner = backends.session_for(conversation_id)
        runner("catia_new_part", {"name": "Plate"})
        runner("catia_sketch_create", {"support": "XY", "name": "base"})
        runner("catia_sketch_rectangle", {"sketch": "base", "width_mm": 60.0, "height_mm": 40.0})
        runner("catia_pad", {"sketch": "base", "length_mm": 20.0})

    @needs_kernel
    def test_a_part_built_on_the_open_kernel_becomes_a_geometry_version(
        self, occt: None, project_conversation
    ) -> None:
        """The whole defect, in one call.

        A geometry version is what `run_simulation` meshes, so this passing is
        the difference between the open kernel being a modelling toy and being
        the product.
        """
        from app.catia.dispatch import call_catia

        wired = project_conversation
        self._build_a_plate(wired["conversation"].id)

        result = call_catia(
            wired["db"],
            user_id=wired["user_id"],
            tool="catia_export_step",
            arguments={},
            conversation_id=wired["conversation"].id,
        )

        assert result["project_id"] == wired["project"].id
        assert result["version_number"] == 1
        assert result["geometry_version_id"]
        assert result["filename"].endswith(".step")
        assert result["size_bytes"] > 0
        # The stats are what the mesher and the load-case drafter read; a version
        # with no bounding box is a row the next step cannot use.
        assert result["stats"]["bounding_box"]

    @needs_kernel
    def test_the_geometry_that_arrives_is_the_part_that_was_built(
        self, occt: None, project_conversation
    ) -> None:
        """A file appearing is not evidence. 60 × 40 × 20 went in.

        Without this the test above passes on an export of an empty document, a
        stale document, or the wrong conversation's part.
        """
        from app.catia.dispatch import call_catia

        wired = project_conversation
        self._build_a_plate(wired["conversation"].id)
        result = call_catia(
            wired["db"],
            user_id=wired["user_id"],
            tool="catia_export_step",
            arguments={},
            conversation_id=wired["conversation"].id,
        )

        box = result["stats"]["bounding_box"]
        size = sorted(
            [
                abs(box["max"][index] - box["min"][index])
                for index in range(3)
            ]
        )
        assert size == pytest.approx([20.0, 40.0, 60.0], abs=1e-6)

    @needs_kernel
    def test_the_version_number_climbs_so_a_re_export_is_a_new_version(
        self, occt: None, project_conversation
    ) -> None:
        """"Export again after any change you want analysed" is what the system
        prompt tells the agent; a second export that overwrote the first would
        make a re-run answer the old question."""
        from app.catia.dispatch import call_catia

        wired = project_conversation
        self._build_a_plate(wired["conversation"].id)
        first = call_catia(
            wired["db"], user_id=wired["user_id"], tool="catia_export_step",
            arguments={}, conversation_id=wired["conversation"].id,
        )
        # Through `catia_set_parameter`, which is the route the kernel names when
        # a second identical pad is refused — and the one the system prompt tells
        # the agent to prefer for a dimension change.
        backends.session_for(wired["conversation"].id)(
            "catia_set_parameter",
            {"name": r"Pad.1\length_mm", "value": 25.0, "unit": "mm"},
        )
        second = call_catia(
            wired["db"], user_id=wired["user_id"], tool="catia_export_step",
            arguments={}, conversation_id=wired["conversation"].id,
        )

        assert (first["version_number"], second["version_number"]) == (1, 2)
        assert first["geometry_version_id"] != second["geometry_version_id"]

    @needs_kernel
    def test_exporting_nothing_is_refused_in_words_the_agent_can_act_on(
        self, occt: None, project_conversation
    ) -> None:
        """Two refusals cover this and they are not equally useful.

        `write_step` already declines a null shape, so an empty STEP cannot be
        written either way — verified by removing the check here and watching
        `manufacture.export`'s own message come through instead. What that
        message cannot say is *which* conversation is empty or what to call
        next, because it is a file-writing function and knows about neither. The
        agent's recovery from a bare failure is to retry the export, which fails
        identically; naming `catia_new_part` is what turns the refusal into a
        next move.
        """
        from app.catia.dispatch import CatiaError, call_catia

        wired = project_conversation
        backends.session_for(wired["conversation"].id)("catia_new_part", {"name": "Empty"})

        with pytest.raises(CatiaError, match="nothing built in this conversation"):
            call_catia(
                wired["db"], user_id=wired["user_id"], tool="catia_export_step",
                arguments={}, conversation_id=wired["conversation"].id,
            )

    @needs_kernel
    def test_a_conversation_with_no_project_is_told_what_to_do_about_it(
        self, occt: None, db_session, current_user_id, media_store, monkeypatch
    ) -> None:
        """There is nowhere to put a geometry version without a project, and the
        refusal has to name the fix — the agent's recovery from a bare failure is
        to try the export again, which will fail identically."""
        from app.catia import dispatch
        from app.catia.dispatch import CatiaError, call_catia
        from app.models import Conversation

        monkeypatch.setattr(dispatch, "get_media_store", lambda: media_store)
        conversation = Conversation(owner_id=current_user_id, title="No project")
        db_session.add(conversation)
        db_session.commit()
        self._build_a_plate(conversation.id)

        with pytest.raises(CatiaError, match="Create a project first"):
            call_catia(
                db_session, user_id=current_user_id, tool="catia_export_step",
                arguments={}, conversation_id=conversation.id,
            )

    @needs_kernel
    def test_another_users_conversation_is_refused(
        self, occt: None, project_conversation
    ) -> None:
        from app.catia.dispatch import CatiaError, call_catia

        wired = project_conversation
        self._build_a_plate(wired["conversation"].id)

        with pytest.raises(CatiaError, match="not attached to one of your conversations"):
            call_catia(
                wired["db"], user_id="00000000-0000-0000-0000-000000000000",
                tool="catia_export_step", arguments={},
                conversation_id=wired["conversation"].id,
            )
