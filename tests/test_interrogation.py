"""Master plan Phase 3 — interrogation, the measurement contract, and provenance.

**Every geometric claim here is checked against an answer known in advance**, in the same
discipline as `test_solver.py`: a 2.5 mm shell reports a 2.5 mm minimum wall, a Ø10
cylinder reports a 5 mm convex radius, a Ø6 bore reports a 3 mm concave one, a box's
edges are 90° dihedrals. Nothing asserts against recorded output, because recorded output
records bugs as faithfully as it records correctness.

Three of these tests exist because the code was wrong before them and the wrongness was
invisible:

* `test_curvature_sign_convention_matches_geometry` — OCCT's raw curvature is *negative*
  for convex against the surface normal, the opposite of the intuitive reading, and a
  REVERSED face flips it again. The first implementation had both a plausible convention
  and inverted answers: a solid cylinder reported a concave barrel.
* `test_extreme_faces_lie_in_the_plane_not_merely_touch_it` — via the shell volume, which
  is what caught it originally.
* `test_narrow_rim_face_is_sampled` — a 2.5 mm rim on a 40 mm face is missed entirely by
  a 4×4 grid, which silently dropped a face from every scan.

The offline rule holds: no database fixture, no network, no CATIA. Tests skip cleanly
when OCCT is not installed, so the suite still runs for anyone who has not pulled a
~800 MB dependency.
"""

from __future__ import annotations

import math

import pytest

from app.design.assertions import Assertion, Outcome, check_assertions
from app.kernel import contract, provenance
from app.kernel.errors import GeometryError
from app.kernel.interrogation import unit_vector
from app.kernel.occt.binding import available

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)


# -- fixtures: shapes whose answers are known by hand -------------------------


def _box(dx: float = 10.0, dy: float = 20.0, dz: float = 30.0):
    from app.kernel.occt.binding import symbol

    return symbol("BRepPrimAPI_MakeBox")(dx, dy, dz).Shape()


def _moved(shape, dx: float, dy: float, dz: float):
    from app.kernel.occt.binding import symbol

    transform = symbol("gp_Trsf")()
    transform.SetTranslation(symbol("gp_Vec")(dx, dy, dz))
    return symbol("BRepBuilderAPI_Transform")(shape, transform, True).Shape()


def _cut(a, b):
    from app.kernel.occt.binding import symbol

    return symbol("BRepAlgoAPI_Cut")(a, b).Shape()


def _cylinder(radius: float, height: float):
    from app.kernel.occt.binding import symbol

    return symbol("BRepPrimAPI_MakeCylinder")(radius, height).Shape()


@pytest.fixture
def open_box():
    """20 mm cube hollowed to a 2 mm wall, open at the top. Minimum wall is exactly 2."""
    inner = _moved(_box(16.0, 16.0, 18.0), 2.0, 2.0, 2.0)
    return _cut(_box(20.0, 20.0, 20.0), inner)


# -- 3.1 oriented bounding box ------------------------------------------------


class TestOrientedBoundingBox:
    def test_matches_the_axis_aligned_box_on_an_aligned_part(self):
        from app.kernel.occt.metrology import bounding_box_mm, oriented_bounding_box

        shape = _box(10.0, 20.0, 30.0)
        oriented = oriented_bounding_box(shape)
        aligned = sorted(bounding_box_mm(shape)["size"], reverse=True)

        assert oriented.size == pytest.approx(aligned, abs=1e-9)
        assert oriented.is_axis_aligned

    def test_stays_tight_when_the_part_is_rotated(self):
        """The whole reason this exists: the AABB grows, the OBB does not.

        A 10×20×30 block turned 45° about Z has an axis-aligned box of about
        21.2×21.2×30 — half again its own footprint — and buying billet from that number
        buys the wrong billet.
        """
        from app.kernel.occt.binding import symbol
        from app.kernel.occt.metrology import bounding_box_mm, oriented_bounding_box

        transform = symbol("gp_Trsf")()
        transform.SetRotation(
            symbol("gp_Ax1")(symbol("gp_Pnt")(0, 0, 0), symbol("gp_Dir")(0, 0, 1)),
            math.radians(45.0),
        )
        rotated = symbol("BRepBuilderAPI_Transform")(
            _box(10.0, 20.0, 30.0), transform, True
        ).Shape()

        assert oriented_bounding_box(rotated).size == pytest.approx(
            [30.0, 20.0, 10.0], abs=1e-6
        )
        assert not oriented_bounding_box(rotated).is_axis_aligned

        aligned = bounding_box_mm(rotated)["size"]
        assert max(aligned) >= 29.9
        assert sorted(aligned)[0] > 21.0, "the AABB should have grown; that is the point"

    def test_alignment_is_measured_not_read_from_occt(self):
        """`Bnd_OBB.IsAABox()` reports construction, not orientation, and returns False
        for an axis-aligned box found by the optimal search. Regression against reading
        it directly."""
        from app.kernel.occt.binding import require, symbol
        from app.kernel.occt.metrology import oriented_bounding_box

        require()
        raw = symbol("Bnd_OBB")()
        symbol("BRepBndLib").AddOBB_s(_box(), raw, True, True, True)

        assert not raw.IsAABox()
        assert oriented_bounding_box(_box()).is_axis_aligned


