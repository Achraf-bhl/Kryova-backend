"""Several load cases on one mesh share one factorisation (ROAD_TO_10 6.6).

Offline, no database. The claim has two halves and each is tested as its own thing: the answers
are the ones separate solves give (to round-off, because it is the same SuperLU factorisation
applied to each right-hand side), and the factorisation really is done once -- a result that
agrees while factorising three times would be a correct implementation of nothing.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.primitives import box_mesh
from app.solve import linear_static
from app.solve.linear_static import LinearStaticSolver
from app.solve.materials import MATERIALS
from app.solve.types import FaceSelector, Fixture, ForceLoad, LoadCase, SolverError

STEEL = MATERIALS["steel-1018"]
ALUMINIUM = MATERIALS["aluminium-6061-t6"]

ROLLERS = [
    Fixture(where=FaceSelector(axis="z", side="min"), dofs=["z"]),
    Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"]),
    Fixture(where=FaceSelector(axis="y", side="min"), dofs=["y"]),
]


def case(
    name: str, force: tuple[float, float, float], *, material=STEEL, fixtures=None
) -> LoadCase:
    return LoadCase(
        name=name,
        material=material,
        fixtures=fixtures if fixtures is not None else ROLLERS,
        loads=[ForceLoad(where=FaceSelector(axis="z", side="max"), force_n=force)],
    )


@pytest.fixture
def mesh():
    return box_mesh((10.0, 10.0, 40.0), divisions=(2, 2, 6))


CASES = [
    case("pull", (0.0, 0.0, 1000.0)),
    case("push", (0.0, 0.0, -2500.0)),
    case("shear", (400.0, -300.0, 0.0)),
]


class TestTheAnswersAreTheSeparateSolvesAnswers:
    def test_every_case_matches_a_solve_of_its_own(self, mesh) -> None:
        solver = LinearStaticSolver()
        together = solver.solve_cases(mesh, CASES)
        assert len(together) == len(CASES)
        for one, output in zip(CASES, together, strict=True):
            alone = solver.solve(mesh, one)
            np.testing.assert_allclose(
                output.displacements, alone.displacements, rtol=1e-9, atol=1e-12
            )
            np.testing.assert_allclose(output.von_mises, alone.von_mises, rtol=1e-9, atol=1e-9)
            assert output.result.max_von_mises_mpa == pytest.approx(
                alone.result.max_von_mises_mpa, rel=1e-9
            )

    def test_the_cases_really_are_different_from_each_other(self, mesh) -> None:
        # Otherwise "every case matches" could be one answer repeated.
        outputs = LinearStaticSolver().solve_cases(mesh, CASES)
        peaks = {round(o.result.max_displacement_mm, 9) for o in outputs}
        assert len(peaks) == len(CASES)

    def test_a_single_case_is_exactly_the_old_single_solve(self, mesh) -> None:
        solver = LinearStaticSolver()
        one = solver.solve_cases(mesh, [CASES[0]])[0]
        alone = solver.solve(mesh, CASES[0])
        assert np.array_equal(one.displacements, alone.displacements)

    def test_no_cases_is_no_outputs(self, mesh) -> None:
        assert LinearStaticSolver().solve_cases(mesh, []) == []


class TestTheFactorisationIsDoneOnce:
    def test_three_cases_factorise_once(self, mesh, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []
        real = LinearStaticSolver._factorise

        def spy(k_ff):
            calls.append(k_ff.shape[0])
            return real(k_ff)

        monkeypatch.setattr(LinearStaticSolver, "_factorise", staticmethod(spy))
        LinearStaticSolver().solve_cases(mesh, CASES)
        assert len(calls) == 1

    def test_the_stiffness_matrix_is_assembled_once(
        self, mesh, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []
        real = linear_static.assemble_stiffness

        def spy(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(linear_static, "assemble_stiffness", spy)
        LinearStaticSolver().solve_cases(mesh, CASES)
        assert len(calls) == 1

    def test_separate_solves_assemble_each_time(
        self, mesh, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The contrast that gives the previous test its meaning.
        calls: list[int] = []
        real = linear_static.assemble_stiffness

        def spy(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        monkeypatch.setattr(linear_static, "assemble_stiffness", spy)
        solver = LinearStaticSolver()
        for one in CASES:
            solver.solve(mesh, one)
        assert len(calls) == len(CASES)

    def test_a_large_model_assembles_once_and_iterates_each_case(
        self, mesh, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Above the threshold there is no factorisation to share, and the code does not pretend.
        monkeypatch.setattr(linear_static, "_ITERATIVE_THRESHOLD_DOF", 10)
        factorised: list[int] = []
        monkeypatch.setattr(
            LinearStaticSolver,
            "_factorise",
            staticmethod(lambda k: factorised.append(1)),  # type: ignore[arg-type,return-value]
        )
        together = LinearStaticSolver().solve_cases(mesh, CASES[:2])
        assert factorised == []
        alone = LinearStaticSolver().solve(mesh, CASES[0])
        np.testing.assert_allclose(together[0].displacements, alone.displacements, rtol=1e-6)


class TestCasesThatDoNotShareAStiffnessMatrixAreRefusedByName:
    def test_a_different_material_is_refused(self, mesh) -> None:
        other = case("light", (0.0, 0.0, 1000.0), material=ALUMINIUM)
        with pytest.raises(SolverError, match=r"Load case 2 \('light'\).*material"):
            LinearStaticSolver().solve_cases(mesh, [CASES[0], other])

    def test_different_fixtures_are_refused(self, mesh) -> None:
        other = case(
            "pinned",
            (0.0, 0.0, 1000.0),
            fixtures=[Fixture(where=FaceSelector(axis="z", side="min"), dofs=["x", "y", "z"])],
        )
        with pytest.raises(SolverError, match=r"Load case 2 \('pinned'\).*fixtures"):
            LinearStaticSolver().solve_cases(mesh, [CASES[0], other])

    def test_it_is_refused_before_anything_is_solved(
        self, mesh, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[int] = []
        monkeypatch.setattr(
            LinearStaticSolver,
            "_factorise",
            staticmethod(lambda k: called.append(1)),  # type: ignore[arg-type,return-value]
        )
        other = case("light", (0.0, 0.0, 1000.0), material=ALUMINIUM)
        with pytest.raises(SolverError):
            LinearStaticSolver().solve_cases(mesh, [CASES[0], other])
        assert called == []


class TestAnUnderConstrainedModelIsStillRefused:
    def test_a_free_body_is_refused_with_several_cases(self, mesh) -> None:
        free = [
            case(
                f"c{i}",
                (0.0, 0.0, 100.0 * (i + 1)),
                fixtures=[Fixture(where=FaceSelector(axis="z", side="min"), dofs=["z"])],
            )
            for i in range(2)
        ]
        with pytest.raises(SolverError):
            LinearStaticSolver().solve_cases(mesh, free)
