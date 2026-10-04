"""M8 — the motorcycle chassis and swingarm. Where E8, E9 and E17.3 meet on one machine.

M7 was the first rung whose answer was a load rather than a dimension, and its own
`unproven` names what it could not say: *"E8 — no fatigue. The reactions this rung
computes are the input `app/fatigue/duty.py` wants. Nothing has joined them up."* This
is the rung that joins them up, and it needs a third thing to be worth doing — a frame
a fabricator could actually cut, which is E17.3's weldment.

So three descriptions of one machine have to agree:

* **the solids**, built and weighed through the real kernel;
* **the cut list**, summed the way a fabricator sums stock;
* **the mechanism**, derived from the product graph and swung about its pivot.

**The centre of this file is the transition cycle.** `app/fatigue/duty.py` opens by
saying what it exists to prevent: count each mode alone, multiply by its repetitions and
add, and the widest cycle the machine ever sees is in no mode's count — it runs from one
mode's trough to another's peak and closes once per pass of the sequence. A motorcycle is
the textbook case. Under drive and braking the swingarm is loaded one way about a high
mean; over a pothole the rear unloads and reverses. Neither mode holds both extremes, so
the widest cycle of the life belongs to neither, and the tests below measure exactly how
much is missed by summing.

**Measured on Windows, 2026-09-17**, by building the real machine through the real OCCT
kernel and running the real dynamics and the real counting. Every number asserted here
was read off that run before it was written down.

Two findings from it, both kept as tests:

1. **A tube meeting a cylinder at an angle is coped, and a square end buried in it is an
   interference.** Drawn from the headstock's tangent plane, the down tube's tilted end
   face reached back to x = 14.8 mm inside a headstock whose surface is at 25 mm, and the
   clash check found **13.529 mm³** of overlap — small enough to miss by eye on a machine
   holding 1.1 × 10⁶ mm³ of steel. `app/manufacture/weldment.py` already refuses to
   compute a cope ("a cope is a fitting decision"), so the tube stands clear and says so.
2. **A duty cycle whose worst mode holds both extremes demonstrates nothing.** The first
   table here gave the pothole the highest peak *and* the lowest trough, and the
   transition cycles then came out exactly equal to that mode's own amplitude — ratio
   1.000. The arithmetic was right and the rung was silent. That is the easy way to write
   a duty cycle that cannot show the error it exists to show, and it is why the means
   straddle.

The kernel is imported inside the tests that need it, as `test_mission_m7.py` does, so
collecting this file does not drag ~166 MB of OCP into every run.
"""

from __future__ import annotations

import math

import pytest

from app.design.missions import (
    LADDER,
    MissionOutcome,
    m8_counted_life,
    m8_cut_length_mm,
    m8_duty_cycle,
    m8_mass_kg,
    m8_nominal_stress_mpa,
    m8_pivot_to_axle_mm,
    m8_swingarm_inertia_kg_mm2,
    m8_swingarm_mass_kg,
    m8_swingarm_weight_n,
    m8_tube_mass_kg,
    m8_weldment,
    mission,
    run_mission,
)

DENSITY_KG_M3 = 7870.0

#: The peak pivot reaction the real run produced, in newtons. Used by the fatigue tests
#: so they do not each pay for a kernel build; the one test that checks it against the
#: real mechanism is `TestTheMechanism::test_the_peak_reaction_is_the_number_the_fatigue_tests_use`.
PEAK_REACTION_N = 45.07820700684005


def _tube_mass_kg(outside_mm: float, wall_mm: float, length_mm: float) -> float:
    """The closed form, written out here rather than imported.

    A test that computes a number with the same function the code does is a test that
    the function is self-consistent, which is not the claim. This is `pi/4 (D^2 - d^2) L
    rho` typed out from the definition.
    """
    bore_mm = outside_mm - 2.0 * wall_mm
    area_mm2 = math.pi / 4.0 * (outside_mm**2 - bore_mm**2)
    return area_mm2 * length_mm * 1e-9 * DENSITY_KG_M3


