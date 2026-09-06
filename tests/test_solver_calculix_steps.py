"""The CalculiX analysis-step cards — master plan 6.4.

Offline and instant: a step writer is string generation over a case, so none of
this needs `ccx`, a database or a network. There is no `ccx` on the machine these
were written on, which is the honest limit of what they prove — **a test built
from the same documentation as the code can be wrong in the same direction**, and
these are. What they can still do is stop the code drifting away from what the
documentation said, and pin the three orderings and omissions that CalculiX
accepts silently and answers wrongly:

- a `*CLOAD` written before `*BUCKLE` is thrown away by `buckles.f` and the run
  reports the buckling factor of an unloaded structure;
- an explicit `0.0` lower bound on `*FREQUENCY` is not the same as omitting the
  field, and drops every negative eigenvalue from the output;
- a `*BOUNDARY` card with no data line under it turns a legitimate free-free
  modal case into a deck ccx refuses.

Every test here is named after the wrong answer it prevents.
"""

from __future__ import annotations

import pytest

from app.solve.calculix.steps import buckle_step, frequency_step
from app.solve.materials import MATERIALS
from app.solve.types import (
    BucklingCase,
    FaceSelector,
    Fixture,
    ForceLoad,
    ModalCase,
    SolverError,
)

_BOUNDARY = ["FIX1, 1, 1, 0.0", "FIX1, 2, 2, 0.0", "FIX1, 3, 3, 0.0"]
_LOADS = ["7, 3, -2000.0", "9, 3, -2000.0"]


def _modal(**overrides) -> ModalCase:
    base: dict = {"material": MATERIALS["steel-1018"], "modes": 6}
    base.update(overrides)
    return ModalCase(**base)


