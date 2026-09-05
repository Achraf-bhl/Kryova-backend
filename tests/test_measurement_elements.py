"""Measuring named elements and pairs of them — master plan 3.3, on 2.2's references.

`catia_measure_between` was the one part of Phase 3 that could not be written when the
rest of it was: a clearance is measured *between two named things*, and until
`feature#selector` resolved there was no way to name one. This is that wiring, plus
`catia_measure_item`, which shares the same element resolver and would have been a
second parser if it had been left for later.

**Every number here is known before the test runs.** A 40×30×20 slab with a Ø12 boss on
top has a top face of exactly 1200 − 36π mm²; the boss contributes exactly 156π mm² of
surface; the seam edge of the cylinder is exactly 10 mm. Nothing is asserted against
recorded output.

Three tests exist because the code was wrong before them:

* `test_patch_size_cannot_change_the_answer` — a construction plane is unbounded and
  OCCT's distance search needs a shape, so the plane is bounded against its counterpart.
  If that bound were a fixed size, the measured distance would silently depend on it.
* `test_the_name_hint_lists_names_that_actually_resolve` — the first version listed
  `feature_names()`, which returns CATIA-style names (`Pad.1`), and `document.feature()`
  resolves design names (`slab`). It answered "there is nothing called slab" with three
  more names that also do not work.
* `test_all_and_the_directional_words_work_after_a_hash` — `boss#all` and `boss#vertical`
  were both refused while being the spellings the docstrings recommend.

Offline: no database, no network, no CATIA seat. Skips cleanly without OCCT.
"""

from __future__ import annotations

import math

import pytest

from app.kernel import contract, provenance
from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.binding import available

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)

#: The example part every test below measures, and its hand-computed answers.
SLAB_X, SLAB_Y, SLAB_Z = 40.0, 30.0, 20.0
BOSS_DIAMETER, BOSS_HEIGHT = 12.0, 10.0
BOSS_RADIUS = BOSS_DIAMETER / 2.0

#: The slab's top face is the rectangle minus the boss footprint — the annulus that the
#: plain word `top` can never return, because `top` is the top of the *part*.
SLAB_TOP_AREA_MM2 = SLAB_X * SLAB_Y - math.pi * BOSS_RADIUS**2

#: The boss contributes its cylindrical wall and its own top disc, and nothing else.
BOSS_AREA_MM2 = (
    math.pi * BOSS_DIAMETER * BOSS_HEIGHT + math.pi * BOSS_RADIUS**2
)


def _part():
    """The slab-and-boss part, plus a construction plane and a reference point."""
    from app.kernel.occt.document import PartDocument
    from app.kernel.occt.operations import HANDLERS
    from app.kernel.occt.operations.context import BuildContext

    document = PartDocument(name="probe")
    context = BuildContext(document=document)

    def run(tool: str, **arguments: object) -> object:
        return HANDLERS[tool](context, arguments)

    run("catia_sketch_create", support="XY", name="base")
    run("catia_sketch_rectangle", sketch="base", width_mm=SLAB_X, height_mm=SLAB_Y)
    run("catia_pad", sketch="base", name="slab", length_mm=SLAB_Z)
    run("catia_set_material", material="steel-1018")
    run("catia_plane_offset", name="slab_top", reference="XY", distance_mm=SLAB_Z)
    run("catia_sketch_create", support="slab_top", name="boss_profile")
    run("catia_sketch_circle", sketch="boss_profile", diameter_mm=BOSS_DIAMETER)
    run("catia_pad", sketch="boss_profile", name="boss", length_mm=BOSS_HEIGHT)
    run("catia_point_at", name="overhead", at=[0.0, 0.0, 60.0])
    return document, run


@pytest.fixture
def part():
    return _part()


# -- what a reference may name ------------------------------------------------


