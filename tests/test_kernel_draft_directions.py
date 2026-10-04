"""A mould can be pulled along any direction, not only three — THE QUEUE E10.

Closed 2026-09-17. `catia_analysis_part`'s `direction` used to be an origin *plane* —
`XY`, `YZ`, `ZX` — because that is how a CATIA user says it: "pulled off the XY plane".
**A plane has no side**, so a pull along −Z was a question the analysis had no way to be
asked, and the route answered it with a 400 naming the three that could.

The fix is not a fourth name. `catia_draft` — the operation that *creates* the taper —
has always taken `pulling_direction` as a **vector**, so the product carried two
vocabularies for one physical quantity: an agent could draft along `[0, 0, -1]` and then
be unable to ask about what it had just built. The analysis now takes the same vector,
the six plane names are **declared beside it as a union** rather than kept as a quiet
accept-list, and the adapter in `app/api/routes/kernel.py` that mapped one to the other
is gone, because there is nothing left to map.

**Why the names are in the schema and not only in the handler.** The first draft of this
change put them in `_pull_direction` alone and left the schema a bare vector. Every test
here passed — they call the runner — and `"XY"` through the actual product answered
`direction must be array, got str`, because `app/catia/validation.py` checks arguments
against the operation's document two storeys above the handler. That is CLAUDE.md's
testing item 8 in miniature, caught before it shipped rather than on a seat.
`TestBothSpellingsSurviveTheValidator` is what keeps it caught.

**The arithmetic never needed any of this.** `analyse_draft` and `find_undercuts` both
take a bare direction and normalise it, and both have since they were written. What
refused an arbitrary pull was the vocabulary three storeys up — which is why the symptom
was every draft rule on every part reporting `unmeasured` rather than an error: a
question nobody could ask looks exactly like a scan that failed.

**What the flip actually changes, measured rather than assumed — and it is nothing.**
The first draft of this file asserted that reversing the pull negates every reported
angle. It does not, and the code is right: `draft.py`'s convention is
`draft = asin(n · pull)` signed, where **the sign says which half of the tool takes the
face and the magnitude is the draft angle**, so the report's `minimum_draft_deg` is
`min(|draft|)` — the worst face on the part regardless of which half owns it. That is
invariant under the flip. So is the undercut set, because `find_undercuts` already tests
both halves. A straight-pull tool is symmetric and the report says so.

That makes the ± half of E10 a **no-op with a name**, which is worth pinning precisely
because it is surprising: the next person to read `min(|draft|)` may decide it is a bug
and make the answer depend on which way up the tool is drawn. `TestFlippingThePullIsA
NoOpAndThatIsTheConvention` is what stops that. The half of E10 that changes numbers is
the *arbitrary* direction, and it is pinned beside it.

Mirrors `tests/test_kernel_routes.py` from below, per CLAUDE.md's testing item 9 — the
route proves the path, this proves the geometry. There is no CATIA mirror to write:
`scripts/catia_bridge/com/inspection.py` refuses every kind but `validity` by name,
because CATIA's draft analysis is a screen overlay with no automation API, so `direction`
has never reached a seat at all. `TestTheCatiaSideHasNoDraftToMirror` states that, so the
day somebody implements it the claim is in front of them.

Offline: no seat, no database, no bridge.
"""

from __future__ import annotations

import math
import pathlib
from typing import Any

import pytest

from app.kernel import available
from app.kernel.errors import OperationNotSupported

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)


def _block():  # type: ignore[no-untyped-def]
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "Moulding"})
    runner("catia_sketch_create", {"name": "S", "support": "XY"})
    runner("catia_sketch_rectangle", {"sketch": "S", "width_mm": 60, "height_mm": 40})
    runner("catia_pad", {"sketch": "S", "length_mm": 20})
    return runner


def _drafted_block():  # type: ignore[no-untyped-def]
    """60 x 40 x 20 with its four sides drafted 5° off the +Z pull.

    Built rather than asserted on a bare box because a box has 0° of draft on every
    wall, and a test whose expected answer is zero cannot tell a working scan from one
    that returns zero for everything.
    """
    runner = _block()
    for face in ("front", "back", "left", "right"):
        # One selector per call: the vocabulary has no disjunction, deliberately.
        runner("catia_draft", {"faces": face, "angle_deg": 5.0, "neutral": "bottom"})
    return runner


