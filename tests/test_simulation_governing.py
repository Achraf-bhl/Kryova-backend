"""The response carries the peak a verdict rests on, and which number it is (ROAD_TO_10 8.3).

The rule lives in `StaticResult`; these pin that the *response* reports it rather than leaving
a client to reproduce it from the two raw peaks.

Written on Linux and not run (the user's rule; Windows runs them).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.schemas.simulation import SimulationRead
from app.solve.types import StaticResult


def _read(result: dict[str, Any] | None, analysis: str = "linear-static") -> SimulationRead:
    return SimulationRead.model_validate(
        {
            "id": "sim-1",
            "project_id": "proj-1",
            "geometry_version_id": "geo-1",
            "status": "succeeded",
            "solver": "linear-static",
            "load_case": None,
            "thermal_case": None,
            "element_size_mm": 4.0,
            "element_order": 2,
            "grids": 1,
            "analysis": analysis,
            "thickness_mm": None,
            "mesh_stats": None,
            "result": result,
            "fields_media_id": None,
            "error": None,
            "created_at": datetime.now(UTC),
            "started_at": None,
            "finished_at": None,
        }
    )


def _static(**over: Any) -> dict[str, Any]:
    base = StaticResult(
        max_displacement_mm=0.1,
        max_displacement_node=1,
        max_von_mises_mpa=41.0,
        max_von_mises_element=2,
        factor_of_safety=6.0,
        yields=False,
        mass_kg=1.0,
        volume_mm3=100.0,
        node_count=10,
        element_count=5,
        solve_seconds=0.1,
    ).model_dump()
    base.update(over)
    return base


class TestTheGoverningPeak:
    def test_the_nodal_surface_value_governs_when_it_is_larger(self) -> None:
        read = _read(_static(max_von_mises_surface_mpa=59.8, factor_of_safety_surface=4.2))

        assert read.governing is not None
        assert read.governing.peak_mpa == 59.8
        assert read.governing.basis == "nodal, at the surface"
        assert read.governing.factor_of_safety == 4.2

    def test_the_element_value_governs_when_it_is_larger(self) -> None:
        read = _read(_static(max_von_mises_surface_mpa=30.0, factor_of_safety_surface=9.0))

        assert read.governing is not None
        assert read.governing.peak_mpa == 41.0
        assert read.governing.basis == "element centroid"
        assert read.governing.factor_of_safety == 6.0

    def test_no_nodal_tensor_says_so_in_the_basis(self) -> None:
        read = _read(_static())

        assert read.governing is not None
        assert read.governing.peak_mpa == 41.0
        assert "no nodal tensor" in read.governing.basis

    def test_it_is_in_the_serialised_response(self) -> None:
        read = _read(_static(max_von_mises_surface_mpa=59.8, factor_of_safety_surface=4.2))

        assert read.model_dump()["governing"]["peak_mpa"] == 59.8

    def test_a_result_that_is_not_a_stress_has_none_rather_than_a_zero(self) -> None:
        read = _read({"min_temperature_k": 290.0, "max_temperature_k": 330.0}, "thermal-conduction")

        assert read.governing is None

    def test_a_run_with_no_result_has_none(self) -> None:
        assert _read(None).governing is None
