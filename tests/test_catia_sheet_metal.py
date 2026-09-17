"""Sheet Metal Design over COM — THE QUEUE E1, measured on the seat 2026-09-17.

E1 asked a seat session three questions. All three are answered, and the first answer is
a refusal:

1. **"Do `AddNewWall`/`AddNewFlange` behave as the COM documentation says?"** They do not
   behave any way at all — **they do not exist**. `CATShfInterfaces` declares four classes
   and no creation method among them. Read off the generated type library *before* calling
   anything, which is the discipline the `AddJoint` crash bought earlier the same day.
2. **"Can the parameters be set before the first wall or only after?"** Before:
   `CreateSheetMetalParameters()` works on an empty part. But not through the COM object —
   it has `GetThickness()` and no setter — through knowledge-ware, and the names there are
   **localised**.
3. **"Does a CATIA-built part unfold to the same blank Kryova computes?"** Answerable now
   that `CreateManufacturingFace` + `SaveAsDXF` are declared, and **only** if both are
   given the same K — which needs CATIA's DIN formula switched off, because it computes K
   rather than accepting one.

The K-factor finding is the sharpest thing here and is pinned below: CATIA's DIN formula
is `(0.5 + 0.5 log10(2 r/t)) / 2`, which is DIN 6935's factor halved with the **unrounded**
constant. The printed standard's 0.65 is wrong by 2.575e-4 at every ratio — small,
plausible, and exactly the size of disagreement between two unfold implementations that
nobody can explain.

Every test here is offline. What needed the seat has already been done, and its numbers
are the constants.
"""

from __future__ import annotations

import math

import pytest

from app.catia.ops.registry import OPERATIONS
from app.catia.ops.sheet_metal import (
    PARAMETER_NAMES,
    TYPE_LIBRARY,
    UNREACHABLE_OVER_COM,
    din_k_factor,
)
from app.catia.ops.spec import Tier, Workbench
from app.kernel.occt.refusals import REASONS

#: What the seat reported, exactly, with the DIN formula active. Three ratios, measured
#: 2026-09-17 on French V5-R33.
SEAT = {1.0: 0.3252574989159953, 2.0: 0.40051499783199057, 4.0: 0.4757724967479859}


class TestTheKFactorIsCatiasAndNotTheTextbooks:
    @pytest.mark.parametrize(("ratio", "expected"), sorted(SEAT.items()))
    def test_the_formula_reproduces_the_seat_exactly(
        self, ratio: float, expected: float
    ) -> None:
        """To the last bit, at every ratio measured. Solved from the three points rather
        than recalled from a standard — which is why it came out *not* matching the
        printed constant."""
        assert din_k_factor(ratio, 1.0) == pytest.approx(expected, rel=1e-15)

    def test_it_depends_only_on_the_ratio(self) -> None:
        """Measured: t=1 r=1, t=2 r=2 and t=3 r=3 all gave the same K."""
        assert din_k_factor(2.0, 2.0) == pytest.approx(din_k_factor(3.0, 3.0), rel=1e-15)
        assert din_k_factor(4.0, 2.0) == pytest.approx(din_k_factor(8.0, 4.0), rel=1e-15)

    def test_the_printed_din_constant_is_wrong_by_a_fixed_amount(self) -> None:
        """**The finding.** DIN 6935 as printed reads `k = 0.65 + 0.5 log10(r/t)`; CATIA
        uses `0.5 + 0.5 log10 2 = 0.650515…`. Using 0.65 is off by 2.575e-4 in K at
        *every* ratio — a constant, plausible error that would make two unfolds disagree
        by a few tenths of a millimetre with nothing to point at."""
        for ratio in SEAT:
            printed = (0.65 + 0.5 * math.log10(ratio)) / 2.0
            assert abs(din_k_factor(ratio, 1.0) - printed) == pytest.approx(
                2.575e-4, rel=1e-3
            )

    def test_a_doubling_of_the_ratio_adds_a_fixed_step(self) -> None:
        """How the formula was solved: K(2) - K(1) equals K(4) - K(2) to 1e-16, which is
        what says it is logarithmic before any constant is guessed."""
        first = SEAT[2.0] - SEAT[1.0]
        second = SEAT[4.0] - SEAT[2.0]

        assert first == pytest.approx(second, abs=1e-15)
        assert first == pytest.approx(0.25 * math.log10(2.0), rel=1e-12)

    def test_it_refuses_a_bend_that_is_not_one(self) -> None:
        """`log10` of a non-positive ratio is not a K-factor, and a NaN here would travel
        into a blank length with nothing raising."""
        for radius, thickness in ((0.0, 2.0), (2.0, 0.0), (-1.0, 2.0)):
            with pytest.raises(ValueError, match="do not describe a bend"):
                din_k_factor(radius, thickness)


