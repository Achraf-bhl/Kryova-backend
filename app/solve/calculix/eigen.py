"""Modal and buckling through CalculiX — master plan 6.4.

`solver.py` is the linear static path: `deck.py` writes, `run.py` runs, `frd.py`
reads, `diagnose.py` explains. This is the eigenvalue path over the same four
pieces, plus `steps.py` for the step card and two readers for the one result file
the static path never needed.

Two solvers live here rather than one, mirroring `app/solve/` upstairs.
`CalculiXModalSolver` implements `ModalSolver` — the sibling of `Solver`, not a
method on it, because a natural frequency comes from a different input
(`ModalCase`: no loads, fixtures optional) and returns a different output, and
folding them together would make every caller branch on what it got back.
`CalculiXBucklingSolver` matches `LinearBucklingSolver`'s shape instead: it
returns `(BucklingResult, shapes, SolveOutput)`, because a load factor means
nothing without the load it multiplies and the caller almost always wants the
stress at the same time. Neither ABC changes; the job layer cannot tell which
implementation ran, which is the seam `CLAUDE.md` says must never be reached
around.

**There is no `ccx` binary on the machine this was written on. Nothing here has
been run against the real solver.** `frd.py` and `diagnose.py` say the same about
themselves and it is worth restating rather than inheriting. The keywords come
from the CalculiX manual and the result formats from ccx's own source (the full
citation list is in `steps.py`'s docstring); the risk that a test written from
the same documentation as the code is wrong in the same direction is real and is
not removed by having both. What is done about it is to pin the **couplings**
rather than the numbers — which mode a shape belongs to, which column a frequency
came from — so a misreading surfaces as a refusal instead of as a plausible
number. Each of those checks is named below.

Where the numbers are read from, and why not from the results file
------------------------------------------------------------------
**The eigenvalues come from `jobname.dat`, not from `jobname.frd`.** Both carry
them, and the `.dat` is the one the manual guarantees: "The eigenfrequencies are
always stored in file jobname.dat" (`*FREQUENCY`) and "The eigenvalues are
automatically stored in file jobname.dat" (`*BUCKLE`). The `.frd` carries the
value too — the cgx manual's Nodal Results Block gives the `100C` record as
`KEY,CODE,SETNAME,VALUE,NUMNOD,TEXT,ICTYPE,NUMSTP,ANALYS,FORMAT`, "VALUE = Could
be frequency, time or any numerical value" — and `arpack.c` does pass the
frequency there. But it passes a *lossy* version of it:

    if(d[j]>=0.){ freq=sqrt(d[j])/6.283185308; } else { freq=0.; }

so a negative eigenvalue reaches the results file as 0.0 Hz, indistinguishable
from a rigid-body mode. `writeev.f` writes the eigenvalue itself into the `.dat`,
and puts `sqrt(-x)` in the imaginary column when it is negative. A modal run that
quietly reported an instability as a rigid-body mode would be the worst failure
this module could have, so the `.dat` it is; the `.frd` value is used only to
check that shape *i* belongs to number *i*.

**Mode shapes come from the `.frd`, one results *step* per mode — which is not
the same as one results *block* per mode.** `frd.c` calls `frdheader()` once per
requested output entity, so a step that asks for `U` writes one `100C` block and
a step that asks for `U` and `S` writes two, with identical headers. What
identifies them as one mode is the `NUMSTP` field, which `frdheader.c` fills from
ccx's `kode` counter — incremented once per `frd()` call, i.e. once per mode:

    sprintf(tmp,"%5d",*kode); strcpy1(&text[58],tmp,5);

`result_steps` groups on that, which is why `_STEP_COLUMNS` and `_VALUE_COLUMNS`
are stated as columns from the source rather than found by splitting: a whitespace
split of that header is ambiguous the moment a field runs full.

The counts are then checked rather than assumed:

- A `*FREQUENCY` run must produce exactly one results step per row of the `.dat`
  table. `arpack.c` writes one `frd()` call per accepted mode and nothing before
  it — its only earlier call is the `nmethod==0` mafill-failure path, which
  cannot coexist with a completed eigenvalue table.
- A `*BUCKLE` run must produce exactly **one more** step than it has factors.
  `arpackbu.c` writes the static pre-buckling solution first, with `time` (0.0)
  as its value, and then one step per mode with `d[j]` — the factor itself — as
  its value. That leading step is not waste: it is the stress state the factor
  multiplies, and reading it is what lets one subprocess answer both halves of a
  buckling question instead of solving the static case twice.
- Where a `100C` header can be read, its value is compared against the number the
  `.dat` gives for that mode. A disagreement is a refusal: it means the shapes
  and the numbers are in different orders, and every picture after it is
  captioned wrongly.

**`frd.parse_frd` is called once per results step, not once per file, and that is
a limitation being worked around rather than a stylistic choice.** It keys blocks
by entity name in a dict, so a multi-step `.frd` — exactly what a modal run
writes — collapses to whichever mode CalculiX wrote last, with no error and no
warning. Splitting first gives that parser the single-step file it was written
for. This is reported as a finding against `frd.py`; when it is fixed, the
splitter below belongs beside it, and so do the two `.dat` readers, which are
`frd.py`'s own pattern applied to the other result file.

**Eigenvalue tolerances are relative, everywhere.** An eigenvalue is omega^2 —
order 1e10 for a steel bracket, 1e4 for a rubber grommet — so no absolute
threshold can be right for both. `modal.RIGID_BODY_HZ` and
`modal._NEGATIVE_TOLERANCE` are imported rather than restated so this path and
the in-house path cannot drift on what "rigid body" or "negative enough to
refuse" means. That is the point of 6.5's oracle: a disagreement must localise to
the physics, never to two copies of a constant.

The unsatisfied seam
--------------------
This module cannot write a deck on its own and does not pretend to. `deck.py`
today exposes one writer, `write_deck`, which emits the model *and* a `*STATIC`
step; there is no way to get the model without the step, and a modal case has no
`LoadCase` to hand it anyway — `ModalCase` carries no loads and `LoadCase`
requires at least one. So the model half arrives here as an **injected callable**,
the way `app/design/execute` takes its runner, and the default resolves
`deck.write_model` at call time, raising a message that names the exact function
and signature when it is absent. Nothing is copied: there is no node writer, no
element writer and no DOF table in this file, because a second copy of any of
those is how two decks start describing different models. `_MISSING_MODEL` and
`_MISSING_CLOAD` are the two requests, written out in full.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.base import ModalOutput, ModalSolver, SolveOutput
from app.solve.calculix.diagnose import Diagnosis, complaints, diagnose
from app.solve.calculix.frd import (
    displacements,
    nodal_stress_tensor,
    parse_frd,
    von_mises_from_tensor,
)
from app.solve.calculix.run import DEFAULT_TIMEOUT_S, CcxRun, run_ccx
from app.solve.calculix.steps import buckle_step, frequency_step
from app.solve.loads import assemble_loads
from app.solve.modal import _NEGATIVE_TOLERANCE, RIGID_BODY_HZ
from app.solve.postprocess import element_average, summarise_static
from app.solve.types import (
    BucklingCase,
    BucklingResult,
    Fixture,
    LoadCase,
    Material,
    ModalCase,
    ModalResult,
    SolverError,
)

# -- the .dat tables ---------------------------------------------------------
#
# `writeev.f` and `writebv.f` are the only two places CalculiX writes these, and
# both write a spaced-out banner followed by a fixed-format table. The banners
# are compared with their runs of whitespace collapsed on both sides: they are
# emitted by Fortran list-directed `write(5,*)`, which prepends a blank of its
# own, and a reader that depended on the exact leading space would break on a
# build that spaced them differently.

#: `writeev.f`: `write(5,*) '    E I G E N V A L U E   O U T P U T'`. Not to be
#: confused with `writehe.f`'s `E I G E N V A L U E    N U M B E R`, which is a
#: per-mode heading for `*NODE PRINT` output and introduces no table.
_EIGENVALUE_BANNER: Final = "E I G E N V A L U E O U T P U T"

#: `writebv.f`: `write(5,*) '    B U C K L I N G   F A C T O R   O U T P U T'`.
_BUCKLING_BANNER: Final = "B U C K L I N G F A C T O R O U T P U T"

#: How far the `.dat`'s own frequency column may sit from `sqrt(eigenvalue)/2pi`
#: before the table is judged to have been read in the wrong columns. Relative,
#: and loose enough for the seven significant digits `e14.7` carries.
_COLUMN_CONSISTENCY = 1e-4

#: How far a mode's value in the `.frd` header may sit from the same mode's value
#: in the `.dat` before the shapes are judged to be paired with the wrong
#: numbers. Relative, for the same reason every other tolerance here is.
_PAIRING_TOLERANCE = 1e-3


@dataclass(frozen=True)
class EigenRow:
    """One row of CalculiX's eigenvalue table, as `writeev.f` writes it.

    The row is `(i7,4(2x,e14.7))`: mode number, then the eigenvalue itself
    (omega^2, in rad^2/time^2), then that frequency's real part in rad/time, then
    the same frequency in cycles/time, then its imaginary part in rad/time.

    **A negative eigenvalue takes the other branch of that write**, which is the
    whole reason this table is read instead of the results file: `writeev.f`
    emits `j, x(j), 0.0, 0.0, sqrt(-x(j))`, so both frequency columns read zero
    and the magnitude survives only in `imaginary_rad_s`. Reading the cycles
    column alone would report a buckled structure as a rigid-body mode.
    """

    mode: int
    eigenvalue: float
    omega_rad_s: float
    frequency_hz: float
    imaginary_rad_s: float


def _numeric_row(line: str, values: int) -> tuple[int, list[float]] | None:
    """`(index, values)` when the line is a table row of that width, else None.

    Split on whitespace rather than by column — deliberately, and unlike
    `frd.py`. The `.dat` tables are written `(2x,e14.7)`, two spaces between
    every pair, so two values can never pack against each other the way they do
    in a twelve-wide `.frd` record. What the columns *mean* is then checked
    against physics by `eigenvalue_table`, which is a stronger test than counting
    characters would be.
    """
    parts = line.split()
    if len(parts) != values + 1:
        return None
    try:
        index = int(parts[0])
        numbers = [float(part) for part in parts[1:]]
    except ValueError:
        return None
    return index, numbers


def _table(text: str, banner: str, width: int) -> list[tuple[int, list[float]]]:
    """The rows of the first table under `banner`, each `width` values wide.

    Stops at the first non-row line after the rows have begun, so anything the
    `.dat` carries after the table does not bleed into it. Lines between the
    banner and the first row are the column headings and are skipped rather than
    parsed.
    """
    collapsed = " ".join(banner.split())
    rows: list[tuple[int, list[float]]] = []
    started = False
    for line in text.splitlines():
        if not started:
            if " ".join(line.split()) == collapsed:
                started = True
            continue
        row = _numeric_row(line, width)
        if row is None:
            if rows:
                break
            continue
        rows.append(row)
    return rows


def eigenvalue_table(dat: str) -> list[EigenRow]:
    """CalculiX's `E I G E N V A L U E   O U T P U T` table from a `.dat`.

    The consistency between the eigenvalue and the frequency printed beside it is
    checked here rather than trusted. It costs one square root per mode and it is
    the only available defence against a future CalculiX writing the same four
    numbers in a different order: the columns would still parse, the frequencies
    would still look like frequencies, and every reported number would be wrong.
    """
    rows = _table(dat, _EIGENVALUE_BANNER, 4)
    if not rows:
        raise SolverError(
            "CalculiX wrote no eigenvalue table. The frequencies are always stored in "
            "the .dat file, so a run that produced none never reached the eigenvalue "
            "extraction — the solver's own output says why."
        )

    out: list[EigenRow] = []
    for mode, (eigenvalue, omega, frequency, imaginary) in rows:
        if eigenvalue >= 0.0:
            expected = float(np.sqrt(eigenvalue)) / (2.0 * float(np.pi))
            scale = max(abs(expected), abs(frequency), 1.0)
            if abs(expected - frequency) > _COLUMN_CONSISTENCY * scale:
                raise SolverError(
                    f"Mode {mode} of CalculiX's eigenvalue table reports an eigenvalue of "
                    f"{eigenvalue:g} beside a frequency of {frequency:g} Hz, but an "
                    f"eigenvalue is omega squared and that one is {expected:g} Hz. The "
                    "columns are not where this reader expects them. Please report the "
                    ".dat file: nothing in it can be trusted until the layout is confirmed."
                )
        out.append(
            EigenRow(
                mode=mode,
                eigenvalue=eigenvalue,
                omega_rad_s=omega,
                frequency_hz=frequency,
                imaginary_rad_s=imaginary,
            )
        )
    return out


def buckling_factor_table(dat: str) -> list[tuple[int, float]]:
    """CalculiX's `B U C K L I N G   F A C T O R   O U T P U T` table.

    `(mode number, factor)` per row, in the order written, from `writebv.f`'s
    `(i7,2x,e14.7)`. The factor is the number the load in the buckling step is
    multiplied by to reach the buckling load — the manual's own definition, and
    the same quantity `BucklingResult.load_factors` carries, so nothing is
    converted anywhere between the two.
    """
    rows = _table(dat, _BUCKLING_BANNER, 1)
    if not rows:
        raise SolverError(
            "CalculiX wrote no buckling factor table. The factors are always stored in "
            "the .dat file, so a run that produced none never reached the eigenvalue "
            "extraction — the solver's own output says why."
        )
    return [(mode, values[0]) for mode, values in rows]


# -- the multi-step .frd -----------------------------------------------------
#
# Column bounds of the `100C` results-block header. The cgx manual gives the
# record as `(1X,' 100','C',6A1,E12.5,I12,20A1,I2,I5,10A1,I2)`; `frdheader.c`
# fills the two fields read here at exactly these offsets, which is quoted
# because a Fortran format and a C `strcpy1` offset agreeing is worth checking
# rather than assuming.

#: `strcpy1(&text[12],tmp,12)` where `tmp` is the formatted `time` — the
#: frequency for a `*FREQUENCY` step, the buckling factor for a `*BUCKLE` mode,
#: and 0.0 for the static state a buckling run writes first.
_VALUE_COLUMNS: Final = (12, 24)

#: `sprintf(tmp,"%5d",*kode); strcpy1(&text[58],tmp,5)` — the cgx manual's
#: NUMSTP. `kode` increments once per `frd()` call, so every block belonging to
#: one mode carries the same number and every mode carries a different one.
_STEP_COLUMNS: Final = (58, 63)


@dataclass(frozen=True)
class ResultStep:
    """Every `100C` block CalculiX wrote for one `frd()` call, as one text.

    One step is one mode. It can hold several blocks, because `frd.c` writes one
    per requested output entity: a step asking for `U` and `S` writes a `DISP`
    block and a `STRESS` block under the same step number, and reading only the
    first would lose the stresses without saying so.

    `step` and `value` are `None` when the header could not be read in the
    documented columns. That is a degradation rather than a failure — the numbers
    themselves come from the `.dat` — and the caller reports it as a warning.
    """

    step: int | None
    value: float | None
    text: str


def _header_field(header: str, columns: tuple[int, int]) -> str:
    start, end = columns
    return header[start:end].strip()


def result_steps(frd: str) -> list[ResultStep]:
    """The results steps of a `.frd`, in the order CalculiX wrote them.

    Blocks are grouped by the `NUMSTP` field so that one mode arrives as one
    step whatever it was asked to output. Consecutive runs are grouped rather
    than a dictionary keyed on the number, so ordering is preserved and a file
    that reused a step number would come back as two steps rather than as one
    silently merged one.

    The mesh blocks (`2C`, `3C`) precede the first `100C` and are dropped with
    the rest of the preamble: the mesh is already ours.
    """
    steps: list[ResultStep] = []
    lines: list[list[str]] = []
    for line in frd.splitlines():
        if line.strip().startswith("100C"):
            number = _header_field(line, _STEP_COLUMNS)
            value = _header_field(line, _VALUE_COLUMNS)
            try:
                parsed_step: int | None = int(number)
            except ValueError:
                parsed_step = None
            try:
                parsed_value: float | None = float(value)
            except ValueError:
                parsed_value = None

            if (
                steps
                and parsed_step is not None
                and steps[-1].step == parsed_step
            ):
                lines[-1].append(line)
                continue
            steps.append(ResultStep(step=parsed_step, value=parsed_value, text=""))
            lines.append([line])
        elif steps:
            lines[-1].append(line)

    return [
        ResultStep(step=step.step, value=step.value, text="\n".join(block))
        for step, block in zip(steps, lines, strict=True)
    ]


# -- the model seam ----------------------------------------------------------

_MISSING_MODEL = (
    "This solver cannot write a deck yet: app/solve/calculix/deck.py exposes no "
    "`write_model`. It needs\n\n"
    "    def write_model(\n"
    "        mesh: TetMesh,\n"
    "        material: Material,\n"
    "        fixtures: Sequence[Fixture],\n"
    "        *,\n"
    "        name: str = 'Kryova',\n"
    "    ) -> tuple[list[str], list[str]]\n\n"
    "returning (the deck lines from *HEADING down to *SOLID SECTION, including "
    "the per-fixture *NSET blocks, and the *BOUNDARY data lines for those sets "
    "without the *BOUNDARY card itself). That is exactly the first half of "
    "`write_deck`, which today can only be had with a *STATIC step attached — and "
    "a modal case has no LoadCase to give it. Nothing is copied here on purpose: "
    "a second node writer, or a second DOF table, is how two decks start "
    "describing different models."
)

_MISSING_CLOAD = (
    "This solver cannot write a buckling deck yet: app/solve/calculix/deck.py "
    "exposes no `cload_data_lines`. It needs\n\n"
    "    def cload_data_lines(forces: NDArray[np.float64]) -> list[str]\n\n"
    "which is today's private `_cload_lines`, unchanged, made public: the "
    "assembled nodal force vector in, `'12, 3, -250.0'` rows out, without the "
    "*CLOAD card. A buckling step carries its own load and must write those rows "
    "after its own keyword, and re-deriving the 1-based numbering and the axis "
    "mapping here would leave 6.5's oracle comparison able to localise to the "
    "deck writer rather than to the physics."
)


class ModelWriter(Protocol):
    """The model half of a deck: everything above the first `*STEP`."""

    def __call__(
        self,
        mesh: TetMesh,
        material: Material,
        fixtures: Sequence[Fixture],
        *,
        name: str = ...,
    ) -> tuple[list[str], list[str]]: ...


class CloadWriter(Protocol):
    """An assembled nodal force vector as `*CLOAD` data lines."""

    def __call__(self, forces: NDArray[np.float64]) -> list[str]: ...


def _from_deck(attribute: str, message: str) -> Any:
    """`deck.<attribute>`, or a refusal saying what to add and why.

    Looked up at call time rather than imported at module scope so this module
    imports, and everything in it that does not need a deck stays testable, while
    the seam is open. The refusal is a `SolverError` carrying the full signature:
    an integration gap that reports itself as `AttributeError: module has no
    attribute` costs a session to work out.
    """
    from app.solve.calculix import deck

    found = getattr(deck, attribute, None)
    if found is None:
        raise SolverError(message)
    return found


# -- the solvers -------------------------------------------------------------


class _CalculiXEigenSolver:
    """What the two eigenvalue solvers share: where the binary is, and the run.

    The constructor takes the executable and the timeout rather than reading them
    from settings, for the reason `CalculiXSolver` does the same: a solver that
    reads global configuration cannot be tested at two settings in one process,
    and the sweep in 5.3 needs exactly that.
    """

    def __init__(
        self,
        *,
        executable: str | os.PathLike[str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        threads: int | None = None,
        model_writer: ModelWriter | None = None,
    ) -> None:
        self.executable = executable
        self.timeout_s = timeout_s
        self.threads = threads
        self._model_writer = model_writer

    def _model(
        self, mesh: TetMesh, material: Material, fixtures: Sequence[Fixture], name: str
    ) -> tuple[list[str], list[str]]:
        writer: ModelWriter = self._model_writer or _from_deck("write_model", _MISSING_MODEL)
        return writer(mesh, material, fixtures, name=name)

    def _run(self, lines: Sequence[str]) -> CcxRun:
        run = run_ccx(
            "\n".join([*lines, ""]),
            executable=self.executable,
            timeout_s=self.timeout_s,
            threads=self.threads,
        )
        failure = diagnose(run)
        if failure is not None:
            raise SolverError(_with_evidence(failure))
        return run

    @staticmethod
    def _dat(run: CcxRun) -> str:
        dat = run.artefacts.get("dat", "")
        if not dat.strip():
            raise SolverError(
                "CalculiX finished without writing a .dat file, which is where it always "
                "stores eigenvalues. Without it there are mode shapes but no frequencies "
                "or factors to attach them to, and a picture with no number under it is "
                "not a result."
            )
        return dat


class CalculiXModalSolver(_CalculiXEigenSolver, ModalSolver):
    """Natural frequencies through CalculiX's `*FREQUENCY` procedure.

    A drop-in for `ModalEigenSolver`: same `ModalCase` in, same `ModalOutput` out,
    mass-normalised shapes in both. CalculiX normalises each eigenvector by
    `sqrt(z^T M z)` (`arpack.c`) and `scipy.sparse.linalg.eigsh` with an `M`
    returns mass-orthonormal vectors, so the two agree on what a mode shape's
    magnitude means — without which no comparison between them would be readable.
    """

    name = "calculix-frequency"

    def solve(self, mesh: TetMesh, case: ModalCase) -> ModalOutput:
        started = time.perf_counter()
        warnings: list[str] = []

        model, boundary = self._model(mesh, case.material, case.fixtures, case.name)
        run = self._run([*model, *frequency_step(case, boundary=boundary)])

        rows = eigenvalue_table(self._dat(run))
        _refuse_negative_eigenvalues(rows)

        frequencies = np.array([row.frequency_hz for row in rows], dtype=np.float64)
        steps = result_steps(run.frd)
        if len(steps) != len(rows):
            raise SolverError(
                f"CalculiX reported {len(rows)} eigenfrequencies but wrote {len(steps)} "
                "mode shapes. A frequency run writes one results step per mode, so the "
                "two cannot be paired and no shape can be attributed to a frequency. "
                "Please report the .frd: this is an integration fault, not a model one."
            )
        warnings.extend(_check_pairing(steps, list(frequencies), "frequency"))
        shapes = _shapes(steps, mesh.node_count)

        if len(rows) < case.modes:
            warnings.append(f"Asked for {case.modes} modes; CalculiX returned {len(rows)}.")
        rigid = int(np.count_nonzero(frequencies < RIGID_BODY_HZ))
        if not case.fixtures and rigid < 6 and len(frequencies) >= 6:
            warnings.append(
                f"A free-free model has six rigid-body modes; only {rigid} came back "
                "below the threshold, so the lowest elastic frequency may be wrong."
            )
        warnings.extend(_solver_warnings(run))

        volume = float(mesh.volume)
        result = ModalResult(
            frequencies_hz=[float(value) for value in frequencies],
            rigid_body_modes=rigid,
            mass_kg=volume * 1e-9 * float(case.material.density_kg_m3),
            volume_mm3=volume,
            node_count=mesh.node_count,
            element_count=mesh.tet_count,
            solve_seconds=time.perf_counter() - started,
            warnings=warnings,
        )
        return ModalOutput(result=result, frequencies_hz=frequencies, shapes=shapes)


class CalculiXBucklingSolver(_CalculiXEigenSolver):
    """Linear buckling through CalculiX's `*BUCKLE` procedure.

    Shaped like `LinearBucklingSolver` — `(BucklingResult, shapes, SolveOutput)` —
    but it takes no static solver, and that difference is the interesting one.
    `LinearBucklingSolver` runs a static solve first because it has to build `Kg`
    from the stress state itself. CalculiX does that internally, and `arpackbu.c`
    writes the pre-buckling static solution as the first results step of the same
    run, so the static output here is *read* rather than re-solved: one
    subprocess, and a stress state guaranteed to be the one the factor was
    computed from rather than one computed alongside it.

    The factors are ordered the way `LinearBucklingSolver` orders them — positive
    first, then by magnitude — rather than in CalculiX's own order. Two solvers
    answering one question must present the answer the same way round, or 6.5's
    oracle comparison begins by disagreeing about index 0.
    """

    name = "calculix-buckle"

    def __init__(
        self,
        *,
        executable: str | os.PathLike[str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        threads: int | None = None,
        model_writer: ModelWriter | None = None,
        cload_writer: CloadWriter | None = None,
    ) -> None:
        super().__init__(
            executable=executable,
            timeout_s=timeout_s,
            threads=threads,
            model_writer=model_writer,
        )
        self._cload_writer = cload_writer

    def solve(
        self, mesh: TetMesh, case: BucklingCase
    ) -> tuple[BucklingResult, NDArray[np.float64], SolveOutput]:
        started = time.perf_counter()
        warnings: list[str] = []

        model, boundary = self._model(mesh, case.material, case.fixtures, case.name)
        forces, load_warnings = assemble_loads(mesh, case.loads, case.material.density_kg_m3)
        warnings.extend(load_warnings)
        writer: CloadWriter = self._cload_writer or _from_deck("cload_data_lines", _MISSING_CLOAD)
        step = buckle_step(case, boundary=boundary, loads=writer(forces))
        run = self._run([*model, *step])

        table = buckling_factor_table(self._dat(run))
        steps = result_steps(run.frd)
        if len(steps) != len(table) + 1:
            raise SolverError(
                f"CalculiX reported {len(table)} buckling factors and wrote {len(steps)} "
                f"results steps; {len(table) + 1} were expected — the pre-buckling static "
                "solution, then one per mode. The shapes cannot be paired with the "
                "factors. Please report the .frd: this is an integration fault."
            )

        raw = [factor for _mode, factor in table]
        warnings.extend(_check_pairing(steps[1:], raw, "buckling factor"))
        shapes = _shapes(steps[1:], mesh.node_count)

        # Positive factors first, then by magnitude: `LinearBucklingSolver`'s own
        # ordering. A negative factor is kept rather than dropped -- it is the
        # answer to "and what if this load were reversed", which is a question an
        # engineer asks about every strut.
        order = sorted(range(len(raw)), key=lambda i: (raw[i] <= 0.0, abs(raw[i])))
        factors = [float(raw[i]) for i in order]
        shapes = shapes[order]

        if not any(factor > 0.0 for factor in factors):
            warnings.append(
                "No positive load factor was found: the structure does not buckle under "
                "this load in this direction. Reverse the load to check the other."
            )
        if len(factors) < case.modes:
            warnings.append(f"Asked for {case.modes} modes; CalculiX returned {len(factors)}.")
        warnings.extend(_solver_warnings(run))

        static = self._static_output(mesh, case, steps[0], warnings, started)

        volume = float(mesh.volume)
        result = BucklingResult(
            load_factors=factors,
            mass_kg=volume * 1e-9 * float(case.material.density_kg_m3),
            volume_mm3=volume,
            node_count=mesh.node_count,
            element_count=mesh.tet_count,
            solve_seconds=time.perf_counter() - started,
            warnings=warnings,
        )
        return result, shapes, static

    @staticmethod
    def _static_output(
        mesh: TetMesh,
        case: BucklingCase,
        step: ResultStep,
        warnings: list[str],
        started: float,
    ) -> SolveOutput:
        """The pre-buckling static state, read from the run's first results step.

        The stress is averaged onto elements the way `solver.py` does it — on the
        tensor, then the invariant once, never the other way round — through the
        same `postprocess.element_average`, so a buckling run and a static run
        report the same peak stress for the same load.
        """
        frd = parse_frd(step.text)
        node_displacements = displacements(frd, mesh.node_count)
        nodal_tensor = nodal_stress_tensor(frd, mesh.node_count)
        mises = von_mises_from_tensor(element_average(mesh, nodal_tensor))
        static_case = LoadCase(
            name=case.name,
            material=case.material,
            fixtures=case.fixtures,
            loads=case.loads,
        )
        return SolveOutput(
            result=summarise_static(
                mesh,
                static_case,
                node_displacements,
                mises,
                list(warnings),
                time.perf_counter() - started,
            ),
            displacements=node_displacements,
            von_mises=mises,
            nodal_stress=nodal_tensor,
        )


# -- shared reading ----------------------------------------------------------


def _refuse_negative_eigenvalues(rows: Sequence[EigenRow]) -> None:
    """A negative eigenvalue is a refusal, in the register `modal.py` uses.

    Measured against the spectrum this problem actually has rather than against a
    constant, because an eigenvalue is omega^2 and its scale is the material's.
    Round-off puts a rigid-body eigenvalue a little either side of zero and that
    is fine; anything larger means the stiffness matrix is not positive
    semi-definite — and CalculiX writes 0.0 Hz for such a mode in the results
    file, so letting it through would count an unstable structure as a rigid body.
    """
    if not rows:
        return
    scale = max(abs(row.eigenvalue) for row in rows) or 1.0
    for row in rows:
        if row.eigenvalue < _NEGATIVE_TOLERANCE * scale:
            raise SolverError(
                f"CalculiX returned a negative eigenvalue for mode {row.mode} "
                f"({row.eigenvalue:g}, reported as {row.imaginary_rad_s:g} rad/s in the "
                "imaginary column), which means the stiffness matrix is not positive "
                "semi-definite. The mesh is probably degenerate. Note that the results "
                "file gives this mode as 0.0 Hz, so it would otherwise have been counted "
                "as a rigid-body mode."
            )


def _check_pairing(steps: Sequence[ResultStep], values: Sequence[float], what: str) -> list[str]:
    """Warnings from comparing each step's own header value with the `.dat`.

    A mismatch is not a warning, it is a refusal: it means shape *i* is not the
    shape of value *i*, and every picture after it is captioned wrongly. What
    comes back as a warning is only the case where the header could not be read
    at all, which costs a check rather than an answer.
    """
    warnings: list[str] = []
    unreadable = 0
    for index, (step, value) in enumerate(zip(steps, values, strict=True)):
        if step.value is None:
            unreadable += 1
            continue
        scale = max(abs(value), abs(step.value), 1.0)
        if abs(step.value - value) > _PAIRING_TOLERANCE * scale:
            raise SolverError(
                f"Results step {index + 1} of the .frd carries {step.value:g} where the "
                f".dat gives {value:g} for that mode's {what}. The mode shapes and the "
                "numbers are not in the same order, so no shape can be attributed to a "
                "mode. Please report both files."
            )
    if unreadable:
        warnings.append(
            f"{unreadable} of {len(steps)} results steps carried no readable value in the "
            "documented columns of their 100C header, so their mode shapes could not be "
            "checked against the eigenvalues they were paired with. The numbers themselves "
            "come from the .dat and are unaffected — please report this, as an unreadable "
            "header is a gap in the reader."
        )
    return warnings


def _shapes(steps: Sequence[ResultStep], node_count: int) -> NDArray[np.float64]:
    """(n_modes, n_nodes, 3) displacements, one results step per mode."""
    if not steps:
        return np.zeros((0, node_count, 3), dtype=np.float64)
    return np.stack([displacements(parse_frd(step.text), node_count) for step in steps])


def _solver_warnings(run: CcxRun) -> list[str]:
    """What CalculiX said that did not stop it but the caller should see."""
    return [line for line in complaints(run.output) if "*WARNING" in line.upper()]


def _with_evidence(failure: Diagnosis) -> str:
    """The diagnosis, then CalculiX's own words underneath it.

    Both, always: the diagnosis is what the agent acts on and the raw text is
    what a human checks it against, and dropping either leaves one of the two
    readers with nothing. `solver.py` has a private helper of the same name and
    intent; this one is written against `Diagnosis` alone rather than re-running
    `diagnose`, and the two should become one method on `Diagnosis` when 6.1 and
    6.4 are integrated.
    """
    message = failure.message()
    if not failure.evidence:
        return message
    quoted = "\n".join(f"  {line}" for line in failure.evidence[:20])
    extra = len(failure.evidence) - 20
    more = "" if extra <= 0 else f"\n  ... and {extra} more"
    return f"{message}\n\nCalculiX said:\n{quoted}{more}"


__all__ = [
    "CalculiXBucklingSolver",
    "CalculiXModalSolver",
    "CloadWriter",
    "EigenRow",
    "ModelWriter",
    "ResultStep",
    "buckling_factor_table",
    "eigenvalue_table",
    "result_steps",
]
