"""Modal and buckling through CalculiX — master plan 6.4.

Offline: no `ccx`, no database, no network. `run_ccx` is replaced with a function
that hands back files built here, so what is exercised is the composition — deck
in, `.dat` and `.frd` out, `ModalOutput` and `BucklingResult` back — rather than
the solver.

**There is no `ccx` on this machine, and these fixtures were written from the
same documentation as the code they test.** That is the known risk and naming it
is part of the job: if CalculiX's real `.dat` banner or `100C` column layout
differs from what `writeev.f`, `writebv.f` and `frdheader.c` say, these tests
agree with the code about a shared mistake. What they can still do — and what
they are built to do — is pin the **couplings** rather than the numbers:

- that a frequency comes from the eigenvalue table and not from the results file,
  because the results file writes 0.0 Hz for a negative eigenvalue and a modal
  run must never report an instability as a rigid-body mode;
- that mode shape *i* belongs to frequency *i*, checked against the `.frd`'s own
  header value rather than assumed from file order;
- that a results *step* is not a results *block* — `frd.c` writes one block per
  requested output entity, so a step asking for `U` and `S` writes two with the
  same step number, and counting blocks would say a buckling run had twice as
  many modes as it has;
- that a buckling run's first results step is the static state and not a mode.

Each of those is a wrong number rather than an exception if it is got wrong.
"""

from __future__ import annotations

import math

import pytest

from app.mesh.primitives import box_mesh
from app.solve.calculix import eigen
from app.solve.calculix.eigen import (
    CalculiXBucklingSolver,
    CalculiXModalSolver,
    _from_deck,
    buckling_factor_table,
    eigenvalue_table,
    result_steps,
)
from app.solve.calculix.run import CcxRun
from app.solve.materials import MATERIALS
from app.solve.types import (
    BucklingCase,
    FaceSelector,
    Fixture,
    ForceLoad,
    ModalCase,
    SolverError,
)

MATERIAL = MATERIALS["steel-1018"]


# -- fixtures shaped like CalculiX's own output ------------------------------


def _dat_eigenvalues(frequencies_hz, eigenvalues=None) -> str:
    """`writeev.f`'s table: `(i7,4(2x,e14.7))` under its spaced-out banner.

    `eigenvalues` overrides the omega^2 column so a test can write a row the
    solver must refuse — a negative eigenvalue, or a frequency that does not
    match the eigenvalue printed beside it.
    """
    lines = [
        "",
        "     E I G E N V A L U E   O U T P U T",
        "",
        " MODE NO    EIGENVALUE                       FREQUENCY",
        "                                    REAL PART             IMAGINARY PART",
        "                          (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)",
        "",
    ]
    for index, frequency in enumerate(frequencies_hz):
        omega = 2.0 * math.pi * frequency
        value = omega * omega if eigenvalues is None else eigenvalues[index]
        if value < 0.0:
            columns = (value, 0.0, 0.0, math.sqrt(-value))
        else:
            columns = (value, omega, frequency, 0.0)
        lines.append(f"{index + 1:7d}" + "".join(f"  {column:14.7E}" for column in columns))
    lines.append("")
    return "\n".join(lines)


def _dat_buckling(factors) -> str:
    """`writebv.f`'s table: `(i7,2x,e14.7)` under its own banner."""
    lines = [
        "",
        "     B U C K L I N G   F A C T O R   O U T P U T",
        "",
        " MODE NO       BUCKLING",
        "                FACTOR",
        "",
    ]
    for index, factor in enumerate(factors):
        lines.append(f"{index + 1:7d}  {factor:14.7E}")
    lines.append("")
    return "\n".join(lines)


def _frd_header(value: float, step: int, nodes: int) -> str:
    """A `100C` record with its fields at the columns `frdheader.c` writes them.

    `strcpy1(&text[12],tmp,12)` for the value and `strcpy1(&text[58],tmp,5)` for
    the step counter, with the `L` plus `100+kode` set name in between them at
    `text[6]` and `text[7]`.
    """
    row = [" "] * 76

    def put(at: int, text: str) -> None:
        row[at : at + len(text)] = list(text)

    put(0, "  100C")
    put(6, "L")
    put(7, f"{100 + step:5d}")
    put(12, f"{value:12.5E}")
    put(24, f"{nodes:12d}")
    put(58, f"{step:5d}")
    put(74, "1")
    return "".join(row)