class TestElementReferences:
    def test_each_namespace_resolves_to_its_own_kind(self, part):
        from app.kernel.occt.elements import resolve_element

        document, _ = part
        kinds = {
            name: resolve_element(document, name, tool="test").kind
            for name in ("slab", "slab_top", "overhead")
        }
        assert kinds == {"slab": "body", "slab_top": "plane", "overhead": "point"}

    def test_a_name_meaning_two_things_is_refused_not_guessed(self, part):
        """Namespaces are separate, so nothing stops a plane and a feature sharing a
        name. Picking one by table order would work until the day it measured the
        wrong thing silently."""
        from app.kernel.occt.elements import resolve_element
        from app.kernel.occt.reference import offset_frame

        document, _ = part
        document.add_plane(
            type(document.plane("slab_top"))(
                name="slab",
                frame=offset_frame(document.plane("slab_top").frame, 5.0),
            )
        )
        with pytest.raises(GeometryError, match="more than one thing"):
            resolve_element(document, "slab", tool="test")

    def test_the_name_hint_lists_names_that_actually_resolve(self, part):
        """`feature_names()` returns `Pad.1`; `document.feature()` resolves `slab`.
        A hint built from the former answers a missing name with more missing names."""
        from app.kernel.occt.elements import resolve_element

        document, _ = part
        with pytest.raises(GeometryError) as caught:
            resolve_element(document, "no_such_thing", tool="test")

        message = str(caught.value)
        assert "slab" in message and "boss" in message
        assert "Pad." not in message

    def test_all_and_the_directional_words_work_after_a_hash(self, part):
        """Both were refused while being the spellings the docstrings recommend —
        `all` was absent from the word table and `vertical` has no predicate form."""
        from app.kernel.occt.elements import resolve_element

        document, _ = part
        every = resolve_element(document, "boss#all", tool="test")
        seam = resolve_element(document, "boss#vertical", tool="test")

        assert every.kind == "faces" and every.entity_count == 2
        assert seam.kind == "edges" and seam.entity_count == 1

    def test_a_selector_word_chooses_faces_or_edges_by_its_own_meaning(self, part):
        from app.kernel.occt.elements import resolve_element

        document, _ = part
        assert resolve_element(document, "slab#top", tool="test").kind == "faces"
        assert resolve_element(document, "slab#convex", tool="test").kind == "edges"

    def test_convexity_is_judged_against_the_part_not_the_narrowed_selection(self, part):
        """The bug this pins: restricting to a feature builds a compound of just that
        feature's *edges* so `axis`/`side` measure the feature's extent — and a compound
        of edges has no faces, so the adjacency map came out empty and every edge's
        convexity `None`. `slab#convex` matched nothing on a slab with twelve convex
        edges, and said so as "matched no edges", which reads like a modelling problem.
        """
        from app.kernel.occt.elements import resolve_element

        document, _ = part
        assert resolve_element(document, "slab#convex", tool="test").entity_count == 12
        assert resolve_element(document, "boss#convex", tool="test").entity_count == 1
        assert resolve_element(document, "boss#concave", tool="test").entity_count == 1

        # A plain box has no concave edges, and that must still be a clean refusal —
        # the fix must not make everything match.
        with pytest.raises(GeometryError, match="matched no edges"):
            resolve_element(document, "slab#concave", tool="test")

    def test_an_unknown_selector_word_names_the_whole_vocabulary(self, part):
        from app.kernel.occt.elements import resolve_element

        document, _ = part
        with pytest.raises(GeometryError) as caught:
            resolve_element(document, "slab#leftish", tool="test")

        message = str(caught.value)
        for word in ("all", "top", "bottom", "convex", "concave", "vertical", "horizontal"):
            assert word in message


# -- measuring one element ----------------------------------------------------


class TestMeasureItem:
    def test_the_top_face_is_the_annulus_under_the_boss(self, part):
        """1200 − 36π, not 1200. The whole point of `feature#selector`: the boss ate
        part of the slab's top face, and the plain word `top` returns the boss's."""
        _, run = part
        measured = run("catia_measure_item", element="slab#top")

        assert measured["measured_kind"] == "Plane"
        assert measured["area_mm2"] == pytest.approx(SLAB_TOP_AREA_MM2, rel=1e-9)

    def test_a_feature_contributes_exactly_its_own_faces(self, part):
        _, run = part
        measured = run("catia_measure_item", element="boss#all")

        assert measured["element"]["entity_count"] == 2
        assert measured["area_mm2"] == pytest.approx(BOSS_AREA_MM2, rel=1e-9)

    def test_a_single_circular_edge_reports_its_diameter(self, part):
        """`boss#concave` is exactly one edge — the circle where the boss meets the
        slab, which is the only concave edge on the part."""
        _, run = part
        measured = run("catia_measure_item", element="boss#concave")

        assert measured["measured_kind"] == "Circle"
        assert measured["element"]["entity_count"] == 1
        assert measured["diameter_mm"] == pytest.approx(BOSS_DIAMETER, rel=1e-9)
        assert measured["radius_mm"] == pytest.approx(BOSS_RADIUS, rel=1e-9)

    def test_several_circles_report_total_length_and_no_diameter(self, part):
        """The boss has two horizontal circles, top and bottom. Their lengths add up to
        something meaningful; their diameters do not, so no diameter is reported rather
        than a sum that reads exactly like a measurement."""
        _, run = part
        measured = run("catia_measure_item", element="boss#horizontal")

        assert measured["element"]["entity_count"] == 2
        assert measured["length_mm"] == pytest.approx(2 * math.pi * BOSS_DIAMETER, rel=1e-9)
        assert "diameter_mm" not in measured

    def test_the_cylinder_seam_is_the_boss_height(self, part):
        _, run = part
        measured = run("catia_measure_item", element="boss#vertical")

        assert measured["measured_kind"] == "Line"
        assert measured["length_mm"] == pytest.approx(BOSS_HEIGHT, rel=1e-9)

    def test_a_body_gets_the_full_measurement_not_just_an_area(self, part):
        """Naming a whole feature means the same question `catia_measure` answers.
        Reporting only surface area because this operation is element-scoped would be
        an answer nobody wants when the full one is one call away."""
        _, run = part
        measured = run("catia_measure_item", element="slab")

        assert measured["measured_kind"] == "body"
        assert measured["volume_mm3"] == pytest.approx(SLAB_X * SLAB_Y * SLAB_Z, rel=1e-9)
        assert measured["mass_kg"] > 0.0

    def test_a_point_reports_where_it_is(self, part):
        _, run = part
        measured = run("catia_measure_item", element="overhead")

        assert measured["measured_kind"] == "point"
        assert measured["position_mm"] == [0.0, 0.0, 60.0]

    def test_a_construction_plane_reports_its_origin_and_normal(self, part):
        _, run = part
        measured = run("catia_measure_item", element="slab_top")

        assert measured["measured_kind"] == "plane"
        assert measured["position_mm"] == [0.0, 0.0, SLAB_Z]
        assert measured["normal"] == [0.0, 0.0, 1.0]

    def test_an_aggregate_says_how_many_it_aggregated(self, part):
        """Summing without saying over what is how "the area of the top face" quietly
        becomes the area of four of them."""
        _, run = part
        measured = run("catia_measure_item", element="boss#all")
        assert measured["element"]["entity_count"] == 2


