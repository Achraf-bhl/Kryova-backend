"""Master plan Phase 2.4 — constructed planes, and sketching on them.

**What this phase is actually for**: before it, every sketch sat on `XY`, `YZ` or `ZX`,
so every profile in a design passed through the world origin. That is enough for a first
pad and nothing after it. `test_a_boss_can_be_built_on_top_of_a_pad` is the phase in one
test — the second feature of a real part.

Verified against closed-form answers, not recorded output: a boss of Ø12 × 10 on a
40×30×20 pad has volume 24000 + π·6²·10, and its plane sits at exactly z = 20.

Offline: no database, no network, no CATIA. Skips cleanly without OCCT.
"""

from __future__ import annotations

import math

import pytest

from app.kernel.errors import GeometryError, NamingError, OperationNotSupported
from app.kernel.occt.binding import available

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)

TOL = 1e-6


def _part(name: str = "P"):
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": name})
    return runner


def _padded(runner, width=40.0, height=30.0, length=20.0, name=None):
    runner("catia_sketch_create", {"support": "XY", "name": "base"})
    runner("catia_sketch_rectangle", {"sketch": "base", "width_mm": width, "height_mm": height})
    pad: dict[str, object] = {"sketch": "base", "length_mm": length}
    if name is not None:
        pad["name"] = name
    runner("catia_pad", pad)
    return runner


class TestConstructedPlanes:
    def test_an_offset_plane_sits_where_it_was_asked_to(self):
        runner = _part()
        result = runner(
            "catia_plane_offset", {"reference": "XY", "distance_mm": 20.0, "name": "top"}
        )

        assert result["origin_mm"] == pytest.approx([0.0, 0.0, 20.0], abs=TOL)
        assert result["normal"] == pytest.approx([0.0, 0.0, 1.0], abs=TOL)
        assert "top" in result["planes"]

    def test_a_negative_offset_goes_the_other_way(self):
        runner = _part()
        result = runner(
            "catia_plane_offset", {"reference": "XY", "distance_mm": -8.0, "name": "under"}
        )
        assert result["origin_mm"] == pytest.approx([0.0, 0.0, -8.0], abs=TOL)

    def test_reversed_flips_whatever_was_given(self):
        """Both spellings are in the operation's schema, so honouring only one would
        make the other silently do nothing."""
        runner = _part()
        result = runner(
            "catia_plane_offset",
            {"reference": "XY", "distance_mm": 8.0, "reversed": True, "name": "under"},
        )
        assert result["origin_mm"] == pytest.approx([0.0, 0.0, -8.0], abs=TOL)

    def test_offsets_compose_from_a_constructed_plane(self):
        runner = _part()
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 20.0, "name": "a"})
        second = runner(
            "catia_plane_offset", {"reference": "a", "distance_mm": 5.0, "name": "b"}
        )
        assert second["origin_mm"] == pytest.approx([0.0, 0.0, 25.0], abs=TOL)
        assert second["derived_from"] == "a"

    def test_an_offset_plane_inherits_the_local_x_of_its_reference(self):
        """What makes offsets compose at all.

        `gp_Ax3(point, normal)` invents a local X from the normal if you let it, which
        silently rotates the sketch frame — so a rectangle placed `at (10, 5)` on the
        offset plane would land somewhere other than directly above the same rectangle
        on the plane it came from.
        """
        from app.kernel.occt.sketching import frame_of

        runner = _part()
        runner("catia_plane_offset", {"reference": "YZ", "distance_mm": 7.0, "name": "side"})
        constructed = runner.document.plane("side").frame
        reference = frame_of("YZ")

        for axis in ("XDirection", "YDirection", "Direction"):
            built = getattr(constructed, axis)()
            base = getattr(reference, axis)()
            assert (built.X(), built.Y(), built.Z()) == pytest.approx(
                (base.X(), base.Y(), base.Z()), abs=TOL
            )

    def test_a_plane_can_be_offset_from_a_named_face(self):
        """This was refused until 2.2 shipped, with a message pointing at 2.2. Once
        `feature#selector` resolved, the refusal was the only thing left standing
        between a design and `catia_plane_offset(reference="slab#top")` — a stale
        "blocked on X" outliving X, which is how a capability stays unreachable.

        The slab is 20 mm tall, so a plane 5 mm above its top face sits at z=25.
        """
        runner = _padded(_part(), name="slab")
        result = runner(
            "catia_plane_offset",
            {"reference": "slab#top", "distance_mm": 5.0, "name": "above"},
        )

        assert result["origin_mm"][2] == pytest.approx(25.0, abs=TOL)
        assert result["normal"] == pytest.approx([0.0, 0.0, 1.0], abs=TOL)

    def test_offsetting_from_something_with_no_plane_is_still_refused(self):
        """The fix must not make everything resolve: a whole body has no single plane,
        and answering with one of its six faces would build at a height nobody chose."""
        runner = _padded(_part(), name="slab")
        with pytest.raises(GeometryError, match="no single plane"):
            runner("catia_plane_offset", {"reference": "slab", "distance_mm": 5.0})

    def test_a_missing_distance_is_refused_not_defaulted(self):
        runner = _part()
        with pytest.raises(GeometryError, match="distance_mm"):
            runner("catia_plane_offset", {"reference": "XY"})

    def test_an_unknown_plane_is_reported_with_what_does_exist(self):
        runner = _part()
        with pytest.raises(NamingError, match="constructed planes here: none"):
            runner.document.plane("nope")