class TestTheMachineAsArithmetic:
    """Every number the rung binds, re-derived from the tube sizes."""

    @pytest.mark.parametrize(
        ("name", "outside_mm", "wall_mm", "length_mm"),
        [
            ("headstock", 50.0, 6.0, 180.0),
            ("spine", 60.0, 3.0, 520.0),
            ("pivot_boss", 45.0, 10.0, 30.0),
            ("swingarm_arm", 45.0, 3.0, 560.0),
            ("swingarm_brace", 30.0, 2.0, 175.0),
        ],
    )
    def test_each_tube_weighs_its_annulus(
        self, name: str, outside_mm: float, wall_mm: float, length_mm: float
    ) -> None:
        assert m8_tube_mass_kg(name) == pytest.approx(
            _tube_mass_kg(outside_mm, wall_mm, length_mm), rel=1e-12
        )

    def test_the_brace_spans_between_the_arms_and_not_through_them(self) -> None:
        """175 mm is `2 x (110 - 22.5)`: the track less one arm radius each side.

        A brace drawn the full 220 mm track would end at the arms' *centrelines* and
        overlap both — an interference of real size, and a frame nobody can fabricate.
        """
        assert m8_tube_mass_kg("swingarm_brace") == pytest.approx(
            _tube_mass_kg(30.0, 2.0, 2.0 * (110.0 - 22.5)), rel=1e-12
        )

    def test_the_down_tube_length_comes_from_its_two_ends(self) -> None:
        """Derived, never typed. Start (44, 0, 630), end (545, 0, 300)."""
        expected = math.dist((44.0, 0.0, 630.0), (545.0, 0.0, 300.0))
        assert m8_tube_mass_kg("down_tube") == pytest.approx(
            _tube_mass_kg(38.0, 2.5, expected), rel=1e-12
        )

    def test_the_machine_weighs_what_its_tubes_add_up_to(self) -> None:
        expected = (
            _tube_mass_kg(50.0, 6.0, 180.0)
            + _tube_mass_kg(60.0, 3.0, 520.0)
            + _tube_mass_kg(38.0, 2.5, math.dist((44.0, 0.0, 630.0), (545.0, 0.0, 300.0)))
            + 2.0 * _tube_mass_kg(45.0, 10.0, 30.0)
            + 2.0 * _tube_mass_kg(45.0, 3.0, 560.0)
            + _tube_mass_kg(30.0, 2.0, 175.0)
        )
        assert m8_mass_kg() == pytest.approx(expected, rel=1e-12)
        assert m8_mass_kg() == pytest.approx(8.9404, abs=5e-4)

    def test_only_the_swingarm_moves(self) -> None:
        """The frame is ground. Declaring it a body would put the whole chassis into the
        swinging mass and make every pivot reaction larger — the direction nothing
        downstream complains about."""
        expected = 2.0 * _tube_mass_kg(45.0, 3.0, 560.0) + _tube_mass_kg(30.0, 2.0, 175.0)
        assert m8_swingarm_mass_kg() == pytest.approx(expected, rel=1e-12)
        assert m8_swingarm_mass_kg() < m8_mass_kg()

    def test_the_static_reaction_is_the_swingarms_own_weight(self) -> None:
        assert m8_swingarm_weight_n() == pytest.approx(
            m8_swingarm_mass_kg() * 9.80665, rel=1e-9
        )

    def test_pivot_to_axle_is_not_called_a_wheelbase(self) -> None:
        """There are no forks and no front wheel, so there is no wheelbase to state."""
        assert m8_pivot_to_axle_mm() == pytest.approx(560.0)