# -- measuring between two elements -------------------------------------------


class TestMeasureBetween:
    def test_a_point_above_the_part_measures_to_the_nearest_face(self, part):
        _, run = part
        measured = run("catia_measure_between", elements=["overhead", "slab"])

        assert measured["minimum_clearance_mm"] == pytest.approx(40.0, rel=1e-9)
        assert measured["closest_points_mm"][1] == [0.0, 0.0, SLAB_Z]

    def test_distance_and_overlap_are_not_redundant(self, part):
        """Two shapes that interpenetrate have a minimum distance of zero, and so do
        two that merely touch. Only the common volume separates them."""
        _, run = part
        measured = run("catia_measure_between", elements=["boss", "slab"])

        assert measured["minimum_clearance_mm"] == pytest.approx(0.0, abs=1e-9)
        assert measured["interferes"] is True
        assert measured["interference_volume_mm3"] > 0.0

    def test_patch_size_cannot_change_the_answer(self, part):
        """A plane is unbounded and the distance search needs a shape, so the plane is
        bounded against its counterpart. Two counterparts at wildly different distances
        from the plane's origin must both measure exactly."""
        from app.kernel.occt.operations.context import BuildContext

        document, run = part
        run("catia_point_at", name="near_origin", at=[0.0, 0.0, 60.0])
        run("catia_point_at", name="far_away", at=[5000.0, -3000.0, 60.0])

        near = run("catia_measure_between", elements=["slab_top", "near_origin"])
        far = run("catia_measure_between", elements=["slab_top", "far_away"])

        assert near["minimum_clearance_mm"] == pytest.approx(40.0, rel=1e-12)
        assert far["minimum_clearance_mm"] == pytest.approx(40.0, rel=1e-12)
        assert isinstance(BuildContext(document=document).document, type(document))

    def test_a_plane_refuses_an_overlap_rather_than_reporting_zero(self, part):
        """`0.0` would read as "they do not clash", which is a claim nobody made."""
        from app.kernel.interrogation import INTERFERENCE_VOLUME_MM3

        _, run = part
        measured = run("catia_measure_between", elements=["slab_top", "slab"])

        assert INTERFERENCE_VOLUME_MM3 not in measured
        assert (
            provenance.basis_of(measured, INTERFERENCE_VOLUME_MM3)
            is provenance.Basis.UNAVAILABLE
        )
        assert "bounds no volume" in provenance.reason_for(measured, INTERFERENCE_VOLUME_MM3)

    def test_two_parallel_planes_are_measured_analytically(self, part):
        document, run = part
        run("catia_plane_offset", name="higher", reference="slab_top", distance_mm=15.0)

        measured = run("catia_measure_between", elements=["slab_top", "higher"])
        assert measured["minimum_clearance_mm"] == pytest.approx(15.0, rel=1e-12)

    def test_two_crossing_planes_are_zero_apart_with_no_closest_pair(self, part):
        """Non-parallel planes intersect somewhere however far apart their origins are.
        Naming a closest pair there would invent a location out of infinitely many."""
        _, run = part
        run("catia_plane_offset", name="side", reference="YZ", distance_mm=100.0)

        measured = run("catia_measure_between", elements=["slab_top", "side"])
        assert measured["minimum_clearance_mm"] == pytest.approx(0.0, abs=1e-12)
        assert "closest_points_mm" not in measured