# -- 3.2 wall thickness -------------------------------------------------------


class TestWallThickness:
    def test_solid_block_reports_its_thinnest_dimension(self):
        from app.kernel.occt.interrogate import scan_thickness

        report = scan_thickness(_box(10.0, 20.0, 30.0))
        assert report.minimum_mm == pytest.approx(10.0, abs=1e-6)
        assert report.misses == 0

    def test_shelled_box_reports_the_wall(self, open_box):
        from app.kernel.occt.interrogate import scan_thickness

        assert scan_thickness(open_box).minimum_mm == pytest.approx(2.0, abs=1e-6)

    def test_thickness_is_never_reported_as_measured(self, open_box):
        """Ray casting bounds the minimum, it does not prove it. A payload that called
        this measured would let an assertion read a sampled number as a fact."""
        from app.kernel.occt.interrogate import scan_thickness

        payload = scan_thickness(open_box).to_payload()
        basis = provenance.basis_of(payload, "minimum_wall_mm")

        assert basis is provenance.Basis.APPROXIMATED
        assert "ray cast" in provenance.method_for(payload, "minimum_wall_mm")

    def test_thinnest_point_is_reported_so_a_repair_can_be_aimed(self, open_box):
        from app.kernel.occt.interrogate import scan_thickness

        payload = scan_thickness(open_box).to_payload()
        assert len(payload["thinnest_point_mm"]) == 3

    def test_narrow_rim_face_is_sampled(self, open_box):
        """A 2 mm rim on a 20 mm face has no cell centre at a 4×4 grid.

        Without the empty-face refinement the rim contributes nothing, and is then
        reported as an untested face by the undercut scan — a face silently missing from
        every analysis.
        """
        from app.kernel.occt.classify import entity_extent, face_normal
        from app.kernel.occt.interrogate.sampling import sample_face
        from app.kernel.occt.topology import faces

        # Upward-facing AND at the top. The cavity floor also faces +Z, and it is a
        # generous 16 mm square whose centre the coarse grid finds easily — including it
        # would make this test pass without the rim ever being sampled.
        rims = [
            face
            for face in faces(open_box)
            if (normal := face_normal(face)) is not None
            and normal[2] > 0.9
            and entity_extent(face, 2)[0] == pytest.approx(20.0, abs=1e-6)
        ]
        assert len(rims) == 1, "the open box has exactly one rim, at the top"

        assert sample_face(rims[0]), "the refinement should reach a 2 mm rim"
        assert not sample_face(rims[0], refine_if_empty=False), (
            "if the coarse grid now finds the rim unaided, this test has stopped "
            "guarding the refinement"
        )


# -- 3.2 draft ----------------------------------------------------------------


