"""Pre-flight limits on a meshing request.

Offline: these read a recorded bounding box, never a CAD file, which is the
whole point -- the check has to cost nothing so it can run before gmsh does.
"""

import pytest

from app.core.config import settings
from app.mesh.types import MeshError
from app.simulation.limits import (
    bounding_box_size,
    check_mesh_request,
    estimate_element_count,
)

# A 20 x 20 x 60 mm bar: diagonal ~= 66.3 mm.
BAR = {"bounding_box": {"min": [0, 0, 0], "max": [20, 20, 60], "size": [20, 20, 60]}}


class TestBoundingBoxSize:
    def test_reads_the_recorded_extents(self) -> None:
        assert bounding_box_size(BAR) == (20.0, 20.0, 60.0)

    @pytest.mark.parametrize(
        "stats",
        [
            None,
            {},
            {"bounding_box": None},
            {"bounding_box": {"size": [1, 2]}},
            {"bounding_box": {"size": ["a", "b", "c"]}},
            {"bounding_box": {"size": [1, 2, float("nan")]}},
        ],
    )
    def test_anything_unusable_reads_as_unknown(self, stats) -> None:
        # Geometry uploaded before its format had an inspector has no box, and
        # "cannot check" must never become "reject".
        assert bounding_box_size(stats) is None


class TestElementSizeFloor:
    def test_an_absurdly_fine_size_is_refused_before_meshing(self) -> None:
        with pytest.raises(MeshError, match="finer than"):
            check_mesh_request(BAR, element_size_mm=0.001)

    def test_the_message_names_a_size_that_would_work(self) -> None:
        with pytest.raises(MeshError) as caught:
            check_mesh_request(BAR, element_size_mm=0.001)
        assert "Use at least" in str(caught.value)

    def test_a_size_just_above_the_floor_is_allowed(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "max_elements", 10**12)
        diagonal = (20**2 + 20**2 + 60**2) ** 0.5
        check_mesh_request(BAR, diagonal / settings.max_elements_along_diagonal * 1.01)


class TestElementCountEstimate:
    def test_a_cube_of_side_h_holds_about_six_tets(self) -> None:
        assert estimate_element_count(1.0**3, 1.0) == pytest.approx(6.0)

    def test_a_request_that_would_blow_the_limit_is_refused(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "max_elements", 1_000)
        with pytest.raises(MeshError, match="over the 1,000 limit"):
            check_mesh_request(BAR, element_size_mm=1.0)

    def test_the_message_says_what_to_change(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "max_elements", 1_000)
        with pytest.raises(MeshError) as caught:
            check_mesh_request(BAR, element_size_mm=1.0)
        assert "Increase element_size_mm" in str(caught.value)

    def test_a_reasonable_request_passes(self) -> None:
        check_mesh_request(BAR, element_size_mm=10.0)

    def test_a_recorded_solid_volume_beats_the_bounding_box(self, monkeypatch) -> None:
        # A bracket occupies a fraction of its box. Estimating from the box
        # alone would refuse a mesh it can comfortably produce.
        # 2 mm elements fill the 24,000 mm^3 box with ~18,000 tets, but the
        # 2,000 mm^3 of actual metal in it with only ~1,500.
        monkeypatch.setattr(settings, "max_elements", 10_000)
        hollow = {**BAR, "volume_mm3": 2_000.0}
        with pytest.raises(MeshError):
            check_mesh_request(BAR, element_size_mm=2.0)
        check_mesh_request(hollow, element_size_mm=2.0)


class TestSilentCases:
    def test_an_automatic_element_size_is_never_refused(self) -> None:
        # It is derived from the same bounding box, so it is safe by
        # construction.
        check_mesh_request(BAR, element_size_mm=None)

    def test_geometry_with_no_bounding_box_is_not_refused(self) -> None:
        check_mesh_request({"schema": "AP214"}, element_size_mm=0.0001)

    def test_a_zero_extent_box_is_not_refused(self) -> None:
        flat = {"bounding_box": {"size": [0, 0, 0]}}
        check_mesh_request(flat, element_size_mm=0.0001)
