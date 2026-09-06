"""A real simulation, really metered — and really metered badly (P8.1, P8.3).

This is the test that would have caught the thing the unit tests cannot: the
runner opening no scope, the mapping naming a span nothing emits, the element
count never being annotated. It drives the actual `POST /simulations` endpoint,
which runs the job inline (see `conftest`), so a mesh is built and a solve runs
and the ledger is read afterwards.

It is deliberately *not* called an end-to-end test. The standing rule here is
that "end to end" means the path a user takes through the chatbot; this starts
at the HTTP route, which is a middle. What it does prove is that the meter is
attached to the pipeline rather than to a fixture.

The second class is the one that matters most. **Metering must never fail the
thing it measures, and must not fail silently either.** Both halves are broken
on purpose: the ledger is made to throw on every write, and the job still has to
succeed, and the failure still has to be findable afterwards.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.metering import METERING, LedgerSink
from app.models.billing import Meter, MeteringFault, UsageRecord
from tests.test_simulations import load_case, project_with_geometry  # noqa: F401  -- fixtures
from tests.typing import AuthenticatedTestClient


@pytest.fixture(autouse=True)
def _fresh_monitor():
    METERING.reset()
    yield
    METERING.reset()


def run_a_simulation(client: AuthenticatedTestClient, project_id: str) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/simulations",
        json={"load_case": load_case(), "element_size_mm": 10.0},
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


class TestASolveIsBilledToTheRunThatCausedIt:
    def test_the_ledger_names_the_job_the_project_and_the_geometry(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        db_session: Session,
    ) -> None:
        job = run_a_simulation(auth_client, project_with_geometry)
        assert job["status"] == "succeeded", job["error"]

        records = list(
            db_session.scalars(
                select(UsageRecord).where(UsageRecord.simulation_job_id == job["id"])
            )
        )
        assert records, "the run produced no usage at all — is a scope opened?"
        for record in records:
            assert record.subject_type == "simulation_job"
            assert record.subject_id == job["id"]
            assert record.project_id == project_with_geometry
            assert record.geometry_version_id
            assert record.source == "simulation.runner"
            assert record.method

    def test_the_solver_seconds_carry_the_solver_that_actually_ran(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        db_session: Session,
    ) -> None:
        """Decision 3 at the till: the bill names what produced the number."""
        job = run_a_simulation(auth_client, project_with_geometry)
        record = db_session.scalar(
            select(UsageRecord).where(
                UsageRecord.simulation_job_id == job["id"],
                UsageRecord.meter == Meter.SOLVER_SECONDS,
            )
        )
        assert record is not None
        assert record.quantity > Decimal(0)
        assert record.detail["annotations"]["solver"] == job["solver"]
        assert "solve." in "".join(record.detail["spans"])

    def test_meshing_is_billed_in_element_seconds_not_bare_seconds(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        db_session: Session,
    ) -> None:
        job = run_a_simulation(auth_client, project_with_geometry)
        record = db_session.scalar(
            select(UsageRecord).where(
                UsageRecord.simulation_job_id == job["id"],
                UsageRecord.meter == Meter.MESH_ELEMENT_SECONDS,
            )
        )
        assert record is not None
        elements = record.detail["annotations"]["elements"]
        assert elements == job["mesh_stats"]["element_count"]
        assert record.quantity > Decimal(0)

    def test_the_stored_result_fields_are_billed_as_storage(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        db_session: Session,
    ) -> None:
        job = run_a_simulation(auth_client, project_with_geometry)
        record = db_session.scalar(
            select(UsageRecord).where(
                UsageRecord.simulation_job_id == job["id"],
                UsageRecord.meter == Meter.STORAGE_BYTES,
            )
        )
        assert record is not None
        assert record.media_id == job["fields_media_id"]
        assert record.quantity > Decimal(0)

    def test_the_run_appears_on_the_billing_summary_for_its_tenant(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        db_session: Session,
    ) -> None:
        """The tenant is the project's, resolved through P2 and nowhere else."""
        from app.models import Project

        run_a_simulation(auth_client, project_with_geometry)
        project = db_session.get(Project, project_with_geometry)
        assert project is not None

        summary = auth_client.get(
            f"/api/v1/organisations/{project.organisation_id}/billing/summary"
        )
        assert summary.status_code == 200, summary.text
        meters = {row["meter"] for row in summary.json()["totals"]}
        assert {"solver_seconds", "mesh_element_seconds", "storage_bytes"} <= meters


class TestMeteringCannotFailTheJob:
    def test_a_ledger_that_throws_leaves_the_simulation_successful(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Break the ledger and check the physics still ships.

        This is the guard the whole `emit_safely` design exists for, verified by
        breaking the thing it guards rather than by reading the code.
        """

        def explode(self, events) -> None:
            raise RuntimeError("the ledger is unreachable")

        monkeypatch.setattr(LedgerSink, "emit", explode)

        job = run_a_simulation(auth_client, project_with_geometry)
        assert job["status"] == "succeeded", job["error"]
        assert job["result"]["max_von_mises_mpa"] > 0

        assert (
            db_session.scalars(
                select(UsageRecord).where(UsageRecord.simulation_job_id == job["id"])
            ).first()
            is None
        )

    def test_the_failure_is_recorded_rather_than_swallowed(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The other half: absorbing a failure is not the same as hiding it.

        `QueueMeter._finished` shadowed its own counter on 2026-09-06 and every
        job silently failed to be counted, because the exception died inside a
        `Future` nobody reads. Three places have to move here — a live counter,
        a logged stack, and a row somebody can find later.
        """

        def explode(self, events) -> None:
            raise RuntimeError("the ledger is unreachable")

        monkeypatch.setattr(LedgerSink, "emit", explode)
        run_a_simulation(auth_client, project_with_geometry)

        snapshot = METERING.snapshot()
        assert snapshot.events_failed >= 1
        assert not snapshot.healthy
        assert any("RuntimeError" in fault for fault in snapshot.faults)

        fault = db_session.scalars(select(MeteringFault)).first()
        assert fault is not None
        assert fault.source == "simulation.runner"
        assert "RuntimeError" in fault.failure
        assert fault.detail["subject_type"] == "simulation_job"

    def test_a_failed_solve_is_still_billed_for_the_meshing_it_did(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,  # noqa: F811
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A solver that falls over *after* gmsh has run.

        The scope posts in a `finally`, so the meshing that really happened is
        billed. A ledger holding only the successful runs makes the arithmetic
        on what is left look better than the system is — the same argument
        `app.observe.Span` makes for recording a span that raised.

        The solver is replaced rather than the model made singular: the
        pre-flight limits refuse an over-fine request *before* gmsh is touched,
        so the obvious ways to make a job fail also skip the meshing this is
        about.
        """
        from app.simulation import runner as runner_module
        from app.solve.types import SolverError

        class FallsOverAfterMeshing:
            name = "internal"

            def solve(self, mesh, case):
                raise SolverError("the factorisation did not converge")

        monkeypatch.setattr(
            runner_module, "build_solver", lambda *args, **kwargs: FallsOverAfterMeshing()
        )

        response = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations",
            json={"load_case": load_case(), "element_size_mm": 10.0},
        )
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["status"] == "failed", job

        records = list(
            db_session.scalars(
                select(UsageRecord).where(UsageRecord.simulation_job_id == job["id"])
            )
        )
        assert {record.meter for record in records} >= {Meter.MESH_ELEMENT_SECONDS}