class TestDraft:
    def test_vertical_walls_have_zero_draft(self):
        from app.kernel.occt.interrogate import analyse_draft

        report = analyse_draft(_box(), [0, 0, 1], required_deg=1.0)

        assert report.minimum_deg == pytest.approx(0.0, abs=1e-9)
        assert len(report.undrafted) == 4, "four vertical walls on a box"
        assert report.unevaluated == 0

    def test_top_and_bottom_are_fully_drafted(self):
        from app.kernel.occt.interrogate import analyse_draft

        report = analyse_draft(_box(), [0, 0, 1])
        magnitudes = sorted(face.magnitude_deg for face in report.faces)

        assert magnitudes[-2:] == pytest.approx([90.0, 90.0], abs=1e-9)

    def test_sign_says_which_half_of_the_tool_takes_the_face(self):
        from app.kernel.occt.interrogate import analyse_draft

        report = analyse_draft(_box(), [0, 0, 1])
        drafts = sorted(face.draft_deg for face in report.faces)

        assert drafts[0] == pytest.approx(-90.0, abs=1e-9), "the bottom face"
        assert drafts[-1] == pytest.approx(+90.0, abs=1e-9), "the top face"

    def test_a_planar_part_gets_a_measured_answer(self):
        """Not approximated. A plane has one exact normal, and calling that a sample
        would understate what the kernel actually knows."""
        from app.kernel.occt.interrogate import analyse_draft

        payload = analyse_draft(_box(), [0, 0, 1]).to_payload()
        assert provenance.basis_of(payload, "minimum_draft_deg") is provenance.Basis.MEASURED

    def test_a_curved_part_gets_an_approximated_answer(self):
        from app.kernel.occt.interrogate import analyse_draft

        report = analyse_draft(_cylinder(5.0, 30.0), [0, 0, 1])
        payload = report.to_payload()

        assert report.curved_faces >= 1
        assert (
            provenance.basis_of(payload, "minimum_draft_deg")
            is provenance.Basis.APPROXIMATED
        )

    def test_cylinder_barrel_drags_along_its_own_axis(self):
        """A barrel parallel to the pull has zero draft everywhere on it — the classic
        moulding failure, and the case a per-face average would hide."""
        from app.kernel.occt.interrogate import analyse_draft

        report = analyse_draft(_cylinder(5.0, 30.0), [0, 0, 1])
        assert report.minimum_deg == pytest.approx(0.0, abs=1e-6)

    def test_zero_direction_is_refused_with_a_reason(self):
        with pytest.raises(ValueError, match="points nowhere"):
            unit_vector([0.0, 0.0, 0.0])


class TestOrientationPredicates:
    """`parallel_to` / `perpendicular_to` — what made "the vertical walls" sayable.

    Before these, `normal` could name one wall at a time and nothing could name the set,
    which is the selection every draft, every side-wall fillet and every mould question
    actually wants.
    """

    def test_a_face_parallel_to_an_axis_is_a_wall_not_a_cap(self):
        """The convention is about the face's *plane*, not its normal — a face parallel
        to Z has a normal perpendicular to Z. Inverting this is the obvious mistake and
        it selects exactly the wrong four faces."""
        from app.kernel.occt.selectors import select_faces

        box = _box(40.0, 30.0, 20.0)
        assert len(select_faces(box, {"type": "face", "parallel_to": "z"})) == 4
        assert len(select_faces(box, {"type": "face", "perpendicular_to": "z"})) == 2

    def test_perpendicularity_has_its_own_tolerance_not_a_negated_one(self):
        """A 45° face must match neither.

        Testing perpendicular as "not within tolerance of parallel" accepts everything
        from the tolerance to 90°, so a 45° face passes as parallel and the predicate
        selects nearly every face on the part. Caught before this test existed; kept so
        it stays caught.
        """
        import math

        from app.kernel.occt.binding import symbol
        from app.kernel.occt.selectors import select_faces

        transform = symbol("gp_Trsf")()
        transform.SetRotation(
            symbol("gp_Ax1")(symbol("gp_Pnt")(0, 0, 0), symbol("gp_Dir")(0, 1, 0)),
            math.radians(45.0),
        )
        tilted = symbol("BRepBuilderAPI_Transform")(
            _box(40.0, 30.0, 20.0), transform, True
        ).Shape()

        # Rotating about Y leaves the two ±Y faces containing Z; the other four are now
        # at 45° and must match neither predicate. The perpendicular query therefore
        # matches nothing — and *raises* rather than returning an empty list, which is
        # the rule that a selector matching nothing is never a silent no-op.
        assert len(select_faces(tilted, {"type": "face", "parallel_to": "z"})) == 2
        with pytest.raises(GeometryError, match="matched no faces"):
            select_faces(tilted, {"type": "face", "perpendicular_to": "z"})

    def test_an_edge_is_judged_along_its_whole_length(self):
        """A circle's tangent sweeps its plane, so "perpendicular to Z" is true of a
        horizontal circle at every point — and a single end-to-end comparison, which is
        what a closed edge cannot even provide, would never see it."""
        from app.kernel.occt.selectors import select_edges

        cylinder = _cylinder(10.0, 30.0)
        assert len(select_edges(cylinder, {"type": "edge", "perpendicular_to": "z"})) == 2
        assert len(select_edges(cylinder, {"type": "edge", "parallel_to": "z"})) == 1

    def test_box_edges_split_four_and_eight(self):
        from app.kernel.occt.selectors import select_edges

        box = _box(40.0, 30.0, 20.0)
        assert len(select_edges(box, {"type": "edge", "parallel_to": "z"})) == 4
        assert len(select_edges(box, {"type": "edge", "perpendicular_to": "z"})) == 8

    def test_a_bare_axis_is_accepted_where_a_sign_would_be_meaningless(self):
        """`parallel_to: "z"` needs no sign — a wall parallel to Z is parallel to it
        whichever way it faces. `normal` still requires one, because there the sign is
        the entire question."""
        from app.kernel.selection import parse

        assert parse({"parallel_to": "z"}, kind="face").parallel_to == "+z"
        with pytest.raises(GeometryError, match="not a direction"):
            parse({"normal": "z"}, kind="face")

    def test_asking_for_both_at_once_is_refused(self):
        from app.kernel.selection import parse

        with pytest.raises(GeometryError, match="nothing is both"):
            parse({"parallel_to": "z", "perpendicular_to": "x"}, kind="face")