class TestWhatCanBeDeclaredAndWhatCannot:
    def test_four_operations_are_declared(self) -> None:
        declared = {o.name for o in OPERATIONS if o.name.startswith("catia_sheetmetal")}

        assert declared == {
            "catia_sheetmetal_start",
            "catia_sheetmetal_parameters",
            "catia_sheetmetal_bends",
            "catia_sheetmetal_export_flat",
        }

    def test_there_is_no_wall_or_flange_operation(self) -> None:
        """**The headline, and it is a measurement.** `CATShfInterfaces` declares no
        creation method of any kind, so a `catia_sheetmetal_wall` would be a promise the
        bridge cannot keep — and the registry is read by the agent as the list of things
        it can do."""
        names = {o.name for o in OPERATIONS}

        assert not any("wall" in name for name in names if "sheetmetal" in name)
        assert not any("flange" in name for name in names if "sheetmetal" in name)

    def test_the_unreachable_ones_are_recorded_with_their_reason(self) -> None:
        """Declared as data rather than simply absent: "the registry has no wall
        operation" is indistinguishable from "nobody got to it yet" when it is missing,
        and the first is a measured fact while the second invites somebody to write one
        blind."""
        assert set(UNREACHABLE_OVER_COM) == {"wall", "flange", "unfold_in_place"}
        for name, reason in UNREACHABLE_OVER_COM.items():
            assert len(reason) > 40, name

    def test_none_of_the_unreachable_ones_is_declared(self) -> None:
        names = {o.name for o in OPERATIONS}
        for unreachable in UNREACHABLE_OVER_COM:
            assert f"catia_sheetmetal_{unreachable}" not in names

    def test_every_operation_is_on_the_sheet_metal_workbench(self) -> None:
        for operation in OPERATIONS:
            if operation.name.startswith("catia_sheetmetal"):
                assert operation.workbench is Workbench.SHEET_METAL

    def test_reading_is_a_read_and_writing_is_a_write(self) -> None:
        tiers = {
            o.name: o.tier for o in OPERATIONS if o.name.startswith("catia_sheetmetal")
        }

        assert tiers["catia_sheetmetal_parameters"] is Tier.READ
        assert tiers["catia_sheetmetal_bends"] is Tier.READ
        assert tiers["catia_sheetmetal_start"] is Tier.WRITE
        # Writes a file on the workstation, so it is not a read however much it only
        # reads the part.
        assert tiers["catia_sheetmetal_export_flat"] is Tier.WRITE


class TestTheOpenKernelRefusesEachOneByName:
    """CLAUDE.md's rule: every declared operation is in `HANDLERS`, `LOCALLY_SERVED` or
    `refusals.REASONS`. These are refused, and each refusal says something true about the
    open kernel rather than "not supported"."""

    @pytest.mark.parametrize(
        "name",
        [
            "catia_sheetmetal_start",
            "catia_sheetmetal_parameters",
            "catia_sheetmetal_bends",
            "catia_sheetmetal_export_flat",
        ],
    )
    def test_it_has_a_reason(self, name: str) -> None:
        assert name in REASONS
        assert len(REASONS[name]) > 50

    def test_the_export_refusal_says_why_serving_it_would_be_pointless(self) -> None:
        """The sharpest of the four: served by the open kernel it would write Kryova's
        unfold back to Kryova and check nothing. The whole value of the operation is that
        the arithmetic on the other end is somebody else's."""
        assert "check nothing" in REASONS["catia_sheetmetal_export_flat"]

    def test_no_refusal_claims_the_open_kernel_cannot_fold(self) -> None:
        """It can — `app/sheetmetal/fold.py` builds the blank and the solid as one
        calculation. The refusals are about CATIA's *document-scoped parameter set*, which
        the open kernel genuinely does not have, and saying otherwise would be a false
        statement about this repository's own capability."""
        for name, reason in REASONS.items():
            if name.startswith("catia_sheetmetal"):
                assert "cannot fold" not in reason
                assert "no sheet metal" not in reason.lower()


class TestTheParameterNamesAreLocalised:
    def test_the_french_spellings_are_the_measured_ones(self) -> None:
        """Measured on this French R33 seat. A table keyed on "Thickness" finds nothing
        here and the operation silently does nothing — the localisation trap CLAUDE.md
        documents for menus, arriving through a parameter name."""
        assert "Epaisseur" in PARAMETER_NAMES["thickness"]
        assert "Rayon pli" in PARAMETER_NAMES["bend_radius"]
        assert "Facteur perte au pli" in PARAMETER_NAMES["k_factor"]

    def test_each_quantity_offers_more_than_one_spelling(self) -> None:
        for quantity, spellings in PARAMETER_NAMES.items():
            assert len(spellings) >= 2, quantity

    def test_the_din_activity_parameter_is_a_path_not_a_name(self) -> None:
        """It is `…\\Formule norme DIN\\Activity` — a sub-parameter of the formula, which
        is why switching the formula off is a write to *that* and not to the K-factor."""
        assert any("Activity" in name for name in PARAMETER_NAMES["din_formula_active"])

    def test_the_type_library_is_recorded(self) -> None:
        """Late binding does not see these classes — the same trap DMU Kinematics has,
        where a session concluded a licence was missing when it was not. The GUID is the
        way in."""
        assert TYPE_LIBRARY == "{AEDE231A-8E0E-11D3-827B-006094EB7FE4}"