class TestAngle:
    def test_parallel_faces_are_zero_degrees_apart(self, part):
        """The slab's top and bottom normals are antiparallel. Reporting 180° would be
        technically defensible and is not what anyone means by the angle between two
        parallel faces."""
        _, run = part
        measured = run("catia_measure_between", elements=["slab#top", "slab#bottom"], kind="angle")

        assert measured["angle_deg"] == pytest.approx(0.0, abs=1e-9)
        assert measured["parallel"] is True

    def test_perpendicular_planes_are_ninety_degrees_apart(self, part):
        _, run = part
        run("catia_plane_offset", name="side", reference="YZ", distance_mm=5.0)

        measured = run("catia_measure_between", elements=["slab_top", "side"], kind="angle")
        assert measured["angle_deg"] == pytest.approx(90.0, rel=1e-9)

    def test_the_report_names_which_two_directions_were_compared(self, part):
        """The angle between a plane and an edge is not the angle between the plane and
        the edge. Naming what was compared is how the report avoids picking a convention
        silently."""
        _, run = part
        measured = run(
            "catia_measure_between", elements=["slab_top", "boss#vertical"], kind="angle"
        )

        assert measured["angle_between"] == "plane normal to edge direction"
        assert len(measured["reference_directions"]) == 2

    def test_an_angle_is_never_reported_above_ninety(self, part):
        _, run = part
        for pair in (["slab#top", "slab#bottom"], ["slab_top", "boss#vertical"]):
            measured = run("catia_measure_between", elements=pair, kind="angle")
            assert 0.0 <= measured["angle_deg"] <= 90.0 + 1e-9

    def test_an_element_with_no_direction_is_refused_with_what_to_do(self, part):
        _, run = part
        with pytest.raises(GeometryError) as caught:
            run("catia_measure_between", elements=["slab", "boss"], kind="angle")

        message = str(caught.value)
        assert "An angle needs a direction" in message
        assert "Measure the minimum distance instead" in message


# -- refusals -----------------------------------------------------------------


class TestRefusals:
    def test_exactly_two_elements_or_nothing(self, part):
        _, run = part
        for elements in (["slab"], ["slab", "boss", "slab_top"], "slab", None):
            with pytest.raises(GeometryError, match="exactly two"):
                run("catia_measure_between", elements=elements)

    def test_an_unsupported_kind_names_the_supported_ones(self, part):
        _, run = part
        with pytest.raises(OperationNotSupported) as caught:
            run("catia_measure_between", elements=["slab", "boss"], kind="volume")

        message = str(caught.value)
        for kind in ("angle", "closest_points", "minimum_distance"):
            assert kind in message

    def test_an_empty_reference_says_what_a_reference_looks_like(self, part):
        from app.kernel.occt.elements import resolve_element

        document, _ = part
        with pytest.raises(GeometryError, match="feature#selector"):
            resolve_element(document, "", tool="test")


# -- the contract -------------------------------------------------------------


class TestTheseMeasurementsAreDocumented:
    def test_no_measurement_payload_invents_an_undocumented_path(self, part):
        """The check that keeps `app.kernel.contract` a contract rather than a comment:
        every number these two operations emit is written down with its unit."""
        _, run = part
        payloads = [
            run("catia_measure_item", element="slab#top"),
            run("catia_measure_item", element="boss#vertical"),
            run("catia_measure_item", element="boss#horizontal"),
            run("catia_measure_item", element="boss#concave"),
            run("catia_measure_item", element="overhead"),
            run("catia_measure_item", element="slab_top"),
            run("catia_measure_item", element="slab"),
            run("catia_measure_between", elements=["overhead", "slab"]),
            run("catia_measure_between", elements=["slab_top", "slab"]),
            run(
                "catia_measure_between",
                elements=["slab#top", "slab#bottom"],
                kind="angle",
            ),
        ]
        for payload in payloads:
            assert contract.undocumented_paths(payload) == ()

    def test_every_measured_number_carries_a_basis(self, part):
        _, run = part
        measured = run("catia_measure_item", element="boss#concave")

        for path in ("length_mm", "diameter_mm"):
            assert provenance.basis_of(measured, path) is provenance.Basis.MEASURED


# -- coverage -----------------------------------------------------------------


def test_both_operations_are_wired_into_the_backend():
    """An unwired handler is invisible everywhere, which is the whole reason the
    registry cross-check exists."""
    from app.kernel.occt.operations import HANDLERS, unknown_handler_names

    assert "catia_measure_between" in HANDLERS
    assert "catia_measure_item" in HANDLERS
    assert unknown_handler_names() == ()
