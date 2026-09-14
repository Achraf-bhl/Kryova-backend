"""Signed stress histories from a solver's tensor — master plan 8.1.

Every expected value is closed form: a tensor component is arithmetic, superposition is
a sum, and a prismatic bar in tension carries σ = F/A at every node, which the in-house
solver reproduces to 1e-12 on an exact box mesh. So a history read off a real solve,
counted and summed, has a damage that can be written down by hand.

Nothing here opens a database; the solve runs on `app.mesh.primitives`, not gmsh.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.design.assertions import Outcome
from app.fatigue import Assessment, Factor, FactorSet, SNCurve, StressBasis, available
from app.fatigue import field as field_module
from app.fatigue.field import (
    Direction,
    LoadChannel,
    Scalar,
    component,
    history_at,
    nearest_node,
    principal,
    ranked_nodes,
    signed_von_mises,
    tensor_history,
    von_mises,
)
from app.mesh.primitives import box_mesh, promote_to_tet10
from app.solve.base import SolveOutput
from app.solve.linear_static import LinearStaticSolver
from app.solve.materials import MATERIALS
from app.solve.types import FaceSelector, Fixture, ForceLoad, LoadCase

SOURCE = "test fixture signal, not a measured duty cycle"
AXIAL = Direction((0.0, 0.0, 1.0), reason="the bar's axis, the plane a tension crack opens on")


def _tensor(sxx=0.0, syy=0.0, szz=0.0, sxy=0.0, syz=0.0, szx=0.0) -> np.ndarray:
    return np.array([sxx, syy, szz, sxy, syz, szx], dtype=np.float64)


def _field(*rows: np.ndarray) -> np.ndarray:
    return np.array(rows, dtype=np.float64)


def _channel(name: str, stress: np.ndarray, signal: tuple[float, ...]) -> LoadChannel:
    return LoadChannel(name=name, stress_mpa=stress, signal=signal, source=SOURCE)


class TestTheTensorIsReadInVoigtOrder:
    """SXX SYY SZZ SXY SYZ SZX — the order CalculiX writes and `SolveOutput` keeps."""

    def test_each_axis_reads_its_own_normal_stress(self) -> None:
        t = _tensor(sxx=1.0, syy=2.0, szz=3.0, sxy=4.0, syz=5.0, szx=6.0)
        assert component(t, Direction((1, 0, 0), reason="x")) == pytest.approx(1.0)
        assert component(t, Direction((0, 1, 0), reason="y")) == pytest.approx(2.0)
        assert component(t, Direction((0, 0, 1), reason="z")) == pytest.approx(3.0)

    def test_a_diagonal_plane_reads_the_shear_it_crosses(self) -> None:
        # n = (1, 1, 0)/√2: n·σ·n = (σxx + σyy)/2 + σxy.
        t = _tensor(sxx=10.0, syy=30.0, sxy=7.0, syz=100.0, szx=100.0)
        assert component(t, Direction((1, 1, 0), reason="diagonal")) == pytest.approx(27.0)

    def test_the_direction_is_normalised_so_its_length_is_irrelevant(self) -> None:
        t = _tensor(sxx=10.0, syy=30.0, sxy=7.0)
        short = component(t, Direction((1, 1, 0), reason="d"))
        long = component(t, Direction((5, 5, 0), reason="d"))
        assert short == pytest.approx(long)

    def test_von_mises_of_uniaxial_and_of_pure_shear(self) -> None:
        assert von_mises(_tensor(szz=100.0)) == pytest.approx(100.0)
        assert von_mises(_tensor(sxy=100.0)) == pytest.approx(100.0 * math.sqrt(3.0))


class TestTheSignSurvives:
    def test_uniaxial_compression_is_negative_on_every_signed_scalar(self) -> None:
        t = _tensor(szz=-80.0)
        assert signed_von_mises(t) == pytest.approx(-80.0)
        value, axis = principal(t)
        assert value == pytest.approx(-80.0)
        assert abs(axis @ np.array([0.0, 0.0, 1.0])) == pytest.approx(1.0)

    def test_a_tie_between_tension_and_compression_goes_to_tension(self) -> None:
        # Pure shear: principal stresses +τ and −τ. The tensile one opens a crack.
        value, _ = principal(_tensor(sxy=50.0))
        assert value == pytest.approx(50.0)

    def test_a_zero_hydrostatic_stress_signs_positive_not_zero(self) -> None:
        # Zero would read a shaft in pure torsion as unstressed.
        assert signed_von_mises(_tensor(sxy=10.0)) == pytest.approx(10.0 * math.sqrt(3.0))


class TestSuperposition:
    def test_the_history_is_the_signal_weighted_sum_of_the_tensors(self) -> None:
        a = _field(_tensor(sxx=10.0), _tensor(sxx=1.0))
        b = _field(_tensor(syy=5.0, sxy=2.0), _tensor(syy=3.0))
        channels = [_channel("a", a, (0.0, 1.0, -2.0)), _channel("b", b, (1.0, 0.5, 4.0))]
        expected = np.array(
            [
                0.0 * a[0] + 1.0 * b[0],
                1.0 * a[0] + 0.5 * b[0],
                -2.0 * a[0] + 4.0 * b[0],
            ]
        )
        assert tensor_history(channels, 0) == pytest.approx(expected)

    def test_a_component_history_is_linear_so_order_does_not_matter(self) -> None:
        a = _field(_tensor(sxx=10.0, sxy=3.0))
        b = _field(_tensor(syy=-4.0, sxy=1.0))
        n = Direction((1, 2, 0), reason="d")
        channels = [_channel("a", a, (0.0, 1.0, -1.0, 2.0)), _channel("b", b, (3.0, 0.0, 1.0, 1.0))]
        read = history_at(channels, 0, scalar=Scalar.COMPONENT, direction=n).history.values_mpa
        by_hand = [
            s * float(component(a[0], n)) + r * float(component(b[0], n))
            for s, r in zip((0.0, 1.0, -1.0, 2.0), (3.0, 0.0, 1.0, 1.0), strict=True)
        ]
        assert read == pytest.approx(by_hand)


class TestWhatAHistoryReportsAboutItself:
    def test_one_channel_cannot_turn_the_principal_axis(self) -> None:
        stress = _field(_tensor(sxx=40.0, syy=-10.0, sxy=15.0))
        read = history_at(
            [_channel("only", stress, (0.0, 1.0, -1.0, 0.5))], 0, scalar=Scalar.PRINCIPAL
        )
        assert read.principal_axis_rotation_deg == pytest.approx(0.0, abs=1e-6)
        assert read.notes == ()

    def test_two_orthogonal_loads_out_of_phase_turn_it_ninety_degrees(self) -> None:
        x = _field(_tensor(sxx=100.0))
        y = _field(_tensor(syy=100.0))
        read = history_at(
            [_channel("x", x, (1.0, 0.0, 0.2)), _channel("y", y, (0.0, 1.0, 0.1))],
            0,
            scalar=Scalar.PRINCIPAL,
        )
        assert read.principal_axis_rotation_deg == pytest.approx(90.0)
        assert any("critical-plane" in note for note in read.notes)

    def test_a_torsion_point_counts_its_hydrostatic_sign_changes(self) -> None:
        # Shear plus a small axial stress that reverses: the sign of signed von Mises
        # follows the small axial term, and each reversal is counted.
        shear = _field(_tensor(sxy=100.0))
        axial = _field(_tensor(sxx=1.0))
        read = history_at(
            [_channel("shear", shear, (1.0, 1.0, 1.0, 1.0)), _channel("axial", axial, (1.0, -1.0, 1.0, -1.0))],
            0,
            scalar=Scalar.SIGNED_VON_MISES,
        )
        assert read.hydrostatic_sign_changes == 3
        assert read.notes

    def test_the_source_names_every_channel_and_the_node(self) -> None:
        read = history_at(
            [_channel("brake", _field(_tensor(szz=1.0)), (0.0, 1.0, 0.0))],
            0,
            scalar=Scalar.COMPONENT,
            direction=AXIAL,
        )
        assert "node 0" in read.history.source
        assert "'brake'" in read.history.source
        assert SOURCE in read.history.source


class TestRefusals:
    def test_a_solve_with_no_tensor_is_refused_by_name(self) -> None:
        output = SolveOutput(result=None, displacements=np.zeros((2, 3)), von_mises=np.zeros(1))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="no nodal stress tensor"):
            LoadChannel.from_solve("brake", output, (0.0, 1.0, 0.0), source=SOURCE, solver="surrogate")

    def test_the_refusal_names_the_solver(self) -> None:
        output = SolveOutput(result=None, displacements=np.zeros((2, 3)), von_mises=np.zeros(1))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="from surrogate"):
            LoadChannel.from_solve("brake", output, (0.0, 1.0, 0.0), source=SOURCE, solver="surrogate")

    def test_a_signal_with_no_source_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source"):
            LoadChannel(name="a", stress_mpa=_field(_tensor()), signal=(0, 1, 0), source="  ")

    def test_a_stress_that_is_not_six_components_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"\(n_nodes, 6\)"):
            _channel("a", np.zeros((3, 3)), (0.0, 1.0, 0.0))

    def test_a_non_finite_stress_is_refused(self) -> None:
        with pytest.raises(ValueError, match="non-finite"):
            _channel("a", _field(_tensor(sxx=float("nan"))), (0.0, 1.0, 0.0))

    def test_a_two_sample_signal_is_refused(self) -> None:
        with pytest.raises(ValueError, match="three samples"):
            _channel("a", _field(_tensor(sxx=1.0)), (0.0, 1.0))

    def test_channels_from_two_meshes_are_refused(self) -> None:
        one = _channel("a", _field(_tensor(sxx=1.0)), (0.0, 1.0, 0.0))
        two = _channel("b", _field(_tensor(sxx=1.0), _tensor(sxx=2.0)), (0.0, 1.0, 0.0))
        with pytest.raises(ValueError, match="same mesh"):
            tensor_history([one, two], 0)

    def test_signals_of_different_lengths_are_refused(self) -> None:
        one = _channel("a", _field(_tensor(sxx=1.0)), (0.0, 1.0, 0.0))
        two = _channel("b", _field(_tensor(sxx=1.0)), (0.0, 1.0, 0.0, 1.0))
        with pytest.raises(ValueError, match="same number of"):
            tensor_history([one, two], 0)

    def test_two_channels_with_one_name_are_refused(self) -> None:
        one = _channel("a", _field(_tensor(sxx=1.0)), (0.0, 1.0, 0.0))
        with pytest.raises(ValueError, match="share a name"):
            tensor_history([one, one], 0)

    def test_a_node_off_the_mesh_is_refused(self) -> None:
        one = _channel("a", _field(_tensor(sxx=1.0)), (0.0, 1.0, 0.0))
        with pytest.raises(ValueError, match="not on this mesh"):
            tensor_history([one], 1)

    def test_a_nodal_stress_is_never_a_hot_spot_stress(self) -> None:
        one = _channel("a", _field(_tensor(sxx=1.0)), (0.0, 1.0, 0.0))
        with pytest.raises(ValueError, match="hot-spot"):
            history_at([one], 0, scalar=Scalar.PRINCIPAL, basis=StressBasis.HOT_SPOT)

    def test_a_component_history_without_a_plane_is_refused(self) -> None:
        one = _channel("a", _field(_tensor(sxx=1.0)), (0.0, 1.0, 0.0))
        with pytest.raises(ValueError, match="plane it is read across"):
            history_at([one], 0, scalar=Scalar.COMPONENT)

    def test_a_direction_that_would_be_ignored_is_refused(self) -> None:
        one = _channel("a", _field(_tensor(sxx=1.0)), (0.0, 1.0, 0.0))
        with pytest.raises(ValueError, match="does not read one"):
            history_at([one], 0, scalar=Scalar.PRINCIPAL, direction=AXIAL)

    def test_a_zero_direction_is_refused(self) -> None:
        with pytest.raises(ValueError, match="non-zero"):
            Direction((0.0, 0.0, 0.0), reason="nothing")

    def test_a_direction_with_no_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="source"):
            Direction((1.0, 0.0, 0.0), reason="")


class TestRankingChoosesWhereToAssess:
    def _channels(self) -> list[LoadChannel]:
        stress = _field(_tensor(szz=10.0), _tensor(szz=-40.0), _tensor(szz=25.0), _tensor(szz=0.0))
        return [_channel("a", stress, (0.0, 1.0, -1.0, 0.0))]

    def test_the_widest_range_comes_first_whatever_its_sign(self) -> None:
        ranked = ranked_nodes(self._channels(), scalar=Scalar.COMPONENT, direction=AXIAL, count=3)
        assert [r.node for r in ranked] == [1, 2, 0]
        assert ranked[0].range_mpa == pytest.approx(80.0)

    def test_the_range_is_the_range_of_the_history_itself(self) -> None:
        channels = self._channels()
        ranked = ranked_nodes(channels, scalar=Scalar.COMPONENT, direction=AXIAL, count=1)
        read = history_at(channels, ranked[0].node, scalar=Scalar.COMPONENT, direction=AXIAL)
        assert ranked[0].range_mpa == pytest.approx(read.history.peak_to_peak_mpa)

    def test_candidates_restrict_the_search(self) -> None:
        ranked = ranked_nodes(
            self._channels(), scalar=Scalar.COMPONENT, direction=AXIAL, count=2, candidates=[0, 3]
        )
        assert [r.node for r in ranked] == [0, 3]

    def test_blocking_does_not_change_the_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rng = np.random.default_rng(7)
        a = rng.normal(size=(50, 6))
        b = rng.normal(size=(50, 6))
        channels = [_channel("a", a, (0.0, 1.0, -1.0, 0.5)), _channel("b", b, (1.0, 0.0, 2.0, -1.0))]
        whole = ranked_nodes(channels, scalar=Scalar.PRINCIPAL, count=50)
        monkeypatch.setattr(field_module, "_RANK_BLOCK_NODES", 7)
        blocked = ranked_nodes(channels, scalar=Scalar.PRINCIPAL, count=50)
        assert [r.node for r in whole] == [r.node for r in blocked]
        assert [r.range_mpa for r in whole] == pytest.approx([r.range_mpa for r in blocked])


class TestNearestNode:
    def test_the_distance_is_reported_not_hidden(self) -> None:
        nodes = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
        index, distance = nearest_node(nodes, (7.0, 0.0, 0.0))
        assert index == 1
        assert distance == pytest.approx(3.0)


# -- through a real solve ---------------------------------------------------------

STEEL = MATERIALS["steel-1018"]
FORCE_N = 5_000.0
WIDTH, DEPTH, LENGTH = 10.0, 20.0, 100.0


def _bar_case() -> LoadCase:
    """The roller-supported bar of tests/test_solver.py: σ = F/A exactly, everywhere."""
    return LoadCase(
        name="axial",
        material=STEEL,
        fixtures=[
            Fixture(where=FaceSelector(axis="z", side="min"), dofs=["z"]),
            Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"]),
            Fixture(where=FaceSelector(axis="y", side="min"), dofs=["y"]),
        ],
        loads=[ForceLoad(where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, FORCE_N))],
    )


@pytest.fixture(scope="module", params=["tet4", "tet10"])
def solved_bar(request: pytest.FixtureRequest):
    mesh = box_mesh((WIDTH, DEPTH, LENGTH), divisions=(2, 2, 8))
    if request.param == "tet10":
        mesh = promote_to_tet10(mesh)
    return mesh, LinearStaticSolver().solve(mesh, _bar_case())


class TestAHistoryReadOffARealSolve:
    def test_every_node_of_the_bar_carries_force_over_area_along_its_axis(self, solved_bar) -> None:
        mesh, output = solved_bar
        channel = LoadChannel.from_solve("axial", output, (0.0, 1.0, 0.0), source=SOURCE, solver="internal")
        sigma = FORCE_N / (WIDTH * DEPTH)
        for node in (0, mesh.node_count // 2, mesh.node_count - 1):
            read = history_at([channel], node, scalar=Scalar.COMPONENT, direction=AXIAL)
            assert read.history.values_mpa == pytest.approx((0.0, sigma, 0.0), abs=1e-9)

    def test_a_reversed_signal_is_a_reversed_stress(self, solved_bar) -> None:
        mesh, output = solved_bar
        channel = LoadChannel.from_solve(
            "axial", output, (0.0, 1.0, -1.0, 1.0, -1.0, 0.0), source=SOURCE, solver="internal"
        )
        for scalar in (Scalar.PRINCIPAL, Scalar.SIGNED_VON_MISES):
            read = history_at([channel], 3, scalar=scalar)
            assert min(read.history.values_mpa) == pytest.approx(-25.0, rel=1e-9)
            assert max(read.history.values_mpa) == pytest.approx(25.0, rel=1e-9)

    @pytest.mark.skipif(not available(), reason="pyLife is not installed; counting is federated to it")
    def test_the_damage_is_the_hand_calculated_basquin_sum(self, solved_bar) -> None:
        _, output = solved_bar
        # ±F/A four times over: the three-point count closes the inner reversals and
        # leaves the first and last turning points as residue. Scaling the signal by 8
        # puts the amplitude at 200 MPa, twice a 100 MPa knee.
        signal = (0.0, 8.0, -8.0, 8.0, -8.0, 8.0, -8.0, 8.0, -8.0, 0.0)
        channel = LoadChannel.from_solve("axial", output, signal, source=SOURCE, solver="internal")
        read = history_at([channel], 0, scalar=Scalar.COMPONENT, direction=AXIAL)
        curve = SNCurve(slope_k1=5.0, knee_cycles=1.0e6, knee_amplitude_mpa=100.0, source=SOURCE)
        factors = FactorSet.of([Factor("surface", 1.0, source=SOURCE), Factor("size", 1.0, source=SOURCE)])
        result = Assessment(loading=read.history, curve=curve, factors=factors).run()
        assert result.outcome is not Outcome.UNMEASURED
        closed = sum(b.cycles for b in result.blocks)
        amplitude = FORCE_N / (WIDTH * DEPTH) * 8.0
        cycles_to_failure = 1.0e6 * (amplitude / 100.0) ** -5.0
        assert result.damage == pytest.approx(closed / cycles_to_failure, rel=1e-6)
        assert all(b.amplitude_mpa == pytest.approx(amplitude, rel=1e-9) for b in result.blocks)