class TestTheSwingarmsInertia:
    """The parallel-axis theorem over three tubes, and the axis each is axial about."""

    def test_it_is_the_closed_form_written_out_independently(self) -> None:
        arm = _tube_mass_kg(45.0, 3.0, 560.0)
        brace = _tube_mass_kg(30.0, 2.0, 175.0)

        def axial(outside: float, wall: float, mass: float) -> float:
            ro = outside / 2.0
            return 0.5 * mass * (ro**2 + (ro - wall) ** 2)

        def transverse(outside: float, wall: float, length: float, mass: float) -> float:
            ro = outside / 2.0
            return mass / 12.0 * (3.0 * (ro**2 + (ro - wall) ** 2) + length**2)

        shift = arm * 110.0**2
        expected = (
            2.0 * (axial(45.0, 3.0, arm) + shift) + transverse(30.0, 2.0, 175.0, brace),
            2.0 * transverse(45.0, 3.0, 560.0, arm) + axial(30.0, 2.0, brace),
            2.0 * (transverse(45.0, 3.0, 560.0, arm) + shift)
            + transverse(30.0, 2.0, 175.0, brace),
        )
        assert m8_swingarm_inertia_kg_mm2() == pytest.approx(expected, rel=1e-12)

    def test_the_arms_axial_term_is_not_the_one_the_pivot_feels(self) -> None:
        """Iyy is the only component the pivot resists, and it is the arms' *transverse*
        inertia plus the brace's axial. Swap the two and the swingarm's resistance to
        being swung comes out wrong by the ratio of a tube's axial to transverse inertia
        — for a 45x3 at 560 long, **a factor of 59.5**, measured. No dimension changes and
        no mass changes, so nothing else would notice.

        (An earlier draft of this test asserted "nearly three orders of magnitude" and
        `> 500`. That was written from an impression of how slender the tube is rather
        than from the arithmetic, and the first run caught it — the ratio is
        `(3(Ro^2+Ri^2) + L^2) / (6(Ro^2+Ri^2))`, which for these numbers is 59.5 and not
        900.)"""
        ixx, iyy, izz = m8_swingarm_inertia_kg_mm2()
        arm = _tube_mass_kg(45.0, 3.0, 560.0)
        ro = 22.5
        arm_axial = 0.5 * arm * (ro**2 + (ro - 3.0) ** 2)
        arm_transverse = arm / 12.0 * (3.0 * (ro**2 + (ro - 3.0) ** 2) + 560.0**2)

        assert arm_transverse / arm_axial == pytest.approx(59.5, abs=0.1)
        assert iyy > 2.0 * arm_transverse
        assert iyy < izz  # Izz carries the same transverse term *plus* the track offset.
        assert ixx < iyy  # Ixx is the arms about their own axes; the track offset dominates.


class TestTheCutListAndTheSolidsAgree:
    """E17.3's half: one frame, two arithmetics, and a tube drawn at one length and cut
    at another is the only thing that makes them disagree."""

    def test_every_tube_is_in_the_weldment(self) -> None:
        assert len(m8_weldment().members) == 8

    def test_the_cut_list_sums_to_the_length_the_specs_build(self) -> None:
        """The claim. Measured: **2654.9175 mm** by both routes."""
        total = sum(line.centreline_mm * line.quantity for line in m8_weldment().cut_list())
        assert total == pytest.approx(m8_cut_length_mm(), rel=1e-12)
        assert total == pytest.approx(2654.9175, abs=1e-3)

    def test_the_repeated_tubes_are_grouped_and_counted(self) -> None:
        """A cut list is a stockist's order, so two identical arms are one line of two."""
        by_stock = {line.stock: line for line in m8_weldment().cut_list()}
        assert by_stock["CHS 45x3"].quantity == 2
        assert by_stock["CHS 45x10"].quantity == 2
        assert by_stock["CHS 60x3"].quantity == 1

    def test_the_stock_names_are_what_a_stockist_is_asked_for(self) -> None:
        assert {line.stock for line in m8_weldment().cut_list()} == {
            "CHS 30x2",
            "CHS 38x2.5",
            "CHS 45x10",
            "CHS 45x3",
            "CHS 50x6",
            "CHS 60x3",
        }

    def test_the_welds_all_join_members_that_exist(self) -> None:
        """`Weldment` refuses a weld naming a member it does not hold; this is the
        positive form, so the six declared welds are known to be reachable."""
        held = {member.name for member in m8_weldment().members}
        for weld in m8_weldment().welds:
            assert set(weld.members) <= held, weld.name


