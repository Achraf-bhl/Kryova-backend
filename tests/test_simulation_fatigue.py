"""A fatigue check reaches the product: the service, the route and the agent tool (E8.6).

Three layers, one function. `app.simulation.fatigue.assess_run` is tested offline on
synthetic archives whose stress is known by construction, so every number asserted is
arithmetic. Then the route, end to end through a real mesh and solve: the axial pull
from `tests/test_simulations.py` is 20 MPa everywhere (σ = F/A), so a signal of
[0, 1, −1, 0] must come back as a history of [0, 20, −20, 0]. Then the agent, through
`run_agent` with a scripted provider, because a test of `dispatch` proves the tool and
not the path (CLAUDE.md, *Testing* item 8).

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that
the Windows machine runs the tests.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.provider import AssistantTurn, ToolCall
from app.fatigue import available
from app.media import MediaService
from app.models import Conversation, MediaKind, Project, SimulationJob, User
from app.models.simulation import JobStatus
from app.schemas.fatigue import FatigueRequest
from app.simulation.fatigue import (
    STRESS_ANALYSES,
    TENSOR_KEY,
    FatigueRefused,
    assess_run,
    refuse_unless_assessable,
)
from app.verify.standards import NOT_VALIDATED
from tests import test_agent as _agent
from tests.test_simulations import BOX, FORCE, load_case
from tests.test_simulations import project_with_geometry as _project_with_geometry
from tests.typing import AuthenticatedTestClient

ScriptedProvider = _agent.ScriptedProvider
_toolbox = _agent._toolbox
user = _agent.user
project = _agent.project
conversation = _agent.conversation
geometry = _agent.geometry
project_with_geometry = _project_with_geometry

needs_pylife = pytest.mark.skipif(
    not available(), reason="pyLife is not installed; the damage is federated to it"
)

SOURCE = "test fixture, not a real material"
SIGNAL = [0.0, 1.0, -1.0, 0.0]
CONVERGENCE = {"basis": "single-grid", "grids": 1, "converged": False}


def _request(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "node": 0,
        "signal": SIGNAL,
        "signal_source": "a fully reversed block stated by hand",
        "curve": {
            "slope_k1": 5.0,
            "knee_cycles": 1.0e6,
            "knee_amplitude_mpa": 100.0,
            "source": SOURCE,
        },
        "factors": [
            {"name": "surface", "value": 1.0, "source": SOURCE},
            {"name": "size", "value": 1.0, "source": SOURCE},
        ],
        "design_life_blocks": 1000.0,
    }
    body.update(overrides)
    return body


def _arrays(sigma_zz: float = 20.0, *, with_tensor: bool = True) -> dict[str, Any]:
    """Three nodes along x, each carrying uniaxial σzz. Node 1 is 10 mm from node 0."""
    nodes = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [20.0, 0.0, 0.0]])
    arrays: dict[str, Any] = {
        "nodes": nodes,
        "von_mises_nodal": np.full(3, abs(sigma_zz)),
    }
    if with_tensor:
        tensor = np.zeros((3, 6))
        tensor[:, 2] = sigma_zz
        arrays[TENSOR_KEY] = tensor
    return arrays


def _assess(arrays: dict[str, Any], **overrides: Any) -> Any:
    return assess_run(
        arrays,
        FatigueRequest.model_validate(_request(**overrides)),
        simulation_id="sim-1",
        solver="linear-static",
        result={"mesh_convergence": CONVERGENCE},
    )


class TestTheRequestSaysWhereToRead:
    def test_both_a_node_and_a_point_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="exactly one of node and point_mm"):
            FatigueRequest.model_validate(_request(point_mm=[0.0, 0.0, 0.0]))

    def test_neither_a_node_nor_a_point_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="exactly one of node and point_mm"):
            FatigueRequest.model_validate(_request(node=None))

    def test_a_signal_of_two_samples_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            FatigueRequest.model_validate(_request(signal=[0.0, 1.0]))

    def test_a_curve_with_no_source_is_refused(self) -> None:
        body = _request()
        body["curve"]["source"] = ""
        with pytest.raises(ValidationError):
            FatigueRequest.model_validate(body)


class TestOnlyAFinishedStressRunIsAssessed:
    @pytest.mark.parametrize("analysis", sorted(STRESS_ANALYSES))
    def test_a_finished_structural_run_is_accepted(self, analysis: str) -> None:
        refuse_unless_assessable(analysis, "succeeded")

    @pytest.mark.parametrize(
        "analysis", ["thermal-conduction", "thermal-transient", "flow-laminar", "modal"]
    )
    def test_a_run_with_no_static_stress_is_refused_as_a_conflict(self, analysis: str) -> None:
        with pytest.raises(FatigueRefused, match=analysis) as caught:
            refuse_unless_assessable(analysis, "succeeded")
        assert caught.value.status == 409

    @pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
    def test_an_unfinished_run_is_refused_naming_its_state(self, status: str) -> None:
        with pytest.raises(FatigueRefused, match=status) as caught:
            refuse_unless_assessable("solid", status)
        assert caught.value.status == 409


class TestTheArchiveMustHoldASignedStress:
    def test_an_archive_with_only_von_mises_is_refused_and_says_to_re_run(self) -> None:
        with pytest.raises(FatigueRefused, match="Re-run the analysis") as caught:
            _assess(_arrays(with_tensor=False))
        assert caught.value.status == 409
        assert "norm" in str(caught.value)

    def test_a_node_off_the_mesh_is_refused_naming_the_range(self) -> None:
        with pytest.raises(FatigueRefused, match="nodes 0 to 2") as caught:
            _assess(_arrays(), node=3)
        assert caught.value.status == 422

    def test_a_direction_on_a_principal_history_is_a_422_not_a_500(self) -> None:
        with pytest.raises(FatigueRefused) as caught:
            _assess(
                _arrays(),
                direction={"vector": [0.0, 0.0, 1.0], "reason": "the crack plane"},
            )
        assert caught.value.status == 422


class TestTheHistoryIsTheSolvedStressScaledByTheSignal:
    def test_a_uniaxial_node_reads_back_the_signal_times_the_stress(self) -> None:
        answer = _assess(_arrays(20.0))
        assert answer.history_mpa == pytest.approx([0.0, 20.0, -20.0, 0.0])
        assert answer.node == 0
        assert answer.distance_mm is None
        assert answer.position_mm == (0.0, 0.0, 0.0)

    def test_a_compressive_stress_keeps_its_sign(self) -> None:
        answer = _assess(_arrays(-20.0))
        assert answer.history_mpa == pytest.approx([0.0, -20.0, 20.0, 0.0])

    def test_a_component_across_z_reads_the_same_as_the_principal_here(self) -> None:
        answer = _assess(
            _arrays(20.0),
            scalar="component",
            direction={"vector": [0.0, 0.0, 1.0], "reason": "the loaded axis"},
        )
        assert answer.history_mpa == pytest.approx([0.0, 20.0, -20.0, 0.0])

    def test_a_point_reads_the_nearest_node_and_reports_the_distance(self) -> None:
        answer = _assess(_arrays(), node=None, point_mm=[12.0, 0.0, 0.0])
        assert answer.node == 1
        assert answer.distance_mm == pytest.approx(2.0)
        assert answer.notes[0].startswith("The point asked for is 2 mm from node 1")

    def test_the_source_names_the_signal_and_the_solver(self) -> None:
        answer = _assess(_arrays())
        assert "a fully reversed block stated by hand" in answer.history_source
        assert "linear-static" in answer.history_source


class TestTheAnswerCarriesWhatItRestsOn:
    def test_the_runs_result_block_and_the_statement_travel_with_it(self) -> None:
        answer = _assess(_arrays())
        assert answer.result == {"mesh_convergence": CONVERGENCE}
        assert answer.statement == NOT_VALIDATED
        assert answer.simulation_id == "sim-1"

    @needs_pylife
    def test_a_small_reversed_stress_passes_and_the_damage_is_approximated(self) -> None:
        answer = _assess(_arrays(20.0))
        assert answer.outcome == "passed"
        assert "within" in answer.summary
        assert answer.assessment["outcome"] == "passed"
        # The damage is a model prediction: its provenance is never "measured".
        assert "measured" not in str(answer.assessment.get("provenance", "")).replace(
            "unmeasured", ""
        )

    @needs_pylife
    def test_a_missing_size_factor_comes_back_unmeasured_naming_it(self) -> None:
        answer = _assess(
            _arrays(20.0), factors=[{"name": "surface", "value": 1.0, "source": SOURCE}]
        )
        assert answer.outcome == "unmeasured"
        assert "size" in answer.summary


class TestTheRouteAssessesARealSolve:
    """σ = F/A = 8000 / (20 × 20) = 20 MPa at every node of the bar."""

    def _solve(self, client: AuthenticatedTestClient, project_id: str) -> dict[str, Any]:
        response = client.post(
            f"/api/v1/projects/{project_id}/simulations",
            json={"load_case": load_case(), "element_size_mm": 10.0},
        )
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["status"] == "succeeded", job["error"]
        return job

    def test_the_bar_in_tension_reads_back_twenty_mpa_scaled_by_the_signal(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = self._solve(auth_client, project_with_geometry)
        expected = FORCE / (BOX[0] * BOX[1])
        assert expected == pytest.approx(20.0)

        body = _request(node=None, point_mm=[10.0, 10.0, 30.0])
        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}/fatigue",
            json=body,
        )
        assert response.status_code == 200, response.text
        answer = response.json()

        assert answer["history_mpa"] == pytest.approx(
            [0.0, expected, -expected, 0.0], rel=1e-3, abs=1e-3
        )
        assert answer["distance_mm"] is not None
        assert answer["statement"] == NOT_VALIDATED
        assert "mesh_convergence" in answer["result"]

    def test_the_archive_now_holds_the_tensor(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = self._solve(auth_client, project_with_geometry)
        response = auth_client.get(f"/api/v1/media/{job['fields_media_id']}/content")
        assert response.status_code == 200, response.text
        with np.load(io.BytesIO(response.content)) as data:
            assert data[TENSOR_KEY].shape == (len(data["nodes"]), 6)

    def test_a_node_off_the_mesh_is_a_422(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        job = self._solve(auth_client, project_with_geometry)
        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations/{job['id']}/fatigue",
            json=_request(node=10_000_000),
        )
        assert response.status_code == 422
        assert "not on this mesh" in response.json()["detail"]

    def test_another_projects_run_is_a_404(
        self, auth_client: AuthenticatedTestClient, project_with_geometry: str
    ) -> None:
        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations/no-such-run/fatigue",
            json=_request(),
        )
        assert response.status_code == 404


class TestTheAgentIsOfferedTheCheck:
    def _stored_run(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: Any,
        media: MediaService,
        *,
        analysis: str = "solid",
    ) -> str:
        buffer = io.BytesIO()
        np.savez(buffer, **_arrays(20.0))
        buffer.seek(0)
        fields = media.store_stream(
            owner_id=user.id,
            kind=MediaKind.RESULT_FIELDS,
            filename="fields.npz",
            stream=buffer,
        )
        job = SimulationJob(
            project_id=project.id,
            geometry_version_id=geometry.id,
            status=JobStatus.SUCCEEDED,
            analysis=analysis,
            solver="linear-static",
            load_case=_agent.LOAD_CASE,
            fields_media_id=fields.id,
            result={"max_von_mises_mpa": 20.0, "mesh_convergence": CONVERGENCE},
        )
        db_session.add(job)
        db_session.flush()
        return job.id

    def test_the_tool_is_in_the_vocabulary(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        names = {tool.name for tool in _toolbox(db_session, user, project).every_tool()}
        assert "assess_fatigue" in names

    def test_a_fatigue_answer_through_the_loop_carries_the_statement(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        geometry: Any,
        media_store: Any,
    ) -> None:
        from app.ai.agent import run_agent

        media = MediaService(db_session, media_store)
        job_id = self._stored_run(db_session, user, project, geometry, media)
        arguments = {"simulation_id": job_id, **_request()}
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    text="",
                    tool_calls=[ToolCall(id="c1", name="assess_fatigue", arguments=arguments)],
                ),
                AssistantTurn(text="The history at node 0 swings between +20 and -20 MPa."),
            ]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project, media=media),
            user_message="Check fatigue at node 0 for a fully reversed load.",
        )

        [step] = [s for s in reply.steps if s.tool == "assess_fatigue"]
        assert step.ok, step.result
        assert step.result["history_mpa"] == pytest.approx([0.0, 20.0, -20.0, 0.0])
        assert reply.text.count(NOT_VALIDATED) == 1

    def test_a_thermal_run_is_refused_to_the_model_in_words(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        geometry: Any,
        media_store: Any,
    ) -> None:
        from app.ai.agent import run_agent

        media = MediaService(db_session, media_store)
        job_id = self._stored_run(
            db_session, user, project, geometry, media, analysis="thermal-conduction"
        )
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="assess_fatigue",
                            arguments={"simulation_id": job_id, **_request()},
                        )
                    ],
                ),
                AssistantTurn(text="That run holds no stress."),
            ]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project, media=media),
            user_message="Check fatigue on the thermal run.",
        )

        [step] = [s for s in reply.steps if s.tool == "assess_fatigue"]
        assert not step.ok
        assert "thermal-conduction" in str(step.result)