class TestDraftAnalysisAndDraftFeatureAgree:
    """The analysis and the feature that fixes what it finds, checked against each other.

    Neither could fake this alone: `catia_draft` tilts the walls using OCCT's
    `BRepOffsetAPI_DraftAngle`, and `analyse_draft` measures the result from surface
    normals it computes independently. If either had the sign, the neutral plane or the
    angle convention wrong, the two would disagree.
    """

    #: Every wall of the block in one selection — the faces whose plane contains the
    #: pull direction, which is what a mould designer means by "the vertical walls".
    WALLS = {"type": "face", "parallel_to": "z"}

    @staticmethod
    def _block(angle_deg: float | None = None, faces: object = None):
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Moulded"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 40.0, "height_mm": 30.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 20.0})
        if angle_deg is not None:
            runner(
                "catia_draft",
                {"faces": faces, "angle_deg": angle_deg, "neutral": "XY"},
            )
        return runner

    def test_a_box_with_no_draft_is_reported_as_needing_it(self):
        from app.kernel.occt.interrogate import analyse_draft

        before = analyse_draft(self._block().document.shape, [0, 0, 1], required_deg=2.0)

        assert before.minimum_deg == pytest.approx(0.0, abs=1e-9)
        assert len(before.undrafted) == 4, "four vertical walls will drag"

    def test_drafting_the_walls_makes_the_analysis_measure_the_angle_applied(self):
        """The whole loop in one assertion: scan finds 0°, draft applies 3°, scan reads 3°."""
        from app.kernel.occt.interrogate import analyse_draft

        after = analyse_draft(
            self._block(3.0, self.WALLS).document.shape, [0, 0, 1], required_deg=2.0
        )

        assert after.minimum_deg == pytest.approx(3.0, abs=1e-6)
        assert after.undrafted == (), "3° clears a 2° requirement on every wall"

    def test_a_taper_removes_material_above_the_neutral_plane(self):
        """Tapering about the base keeps the footprint and narrows the top.

        Checked against the prismatoid volume h/6·(A_bottom + 4·A_mid + A_top) rather
        than a recorded number, so a taper applied about the wrong plane — or in the
        wrong direction, which would make the part *grow* — fails instead of being
        enshrined.
        """
        import math

        from app.kernel.occt.metrology import volume_mm3

        angle = 3.0
        taper = math.tan(math.radians(angle))

        def area(z: float) -> float:
            return (40.0 - 2 * z * taper) * (30.0 - 2 * z * taper)

        expected = 20.0 / 6.0 * (area(0.0) + 4 * area(10.0) + area(20.0))
        assert expected < 24000.0, "a taper about the base must remove material"

        built = volume_mm3(self._block(angle, self.WALLS).document.shape)
        assert built == pytest.approx(expected, rel=1e-9)

    def test_an_unsupported_draft_mode_names_what_it_needs(self):
        from app.kernel.errors import OperationNotSupported

        runner = self._block()
        # Pins the *current* reason, not merely that there is one: the silhouette
        # shipped, so a refusal still blaming it would be stale.
        with pytest.raises(OperationNotSupported, match="takes a neutral"):
            runner(
                "catia_draft",
                {"faces": self.WALLS, "angle_deg": 2.0, "neutral": "XY", "mode": "reflect_line"},
            )

    def test_a_neutral_plane_must_be_named(self):
        """Defaulting it would taper the part about an arbitrary height and change every
        dimension downstream, silently."""
        from app.kernel.errors import GeometryError

        runner = self._block()
        with pytest.raises(GeometryError, match="neutral plane"):
            runner("catia_draft", {"faces": self.WALLS, "angle_deg": 2.0})