def _record(node: int, values) -> str:
    """A `-1` data record in the long format: `(1X,'-1',I10,6E12.5)`."""
    return " -1" + f"{node:10d}" + "".join(f"{value:12.5E}" for value in values)


def _entity(name: str, components, rows) -> list[str]:
    lines = [f" -4  {name:<10s}{len(components):2d}    1"]
    for index, component in enumerate(components, start=1):
        lines.append(f" -5  {component:<10s}  1    2{index:5d}    0")
    lines.append(" -5  ALL         1    2    0    0    1ALL")
    lines.extend(_record(node + 1, values) for node, values in enumerate(rows))
    lines.append(" -3")
    return lines


def _displacement_rows(node_count: int, scale: float):
    return [(scale * (node + 1), 0.0, 0.0) for node in range(node_count)]


def _stress_rows(node_count: int, sigma: float):
    return [(sigma, 0.0, 0.0, 0.0, 0.0, 0.0) for _ in range(node_count)]


def _frd(steps) -> str:
    """A results file from `steps`: `(value, step number, entity blocks)`.

    Each entity gets its own `100C` header carrying the same step number, which
    is what `frd.c` does — one `frdheader()` call per requested output entity.
    """
    lines = ["    1CKryova", "    1UDATE 06.09.2026"]
    for value, step, entities in steps:
        for name, components, rows in entities:
            lines.append(_frd_header(value, step, len(rows)))
            lines.extend(_entity(name, components, rows))
    lines.append(" 9999")
    return "\n".join(lines)


def _mesh():
    return box_mesh((20.0, 20.0, 60.0), divisions=(1, 1, 2))


def _modal_case(**overrides) -> ModalCase:
    base: dict = {
        "material": MATERIAL,
        "fixtures": [Fixture(where=FaceSelector(type="face", axis="z", side="min"))],
        "modes": 3,
    }
    base.update(overrides)
    return ModalCase(**base)


def _buckling_case(**overrides) -> BucklingCase:
    base: dict = {
        "material": MATERIAL,
        "fixtures": [Fixture(where=FaceSelector(type="face", axis="z", side="min"))],
        "loads": [
            ForceLoad(
                where=FaceSelector(type="face", axis="z", side="max"),
                force_n=(0.0, 0.0, -4000.0),
            )
        ],
        "modes": 3,
    }
    base.update(overrides)
    return BucklingCase(**base)


def _model_writer(mesh, material, fixtures, *, name="Kryova"):
    """A stand-in for the seam `deck.py` has yet to expose.

    Deliberately not a real model: what is under test here is the composition and
    the reading, and a real one would make these tests depend on a function that
    does not exist yet.
    """
    return (
        ["*HEADING", f"{name} -- test model", "*NODE, NSET=NALL"],
        [f"FIX1, {dof}, {dof}, 0.0" for dof in (1, 2, 3)],
    )


def _cload_writer(forces) -> list[str]:
    return ["1, 3, -1000.0"]


class _Capture:
    """The deck the solver handed to `ccx`, plus the files it gets back."""

    def __init__(self, frd: str, dat: str, output: str = "") -> None:
        self.frd = frd
        self.dat = dat
        self.output = output
        self.deck = ""

    def __call__(self, deck: str, **_kwargs) -> CcxRun:
        self.deck = deck
        return CcxRun(
            frd=self.frd,
            output=self.output,
            returncode=0,
            seconds=0.5,
            artefacts={"dat": self.dat},
        )


def _install(monkeypatch, capture: _Capture) -> _Capture:
    monkeypatch.setattr(eigen, "run_ccx", capture)
    return capture


# -- the .dat tables ---------------------------------------------------------


