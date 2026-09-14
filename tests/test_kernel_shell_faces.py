"""`catia_shell_faces` on the open kernel, and a shell whose walls meet (master plan E1.3).

**Why the open kernel needed it.** The dispatcher's schema for `catia_shell`
takes a thickness and `outward`, and nothing to open. Its own summary sends the
agent to `catia_shell_faces` "to leave it open — which is nearly always what is
wanted", and until 2026-09-14 the open kernel did not have that operation. So an
agent on the open kernel could hollow a part only sealed, and an enclosure,
which is what a shell is for, could not be built at all.

Every volume here is checked against a hand calculation on a 40x30x20 box, not
against a recorded number.

Offline: no seat, no database.
"""

from __future__ import annotations

import math

import pytest

from app.kernel import available
from app.kernel.errors import GeometryError

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)

BOX_MM3 = 40.0 * 30.0 * 20.0


def _box():  # type: ignore[no-untyped-def]
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "Box"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner("catia_sketch_rectangle", {"sketch": "outline", "width_mm": 40.0, "height_mm": 30.0})
    runner("catia_pad", {"sketch": "outline", "length_mm": 20.0})
    return runner


def _cavity(x: float, y: float, z: float) -> float:
    return BOX_MM3 - x * y * z


class TestItHollowsThePartOpen:
    def test_an_open_top_leaves_five_walls(self) -> None:
        built = _box()("catia_shell_faces", {"thickness_mm": 2.0, "open_faces": ["top"]})

        assert built["volume_mm3"] == pytest.approx(_cavity(36.0, 26.0, 18.0))
        assert built["feature"] == "ShellFaces.1"

    def test_two_open_faces_leave_a_tube(self) -> None:
        built = _box()("catia_shell_faces", {"thickness_mm": 2.0, "open_faces": ["top", "bottom"]})

        assert built["volume_mm3"] == pytest.approx(_cavity(36.0, 26.0, 20.0))

    def test_one_wall_can_be_thicker_than_the_rest(self) -> None:
        """The one thing this adds over catia_shell. With the right wall at 5 mm
        the cavity is 40 - 2 - 5 = 33 long."""
        built = _box()(
            "catia_shell_faces",
            {
                "thickness_mm": 2.0,
                "open_faces": ["top"],
                "face_thicknesses": [{"face": "right", "thickness_mm": 5.0}],
            },
        )

        assert built["volume_mm3"] == pytest.approx(_cavity(33.0, 26.0, 18.0))

    def test_outward_walls_are_the_box_grown_by_the_thickness_less_the_box(self) -> None:
        """An outward wall rounds the convex edges, so its volume is the box's
        Minkowski sum with a 2 mm ball, cut off at the open top, less the box:
        V + S·r + (π r²/4)·Σedges + (4/3)π r³, minus the cap above z = 20."""
        r = 2.0
        grown = BOX_MM3 + 5200.0 * r + (math.pi * r * r / 4) * 360.0 + (4 / 3) * math.pi * r**3
        cap = 1200.0 * r + (math.pi * r * r / 4) * 140.0 + 4 * (math.pi * r**3 / 6)

        built = _box()(
            "catia_shell_faces", {"thickness_mm": r, "outward": True, "open_faces": ["top"]}
        )

        assert built["volume_mm3"] == pytest.approx(grown - cap - BOX_MM3, rel=1e-9)

    def test_with_no_override_it_builds_what_catia_shell_builds(self) -> None:
        """Both are passed to OCCT the same way, so the two cannot drift."""
        for outward in (False, True):
            opened = _box()(
                "catia_shell",
                {"thickness_mm": 2.0, "outward": outward, "faces": {"axis": "z", "side": "max"}},
            )
            faces = _box()(
                "catia_shell_faces",
                {"thickness_mm": 2.0, "outward": outward, "open_faces": ["top"]},
            )
            assert faces["volume_mm3"] == pytest.approx(opened["volume_mm3"], rel=1e-12)

    def test_its_thickness_is_a_parameter_the_part_rebuilds_from(self) -> None:
        runner = _box()
        runner("catia_shell_faces", {"thickness_mm": 2.0, "open_faces": ["top"]})

        rebuilt = runner("catia_set_parameter", {"name": "ShellFaces.1\\thickness_mm", "value": 3.0})

        assert rebuilt["volume_mm3"] == pytest.approx(_cavity(34.0, 24.0, 17.0))


class TestItRefusesWhatIsNotAnOpenShell:
    def test_no_open_face_is_catia_shell_and_it_says_so(self) -> None:
        with pytest.raises(GeometryError) as caught:
            _box()("catia_shell_faces", {"thickness_mm": 2.0})

        assert "that is catia_shell" in str(caught.value)

    def test_a_face_removed_and_thickened_is_refused(self) -> None:
        with pytest.raises(GeometryError) as caught:
            _box()(
                "catia_shell_faces",
                {
                    "thickness_mm": 2.0,
                    "open_faces": ["top"],
                    "face_thicknesses": [{"face": "top", "thickness_mm": 5.0}],
                },
            )

        assert "removed face has no wall" in str(caught.value)


class TestAShellWhoseWallsMeetIsNotAShell:
    """Measured 2026-09-14: OCCT reports `IsDone()` for both of these."""

    @pytest.mark.parametrize("tool", ["catia_shell", "catia_shell_faces"])
    def test_walls_that_exactly_meet_are_refused(self, tool: str) -> None:
        """Two 15 mm walls fill the 30 mm width. OCCT answers 20,888.9 mm3, an
        invalid shape."""
        with pytest.raises(GeometryError) as caught:
            _box()(tool, _opened(tool, 15.0))

        assert "invalid" in str(caught.value)

    @pytest.mark.parametrize("tool", ["catia_shell", "catia_shell_faces"])
    def test_walls_thicker_than_that_are_refused_rather_than_returning_the_box(
        self, tool: str
    ) -> None:
        """At 16 mm OCCT hands back the original box, six faces, as the shell."""
        with pytest.raises(GeometryError) as caught:
            _box()(tool, _opened(tool, 16.0))

        assert "unchanged" in str(caught.value)

    @pytest.mark.parametrize("tool", ["catia_shell", "catia_shell_faces"])
    def test_the_thickest_wall_that_leaves_a_cavity_still_builds(self, tool: str) -> None:
        """The guard must not refuse a real shell. At 14.99 mm the cavity is
        10.02 x 0.02 x 5.01, and the volume matches it."""
        built = _box()(tool, _opened(tool, 14.99))

        assert built["volume_mm3"] == pytest.approx(_cavity(10.02, 0.02, 5.01), rel=1e-9)


def _opened(tool: str, thickness: float) -> dict[str, object]:
    if tool == "catia_shell":
        return {"thickness_mm": thickness, "faces": {"axis": "z", "side": "max"}}
    return {"thickness_mm": thickness, "open_faces": ["top"]}