class TestSketchingOnAConstructedPlane:
    def test_a_boss_can_be_built_on_top_of_a_pad(self):
        """Phase 2.4 in one test: the second feature of a real part.

        Volume against the closed form — 40×30×20 pad plus a Ø12×10 boss — so a boss
        placed at the wrong height, or on a plane facing the wrong way, fails rather
        than being recorded.
        """
        runner = _padded(_part("Housing"))
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 20.0, "name": "top"})
        runner("catia_sketch_create", {"support": "top", "name": "boss"})
        runner("catia_sketch_circle", {"sketch": "boss", "diameter_mm": 12.0})
        result = runner("catia_pad", {"sketch": "boss", "length_mm": 10.0})

        assert result["volume_mm3"] == pytest.approx(
            40.0 * 30.0 * 20.0 + math.pi * 36.0 * 10.0, abs=1e-4
        )
        box = result["bounding_box_mm"]
        assert box["size"][2] == pytest.approx(30.0, abs=1e-5), "pad 20 + boss 10"
        assert box["size"][0] == pytest.approx(40.0, abs=1e-5), "the boss is inside the pad"

    def test_the_boss_lands_above_the_pad_not_inside_it(self):
        """A plane built with the wrong sign puts the boss inside the pad, where it
        fuses invisibly and the volume is merely the pad's. Checked by the height."""
        runner = _padded(_part())
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 20.0, "name": "top"})
        runner("catia_sketch_create", {"support": "top", "name": "boss"})
        runner("catia_sketch_circle", {"sketch": "boss", "diameter_mm": 12.0})
        result = runner("catia_pad", {"sketch": "boss", "length_mm": 10.0})

        assert result["bounding_box_mm"]["max"][2] == pytest.approx(30.0, abs=1e-5)

    def test_sketching_on_an_unknown_support_says_what_is_available(self):
        runner = _part()
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 5.0, "name": "mid"})

        with pytest.raises(GeometryError) as caught:
            runner("catia_sketch_create", {"support": "nowhere", "name": "s"})

        message = str(caught.value)
        assert "mid" in message, "it should list the planes that do exist"
        assert "XY" in message
        assert "Phase 2.2" in message, "and name what sketching on a face still needs"

    def test_an_origin_plane_cannot_be_shadowed_by_a_constructed_one(self):
        """`XY` is vocabulary, not a name. `app.design.names` refuses it as a semantic
        name for this reason; the resolver checks the vocabulary first so the guarantee
        holds here too rather than being trusted from another module."""
        runner = _part()
        runner("catia_plane_offset", {"reference": "ZX", "distance_mm": 50.0, "name": "XY"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 10.0, "height_mm": 10.0})
        result = runner("catia_pad", {"sketch": "s", "length_mm": 4.0})

        # The real XY plane, not the 50 mm-away impostor.
        assert result["bounding_box_mm"]["min"][2] == pytest.approx(0.0, abs=1e-5)