class TestTheDutyCycleFindsWhatSummingMisses:
    """The centre of the rung. `app/fatigue/duty.py`'s opening paragraph, measured."""

    def test_the_widest_cycle_of_the_life_is_in_no_modes_count(self) -> None:
        """**The trap, as a number.** Measured: the transition cycles carry an amplitude
        of 6.474 MPa where the widest any single mode contains is 2.266 — a factor of
        **2.857**. Sum the per-mode counts and that cycle is not in the answer at all."""
        counted = m8_counted_life(PEAK_REACTION_N)
        widest_mode = max(
            max(m.within_block.largest_amplitude_mpa, m.between_repetitions.largest_amplitude_mpa)
            for m in counted.modes
        )

        assert counted.transitions.largest_amplitude_mpa > widest_mode
        assert counted.transitions.largest_amplitude_mpa / widest_mode == pytest.approx(
            2.857, abs=5e-3
        )

    def test_the_transition_cycles_exist_and_are_counted_once_a_pass(self) -> None:
        """Four modes, 5 000 passes: one transition per join, and the joins are what the
        sequence has that the modes do not. Measured 19 999 — one short of 4 x 5 000
        because the life's final residue never closes."""
        counted = m8_counted_life(PEAK_REACTION_N)
        assert counted.transitions.total_cycles == pytest.approx(19_999.0, abs=1.0)

    def test_the_life_is_counted_without_expanding_it(self) -> None:
        """17.45 million cycles from four blocks of three points. Expanding the history
        to count it is what the decomposition exists to avoid."""
        counted = m8_counted_life(PEAK_REACTION_N)
        assert counted.collective.total_cycles == pytest.approx(17_449_999.0, abs=2.0)

    def test_a_three_point_block_closes_nothing_within_itself(self) -> None:
        """Trough, peak, trough leaves everything on the stack: the cycle closes where one
        repetition meets the next. Worth pinning because a reader comparing only
        `within_block` would find every mode empty and conclude the count was broken."""
        counted = m8_counted_life(PEAK_REACTION_N)
        for mode in counted.modes:
            assert mode.within_block.total_cycles == 0.0
            assert mode.between_repetitions.total_cycles > 0.0

    def test_the_means_straddle_so_neither_extreme_lives_in_one_mode(self) -> None:
        """Why the trap is demonstrable here at all. `braking` holds the highest stress
        and `pothole` the lowest, and no mode holds both."""
        duty = m8_duty_cycle(PEAK_REACTION_N)
        highest = {m.name: max(m.history.values_mpa) for m in duty.modes}
        lowest = {m.name: min(m.history.values_mpa) for m in duty.modes}

        assert max(highest, key=lambda name: highest[name]) == "braking"
        assert min(lowest, key=lambda name: lowest[name]) == "pothole"

    def test_every_number_in_the_spectrum_says_it_is_assumed(self) -> None:
        """No road-load data exists in this repository, so the sources say so in those
        words rather than citing something. M5's position on shear strength, applied to
        a load spectrum."""
        duty = m8_duty_cycle(PEAK_REACTION_N)
        assert "assumed for this rung and not measured" in duty.source
        for mode in duty.modes:
            assert "assumed for this rung and not measured" in mode.source
            assert "assumed for this rung and not measured" in mode.history.source

    def test_the_stress_is_a_hand_calculation_and_scales_with_the_load(self) -> None:
        """`sigma = F L / (2 Z)` — linear in the reaction, which is what makes it a hand
        calculation rather than a solve. Two arms share the load; one would be twice."""
        assert m8_nominal_stress_mpa(2.0 * PEAK_REACTION_N) == pytest.approx(
            2.0 * m8_nominal_stress_mpa(PEAK_REACTION_N), rel=1e-12
        )
        modulus = math.pi * (45.0**4 - 39.0**4) / (32.0 * 45.0)
        assert m8_nominal_stress_mpa(PEAK_REACTION_N) == pytest.approx(
            PEAK_REACTION_N * 560.0 / (2.0 * modulus), rel=1e-12
        )