# -- 3.2 undercuts ------------------------------------------------------------


class TestUndercuts:
    def test_a_plain_block_has_none(self):
        from app.kernel.occt.interrogate import find_undercuts

        report = find_undercuts(_box(), [0, 0, 1])
        assert report.count == 0
        assert report.untested == 0

    def test_a_vertical_hole_is_mouldable(self):
        from app.kernel.occt.interrogate import find_undercuts

        holed = _cut(_box(20.0, 20.0, 20.0), _moved(_cylinder(3.0, 20.0), 10, 10, 0))
        assert find_undercuts(holed, [0, 0, 1]).count == 0

    def test_a_horizontal_hole_is_not(self):
        """The control for the test above. Same part, hole turned 90°: its wall is now
        reachable from neither half of a straight pull along Z."""
        from app.kernel.occt.binding import symbol
        from app.kernel.occt.interrogate import find_undercuts

        axis = symbol("gp_Ax2")(symbol("gp_Pnt")(10, 0, 10), symbol("gp_Dir")(0, 1, 0))
        sideways = symbol("BRepPrimAPI_MakeCylinder")(axis, 3.0, 20.0).Shape()
        holed = _cut(_box(20.0, 20.0, 20.0), sideways)

        assert find_undercuts(holed, [0, 0, 1]).count >= 1

    def test_undercut_is_reported_as_approximated(self):
        from app.kernel.occt.interrogate import find_undercuts

        payload = find_undercuts(_box(), [0, 0, 1]).to_payload()
        assert (
            provenance.basis_of(payload, "undercut_face_count")
            is provenance.Basis.APPROXIMATED
        )


# -- 3.2 curvature and continuity ---------------------------------------------


class TestCurvature:
    def test_curvature_sign_convention_matches_geometry(self):
        """OCCT's raw curvature is negative for convex, and a REVERSED face flips it.

        Both halves are checked here because getting either wrong inverts the answer, and
        the first implementation got both wrong in a way that still produced a
        plausible-looking radius. A solid cylinder's barrel is convex; a bore's wall is
        concave; both have exactly the radius they were built with.
        """
        from app.kernel.occt.interrogate import scan_curvature

        barrel = scan_curvature(_cylinder(5.0, 30.0))
        assert barrel.minimum_convex_radius_mm == pytest.approx(5.0, rel=1e-6)
        assert barrel.minimum_concave_radius_mm is None

        bore = _cut(_box(20.0, 20.0, 20.0), _moved(_cylinder(3.0, 20.0), 10, 10, 0))
        pocketed = scan_curvature(bore)
        assert pocketed.minimum_concave_radius_mm == pytest.approx(3.0, rel=1e-6)
        assert pocketed.minimum_convex_radius_mm is None

    def test_a_part_with_no_curvature_says_so_rather_than_returning_zero(self):
        from app.kernel.occt.interrogate import scan_curvature

        payload = scan_curvature(_box()).to_payload()

        assert "minimum_concave_radius_mm" not in payload
        assert (
            provenance.basis_of(payload, "minimum_concave_radius_mm")
            is provenance.Basis.UNAVAILABLE
        )
        assert "no concave curvature" in provenance.reason_for(
            payload, "minimum_concave_radius_mm"
        )


