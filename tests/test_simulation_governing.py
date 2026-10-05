"""The response carries the peak a verdict rests on, and which number it is (ROAD_TO_10 8.3).

The rule lives in `StaticResult`; these pin that the *response* reports it rather than leaving
a client to reproduce it from the two raw peaks.

Written on Linux and not run (the user's rule; Windows runs them).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai.tools import ToolBox
from app.models import GeometryVersion, JobStatus, Project, SimulationJob, User
from app.schemas.simulation import SimulationRead
from app.solve.types import StaticResult
from tests.test_agent import LOAD_CASE, geometry, project, user  # noqa: F401 - fixtures


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


class TestTheAgentIsHandedTheSamePeak:
    """The agent reads a run through its tools, not through `SimulationRead`, and was handed the
    raw `result` with both peaks and no `governing`. On the seat, 2026-10-05, it quoted the
    8.39 MPa centroid value as "the peak" of a 50 x 30 x 10 cantilever whose surface value was
    9.64 MPa against a closed-form 10.0 -- the gate G1 failure, one layer up."""

    @pytest.fixture
    def finished(
        self, db_session: Session, project: Project, geometry: GeometryVersion  # noqa: F811
    ) -> str:
        job = SimulationJob(
            project_id=project.id,
            geometry_version_id=geometry.id,
            status=JobStatus.SUCCEEDED,
            solver="linear-static",
            load_case=LOAD_CASE,
            result=_static(
                max_von_mises_mpa=8.39,
                factor_of_safety=44.1,
                max_von_mises_surface_mpa=9.64,
                factor_of_safety_surface=38.4,
            ),
        )
        db_session.add(job)
        db_session.flush()
        return job.id

    def _box(self, db_session: Session, user: User, project: Project) -> ToolBox:  # noqa: F811
        return ToolBox(db=db_session, user=user, project_id=project.id)

    @pytest.mark.parametrize("tool", ["get_simulation", "wait_for_simulation"])
    def test_reading_a_run_carries_the_governing_peak_and_its_basis(
        self, tool: str, db_session: Session, user: User, project: Project, finished: str  # noqa: F811
    ) -> None:
        read = self._box(db_session, user, project).call(
            tool, {"simulation_id": finished}, allow_mutations=False
        )
        assert read["governing"] == {
            "peak_mpa": 9.64,
            "basis": "nodal, at the surface",
            "factor_of_safety": 38.4,
        }

    def test_the_list_carries_the_governing_peak_beside_the_centroid_value(
        self, db_session: Session, user: User, project: Project, finished: str  # noqa: F811
    ) -> None:
        listed = self._box(db_session, user, project).call(
            "list_simulations", {}, allow_mutations=False
        )
        (row,) = listed["simulations"]
        assert row["governing_peak_mpa"] == 9.64
        assert row["max_von_mises_mpa"] == 8.39