class TestTheMachineBuilds:
    """Through the real kernel. Everything above is arithmetic; this is the machine."""

    def test_every_claim_passes(self) -> None:
        from app.kernel import OcctRunner

        result = run_mission(mission("M8"), runner_factory=OcctRunner)

        assert result.outcome is MissionOutcome.PASSED, result.reason
        assert result.passed

    def test_the_peak_reaction_is_the_number_the_fatigue_tests_use(self) -> None:
        """The one place the constant at the top of this file is checked against the real
        mechanism. Without it the fatigue tests would be arithmetic about a number nobody
        measured."""
        from app.kernel import OcctRunner

        result = run_mission(mission("M8"), runner_factory=OcctRunner)
        assert result.motion is not None
        peak = result.motion.payload["motion"]["peak_reaction_force_n"]

        assert peak == pytest.approx(PEAK_REACTION_N, rel=1e-9)

    def test_swinging_it_loads_the_pivot_more_than_standing_still(self) -> None:
        """Measured: 45.078 N moving against 36.592 N standing — 23% more. The claim that
        would fail if the motion were not being applied at all."""
        from app.kernel import OcctRunner

        result = run_mission(mission("M8"), runner_factory=OcctRunner)
        assert result.motion is not None
        peak = result.motion.payload["motion"]["peak_reaction_force_n"]

        assert peak > m8_swingarm_weight_n()
        assert peak / m8_swingarm_weight_n() == pytest.approx(1.232, abs=5e-3)

    def test_nothing_overlaps_anything(self) -> None:
        """The claim the first real build failed, at 13.529 mm³, where the down tube's
        tilted end face buried itself in the headstock."""
        from app.kernel import OcctRunner

        result = run_mission(mission("M8"), runner_factory=OcctRunner)
        check = next(c for c in result.checks if c.name == "no two members occupy the same space")

        assert check.outcome.value == "passed", check.message


class TestTheRungInTheLadder:
    def test_it_moved_by_the_rule_and_not_by_a_decision(self) -> None:
        """M6's rule: a rung whose stated prerequisites are met and which is still marked
        pending is a ladder that has stopped measuring anything. M8's three declared
        needs — E8's fatigue, E9.4's joint-load extraction and E17.3's tubular weldments
        — are all met, so it moved."""
        rung = mission("M8")

        assert rung.buildable
        assert rung.moving is not None
        assert rung.needs == ()

    def test_only_m9_is_left_pending(self) -> None:
        pending = [rung.rung for rung in LADDER if not rung.buildable]
        assert pending == ["M9"]

    def test_it_says_it_homologates_nothing(self) -> None:
        """Homologation is a third of what the `hard` column names and none of it is here.
        A rung claiming a road-legal motorcycle would be the most misleading entry in the
        gallery, because it would be the most convincing."""
        caveats = mission("M8").unproven

        assert any("homologates anything" in caveat for caveat in caveats)
        assert any("not road legal" in caveat or "road legal" in caveat for caveat in caveats)

    def test_it_says_the_frame_is_never_solved(self) -> None:
        assert any("never solved as a structure" in caveat for caveat in mission("M8").unproven)

    def test_it_says_the_spectrum_is_assumed(self) -> None:
        assert any("assumed and not measured" in caveat for caveat in mission("M8").unproven)

    def test_it_states_no_eurocode_verdict(self) -> None:
        """§8 needs a γFf that `app/fatigue/eurocode3.py` records as not being in the
        pages that were read. The cycles are counted exactly; what they do to the machine
        is not claimed."""
        assert any("no EN 1993-1-9 verdict" in caveat for caveat in mission("M8").unproven)

    def test_every_caveat_names_the_phase_that_owns_it(self) -> None:
        for caveat in mission("M8").unproven:
            assert caveat.startswith("E"), caveat