class TestContinuity:
    def test_box_edges_are_ninety_degree_corners(self):
        from app.kernel.occt.interrogate import scan_continuity

        report = scan_continuity(_box())

        assert report.minimum_dihedral_deg == pytest.approx(90.0, abs=1e-6)
        assert report.sharp_edges == 12
        assert report.tangent_edges == 0
        assert report.open_edges == 0
        assert report.unevaluated == 0

    def test_a_fillet_produces_tangent_edges(self):
        """A blend that came out tangent on one side and sharp on the other is a defect
        invisible to mass, volume and bounding box. This is the check that sees it."""
        from app.kernel.occt.interrogate import scan_continuity
        from app.kernel.occt.runner import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Blended"})
        runner("catia_sketch_create", {"support": "XY", "name": "base"})
        runner("catia_sketch_rectangle", {"sketch": "base", "width_mm": 40, "height_mm": 30})
        runner("catia_pad", {"sketch": "base", "length_mm": 20})
        runner("catia_fillet", {"radius_mm": 3.0, "edges": {"type": "edge", "circular": False}})

        shape = runner.document.shape
        assert scan_continuity(shape).tangent_edges > 0


# -- 3.2 validity -------------------------------------------------------------


class TestValidity:
    def test_a_well_built_part_is_valid(self, open_box):
        from app.kernel.occt.interrogate import check_validity

        report = check_validity(open_box)
        assert report.valid
        assert report.invalid_total == 0
        assert report.to_payload()["is_valid"] is True


# -- 3.3 clearance and interference -------------------------------------------


class TestClearance:
    def test_separated_parts_report_the_gap(self):
        from app.kernel.occt.interrogate import measure_clearance

        cube = _box(10.0, 10.0, 10.0)
        report = measure_clearance(cube, _moved(cube, 25.0, 0.0, 0.0))

        assert report.distance_mm == pytest.approx(15.0, abs=1e-9)
        assert not report.interferes

    def test_touching_and_overlapping_are_told_apart(self):
        """Both have a minimum distance of zero. Only the common volume separates a fit
        from a clash, which is why both numbers are reported."""
        from app.kernel.occt.interrogate import measure_clearance

        cube = _box(10.0, 10.0, 10.0)

        touching = measure_clearance(cube, _moved(cube, 10.0, 0.0, 0.0))
        assert touching.distance_mm == pytest.approx(0.0, abs=1e-9)
        assert not touching.interferes

        overlapping = measure_clearance(cube, _moved(cube, 5.0, 0.0, 0.0))
        assert overlapping.distance_mm == pytest.approx(0.0, abs=1e-9)
        assert overlapping.interferes
        assert overlapping.interference_mm3 == pytest.approx(500.0, rel=1e-9)

    def test_closest_points_are_ordered_by_shape(self):
        from app.kernel.occt.interrogate import measure_clearance

        cube = _box(10.0, 10.0, 10.0)
        report = measure_clearance(cube, _moved(cube, 25.0, 0.0, 0.0))

        assert report.closest_points is not None
        near, far = report.closest_points
        assert near[0] == pytest.approx(10.0, abs=1e-9)
        assert far[0] == pytest.approx(25.0, abs=1e-9)

    def test_clearance_is_measured_not_sampled(self):
        from app.kernel.occt.interrogate import measure_clearance

        cube = _box(10.0, 10.0, 10.0)
        payload = measure_clearance(cube, _moved(cube, 25.0, 0.0, 0.0)).to_payload()

        assert (
            provenance.basis_of(payload, "minimum_clearance_mm")
            is provenance.Basis.MEASURED
        )


# -- 3.4 the measurement contract ---------------------------------------------


