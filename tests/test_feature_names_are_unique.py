"""Two unnamed features of the same kind are two features.

Measured end to end on 2026-09-05: a flange built as a pad, a bore pocket, a
four-hole pocket and a corner fillet reported its feature list as
`[Pad.1, Pocket.1, Fillet.1]`. Three features for four operations. The second
pocket had vanished.

The cause is one line and the rule it collides with. Every operation defaulted
an unnamed feature to its own tool's word — `pocket`, `hole`, `fillet` — and
`Document.add_feature` treats a name it has already seen as **a regeneration of
that feature**, which is required when a compiled plan is rebuilt against the
same document. So the second pocket rewrote the first one's labels rather than
becoming a feature of its own.

The geometry was still right, which is exactly why this survived: a cut lands on
the part's shape whether or not the feature bookkeeping is sane, and every
volume, mass and face count came back correct. What was wrong was the *names* —
and names are what a fillet scoped to a feature, a pattern seeded on one, and
every `feature#selector` resolve against. An agent almost never passes a name, so
this was the ordinary path and not an edge case.
"""

from __future__ import annotations

import math

import pytest


def _flange(runner):
    """The part that exposed it, in the order the agent actually built it."""
    runner("catia_new_part", {"name": "Flange"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner(
        "catia_sketch_rectangle",
        {"sketch": "outline", "width_mm": 100.0, "height_mm": 100.0},
    )
    runner("catia_pad", {"sketch": "outline", "length_mm": 12.0})
    runner("catia_sketch_create", {"support": "XY", "name": "bore"})
    runner("catia_sketch_circle", {"sketch": "bore", "diameter_mm": 40.0})
    runner("catia_pocket", {"sketch": "bore", "through_all": True})
    runner("catia_sketch_create", {"support": "XY", "name": "holes"})
    for angle in (45.0, 135.0, 225.0, 315.0):
        runner(
            "catia_sketch_circle",
            {"sketch": "holes", "diameter_mm": 9.0, "at_radius_mm": 35.0,
             "at_angle_deg": angle},
        )
    runner("catia_pocket", {"sketch": "holes", "through_all": True})
    return runner


class TestTheFlangeReportsEveryFeatureItHas:
    def test_two_pockets_are_two_features(self) -> None:
        from app.kernel import OcctRunner

        runner = _flange(OcctRunner())

        names = runner("catia_list_features", {})["features"]

        assert names.count("Pocket.1") == 1
        assert "Pocket.2" in names, f"the second pocket is missing from {names}"

    def test_the_count_matches_the_operations_that_ran(self) -> None:
        from app.kernel import OcctRunner

        runner = _flange(OcctRunner())
        runner("catia_fillet", {"radius_mm": 8.0, "edges": "vertical"})

        assert runner("catia_list_features", {})["features"] == [
            "Pad.1",
            "Pocket.1",
            "Pocket.2",
            "Fillet.1",
        ]

    def test_each_operation_reports_its_own_name_back(self) -> None:
        """`Created(feature)` is bound from this — a plan that binds the same
        name twice cannot address either of them afterwards."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0,
                                          "height_mm": 60.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 10.0})
        runner("catia_sketch_create", {"support": "XY", "name": "a"})
        runner("catia_sketch_circle", {"sketch": "a", "diameter_mm": 10.0})
        first = runner("catia_pocket", {"sketch": "a", "through_all": True})["feature"]
        runner("catia_sketch_create", {"support": "XY", "name": "b"})
        runner("catia_sketch_circle", {"sketch": "b", "diameter_mm": 6.0,
                                       "at_radius_mm": 20.0, "at_angle_deg": 0.0})
        second = runner("catia_pocket", {"sketch": "b", "through_all": True})["feature"]

        assert first != second

    def test_two_unnamed_fillets_do_not_collide_either(self) -> None:
        """The same bug in the operation an agent repeats most often."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 60.0,
                                          "height_mm": 40.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 20.0})

        runner("catia_fillet", {"radius_mm": 4.0, "edges": "vertical"})
        runner("catia_fillet", {"radius_mm": 2.0, "edges": "horizontal"})

        names = runner("catia_list_features", {})["features"]
        assert names.count("Fillet.1") == 1
        assert "Fillet.2" in names


class TestTheSecondFeatureIsSeparatelyAddressable:
    """The consequence that matters, not just the bookkeeping."""

    def test_a_fillet_can_be_scoped_to_the_second_pocket(self) -> None:
        from app.kernel import OcctRunner

        runner = _flange(OcctRunner())

        scoped = runner(
            "catia_fillet", {"radius_mm": 0.5, "edges": "all", "feature": "Pocket.2"}
        )

        assert scoped["volume_mm3"] > 0.0

    def test_scoping_to_each_pocket_reaches_different_geometry(self) -> None:
        """If the names collided, both calls would round the same edges and the
        two volumes would be identical — green, and meaningless."""
        from app.kernel import OcctRunner

        bore = _flange(OcctRunner())
        holes = _flange(OcctRunner())

        on_bore = bore(
            "catia_fillet", {"radius_mm": 1.0, "edges": "all", "feature": "Pocket.1"}
        )["volume_mm3"]
        on_holes = holes(
            "catia_fillet", {"radius_mm": 1.0, "edges": "all", "feature": "Pocket.2"}
        )["volume_mm3"]

        assert on_bore != pytest.approx(on_holes)


class TestNamingBehaviourThatMustNotChange:
    def test_a_design_that_names_its_features_keeps_those_names(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 20.0,
                                          "height_mm": 20.0})
        runner("catia_pad", {"sketch": "s", "length_mm": 5.0, "name": "plate.body"})

        assert runner.document.feature("plate.body").tool == "catia_pad"

    def test_rebuilding_a_named_feature_is_still_a_regeneration(self) -> None:
        """The rule the fix must not break: one label triple per name, forever."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 20.0,
                                          "height_mm": 20.0})
        document = runner.document
        first = document.add_feature("plate.body", "catia_pad")
        again = document.add_feature("plate.body", "catia_pad")

        assert again is first

    def test_a_blank_name_is_treated_as_no_name(self) -> None:
        """A model that sends `name: ""` must not create a feature called nothing."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "P"})
        runner("catia_sketch_create", {"support": "XY", "name": "s"})
        runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 20.0,
                                          "height_mm": 20.0})

        built = runner("catia_pad", {"sketch": "s", "length_mm": 5.0, "name": "   "})

        assert built["feature"] == "Pad.1"

    def test_the_geometry_was_never_the_thing_that_was_wrong(self) -> None:
        """Stated so nobody later 'fixes' the volume too: it was already right,
        which is exactly why the naming defect survived four verification runs."""
        from app.kernel import OcctRunner

        runner = _flange(OcctRunner())
        built = runner("catia_fillet", {"radius_mm": 8.0, "edges": "vertical"})

        corner_loss = 4.0 * 8.0**2 * (1.0 - math.pi / 4.0)
        expected = (
            (100.0 * 100.0 - corner_loss) * 12.0
            - math.pi * 20.0**2 * 12.0
            - 4.0 * math.pi * 4.5**2 * 12.0
        )
        assert built["volume_mm3"] == pytest.approx(expected, abs=1e-6)