def _cross_holed_block():  # type: ignore[no-untyped-def]
    """The same block with an 8 mm hole straight through it along Y.

    The hole's cylindrical wall is reachable from neither half of an **X**-pull tool, so
    this part has real undercuts to be symmetric about. Without it the undercut half of
    the flip test would compare zero with zero.
    """
    runner = _block()
    runner(
        "catia_hole_at",
        {"face": "front", "at": [0, 0], "diameter_mm": 8, "through_all": True},
    )
    return runner


def _draft(runner, direction) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    arguments: dict[str, Any] = {"kind": "draft"}
    if direction is not None:
        arguments["direction"] = direction
    return dict(runner("catia_analysis_part", arguments))


class TestThePullCanBeAVector:
    def test_the_default_is_still_plus_z(self) -> None:
        """Omitting the direction must keep answering what it always answered —
        every stored rule and every open conversation depends on it."""
        runner = _drafted_block()

        assert _draft(runner, None) == _draft(runner, [0.0, 0.0, 1.0])

    def test_a_plane_name_and_its_vector_are_one_question(self) -> None:
        """The accept-list doing its job: a conversation written against the old
        vocabulary still measures, rather than being refused for a spelling that
        used to work."""
        runner = _drafted_block()

        assert _draft(runner, "XY") == _draft(runner, [0.0, 0.0, 1.0])
        assert _draft(runner, "-XY") == _draft(runner, [0.0, 0.0, -1.0])
        assert _draft(runner, "yz") == _draft(runner, [1.0, 0.0, 0.0])
        assert _draft(runner, "+XY") == _draft(runner, [0.0, 0.0, 1.0])

    def test_length_is_not_a_direction(self) -> None:
        """`direction3` says the vector need not be normalised; `unit_vector` in
        the two callers is what makes that true."""
        runner = _drafted_block()

        assert _draft(runner, [0.0, 0.0, 7.5]) == _draft(runner, [0.0, 0.0, 1.0])

    def test_a_direction_that_is_not_an_axis_gives_a_different_answer(self) -> None:
        """**The half of E10 that changes numbers.** A tool drawn off an angled
        parting line is an ordinary thing to want and was unaskable. Pulled along
        [0, 1, 1] the same block's worst wall reads 3.533° rather than 5°, which
        is `asin` of the drafted normal against a 45° pull — a real measurement of
        a real question, not a re-spelling of the axis one.
        """
        runner = _drafted_block()
        axis = _draft(runner, [0.0, 0.0, 1.0])
        angled = _draft(runner, [0.0, 1.0, 1.0])

        assert axis["minimum_draft_deg"] == pytest.approx(5.0, abs=1e-9)
        assert angled["minimum_draft_deg"] == pytest.approx(3.5332871946, abs=1e-9)
        assert angled["pull_direction"] == pytest.approx(
            [0.0, math.sqrt(0.5), math.sqrt(0.5)]
        )