def _buckling(**overrides) -> BucklingCase:
    base: dict = {
        "material": MATERIALS["steel-1018"],
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


def _after(lines: list[str], keyword: str) -> int:
    """Index of `keyword`, so two cards can be ordered against each other."""
    for index, line in enumerate(lines):
        if line.upper().startswith(keyword.upper()):
            return index
    raise AssertionError(f"{keyword} is not in the step: {lines}")


class TestTheFrequencyStep:
    def test_the_mode_count_is_on_its_own_line_under_the_keyword(self) -> None:
        lines = frequency_step(_modal(modes=10))
        assert lines[_after(lines, "*FREQUENCY") + 1] == "10"

    def test_no_frequency_range_is_written(self) -> None:
        """An explicit lower bound loses modes rather than defaulting to nothing.

        `frequencys.f` starts `fmin=-1.d0` and only overwrites it when the field
        is non-blank; `writeev.f` then filters with `if(xmin.gt.-0.5d0)`. So
        writing the manual's stated default of 0 turns the filter *on* and drops
        every mode with a negative eigenvalue — silently, from the table and from
        the results file both. One field on this line, never three.
        """
        lines = frequency_step(_modal(modes=4))
        count_line = lines[_after(lines, "*FREQUENCY") + 1]
        assert "," not in count_line
        assert count_line.split() == ["4"]

    def test_the_step_is_not_a_perturbation_step(self) -> None:
        """PERTURBATION would make this a pre-stressed modal analysis.

        `*STEP, PERTURBATION` takes the last `*STATIC` step as the reference
        state, so the frequencies would be those of a loaded structure. A
        `ModalCase` carries no loads on purpose; a bare `*STEP` is how you ask
        for the unloaded answer.
        """
        lines = frequency_step(_modal())
        assert lines[0] == "*STEP"

    def test_a_free_free_case_writes_no_boundary_card(self) -> None:
        """`ModalCase.fixtures` is optional, and free-free is a real analysis.

        A `*BOUNDARY` card with nothing under it is an input error naming a line
        number, so a bare card here would refuse the one case the optional field
        exists for.
        """
        lines = frequency_step(_modal())
        assert "*BOUNDARY" not in lines

    def test_restraints_are_written_under_a_boundary_card(self) -> None:
        lines = frequency_step(_modal(), boundary=_BOUNDARY)
        start = _after(lines, "*BOUNDARY")
        assert lines[start + 1 : start + 1 + len(_BOUNDARY)] == _BOUNDARY

    def test_it_asks_for_displacements(self) -> None:
        lines = frequency_step(_modal())
        assert lines[_after(lines, "*NODE FILE") + 1] == "U"

    def test_it_does_not_ask_for_stresses(self) -> None:
        """The stress "of a mode" has no magnitude anybody may quote.

        `arpack.c` divides each eigenvector by `sqrt(z^T M z)`, so a mode shape
        is normalised rather than scaled to anything physical. Asking for `S`
        would multiply the results file by the mode count to store numbers that
        must not be read.
        """
        assert "*EL FILE" not in frequency_step(_modal())

    def test_the_step_is_closed(self) -> None:
        assert frequency_step(_modal())[-1] == "*END STEP"

    def test_no_solver_is_named(self) -> None:
        """Naming one pins the deck to a build that may not have it.

        CalculiX takes the first of SGI, PaStiX, PARDISO, SPOOLES, TAUCS that is
        installed. An unrecognised name is answered with a `*WARNING` and the
        default, so getting it wrong is a surprise rather than an error.
        """
        lines = frequency_step(_modal(), boundary=_BOUNDARY) + buckle_step(
            _buckling(), boundary=_BOUNDARY, loads=_LOADS
        )
        assert not any("SOLVER=" in line.upper() for line in lines)

    def test_a_case_asking_for_no_modes_is_refused(self) -> None:
        """`model_construct` skips validation, which is how a stored case reaches
        a solver: pydantic guards the API boundary, not a row read back."""
        case = ModalCase.model_construct(material=MATERIALS["steel-1018"], fixtures=[], modes=0)
        with pytest.raises(SolverError, match="at least one mode"):
            frequency_step(case)


class TestTheBuckleStep:
    def test_the_factor_count_is_on_its_own_line_under_the_keyword(self) -> None:
        lines = buckle_step(_buckling(modes=2), boundary=_BOUNDARY, loads=_LOADS)
        assert lines[_after(lines, "*BUCKLE") + 1] == "2"

    def test_the_accuracy_and_iteration_defaults_are_left_to_calculix(self) -> None:
        """`buckles.f` supplies each of them when its field is absent.

        Writing 0.01, `4 * nev` and 1000 into the deck would restate ccx's own
        defaults in our source, where they can drift out of step with the build
        that is actually installed.
        """
        lines = buckle_step(_buckling(modes=2), boundary=_BOUNDARY, loads=_LOADS)
        assert lines[_after(lines, "*BUCKLE") + 1].split() == ["2"]

    def test_the_load_is_written_after_the_buckle_card(self) -> None:
        """Reading `*BUCKLE` zeroes every load accumulated before it.

        `buckles.f` sets `nforc=0`, `nload=0`, `nbody=0`, `iprestr=0` under the
        comment "removing the natural boundary conditions". A `*CLOAD` above the
        keyword is discarded with no error, and the run then reports the buckling
        factor of a structure with nothing pushing on it.
        """
        lines = buckle_step(_buckling(), boundary=_BOUNDARY, loads=_LOADS)
        assert _after(lines, "*CLOAD") > _after(lines, "*BUCKLE")

    def test_the_load_lines_are_written_verbatim(self) -> None:
        lines = buckle_step(_buckling(), boundary=_BOUNDARY, loads=_LOADS)
        start = _after(lines, "*CLOAD")
        assert lines[start + 1 : start + 1 + len(_LOADS)] == _LOADS

    def test_a_step_with_no_load_is_refused(self) -> None:
        """A buckling factor multiplies the load in the step.

        With no load there is nothing to multiply, and ccx would run and report
        factors that mean nothing rather than complain.
        """
        with pytest.raises(SolverError, match="no load"):
            buckle_step(_buckling(), boundary=_BOUNDARY, loads=[])

    def test_a_step_with_no_restraint_is_refused(self) -> None:
        with pytest.raises(SolverError, match="no restraint"):
            buckle_step(_buckling(), boundary=[], loads=_LOADS)

    def test_it_asks_for_displacements_and_stresses(self) -> None:
        """The stresses are the pre-buckling state, not a mode's.

        `arpackbu.c` writes the static solution as the run's first results step,
        so `*EL FILE, S` here is what lets one subprocess answer both halves of a
        buckling question — the factor, and the stress it multiplies.
        """
        lines = buckle_step(_buckling(), boundary=_BOUNDARY, loads=_LOADS)
        assert lines[_after(lines, "*NODE FILE") + 1] == "U"
        assert lines[_after(lines, "*EL FILE") + 1] == "S"

    def test_the_step_is_not_a_perturbation_step(self) -> None:
        """Without PERTURBATION the step's own load is the one that is scaled.

        With it, ccx would take the last `*STATIC` step as the reference state.
        There is no previous step in a Kryova buckling deck, so asking for one
        would be asking for a preload that does not exist.
        """
        lines = buckle_step(_buckling(), boundary=_BOUNDARY, loads=_LOADS)
        assert lines[0] == "*STEP"

    def test_the_step_is_closed(self) -> None:
        lines = buckle_step(_buckling(), boundary=_BOUNDARY, loads=_LOADS)
        assert lines[-1] == "*END STEP"


class TestBothSteps:
    def test_neither_writes_the_model(self) -> None:
        """The step half owns no nodes, elements, material or section.

        That is the whole reason this module exists separately: a `*FREQUENCY`
        deck and a `*BUCKLE` deck over one part differ in about eight lines, and
        a second node writer to get them would be the worst trade available.
        """
        both = frequency_step(_modal(), boundary=_BOUNDARY) + buckle_step(
            _buckling(), boundary=_BOUNDARY, loads=_LOADS
        )
        # Compared on the whole card name, never on a prefix: `*NODE FILE` is an
        # output request and starts with `*NODE`, so a prefix test would call a
        # correct step wrong -- and then be "fixed" by dropping the check.
        cards = {line.split(",")[0].strip().upper() for line in both if line.startswith("*")}
        for keyword in ("*NODE", "*ELEMENT", "*MATERIAL", "*SOLID SECTION", "*NSET", "*DENSITY"):
            assert keyword not in cards, keyword

    def test_neither_opens_a_static_step(self) -> None:
        assert "*STATIC" not in frequency_step(_modal())
        assert "*STATIC" not in buckle_step(_buckling(), boundary=_BOUNDARY, loads=_LOADS)

    def test_every_line_is_bare_text_with_no_newlines(self) -> None:
        """The caller joins these; a line carrying its own newline doubles up."""
        for line in frequency_step(_modal(), boundary=_BOUNDARY):
            assert "\n" not in line
        for line in buckle_step(_buckling(), boundary=_BOUNDARY, loads=_LOADS):
            assert "\n" not in line