class TestPoints:
    def test_a_point_lands_where_it_was_put(self):
        runner = _part()
        result = runner("catia_point_at", {"at": [3.0, -4.0, 5.0], "name": "p"})
        assert result["position_mm"] == pytest.approx([3.0, -4.0, 5.0], abs=TOL)

    def test_coordinates_can_be_measured_from_another_point(self):
        runner = _part()
        runner("catia_point_at", {"at": [10.0, 0.0, 0.0], "name": "base"})
        result = runner(
            "catia_point_at", {"at": [0.0, 0.0, 5.0], "reference": "base", "name": "up"}
        )
        assert result["position_mm"] == pytest.approx([10.0, 0.0, 5.0], abs=TOL)

    def test_between_defaults_to_the_midpoint(self):
        runner = _part()
        runner("catia_point_at", {"at": [0.0, 0.0, 0.0], "name": "a"})
        runner("catia_point_at", {"at": [30.0, 0.0, 0.0], "name": "b"})
        result = runner("catia_point_between", {"points": ["a", "b"], "name": "mid"})
        assert result["position_mm"] == pytest.approx([15.0, 0.0, 0.0], abs=TOL)

    def test_a_ratio_outside_zero_to_one_extrapolates(self):
        """Not clamped, on purpose. CATIA allows it and "one diameter past the flange"
        is a real thing to want."""
        runner = _part()
        runner("catia_point_at", {"at": [0.0, 0.0, 0.0], "name": "a"})
        runner("catia_point_at", {"at": [30.0, 0.0, 0.0], "name": "b"})
        result = runner(
            "catia_point_between", {"points": ["a", "b"], "ratio": 1.5, "name": "far"}
        )
        assert result["position_mm"] == pytest.approx([45.0, 0.0, 0.0], abs=TOL)

    def test_between_needs_exactly_two_points(self):
        runner = _part()
        runner("catia_point_at", {"at": [0.0, 0.0, 0.0], "name": "a"})
        with pytest.raises(GeometryError, match="exactly two point names"):
            runner("catia_point_between", {"points": ["a"]})


class TestPlaneThroughPoints:
    def test_the_normal_is_the_cross_product_of_the_two_edges(self):
        """Checked against the cross product by hand, not a recorded triple."""
        runner = _part()
        runner("catia_point_at", {"at": [0.0, 0.0, 0.0], "name": "a"})
        runner("catia_point_at", {"at": [30.0, 0.0, 0.0], "name": "b"})
        runner("catia_point_at", {"at": [0.0, 20.0, 10.0], "name": "c"})

        result = runner(
            "catia_plane_through_points", {"points": ["a", "b", "c"], "name": "tri"}
        )

        # (30,0,0) × (0,20,10) = (0, -300, 600), normalised.
        scale = math.sqrt(300.0**2 + 600.0**2)
        assert result["normal"] == pytest.approx(
            [0.0, -300.0 / scale, 600.0 / scale], abs=TOL
        )

    def test_three_collinear_points_are_refused(self):
        """They define a line, not a plane. OCCT's own failure for this arrives much
        later and says much less."""
        runner = _part()
        for index, x in enumerate((0.0, 10.0, 20.0)):
            runner("catia_point_at", {"at": [x, 0.0, 0.0], "name": f"p{index}"})

        with pytest.raises(GeometryError, match="collinear"):
            runner("catia_plane_through_points", {"points": ["p0", "p1", "p2"]})

    def test_two_points_are_refused(self):
        runner = _part()
        runner("catia_point_at", {"at": [0.0, 0.0, 0.0], "name": "a"})
        runner("catia_point_at", {"at": [1.0, 0.0, 0.0], "name": "b"})
        with pytest.raises(GeometryError, match="three point names"):
            runner("catia_plane_through_points", {"points": ["a", "b"]})