class TestTheEigenvalueTable:
    def test_it_reads_the_frequency_column(self) -> None:
        rows = eigenvalue_table(_dat_eigenvalues([120.5, 340.25, 900.0]))
        assert [row.mode for row in rows] == [1, 2, 3]
        assert [round(row.frequency_hz, 3) for row in rows] == [120.5, 340.25, 900.0]

    def test_a_negative_eigenvalue_keeps_its_magnitude(self) -> None:
        """`writeev.f` writes `j, x(j), 0.0, 0.0, sqrt(-x(j))` for a negative one.

        Both frequency columns read zero, so reading the cycles column alone
        would report an instability as a rigid-body mode. The magnitude survives
        only in the imaginary column, and that is why the row is kept whole.
        """
        rows = eigenvalue_table(_dat_eigenvalues([0.0], eigenvalues=[-4.0e6]))
        assert rows[0].eigenvalue == pytest.approx(-4.0e6)
        assert rows[0].frequency_hz == 0.0
        assert rows[0].imaginary_rad_s == pytest.approx(2000.0)

    def test_a_frequency_that_is_not_the_root_of_its_eigenvalue_is_refused(self) -> None:
        """The only defence against the columns moving.

        A reordered table would still parse, and its frequencies would still look
        like frequencies. Checking `sqrt(eigenvalue)/2pi` against the column
        beside it costs one square root per mode and catches it.
        """
        dat = _dat_eigenvalues([120.0], eigenvalues=[(2 * math.pi * 300.0) ** 2])
        with pytest.raises(SolverError, match="columns are not where"):
            eigenvalue_table(dat)

    def test_an_empty_dat_is_refused_rather_than_read_as_no_modes(self) -> None:
        with pytest.raises(SolverError, match="no eigenvalue table"):
            eigenvalue_table("")

    def test_the_per_mode_heading_is_not_mistaken_for_the_table(self) -> None:
        """`writehe.f` writes `E I G E N V A L U E    N U M B E R`, not a table.

        It appears once per mode when `*NODE PRINT` is used. A reader anchored on
        the first three words would start reading at the first of those and find
        nothing.
        """
        with pytest.raises(SolverError, match="no eigenvalue table"):
            eigenvalue_table("                    E I G E N V A L U E    N U M B E R     1\n")

    def test_it_stops_at_the_end_of_the_table(self) -> None:
        dat = _dat_eigenvalues([10.0, 20.0]) + "\n TOTAL SOLVE TIME  1.2\n      9  0.1  0.2\n"
        assert len(eigenvalue_table(dat)) == 2


class TestTheBucklingFactorTable:
    def test_it_reads_the_factors_in_the_order_written(self) -> None:
        table = buckling_factor_table(_dat_buckling([3.25, 7.5, -2.0]))
        assert [mode for mode, _ in table] == [1, 2, 3]
        assert [pytest.approx(factor) for _, factor in table] == [3.25, 7.5, -2.0]

    def test_an_empty_dat_is_refused(self) -> None:
        with pytest.raises(SolverError, match="no buckling factor table"):
            buckling_factor_table("")

    def test_the_eigenvalue_banner_is_not_the_buckling_banner(self) -> None:
        with pytest.raises(SolverError, match="no buckling factor table"):
            buckling_factor_table(_dat_eigenvalues([10.0]))


# -- the multi-step results file ---------------------------------------------