class TestFlippingThePullIsANoOpAndThatIsTheConvention:
    """Surprising, measured, and pinned so nobody "fixes" it.

    A straight-pull tool has two halves. `draft.py` reports `min(|draft|)` — the
    worst face on the part, whichever half owns it — and `find_undercuts` asks
    whether a face is reachable from *either* half, testing `pull` and
    `opposite(pull)`. Both are symmetric in the pull by construction, so drawing
    the same tool the other way up must not change the verdict on the part.
    """

    def test_the_worst_draft_is_the_same_either_way_up(self) -> None:
        runner = _drafted_block()

        assert _draft(runner, [0.0, 0.0, -1.0])["minimum_draft_deg"] == pytest.approx(
            _draft(runner, [0.0, 0.0, 1.0])["minimum_draft_deg"], abs=1e-12
        )

    def test_the_undercuts_are_the_same_either_way_up(self) -> None:
        """Non-vacuous on purpose: this part has **two** undercut faces under an X
        pull — the cross hole's cylindrical wall, reachable from neither half —
        so the two counts agreeing is evidence rather than two zeros matching."""
        runner = _cross_holed_block()
        forward = _draft(runner, [1.0, 0.0, 0.0])
        backward = _draft(runner, [-1.0, 0.0, 0.0])

        assert forward["undercut_face_count"] == 2
        assert backward["undercut_face_count"] == forward["undercut_face_count"]
        assert backward["undercut_faces_tested"] == forward["undercut_faces_tested"]

    def test_the_only_thing_that_differs_is_the_direction_it_was_asked_about(
        self,
    ) -> None:
        """The whole-payload form of the claim, which is what makes it a guard
        rather than three spot checks: if a future change made any reported
        quantity depend on the sign, this fails and names the key."""
        runner = _cross_holed_block()
        forward = _draft(runner, [1.0, 0.0, 0.0])
        backward = _draft(runner, [-1.0, 0.0, 0.0])

        differ = {
            key
            for key in set(forward) | set(backward)
            if forward.get(key) != backward.get(key)
        }

        assert differ == {"pull_direction"}
        assert forward["pull_direction"] == [1.0, 0.0, 0.0]
        assert backward["pull_direction"] == [-1.0, 0.0, 0.0]

    def test_the_direction_asked_about_is_reported_back(self) -> None:
        """Which is the reason the flip is worth supporting even though it moves
        no number: the report is bound to the question, and a scan that silently
        answered +Z for a −Z request would put the wrong direction in the
        provenance of a rule somebody signs."""
        assert _draft(_drafted_block(), "-ZX")["pull_direction"] == [0.0, -1.0, 0.0]


class TestWhatIsStillRefused:
    def test_a_direction_that_points_nowhere_is_refused(self) -> None:
        """Refused rather than defaulted to +Z. A zero vector is what an
        arithmetic slip produces — a difference of two points that turned out to
        be the same point — and a plausible number for a direction nobody chose is
        this codebase's definition of a wrong answer."""
        with pytest.raises(OperationNotSupported, match="points nowhere"):
            _draft(_block(), [0.0, 0.0, 0.0])

    def test_a_name_that_is_not_a_plane_is_refused_with_both_spellings(self) -> None:
        """The refusal teaches the vector first, because that is what the schema
        now advertises, and lists the names because they still work."""
        with pytest.raises(OperationNotSupported) as raised:
            _draft(_block(), "sideways")

        reason = str(raised.value)
        assert "[0, 0, 1]" in reason
        assert "-XY" in reason

    def test_a_vector_of_the_wrong_length_is_refused_by_count(self) -> None:
        with pytest.raises(OperationNotSupported, match="three components"):
            _draft(_block(), [0.0, 1.0])


class TestTheVocabularyIsOneVocabulary:
    def test_the_analysis_takes_the_vector_the_draft_feature_takes(self) -> None:
        """**The defect underneath E10, and why a fourth name would not have been
        the fix.** `catia_draft` creates the taper and takes a vector;
        `catia_analysis_part` measures it and took a plane name. The vector arm's
        constraints are held equal to the authoring operation's, so the two
        cannot drift into meaning different things."""
        from app.catia.ops import OPERATIONS

        analysis = next(o for o in OPERATIONS if o.name == "catia_analysis_part")
        feature = next(o for o in OPERATIONS if o.name == "catia_draft")

        measured = analysis.json_schema()["properties"]["direction"]
        authored = feature.json_schema()["properties"]["pulling_direction"]

        assert measured["type"] == ["array", "string"]
        assert authored["type"] == "array"
        assert measured["minItems"] == authored["minItems"] == 3
        assert measured["maxItems"] == authored["maxItems"] == 3
        assert measured["items"] == authored["items"]
        assert measured["nonZero"] is True

    def test_the_schema_carries_no_enum(self) -> None:
        """**The trap this union has and a plain enum does not.**
        `validation.validate` applies an `enum` to whatever it is handed, so
        listing the six names would refuse every *vector* as "not one of: XY,
        YZ, …" — the union's other arm rejected by its own constraint. A name the
        table does not hold is refused one storey down instead, by the code that
        can see it is a name."""
        from app.catia.ops import OPERATIONS

        analysis = next(o for o in OPERATIONS if o.name == "catia_analysis_part")

        assert "enum" not in analysis.json_schema()["properties"]["direction"]