class TestAxisSystems:
    def test_z_is_the_cross_product_of_the_given_axes(self):
        runner = _part()
        runner("catia_point_at", {"at": [1.0, 2.0, 3.0], "name": "o"})
        result = runner(
            "catia_axis_system",
            {
                "origin": "o",
                "x_direction": [0.0, 1.0, 0.0],
                "y_direction": [0.0, 0.0, 1.0],
                "name": "frame",
            },
        )

        assert result["origin_mm"] == pytest.approx([1.0, 2.0, 3.0], abs=TOL)
        assert result["x_direction"] == pytest.approx([0.0, 1.0, 0.0], abs=TOL)
        assert result["z_direction"] == pytest.approx([1.0, 0.0, 0.0], abs=TOL)

    def test_with_no_directions_it_is_the_world_axes_moved(self):
        runner = _part()
        runner("catia_point_at", {"at": [5.0, 0.0, 0.0], "name": "o"})
        result = runner("catia_axis_system", {"origin": "o", "name": "frame"})

        assert result["x_direction"] == pytest.approx([1.0, 0.0, 0.0], abs=TOL)
        assert result["z_direction"] == pytest.approx([0.0, 0.0, 1.0], abs=TOL)

    def test_parallel_directions_are_refused(self):
        runner = _part()
        runner("catia_point_at", {"at": [0.0, 0.0, 0.0], "name": "o"})
        with pytest.raises(GeometryError, match="parallel"):
            runner(
                "catia_axis_system",
                {"origin": "o", "x_direction": [1.0, 0.0, 0.0], "y_direction": [2.0, 0.0, 0.0]},
            )

    def test_set_current_is_refused_rather_than_ignored(self):
        """It would change the frame every later operation is read in, and the design IR
        cannot record that — so the same plan would mean different things on the two
        backends. Ignoring the flag would be worse than refusing it."""
        runner = _part()
        runner("catia_point_at", {"at": [0.0, 0.0, 0.0], "name": "o"})
        with pytest.raises(OperationNotSupported, match="set_current"):
            runner("catia_axis_system", {"origin": "o", "set_current": True})


class TestAngledPlanes:
    def test_ninety_degrees_about_x_turns_xy_into_zx(self):
        """Rotating +90° about +X maps +Z to −Y, by the right-hand rule."""
        runner = _part()
        result = runner(
            "catia_plane_angle",
            {"reference": "XY", "axis": "X", "angle_deg": 90.0, "name": "t"},
        )
        assert result["normal"] == pytest.approx([0.0, -1.0, 0.0], abs=TOL)

    def test_hinging_about_an_axis_system_swings_the_origin_too(self):
        """A world axis only ever tilts a plane through the world origin. Hinging about
        an axis system's axis is the case that matters, and it moves the plane's origin
        as well as its normal."""
        runner = _part()
        runner("catia_point_at", {"at": [15.0, 0.0, 0.0], "name": "o"})
        runner(
            "catia_axis_system",
            {"origin": "o", "x_direction": [0.0, 1.0, 0.0], "y_direction": [0.0, 0.0, 1.0],
             "name": "frame"},
        )
        result = runner(
            "catia_plane_angle",
            {"reference": "XY", "axis": "frame.x", "angle_deg": 45.0, "name": "t"},
        )

        root = math.sqrt(0.5)
        assert result["normal"] == pytest.approx([root, 0.0, root], abs=1e-9)
        # The world origin swings about the line x=15 along +Y.
        assert result["origin_mm"] == pytest.approx(
            [15.0 - 15.0 * root, 0.0, 15.0 * root], abs=1e-9
        )

    def test_an_unknown_hinge_names_what_is_available(self):
        runner = _part()
        with pytest.raises(OperationNotSupported) as caught:
            runner("catia_plane_angle", {"reference": "XY", "axis": "some_edge",
                                         "angle_deg": 30.0})
        assert "Phase 2.2" in str(caught.value)