class TestResultSteps:
    def test_two_entities_of_one_mode_are_one_step(self) -> None:
        """A results *block* is not a results *step*.

        `frd.c` calls `frdheader()` once per requested output entity, so a step
        asking for `U` and `S` writes two `100C` blocks with the same step
        number. Counting blocks would say a buckling run had twice as many modes
        as it has, and pair every shape with the wrong factor.
        """
        frd = _frd(
            [
                (
                    0.0,
                    1,
                    [
                        ("DISP", ("D1", "D2", "D3"), _displacement_rows(2, 1.0)),
                        ("STRESS", ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"), _stress_rows(2, 5.0)),
                    ],
                )
            ]
        )
        steps = result_steps(frd)
        assert len(steps) == 1
        assert steps[0].step == 1
        assert "DISP" in steps[0].text
        assert "STRESS" in steps[0].text

    def test_different_step_numbers_stay_apart(self) -> None:
        frd = _frd(
            [
                (11.0, 1, [("DISP", ("D1", "D2", "D3"), _displacement_rows(2, 1.0))]),
                (22.0, 2, [("DISP", ("D1", "D2", "D3"), _displacement_rows(2, 2.0))]),
            ]
        )
        steps = result_steps(frd)
        assert [step.step for step in steps] == [1, 2]
        assert [step.value for step in steps] == [pytest.approx(11.0), pytest.approx(22.0)]

    def test_the_mesh_preamble_is_not_a_step(self) -> None:
        frd = "    1CKryova\n    2C\n -1         1 0.0 0.0 0.0\n -3\n 9999\n"
        assert result_steps(frd) == []

    def test_an_unreadable_header_becomes_none_rather_than_raising(self) -> None:
        """Losing the header costs a cross-check, never an answer.

        The numbers come from the `.dat`; this value only confirms the pairing,
        so a header this reader cannot parse is reported as a warning by the
        caller rather than failing a run whose results are intact.
        """
        steps = result_steps("  100C  garbage\n -4  DISP        4    1\n -3\n")
        assert len(steps) == 1
        assert steps[0].step is None
        assert steps[0].value is None


# -- the modal solver --------------------------------------------------------


class TestTheModalSolver:
    def _solve(self, monkeypatch, frequencies, *, output=""):
        mesh = _mesh()
        frd = _frd(
            [
                (
                    frequency,
                    index + 1,
                    [
                        (
                            "DISP",
                            ("D1", "D2", "D3"),
                            _displacement_rows(mesh.node_count, index + 1.0),
                        )
                    ],
                )
                for index, frequency in enumerate(frequencies)
            ]
        )
        capture = _install(
            monkeypatch, _Capture(frd=frd, dat=_dat_eigenvalues(frequencies), output=output)
        )
        solver = CalculiXModalSolver(model_writer=_model_writer)
        return mesh, capture, solver.solve(mesh, _modal_case(modes=len(frequencies)))

    def test_the_frequencies_come_back_in_hertz(self, monkeypatch) -> None:
        _mesh_, _capture, out = self._solve(monkeypatch, [212.5, 640.0, 1180.25])
        assert out.result.frequencies_hz == [
            pytest.approx(212.5),
            pytest.approx(640.0),
            pytest.approx(1180.25),
        ]

    def test_every_mode_gets_its_own_shape(self, monkeypatch) -> None:
        mesh, _capture, out = self._solve(monkeypatch, [212.5, 640.0, 1180.25])
        assert out.shapes.shape == (3, mesh.node_count, 3)
        # Each fixture mode was written with a different amplitude, so a parser
        # that kept only the last block -- which is what `parse_frd` does when it
        # is handed a whole multi-step file -- would return three identical ones.
        assert out.shapes[0][0][0] != out.shapes[1][0][0]
        assert out.shapes[1][0][0] != out.shapes[2][0][0]

    def test_the_deck_carries_the_model_then_the_step(self, monkeypatch) -> None:
        _mesh_, capture, _out = self._solve(monkeypatch, [212.5, 640.0, 1180.25])
        assert capture.deck.index("*HEADING") < capture.deck.index("*FREQUENCY")
        assert "*STATIC" not in capture.deck
        assert capture.deck.endswith("\n")

    def test_rigid_body_modes_are_counted_below_the_shared_threshold(self, monkeypatch) -> None:
        """`modal.RIGID_BODY_HZ` is imported, not restated.

        Two solvers answering one question must agree on what a rigid-body mode
        is, or 6.5's oracle comparison begins by disagreeing about how many there
        were rather than about the physics.
        """
        _mesh_, _capture, out = self._solve(monkeypatch, [1e-4, 2e-4, 350.0])
        assert out.result.rigid_body_modes == 2
        assert out.result.fundamental_hz == pytest.approx(350.0)

    def test_a_negative_eigenvalue_is_refused_not_counted_as_rigid(self, monkeypatch) -> None:
        """The whole reason the `.dat` is read instead of the `.frd`.

        CalculiX writes 0.0 Hz into the results file for a negative eigenvalue,
        which is exactly what a rigid-body mode looks like. Reading the table
        keeps the sign, and the refusal is relative to the spectrum rather than
        to a constant, because an eigenvalue is omega squared.
        """
        mesh = _mesh()
        frd = _frd(
            [
                (0.0, 1, [("DISP", ("D1", "D2", "D3"), _displacement_rows(mesh.node_count, 1.0))]),
                (
                    500.0,
                    2,
                    [("DISP", ("D1", "D2", "D3"), _displacement_rows(mesh.node_count, 2.0))],
                ),
            ]
        )
        dat = _dat_eigenvalues([0.0, 500.0], eigenvalues=[-1.0e9, (2 * math.pi * 500.0) ** 2])
        _install(monkeypatch, _Capture(frd=frd, dat=dat))
        solver = CalculiXModalSolver(model_writer=_model_writer)
        with pytest.raises(SolverError, match="negative eigenvalue"):
            solver.solve(mesh, _modal_case(modes=2))

    def test_a_shape_paired_with_the_wrong_frequency_is_refused(self, monkeypatch) -> None:
        """File order is checked against the header value, not trusted.

        If the shapes and the numbers are in different orders every picture after
        the first is captioned wrongly, and nothing downstream can tell.
        """
        mesh = _mesh()
        frd = _frd(
            [
                (
                    900.0,
                    1,
                    [("DISP", ("D1", "D2", "D3"), _displacement_rows(mesh.node_count, 1.0))],
                ),
                (
                    100.0,
                    2,
                    [("DISP", ("D1", "D2", "D3"), _displacement_rows(mesh.node_count, 2.0))],
                ),
            ]
        )
        _install(monkeypatch, _Capture(frd=frd, dat=_dat_eigenvalues([100.0, 900.0])))
        solver = CalculiXModalSolver(model_writer=_model_writer)
        with pytest.raises(SolverError, match="not in the same order"):
            solver.solve(mesh, _modal_case(modes=2))

    def test_fewer_shapes_than_frequencies_is_refused(self, monkeypatch) -> None:
        mesh = _mesh()
        frd = _frd(
            [(100.0, 1, [("DISP", ("D1", "D2", "D3"), _displacement_rows(mesh.node_count, 1.0))])]
        )
        _install(monkeypatch, _Capture(frd=frd, dat=_dat_eigenvalues([100.0, 900.0])))
        solver = CalculiXModalSolver(model_writer=_model_writer)
        with pytest.raises(SolverError, match="mode shapes"):
            solver.solve(mesh, _modal_case(modes=2))

    def test_a_calculix_warning_reaches_the_caller(self, monkeypatch) -> None:
        _mesh_, _capture, out = self._solve(
            monkeypatch, [212.5], output="*WARNING in e_c3d: nonpositive jacobian is close\n"
        )
        assert any("*WARNING" in warning for warning in out.result.warnings)

    def test_a_run_with_no_dat_is_refused(self, monkeypatch) -> None:
        mesh = _mesh()
        frd = _frd(
            [(100.0, 1, [("DISP", ("D1", "D2", "D3"), _displacement_rows(mesh.node_count, 1.0))])]
        )
        _install(monkeypatch, _Capture(frd=frd, dat=""))
        solver = CalculiXModalSolver(model_writer=_model_writer)
        with pytest.raises(SolverError, match="without writing a .dat"):
            solver.solve(mesh, _modal_case(modes=1))


# -- the buckling solver -----------------------------------------------------


def _buckling_frd(mesh, factors):
    """A `*BUCKLE` results file: the static state first, then one step per mode.

    `arpackbu.c` writes the pre-buckling solution with `time` (0.0) as its value
    before it extracts any eigenvalue, and one step per mode afterwards with
    `d[j]` — the factor — as its value.
    """
    components = ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")
    steps = [
        (
            0.0,
            1,
            [
                ("DISP", ("D1", "D2", "D3"), _displacement_rows(mesh.node_count, 0.01)),
                ("STRESS", components, _stress_rows(mesh.node_count, 40.0)),
            ],
        )
    ]
    for index, factor in enumerate(factors):
        steps.append(
            (
                factor,
                index + 2,
                [
                    (
                        "DISP",
                        ("D1", "D2", "D3"),
                        _displacement_rows(mesh.node_count, index + 1.0),
                    ),
                    ("STRESS", components, _stress_rows(mesh.node_count, 1.0)),
                ],
            )
        )
    return _frd(steps)


class TestTheBucklingSolver:
    def _solve(self, monkeypatch, factors):
        mesh = _mesh()
        capture = _install(
            monkeypatch,
            _Capture(frd=_buckling_frd(mesh, factors), dat=_dat_buckling(factors)),
        )
        solver = CalculiXBucklingSolver(
            model_writer=_model_writer, cload_writer=_cload_writer
        )
        return mesh, capture, solver.solve(mesh, _buckling_case(modes=len(factors)))

    def test_the_factors_come_back_smallest_positive_first(self, monkeypatch) -> None:
        """`LinearBucklingSolver`'s ordering, not CalculiX's.

        Two solvers answering one question must present the answer the same way
        round, or the oracle comparison in 6.5 begins by disagreeing about index
        zero rather than about the physics.
        """
        _mesh_, _capture, (result, _shapes, _static) = self._solve(monkeypatch, [3.5, -2.0, 1.5])
        assert result.load_factors == [
            pytest.approx(1.5),
            pytest.approx(3.5),
            pytest.approx(-2.0),
        ]

    def test_the_shapes_are_reordered_with_the_factors(self, monkeypatch) -> None:
        mesh, _capture, (_result, shapes, _static) = self._solve(monkeypatch, [3.5, -2.0, 1.5])
        assert shapes.shape == (3, mesh.node_count, 3)
        # Written with amplitudes 1, 2, 3 in ccx's order; sorted to 3, 1, 2.
        assert [float(shape[0][0]) for shape in shapes] == [
            pytest.approx(3.0),
            pytest.approx(1.0),
            pytest.approx(2.0),
        ]

    def test_the_static_state_is_read_rather_than_re_solved(self, monkeypatch) -> None:
        """One subprocess answers both halves of a buckling question.

        `arpackbu.c` writes the pre-buckling static solution as the run's first
        results step, so the stress the factor multiplies is *the* stress it was
        computed from rather than one computed alongside it.
        """
        _mesh_, capture, (_result, _shapes, static) = self._solve(monkeypatch, [3.5])
        assert capture.deck.count("*STEP") == 1
        assert static.result.max_von_mises_mpa == pytest.approx(40.0)
        assert static.result.max_displacement_mm > 0.0

    def test_the_first_results_step_is_not_counted_as_a_mode(self, monkeypatch) -> None:
        _mesh_, _capture, (result, shapes, _static) = self._solve(monkeypatch, [3.5, 7.0])
        assert len(result.load_factors) == 2
        assert shapes.shape[0] == 2

    def test_a_missing_static_step_is_refused(self, monkeypatch) -> None:
        """One block short means the shapes cannot be paired with the factors."""
        mesh = _mesh()
        frd = _frd(
            [
                (
                    3.5,
                    1,
                    [("DISP", ("D1", "D2", "D3"), _displacement_rows(mesh.node_count, 1.0))],
                )
            ]
        )
        _install(monkeypatch, _Capture(frd=frd, dat=_dat_buckling([3.5])))
        solver = CalculiXBucklingSolver(model_writer=_model_writer, cload_writer=_cload_writer)
        with pytest.raises(SolverError, match="results steps"):
            solver.solve(mesh, _buckling_case(modes=1))

    def test_no_positive_factor_is_reported_as_a_warning(self, monkeypatch) -> None:
        _mesh_, _capture, (result, _shapes, _static) = self._solve(monkeypatch, [-2.0, -5.0])
        assert any("does not buckle" in warning for warning in result.warnings)

    def test_the_load_is_written_after_the_buckle_card_in_the_real_deck(
        self, monkeypatch
    ) -> None:
        _mesh_, capture, _out = self._solve(monkeypatch, [3.5])
        assert capture.deck.index("*CLOAD") > capture.deck.index("*BUCKLE")


# -- the seam ----------------------------------------------------------------


class TestTheDeckSeam:
    def test_a_missing_deck_function_names_itself(self) -> None:
        """An integration gap must say what to add, not raise AttributeError."""
        with pytest.raises(SolverError, match="needs"):
            _from_deck("write_model_that_does_not_exist", "deck.py needs something")

    def test_deck_exposes_write_model(self) -> None:
        """**Expected to fail until `deck.py` grows the model writer.**

        This is the unsatisfied seam, asserted rather than skipped: a suite that
        skips what it could not check reports green on work nobody did. The
        signature wanted is in `eigen._MISSING_MODEL`, and until it lands the
        two solvers here can only be driven with an injected writer — which every
        other test in this file does.
        """
        from app.solve.calculix import deck

        assert hasattr(deck, "write_model"), eigen._MISSING_MODEL

    def test_deck_exposes_cload_data_lines(self) -> None:
        """**Expected to fail until `deck.py` publishes `_cload_lines`.**

        See `eigen._MISSING_CLOAD`. Re-deriving the 1-based node numbering and
        the axis mapping here would leave 6.5's oracle comparison able to
        localise to the deck writer rather than to the physics.
        """
        from app.solve.calculix import deck

        assert hasattr(deck, "cload_data_lines"), eigen._MISSING_CLOAD