class TestBothSpellingsSurviveTheValidator:
    """**The path proof, and the reason the accept-list is in the schema rather
    than only in the handler.**

    `app/catia/validation.py` checks arguments against the operation's document
    *before* any backend sees them, so a spelling the schema does not declare is
    refused two storeys above the code that would have accepted it. The first
    draft of E10 put the six names in `_pull_direction` alone and left the schema
    a bare `direction3`: every test in this file passed, calling the runner
    directly, and `"XY"` through the product answered `direction must be array,
    got str`. That is CLAUDE.md's testing item 8 exactly — a test that calls the
    backend proves the tool and not the path.
    """

    @staticmethod
    def _validate(direction: object) -> None:
        from app.catia.ops import OPERATIONS
        from app.catia.validation import validate

        analysis = next(o for o in OPERATIONS if o.name == "catia_analysis_part")
        validate({"kind": "draft", "direction": direction}, analysis.json_schema())

    @pytest.mark.parametrize(
        "direction",
        [[0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [0.0, 1.0, 1.0], [0.0, 0.0, 7.5]],
    )
    def test_a_vector_passes(self, direction: list[float]) -> None:
        self._validate(direction)

    @pytest.mark.parametrize("name", ["XY", "YZ", "ZX", "-XY", "-YZ", "-ZX"])
    def test_every_plane_name_passes(self, name: str) -> None:
        self._validate(name)

    def test_the_zero_vector_is_refused_at_the_boundary(self) -> None:
        """`nonZero` catches it here, before the backend, which is where a
        direction that points nowhere should die."""
        from app.catia.validation import SchemaError

        with pytest.raises(SchemaError, match="points nowhere"):
            self._validate([0.0, 0.0, 0.0])

    def test_a_short_vector_is_refused_at_the_boundary(self) -> None:
        from app.catia.validation import SchemaError

        with pytest.raises(SchemaError, match="at least 3"):
            self._validate([0.0, 1.0])

    def test_the_daemon_is_offered_the_same_document(self) -> None:
        """The generated table the CATIA bridge reads is built from the same
        operation, and a stale copy of it is how the two halves of a tool come to
        disagree about what an argument is."""
        import sys

        sys.path.insert(0, "scripts")
        from catia_bridge.generated_tools import TOOLS

        from app.catia.ops import OPERATIONS

        analysis = next(o for o in OPERATIONS if o.name == "catia_analysis_part")

        assert TOOLS["catia_analysis_part"][1] == analysis.json_schema()

    def test_every_accepted_name_resolves_to_a_unit_axis(self) -> None:
        """Six names, three axes, both signs — each a unit vector, so the
        accept-list cannot smuggle in a scaled direction that reads differently
        from its vector spelling."""
        from app.kernel.occt.operations.inspection import _PULL_NORMALS

        assert set(_PULL_NORMALS) == {"XY", "YZ", "ZX", "-XY", "-YZ", "-ZX"}
        for name, normal in _PULL_NORMALS.items():
            assert math.isclose(math.hypot(*normal), 1.0, abs_tol=1e-12), name
            if name.startswith("-"):
                assert normal == tuple(-value for value in _PULL_NORMALS[name[1:]])


class TestTheCatiaSideHasNoDraftToMirror:
    """CLAUDE.md's testing item 9 says close a vocabulary gap on both backends in
    one commit. Here the other backend does not implement the *kind*, so there is
    nothing to widen — and saying so out loud is the point, because "we only fixed
    one backend" and "the other backend cannot do this at all" look identical in a
    diff."""

    def test_the_catia_backend_refuses_every_kind_but_validity(self) -> None:
        """CATIA's draft, thickness and curvature analyses are screen overlays with
        no automation API. If that ever changes this test fails, and the vector
        plus the six names are in front of whoever changes it."""
        source = pathlib.Path("scripts/catia_bridge/com/inspection.py").read_text(
            encoding="utf-8"
        )

        assert 'if kind != "validity":' in source
        assert "is a screen overlay in CATIA with no automation " in source