class TestSketchOriginIsLocalToItsPlane:
    """`origin` means "where the sketch's own (0, 0) sits on the support".

    A world-coordinate reading gives the same answer on `XY`, where the frame axes are
    the world axes, and a different one on `YZ` — so the inconsistency stays invisible
    until somebody sketches on the side of a part. It is also the rule `point_on`
    already applies to every 2D point drawn on a sketch.
    """

    def test_on_xy_the_two_readings_agree(self):
        from app.kernel.occt.sketching import frame_of

        location = frame_of("XY", (10.0, 5.0, 0.0)).Location()
        assert (location.X(), location.Y(), location.Z()) == pytest.approx(
            (10.0, 5.0, 0.0), abs=TOL
        )

    def test_on_yz_the_offset_follows_the_planes_own_axes(self):
        """`YZ` has local X along +Y and local Y along +Z, so a local (10, 5) is world
        (0, 10, 5) — not (10, 5, 0), which would be off the plane entirely."""
        from app.kernel.occt.sketching import frame_of

        location = frame_of("YZ", (10.0, 5.0, 0.0)).Location()
        assert (location.X(), location.Y(), location.Z()) == pytest.approx(
            (0.0, 10.0, 5.0), abs=TOL
        )

    def test_a_sketch_origin_on_a_constructed_plane_stacks_with_the_offset(self):
        runner = _part()
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 12.0, "name": "up"})
        runner(
            "catia_sketch_create",
            {"support": "up", "name": "s", "origin": [4.0, 0.0, 0.0]},
        )
        location = runner.document.sketch("s").frame().Location()

        assert (location.X(), location.Y(), location.Z()) == pytest.approx(
            (4.0, 0.0, 12.0), abs=TOL
        )