class TestMeasurementContract:
    def test_every_quantity_a_scan_emits_is_documented(self, open_box):
        """The check that makes the contract a contract.

        A backend that invents a spelling, or a quantity added to a payload and never
        written down, shows up here rather than in a design review six months later.
        """
        from app.kernel.interrogation import InterrogationPayload
        from app.kernel.occt.interrogate import (
            analyse_draft,
            find_undercuts,
            measure_clearance,
            scan_continuity,
            scan_curvature,
            scan_thickness,
        )
        from app.kernel.occt.metrology import measure

        payload = InterrogationPayload()
        payload.merge(measure(open_box, density_kg_m3=7850.0))
        payload.add(scan_thickness(open_box))
        payload.add(analyse_draft(open_box, [0, 0, 1]))
        payload.add(find_undercuts(open_box, [0, 0, 1]))
        payload.add(scan_curvature(open_box))
        payload.add(scan_continuity(open_box))
        payload.add(measure_clearance(open_box, _moved(open_box, 50.0, 0.0, 0.0)))

        assert contract.undocumented_paths(payload.as_dict()) == ()

    def test_indexed_paths_resolve_to_their_vector(self):
        assert contract.normalise("bounding_box_mm.size[2]") == "bounding_box_mm.size"
        assert contract.entry("bounding_box_mm.size[2]") is not None

    def test_the_catalogue_names_units(self):
        entries = contract.catalogue()
        assert any("mass_kg (kg)" in line for line in entries)
        assert any("minimum_wall_mm (mm)" in line for line in entries)

    def test_an_undocumented_path_is_reported(self):
        assert contract.undocumented_paths({"invented_quantity_mm": 1.0}) == (
            "invented_quantity_mm",
        )

    def test_scan_diagnostics_are_not_treated_as_quantities(self):
        """`thickness_samples` describes how the scan went, not the part. Nobody writes
        an assertion against it, and requiring a contract entry per counter would make
        the table a chore that stops being maintained."""
        assert contract.undocumented_paths({"thickness_samples": 160}) == ()


# -- 3.5 provenance -----------------------------------------------------------


class TestProvenance:
    def test_an_approximated_record_must_name_its_method(self):
        with pytest.raises(ValueError, match="must name how"):
            provenance.approximated("")

    def test_an_unavailable_record_must_give_a_reason(self):
        with pytest.raises(ValueError, match="must say why"):
            provenance.Record(basis=provenance.Basis.UNAVAILABLE)

    def test_a_payload_without_provenance_claims_nothing(self):
        """Not `MEASURED`. Every payload written before this existed carries no
        provenance, and inventing confidence for them is the exact failure this module
        exists to prevent."""
        assert provenance.basis_of({"mass_kg": 1.0}, "mass_kg") is None

    def test_assertions_read_provenance_per_path(self, open_box):
        """A mixed payload: mass is integrated exactly, wall thickness is ray cast. An
        assertion on mass must not be tainted by the approximation next to it."""
        from app.kernel.interrogation import InterrogationPayload
        from app.kernel.occt.interrogate import scan_thickness
        from app.kernel.occt.metrology import measure

        payload = InterrogationPayload()
        payload.merge(measure(open_box, density_kg_m3=7850.0))
        provenance.attach(payload.values, "mass_kg", provenance.measured("integration"))
        payload.add(scan_thickness(open_box))
        values = payload.as_dict()

        report = check_assertions(
            [
                Assertion(name="light enough", measure="mass_kg", comparison="<=", bound=100.0),
                Assertion(name="thick enough", measure="minimum_wall_mm", comparison=">=", bound=1.0),
            ],
            values,
        )

        by_name = {result.name: result for result in report}
        assert by_name["light enough"].outcome is Outcome.PASSED
        assert not by_name["light enough"].approximate
        assert by_name["thick enough"].outcome is Outcome.PASSED
        assert by_name["thick enough"].approximate
        assert report.approximate, "the report as a whole read an approximate number"

    def test_an_unavailable_measurement_explains_itself(self):
        """The payoff of 3.5. The old message was "nothing reports that", which is true
        and useless; this one says what was tried and why it did not work."""
        from app.kernel.occt.interrogate import scan_curvature

        payload = scan_curvature(_box()).to_payload()
        report = check_assertions(
            [
                Assertion(
                    name="cutter fits",
                    measure="minimum_concave_radius_mm",
                    comparison=">=",
                    bound=1.0,
                )
            ],
            payload,
        )

        result = report.results[0]
        assert result.outcome is Outcome.UNMEASURED
        assert "no concave curvature" in result.reason

    def test_a_documented_but_unscanned_path_says_which_scan_produces_it(self):
        report = check_assertions(
            [Assertion(name="thin walls", measure="minimum_wall_mm", comparison=">=", bound=2.0)],
            {"mass_kg": 1.0},
        )

        reason = report.results[0].reason
        assert "no scan that produces it was run" in reason
        assert "ray cast" in reason, "the contract's own summary should be quoted"
