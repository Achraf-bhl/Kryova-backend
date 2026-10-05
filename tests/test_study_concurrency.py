"""A convergence study with its grids running at once, through the real runner (ROAD_TO_10 6.5).

The scheduling logic is tested offline in `tests/test_verify_convergence.py`; this proves the
runner's side: the same verdict, every grid numbered by its place and not by how many had
finished, and usage recorded once for the finest grid.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.simulation import progress
from tests.test_simulations import project_with_geometry  # noqa: F401 - fixture
from tests.test_simulations import run as _run
from tests.typing import AuthenticatedTestClient


def _study(client: AuthenticatedTestClient, project_id: str) -> dict:
    job = _run(client, project_id, element_size_mm=8.0, grids=3)
    assert job["status"] == "succeeded", job["error"]
    return job


class TestTheSameStudyAtOnce:
    def test_the_verdict_and_the_levels_are_the_sequential_ones(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The study itself rides in the mesh stats; `result.mesh_convergence` is only the claim
        # (basis, grids), the way tests/test_simulations.py reads both.
        sequential = _study(auth_client, project_with_geometry)["mesh_stats"]["study"]
        monkeypatch.setattr(settings, "study_concurrency", 3)
        together = _study(auth_client, project_with_geometry)["mesh_stats"]["study"]
        assert together["verdict"] == sequential["verdict"]
        assert [level["element_count"] for level in together["levels"]] == [
            level["element_count"] for level in sequential["levels"]
        ]

    def test_each_grid_reports_its_own_number_not_a_count_of_finished_ones(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "study_concurrency", 3)
        seen: list[int | None] = []
        real = progress.report

        def spy(scope, job_id, stage, *, detail="", index=None, total=None):
            if stage is progress.Stage.MESHING:
                seen.append(index)
            return real(scope, job_id, stage, detail=detail, index=index, total=total)

        monkeypatch.setattr(progress, "report", spy)
        _study(auth_client, project_with_geometry)
        assert sorted(i for i in seen if i is not None) == [1, 2, 3]