class TestSubEntitySelection:
    """Master plan 2.2 — `feature#selector`, the entities *of* one feature.

    `slab#top` is the test that matters: it returns a face that the plain word `top`
    can never return, because the slab's own top is the annulus under the boss while
    the part's top is the boss. Anything that quietly widened `of` to the whole part
    would pass every other test here and fail that one.
    """

    @staticmethod
    def _slab_and_boss():
        runner = _padded(_part("Housing"))
        runner("catia_plane_offset", {"reference": "XY", "distance_mm": 20.0, "name": "tp"})
        runner("catia_sketch_create", {"support": "tp", "name": "bs"})
        runner("catia_sketch_circle", {"sketch": "bs", "diameter_mm": 12.0})
        runner("catia_pad", {"sketch": "bs", "length_mm": 10.0, "name": "boss"})
        # The first pad is named by the operation, not by us; find it by tool order.
        return runner

    def test_a_features_own_top_is_not_the_parts_top(self):
        from app.kernel.occt.classify import face_area_mm2
        from app.kernel.occt.selectors import select_faces

        runner = self._slab_and_boss()
        shape, document = runner.document.shape, runner.document
        slab = next(iter(document)).name

        part_top = select_faces(shape, "top", document=document)
        slab_top = select_faces(shape, f"{slab}#top", document=document)
        boss_top = select_faces(shape, "boss#top", document=document)

        assert face_area_mm2(part_top[0]) == pytest.approx(math.pi * 36.0, abs=1e-4)
        assert face_area_mm2(boss_top[0]) == pytest.approx(math.pi * 36.0, abs=1e-4)
        assert face_area_mm2(slab_top[0]) == pytest.approx(
            1200.0 - math.pi * 36.0, abs=1e-4
        ), "the slab's own top is the annulus the boss stands on"

    def test_a_contribution_is_carried_through_later_features(self):
        """The slab's top face was *replaced* by the fuse. Holding the old handle would
        make `slab#top` resolve to nothing while looking healthy — the topological
        naming problem in miniature."""
        from app.kernel.occt.topology import faces

        runner = self._slab_and_boss()
        current = faces(runner.document.shape)

        for feature in runner.document:
            owned = feature.contributed_faces
            assert owned, f"{feature.name} recorded no contribution"
            assert all(
                any(face.IsSame(live) for live in current) for face in owned
            ), f"{feature.name} holds a face that is no longer part of the shape"

    def test_a_feature_that_contributed_nothing_visible_matches_nothing(self):
        """The boss's underside was absorbed into the slab. Asking for it must come back
        empty rather than falling through to the part's bottom face."""
        from app.kernel.errors import GeometryError
        from app.kernel.occt.selectors import select_faces

        runner = self._slab_and_boss()
        with pytest.raises(GeometryError, match="belonging to boss"):
            select_faces(runner.document.shape, "boss#bottom", document=runner.document)

    def test_the_predicate_form_is_the_same_thing_written_out(self):
        from app.kernel.occt.selectors import select_faces

        runner = self._slab_and_boss()
        shape, document = runner.document.shape, runner.document

        short = select_faces(shape, "boss#top", document=document)
        long = select_faces(
            shape, {"of": "boss", "axis": "z", "side": "max"}, document=document
        )
        assert len(short) == len(long) == 1
        assert short[0].IsSame(long[0])

    def test_a_malformed_reference_says_how_it_is_written(self):
        from app.kernel.errors import GeometryError
        from app.kernel.occt.selectors import parse_sub_entity

        with pytest.raises(GeometryError, match="feature#selector"):
            parse_sub_entity("#top")
        with pytest.raises(GeometryError, match="more than one"):
            parse_sub_entity("a#b#c")
        with pytest.raises(GeometryError, match="not a selector word"):
            parse_sub_entity("boss#sideways")

    def test_selecting_a_features_entities_needs_the_document(self):
        """Without it there is nothing to resolve the feature name against, and guessing
        the whole part would silently change what was asked for."""
        from app.kernel.errors import GeometryError
        from app.kernel.occt.selectors import select_faces

        runner = self._slab_and_boss()
        with pytest.raises(GeometryError, match="needs the document"):
            select_faces(runner.document.shape, "boss#top")


