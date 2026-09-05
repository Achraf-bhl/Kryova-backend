"""Reading a CalculiX `.frd` — master plan 6.1, the return half.

**These fixtures are written to the documented format, not captured from a real
`ccx` run**, because the binary is not on this machine yet. That is stated in the
module under test as well: the parser is documented, not verified, and
`describe()` exists so the first real run reports what it actually met. A test
built from the same documentation as the code can be wrong in the same direction
— the runbook's own warning — so these tests pin *behaviour that must hold
whatever the format turns out to be* wherever they can: fixed-width reading,
components matched by name, a missing node refused rather than zeroed.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.solve.calculix.frd import (
    displacements,
    nodal_stress_tensor,
    von_mises_from_tensor,
)
from app.solve.calculix.frd import (
    parse_frd as parse,
)
from app.solve.types import SolverError


def _record(node: int, *values: float) -> str:
    """One ` -1` data record at the documented column widths."""
    out = " -1" + f"{node:10d}"
    for value in values:
        out += f"{value:12.5E}"
    return out


def _disp_block(rows: list[tuple[int, float, float, float]]) -> str:
    lines = [
        "    1PSTEP                         1",
        "  100CL  101  1.00000E+00" + f"{len(rows):12d}",
        " -4  DISP        4    1",
        " -5  D1          1    2    1    0",
        " -5  D2          1    2    2    0",
        " -5  D3          1    2    3    0",
        " -5  ALL         1    2    0    0    1ALL",
    ]
    lines += [_record(n, x, y, z) for n, x, y, z in rows]
    lines.append(" -3")
    return "\n".join(lines)


def _stress_block(rows: list[tuple[int, tuple[float, ...]]]) -> str:
    lines = [
        " -4  STRESS      6    1",
        " -5  SXX         1    4    1    1",
        " -5  SYY         1    4    2    2",
        " -5  SZZ         1    4    3    3",
        " -5  SXY         1    4    1    2",
        " -5  SYZ         1    4    2    3",
        " -5  SZX         1    4    3    1",
    ]
    lines += [_record(n, *values) for n, values in rows]
    lines.append(" -3")
    return "\n".join(lines)


HEADER = "    1C\n"


class TestTheRecordIsReadByColumn:
    """Whitespace splitting works until two values pack against each other, which
    is ordinary in a file full of negative stresses."""

    def test_two_negative_values_with_no_space_between_them_are_two_values(self) -> None:
        line = " -1" + f"{7:10d}" + f"{-1.23456e00:12.5E}" + f"{-2.34567e00:12.5E}"
        assert " " not in line[13:].strip(), "the fixture must actually pack them"

        frd = parse(_disp_block([]) .replace(" -3", line + "\n -3"))
        block = frd.blocks["DISP"]

        assert block.values[7] == pytest.approx([-1.23456, -2.34567])

    def test_a_node_number_that_is_not_a_number_says_which_columns(self) -> None:
        bad = " -1" + "abcdefghij" + f"{1.0:12.5E}"
        with pytest.raises(SolverError, match="ten columns"):
            parse(_disp_block([]).replace(" -3", bad + "\n -3"))

    def test_a_value_that_is_not_a_number_names_it(self) -> None:
        bad = " -1" + f"{3:10d}" + "  not-a-num "
        with pytest.raises(SolverError, match="not-a-num"):
            parse(_disp_block([]).replace(" -3", bad + "\n -3"))


class TestDisplacements:
    def test_the_field_comes_back_in_mesh_node_order(self) -> None:
        """CalculiX numbers from 1; the array is 0-based, and the shift is the
        same off-by-one the deck writer guards on the way out."""
        text = HEADER + _disp_block([(1, 0.0, 0.0, 0.0), (2, 0.1, 0.2, 0.3)])

        field = displacements(parse(text), node_count=2)

        assert field.shape == (2, 3)
        assert field[1] == pytest.approx([0.1, 0.2, 0.3])

    def test_a_node_with_no_result_is_refused_rather_than_zeroed(self) -> None:
        """A zero displacement is a completely plausible value for a node that
        was never solved, which is why it must not be the default."""
        text = HEADER + _disp_block([(1, 0.0, 0.0, 0.0)])

        with pytest.raises(SolverError, match="missing 1 of 2"):
            displacements(parse(text), node_count=2)

    def test_a_node_outside_the_mesh_says_the_two_disagree(self) -> None:
        text = HEADER + _disp_block([(1, 0.0, 0.0, 0.0), (99, 0.1, 0.0, 0.0)])

        with pytest.raises(SolverError, match="outside the mesh"):
            displacements(parse(text), node_count=2)

    def test_a_file_with_no_displacement_block_says_what_it_has(self) -> None:
        text = HEADER + _stress_block([(1, (0, 0, 0, 0, 0, 0))])

        with pytest.raises(SolverError, match="no DISP block"):
            displacements(parse(text), node_count=1)


class TestStressComponentsAreMatchedByName:
    """CalculiX writes SXY SYZ SZX where Abaqus writes S12 S13 S23. Reading by
    position rather than by name transposes the shears without erroring."""

    def test_the_tensor_comes_back_in_the_documented_order(self) -> None:
        text = HEADER + _stress_block([(1, (10.0, 20.0, 30.0, 1.0, 2.0, 3.0))])

        tensor = nodal_stress_tensor(parse(text), node_count=1)

        assert tensor[0] == pytest.approx([10.0, 20.0, 30.0, 1.0, 2.0, 3.0])

    def test_a_file_declaring_them_in_another_order_is_still_read_correctly(self) -> None:
        """The reason components are matched by name at all."""
        text = HEADER + "\n".join(
            [
                " -4  STRESS      6    1",
                " -5  SZZ         1    4    3    3",
                " -5  SYY         1    4    2    2",
                " -5  SXX         1    4    1    1",
                " -5  SXY         1    4    1    2",
                " -5  SYZ         1    4    2    3",
                " -5  SZX         1    4    3    1",
                _record(1, 30.0, 20.0, 10.0, 1.0, 2.0, 3.0),
                " -3",
            ]
        )

        tensor = nodal_stress_tensor(parse(text), node_count=1)

        assert tensor[0] == pytest.approx([10.0, 20.0, 30.0, 1.0, 2.0, 3.0])

    def test_a_missing_component_is_named(self) -> None:
        text = HEADER + "\n".join(
            [
                " -4  STRESS      5    1",
                " -5  SXX         1    4    1    1",
                " -5  SYY         1    4    2    2",
                " -5  SZZ         1    4    3    3",
                " -5  SXY         1    4    1    2",
                " -5  SYZ         1    4    2    3",
                _record(1, 1.0, 2.0, 3.0, 4.0, 5.0),
                " -3",
            ]
        )

        with pytest.raises(SolverError, match="SZX"):
            nodal_stress_tensor(parse(text), node_count=1)

    def test_the_all_row_is_not_counted_as_a_component(self) -> None:
        """CalculiX appends ALL to name the whole entity; counting it widens
        every row by one and shifts the last component off the end."""
        text = HEADER + _disp_block([(1, 1.0, 2.0, 3.0)])

        assert parse(text).blocks["DISP"].components == ["D1", "D2", "D3"]


class TestVonMises:
    """Checked against closed forms, never against recorded output."""

    def test_uniaxial_tension_is_the_axial_stress(self) -> None:
        tensor = np.array([[100.0, 0.0, 0.0, 0.0, 0.0, 0.0]])

        assert von_mises_from_tensor(tensor)[0] == pytest.approx(100.0)

    def test_hydrostatic_pressure_has_no_von_mises_stress(self) -> None:
        """The whole point of the invariant: pure pressure does not yield metal."""
        tensor = np.array([[-50.0, -50.0, -50.0, 0.0, 0.0, 0.0]])

        assert von_mises_from_tensor(tensor)[0] == pytest.approx(0.0, abs=1e-12)

    def test_pure_shear_is_root_three_times_the_shear(self) -> None:
        tensor = np.array([[0.0, 0.0, 0.0, 40.0, 0.0, 0.0]])

        assert von_mises_from_tensor(tensor)[0] == pytest.approx(40.0 * np.sqrt(3.0))

    def test_it_is_the_hand_written_solvers_function_and_not_a_second_copy(self) -> None:
        """Decision 2 in miniature: do not re-implement what already exists.

        Two implementations of one invariant can drift, and a drift *there* would
        surface as a disagreement about the stresses — a far more alarming finding
        than the truth. Identity is a stronger guarantee than agreement, so it is
        what gets asserted.
        """
        from app.solve.linear_static import von_mises

        tensor = np.array([[120.0, -40.0, 15.0, 22.0, -8.0, 5.0]])

        assert von_mises_from_tensor(tensor)[0] == pytest.approx(
            von_mises(tensor)[0], rel=1e-15
        )

    def test_the_shear_terms_carry_the_factor_of_three(self) -> None:
        """Pinned here because the two spellings of this formula — 3*(...) inside
        the root and 6*(...) inside a half — are easy to mix and differ by 2x."""
        tensor = np.array([[0.0, 0.0, 0.0, 0.0, 10.0, 0.0]])

        assert von_mises_from_tensor(tensor)[0] == pytest.approx(10.0 * np.sqrt(3.0))


class TestTheParserSaysWhatItDidNotUnderstand:
    """The first run against real output is the measurement, so it must report.

    Same pattern as `catia_describe_dialog` naming an unrecognised Win32 control
    class: a shrug from the first real session wastes the session.
    """

    def test_a_clean_file_leaves_nothing_unrecognised(self) -> None:
        text = HEADER + _disp_block([(1, 0.0, 0.0, 0.0)])

        assert parse(text).unrecognised == []

    def test_an_unknown_record_is_kept_verbatim(self) -> None:
        text = HEADER + _disp_block([(1, 0.0, 0.0, 0.0)]) + "\n  42X SOMETHING NEW\n"

        frd = parse(text)

        assert any("SOMETHING NEW" in line for line in frd.unrecognised)

    def test_describe_reports_the_blocks_and_the_surprises(self) -> None:
        text = HEADER + _disp_block([(1, 0.0, 0.0, 0.0), (2, 1.0, 0.0, 0.0)])

        described = parse(text).describe()

        assert described["blocks"]["DISP"]["components"] == ["D1", "D2", "D3"]
        assert described["blocks"]["DISP"]["nodes"] == 2
        assert described["blocks"]["DISP"]["first_node"] == 1
        assert described["unrecognised_records"] == 0

    def test_the_mesh_blocks_are_stepped_over_not_flagged(self) -> None:
        """A .frd repeats the nodes and elements; we already have the mesh."""
        text = (
            HEADER
            + "    2C" + f"{2:30d}\n"
            + _record(1, 0.0, 0.0, 0.0) + "\n"
            + _record(2, 1.0, 0.0, 0.0) + "\n"
            + " -3\n"
            + _disp_block([(1, 0.0, 0.0, 0.0), (2, 0.5, 0.0, 0.0)])
        )

        frd = parse(text)

        assert frd.unrecognised == []
        assert displacements(frd, node_count=2)[1] == pytest.approx([0.5, 0.0, 0.0])
