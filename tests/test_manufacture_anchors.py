"""A GD&T frame's leader lands on the feature it names — master plan E17 task 1.

The claim under test is the one that matters and the one that is easy to fake: **the
anchor is attached to the geometry, not to a coordinate that happens to sit on it
today**. So the part is rebuilt at a different size and the anchor has to move with its
feature; a test that only checked one build would pass against a hard-coded point.

What is deliberately *not* tested here is any inference from a feature's name to a
face. There is none, on purpose — see `app/manufacture/anchors.py`.
"""

from __future__ import annotations

import pytest

from app.kernel import OcctRunner
from app.manufacture.anchors import (
    FeatureAnchor,
    anchors_for,
    best_view,
    faces_towards,
    match_faces,
    project_point,
)
from app.manufacture.errors import DrawingError
from app.render.project import project
from app.render.views import view_named

TOP = {"normal": [0.0, 0.0, 1.0], "planar": True}
BOTTOM = {"normal": [0.0, 0.0, -1.0], "planar": True}
RIGHT_FACE = {"normal": [1.0, 0.0, 0.0], "planar": True}


def _plate(width: float = 120.0, depth: float = 80.0, thickness: float = 12.0):
    """A block on the origin: x,y centred, z from 0 to `thickness`."""
    runner = OcctRunner()
    runner("catia_new_part", {"name": "plate"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner(
        "catia_sketch_rectangle",
        {"sketch": "outline", "width_mm": width, "height_mm": depth},
    )
    runner("catia_pad", {"sketch": "outline", "length_mm": thickness})
    return runner


def _faces(runner) -> list[dict]:
    return list(runner("catia_list_faces", {})["faces"])


class TestTheProjectionAgreesWithTheRenderer:
    """`project_point` must land in the same 2-D frame HLR hands back.

    This is the check that stops a second, independent projection drifting from
    `app/render/project.py` — which carries two sign corrections that cancelled each
    other for a day, so a third place to get the convention wrong is a real risk rather
    than a theoretical one.
    """

    @pytest.mark.parametrize("name", ["front", "top", "right"])
    def test_the_corners_project_to_the_renderers_own_extent(self, name: str) -> None:
        runner = _plate()
        shape = runner._context.document.shape
        view = view_named(name)

        corners = [
            (x, y, z) for x in (-60.0, 60.0) for y in (-40.0, 40.0) for z in (0.0, 12.0)
        ]
        projected = [project_point(corner, view) for corner in corners]
        mine = (
            min(p[0] for p in projected),
            min(p[1] for p in projected),
            max(p[0] for p in projected),
            max(p[1] for p in projected),
        )

        assert mine == pytest.approx(project(shape, view).extent, abs=1e-6)


class TestTheAnchorIsAttachedToTheFeature:
    """The heart of it: rebuild the part and the anchor follows its feature."""

    def test_the_top_faces_anchor_sits_on_the_top_face(self) -> None:
        runner = _plate(thickness=12.0)
        anchors, unresolved = anchors_for({"datum A": TOP}, _faces(runner))

        assert not unresolved, unresolved
        assert len(anchors) == 1
        anchor = anchors[0]
        assert anchor.point_mm[2] == pytest.approx(12.0), (
            "the anchor for the top face must lie on the top face, at z = thickness"
        )
        assert anchor.normal == pytest.approx((0.0, 0.0, 1.0))

    def test_it_moves_with_the_feature_when_the_part_changes(self) -> None:
        """A coordinate would stay put; an attachment moves. This is the whole claim."""
        thin = anchors_for({"datum A": TOP}, _faces(_plate(thickness=12.0)))[0][0]
        thick = anchors_for({"datum A": TOP}, _faces(_plate(thickness=30.0)))[0][0]

        assert thin.point_mm[2] == pytest.approx(12.0)
        assert thick.point_mm[2] == pytest.approx(30.0), (
            "the top face moved to z = 30 and its anchor did not follow it, so the "
            "anchor is a coordinate rather than an attachment"
        )

    def test_it_follows_the_face_sideways_too(self) -> None:
        """Widening the plate moves the right-hand face; the anchor must track it."""
        narrow = anchors_for({"side": RIGHT_FACE}, _faces(_plate(width=120.0)))[0][0]
        wide = anchors_for({"side": RIGHT_FACE}, _faces(_plate(width=200.0)))[0][0]

        assert narrow.point_mm[0] == pytest.approx(60.0)
        assert wide.point_mm[0] == pytest.approx(100.0)

    def test_the_projected_anchor_lands_inside_that_views_outline(self) -> None:
        """Attachment is only meaningful if the point is on the drawing.

        Checked against the renderer's own extent for the view rather than against the
        part's bounding box, because the extent is what the sheet is laid out from.
        """
        runner = _plate()
        shape = runner._context.document.shape
        anchor = anchors_for({"datum A": TOP}, _faces(runner))[0][0]

        view = view_named("top")
        x, y = project_point(anchor.point_mm, view)
        low_x, low_y, high_x, high_y = project(shape, view).extent

        assert low_x - 1e-6 <= x <= high_x + 1e-6
        assert low_y - 1e-6 <= y <= high_y + 1e-6


class TestItRefusesRatherThanPointingAtSomethingPlausible:
    """Every refusal here exists because the alternative is a believable wrong leader."""

    def test_a_selector_matching_several_faces_is_refused_by_name(self) -> None:
        """A cube has six faces and four of them share no normal — but a selector that
        names none narrows nothing, and pointing at the first is the fake."""
        runner = _plate()
        faces = _faces(runner)
        duplicated = faces + [dict(faces[0])]

        _, unresolved = anchors_for({"ambiguous": faces[0]["selector"]}, duplicated)

        assert "ambiguous" in unresolved
        assert "matches 2 faces" in unresolved["ambiguous"]
        assert "nobody chose" in unresolved["ambiguous"]

    def test_a_selector_matching_nothing_says_so(self) -> None:
        runner = _plate()
        _, unresolved = anchors_for(
            {"gone": {"normal": [0.0, 0.7071, 0.7071], "planar": True}}, _faces(runner)
        )

        assert "gone" in unresolved
        assert "no face of this part matches" in unresolved["gone"]

    def test_a_selector_with_no_normal_is_refused_with_the_reason(self) -> None:
        with pytest.raises(DrawingError, match="must name a normal"):
            match_faces([], {"planar": True})

    def test_an_unresolved_feature_does_not_lose_the_resolved_ones(self) -> None:
        """One bad frame must not cost the whole sheet its leaders."""
        runner = _plate()
        anchors, unresolved = anchors_for(
            {"datum A": TOP, "gone": {"normal": [0.0, 0.6, 0.8]}}, _faces(runner)
        )

        assert [one.feature for one in anchors] == ["datum A"]
        assert set(unresolved) == {"gone"}


class TestTheLeaderGoesOnAViewTheFeatureFaces:
    """A leader onto a view the feature is behind points at a silhouette."""

    def test_the_top_face_chooses_the_top_view(self) -> None:
        anchor = FeatureAnchor("A", (0.0, 0.0, 12.0), (0.0, 0.0, 1.0))
        views = [view_named(n) for n in ("front", "top", "right")]

        chosen = best_view(anchor, views)

        assert chosen is not None and chosen.name == "top"

    def test_a_face_visible_in_no_given_view_gets_none(self) -> None:
        """`None`, not a least-bad view: the reader cannot tell a leader onto a
        silhouette from a leader onto the face."""
        anchor = FeatureAnchor("underside", (0.0, 0.0, 0.0), (0.0, 0.0, -1.0))

        assert best_view(anchor, [view_named("top")]) is None

    def test_facing_is_signed_so_the_squarest_view_wins(self) -> None:
        top = view_named("top")
        assert faces_towards((0.0, 0.0, 1.0), top) > faces_towards((1.0, 0.0, 0.0), top)
        assert faces_towards((0.0, 0.0, -1.0), top) < 0.0


class TestTheLeaderReachesTheSheet:
    """End to end: a bound frame gets a leader on the view its feature faces.

    The anchor tests above prove the geometry; this proves the wiring, which is the
    other half of E17 task 1 — a correct anchor nothing draws is still a frame with no
    leader on it.
    """

    def _request(self, **kwargs):
        from app.manufacture.layout import LayoutRequest

        base = {
            "title": "plate",
            "drawing_number": "D-1",
            "views": ("front", "top"),
            "include_iso": False,
        }
        base.update(kwargs)
        return LayoutRequest(**base)

    def test_a_bound_feature_gets_a_leader_on_the_view_it_faces(self) -> None:
        from app.manufacture.layout import lay_out

        runner = _plate()
        drawing = lay_out(
            runner._context.document.shape,
            self._request(feature_anchors={"datum A": TOP}, part_faces=tuple(_faces(runner))),
        )

        assert len(drawing.leaders) == 1
        leader = drawing.leaders[0]
        assert leader.feature == "datum A"
        assert leader.view == "top", "the top face is square to the top view"
        assert not drawing.unanchored

    def test_the_leader_lands_inside_its_own_views_outline(self) -> None:
        from app.manufacture.layout import lay_out

        runner = _plate()
        drawing = lay_out(
            runner._context.document.shape,
            self._request(feature_anchors={"datum A": TOP}, part_faces=tuple(_faces(runner))),
        )
        leader = drawing.leaders[0]
        view = drawing.view_named(leader.view)
        low_x, low_y, high_x, high_y = view.extent

        assert low_x - 1e-6 <= leader.point_mm[0] <= high_x + 1e-6
        assert low_y - 1e-6 <= leader.point_mm[1] <= high_y + 1e-6

    def test_a_feature_facing_away_from_every_view_is_named_not_pointed_at(self) -> None:
        """The bottom face is behind the part in both front and top."""
        from app.manufacture.layout import lay_out

        runner = _plate()
        drawing = lay_out(
            runner._context.document.shape,
            self._request(feature_anchors={"underside": BOTTOM}, part_faces=tuple(_faces(runner))),
        )

        assert drawing.leaders == ()
        assert "underside" in drawing.unanchored
        assert "faces away from every view" in drawing.unanchored["underside"]

    def test_a_drawing_with_no_bindings_is_unchanged(self) -> None:
        """Every drawing made before this feature must lay out exactly as it did."""
        from app.manufacture.layout import lay_out

        runner = _plate()
        drawing = lay_out(runner._context.document.shape, self._request())

        assert drawing.leaders == ()
        assert dict(drawing.unanchored) == {}

    def test_the_sheet_carries_the_projection_of_the_anchor_and_not_a_copy(self) -> None:
        """The wiring is faithful: the leader's point IS the projected anchor.

        **Why this is the end-to-end claim and not a movement test.** An orthographic
        view shows a planar face either face-on — in which case moving that face moves
        it in *depth*, which the view cannot show — or edge-on, where it is a line. So a
        face-centre anchor does not move within the view it faces, however much the part
        changes, and a test asserting that it does would be asserting something
        geometry does not offer. The first draft of this test did exactly that, twice.

        The attachment claim is proved in part coordinates by
        `TestTheAnchorIsAttachedToTheFeature`, where the anchor does move. What is left
        for the sheet is that it carries that anchor faithfully rather than a copy of it,
        and that is what this asserts.
        """
        from app.manufacture.anchors import anchors_for, project_point
        from app.manufacture.layout import lay_out

        for width in (120.0, 200.0):
            runner = _plate(width=width)
            faces = tuple(_faces(runner))
            drawing = lay_out(
                runner._context.document.shape,
                self._request(
                    views=("front", "top", "right"),
                    feature_anchors={"side": RIGHT_FACE},
                    part_faces=faces,
                ),
            )
            assert drawing.leaders, drawing.unanchored
            leader = drawing.leaders[0]
            assert leader.view == "right", "the +x face is square to the right view"

            anchor = anchors_for({"side": RIGHT_FACE}, faces)[0][0]
            expected = project_point(anchor.point_mm, view_named("right"))
            assert leader.point_mm == pytest.approx(expected), (
                "the sheet is not carrying the projection of the anchor it resolved"
            )
            # And the anchor underneath it did move with the part, which is the claim
            # the part-coordinate tests make and this one depends on.
            assert anchor.point_mm[0] == pytest.approx(width / 2.0)