class TestEveryOperationReportsItsOwnFaces:
    """2.2 completed: what "its own faces" means differs per operation, and each answer
    is checked against geometry rather than assumed.

    The rule the five judgements follow: a face the operation **created** is always its
    own; a face it **altered** is its own only when altering it was the point. So a
    draft claims the walls it tilted and a fillet does not claim the neighbours it
    trimmed — those stay with whatever built them.
    """

    def test_no_geometry_operation_leaves_its_contribution_unrecorded(self):
        """An unrecorded operation refuses a `#` selector, which is honest but useless.
        This is the check that keeps the list from quietly growing again."""
        runner = _padded(_part("All"))
        runner("catia_sketch_create", {"support": "XY", "name": "p"})
        runner("catia_sketch_circle", {"sketch": "p", "diameter_mm": 8.0, "at": [10.0, 5.0]})
        runner("catia_pocket", {"sketch": "p", "depth_mm": 6.0, "name": "hole"})
        runner("catia_fillet", {"radius_mm": 2.0, "name": "round",
                                "edges": {"type": "edge", "parallel_to": "z",
                                          "longer_than_mm": 10.0}})
        runner("catia_translate", {"vector": [1.0, 0.0, 0.0], "name": "shift"})

        unrecorded = [
            f.tool for f in runner.document if f.contributed_faces is None
        ]
        assert unrecorded == []

    def test_a_fillet_owns_its_blend_faces_and_nothing_else(self):
        """Four quarter-cylinders of radius 3 over a 20 mm height — checked against
        4 × ¼ × 2πr × h, so claiming the trimmed neighbours too would fail."""
        from app.kernel.occt.classify import face_area_mm2, face_surface_type
        from app.kernel.occt.selectors import select_faces

        runner = _padded(_part())
        runner("catia_fillet", {"radius_mm": 3.0, "name": "round",
                                "edges": {"type": "edge", "parallel_to": "z"}})

        blends = select_faces(runner.document.shape, {"of": "round"},
                              document=runner.document)
        assert len(blends) == 4
        assert all(face_surface_type(f) == "Cylinder" for f in blends)
        assert sum(face_area_mm2(f) for f in blends) == pytest.approx(
            4 * (2 * math.pi * 3.0 / 4) * 20.0, abs=1e-6
        )

    def test_a_draft_owns_the_walls_it_was_asked_to_tilt_not_the_blends(self):
        """OCCT propagates a taper along tangent neighbours, so drafting four walls of a
        filleted block tilts eight faces. The four blends are a side effect and belong to
        the fillet, exactly as a fillet's trimmed neighbours belong to the pad."""
        from app.kernel.occt.interrogate import analyse_draft
        from app.kernel.occt.selectors import select_faces

        runner = _padded(_part())
        runner("catia_fillet", {"radius_mm": 2.0, "name": "round",
                                "edges": {"type": "edge", "parallel_to": "z",
                                          "longer_than_mm": 10.0}})
        runner("catia_draft", {"faces": {"type": "face", "parallel_to": "z",
                                         "planar": True},
                               "angle_deg": 2.0, "neutral": "XY", "name": "taper"})

        tilted = [
            f for f in analyse_draft(runner.document.shape, [0, 0, 1]).faces
            if abs(f.magnitude_deg - 2.0) < 1e-6
        ]
        assert len(tilted) == 8, "the taper propagates to the blends"
        assert len(select_faces(runner.document.shape, {"of": "taper"},
                                document=runner.document)) == 4, (
            "but the draft only claims the four it was asked for"
        )

    def test_a_shell_owns_its_inner_surface_on_both_paths(self):
        """Open and closed shells reach their inner faces through different algorithms,
        so both are checked against the areas they must have."""
        from app.kernel.occt.classify import face_area_mm2
        from app.kernel.occt.selectors import select_faces

        opened = _padded(_part())
        opened("catia_shell", {"thickness_mm": 2.0, "name": "wall",
                               "faces": {"type": "face", "axis": "z", "side": "max"}})
        walls = select_faces(opened.document.shape, {"of": "wall"},
                             document=opened.document)
        # five inner walls (936 + 2×648 + 2×468) plus the 264 mm² rim
        assert sum(face_area_mm2(f) for f in walls) == pytest.approx(3432.0, abs=1e-6)

        closed = _padded(_part())
        closed("catia_shell", {"thickness_mm": 2.0, "name": "wall"})
        inner = select_faces(closed.document.shape, {"of": "wall"},
                             document=closed.document)
        # the six faces of a 36 × 26 × 16 void
        assert sum(face_area_mm2(f) for f in inner) == pytest.approx(3856.0, abs=1e-6)

    def test_contributions_survive_two_later_operations(self):
        """The pad's faces are re-mapped through the fillet and then the draft. A stale
        handle would make `slab#...` resolve to nothing while looking healthy."""
        from app.kernel.occt.topology import faces

        runner = _padded(_part())
        runner("catia_fillet", {"radius_mm": 2.0, "name": "round",
                                "edges": {"type": "edge", "parallel_to": "z",
                                          "longer_than_mm": 10.0}})
        runner("catia_draft", {"faces": {"type": "face", "parallel_to": "z",
                                         "planar": True},
                               "angle_deg": 2.0, "neutral": "XY", "name": "taper"})

        current = faces(runner.document.shape)
        for feature in runner.document:
            assert all(
                any(face.IsSame(live) for live in current)
                for face in feature.contributed_faces or []
            ), f"{feature.name} holds a face that is no longer in the part"
