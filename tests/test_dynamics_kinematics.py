"""Kinematics, closures, reactions and the load-case bridge — against closed form.

Every target number in this file is **computed from a formula written out here**,
never pasted from a previous run. That is the whole point of the file: a
kinematics module checked against its own recorded output is checked against
nothing, and it will happily keep reporting the same wrong number for a year.
So the slider-crank is checked against `x = r·cosθ + √(l² − r²·sin²θ)` typed into
the test, the four-bar against the Grashof inequality and against its own vector
loop closing, the rotating body against `ω²r`, and the pendulum against
`T = 2π√(l/g)` — that last one through the reaction it produces, which is the
strongest form available: if the period is right, the rod carries **no tangential
force**, and the residual is `mg·A³/6` exactly, so the test asserts the size of
the error rather than picking a tolerance.

None of this needs a dynamics engine, which is why it is worth having first.
PyChrono is not installed here and is not in `requirements.txt`; see
`engine.ChronoEngine`, which already records why (`pip install pychrono` fetches
an unrelated timing library of the same name). Nothing below imports it.

Three things this file also pins, because they are the honesty half:

* a **dead centre** is where a naive slider-crank divides by zero, and both are
  checked with the exact closed-form acceleration `−(r + r²/l)·ω²` and
  `(r − r²/l)·ω²`;
* what cannot be computed comes back **UNMEASURED**, in
  `app.design.assertions`'s own vocabulary, and never as a zero. A closed loop's
  reaction is the case that matters: summing it one way round would be very easy
  and the number would be indefensible;
* a load case is **refused** rather than built on an unknown reaction, because a
  zero force on a bracket passes every check there is.
"""

from __future__ import annotations

import math

import pytest

from app.design.assertions import Assertion, Outcome, check_assertions
from app.dynamics import kinematics, loadcases, reactions
from app.dynamics.closures import four_bar, grashof, slider_crank
from app.dynamics.errors import MechanismError
from app.dynamics.pose import norm
from app.dynamics.types import (
    Body,
    Driver,
    Joint,
    Mechanism,
    MechanismResult,
    MotionPath,
    MotionRange,
)
from app.kernel import provenance
from app.solve.types import BoxSelector, Fixture, Material

#: mm/s². The one gravity in the codebase, re-imported rather than restated.
G = 9806.65

STEEL = Material(
    name="steel",
    youngs_modulus_mpa=210000.0,
    poissons_ratio=0.3,
    yield_strength_mpa=250.0,
    density_kg_m3=7850.0,
)


# -- the closed forms, written out once and used by the tests below -----------


def slider_position(r: float, l: float, theta: float) -> float:  # noqa: E741
    """`x = r·cosθ + √(l² − r²·sin²θ)` — the textbook centred slider-crank."""
    return r * math.cos(theta) + math.sqrt(l * l - r * r * math.sin(theta) ** 2)


def slider_dx_dtheta(r: float, l: float, theta: float) -> float:  # noqa: E741
    """`dx/dθ`, differentiated by hand from the line above."""
    root = math.sqrt(l * l - r * r * math.sin(theta) ** 2)
    return -r * math.sin(theta) - r * r * math.sin(theta) * math.cos(theta) / root


def slider_d2x_dtheta2(r: float, l: float, theta: float) -> float:  # noqa: E741
    """`d²x/dθ²`, differentiated by hand again.

    At a dead centre `sinθ = 0`, this collapses to `−r ∓ r²/l`, which is the
    number the dead-centre tests assert directly.
    """
    root = math.sqrt(l * l - r * r * math.sin(theta) ** 2)
    return (
        -r * math.cos(theta)
        - r * r * math.cos(2.0 * theta) / root
        - (r**4) * (math.sin(theta) ** 2) * (math.cos(theta) ** 2) / root**3
    )


class TestTheSliderCrankPosition:
    """`x = r·cosθ + √(l² − r²·sin²θ)`, at angles including both dead centres."""

    r = 50.0
    rod = 200.0

    @pytest.mark.parametrize(
        "degrees", [0.0, 17.0, 45.0, 90.0, 123.5, 180.0, 217.0, 270.0, 359.0]
    )
    def test_position_is_the_formula(self, degrees: float) -> None:
        theta = math.radians(degrees)

        state = slider_crank(crank_mm=self.r, rod_mm=self.rod, angle_rad=theta)

        assert state.position_mm == pytest.approx(
            slider_position(self.r, self.rod, theta), rel=1e-12
        )

    def test_top_dead_centre_is_the_crank_plus_the_rod(self) -> None:
        """θ = 0: the crank and rod are in line, so `x = r + l` exactly, and
        the derivative vanishes — the place a naive implementation divides."""
        state = slider_crank(crank_mm=self.r, rod_mm=self.rod, angle_rad=0.0, rate_rad_s=10.0)

        assert state.position_mm == pytest.approx(self.r + self.rod, rel=1e-12)
        assert state.velocity_mm_s == pytest.approx(0.0, abs=1e-12)

    def test_bottom_dead_centre_is_the_rod_minus_the_crank(self) -> None:
        state = slider_crank(
            crank_mm=self.r, rod_mm=self.rod, angle_rad=math.pi, rate_rad_s=10.0
        )

        assert state.position_mm == pytest.approx(self.rod - self.r, rel=1e-12)
        assert state.velocity_mm_s == pytest.approx(0.0, abs=1e-9)

    def test_the_stroke_is_twice_the_crank(self) -> None:
        """The single fact every press drawing states, and it falls out of the
        two dead centres rather than being asserted separately anywhere."""
        top = slider_crank(crank_mm=self.r, rod_mm=self.rod, angle_rad=0.0).position_mm
        bottom = slider_crank(
            crank_mm=self.r, rod_mm=self.rod, angle_rad=math.pi
        ).position_mm

        assert top - bottom == pytest.approx(2.0 * self.r, rel=1e-12)

    def test_the_rod_angle_is_the_one_that_closes_the_loop(self) -> None:
        """`sin(rod angle) = r·sinθ / l` — the side-thrust number."""
        theta = math.radians(63.0)

        state = slider_crank(crank_mm=self.r, rod_mm=self.rod, angle_rad=theta)

        assert math.sin(state.rod_angle_rad) == pytest.approx(
            self.r * math.sin(theta) / self.rod, rel=1e-12
        )


class TestTheSliderCrankDerivatives:
    """Analytic, and checked against the hand-differentiated closed form."""

    r = 50.0
    rod = 200.0
    omega = 10.0

    @pytest.mark.parametrize("degrees", [0.0, 30.0, 45.0, 90.0, 180.0, 250.0, 300.0])
    def test_velocity_is_dx_dtheta_times_omega(self, degrees: float) -> None:
        theta = math.radians(degrees)

        state = slider_crank(
            crank_mm=self.r, rod_mm=self.rod, angle_rad=theta, rate_rad_s=self.omega
        )

        assert state.velocity_mm_s == pytest.approx(
            slider_dx_dtheta(self.r, self.rod, theta) * self.omega, rel=1e-9, abs=1e-9
        )

    @pytest.mark.parametrize("degrees", [0.0, 30.0, 45.0, 90.0, 180.0, 250.0, 300.0])
    def test_acceleration_is_d2x_dtheta2_times_omega_squared(self, degrees: float) -> None:
        """At constant crank speed the second term of the chain rule is zero, so
        this is the whole of it — and it is why a reciprocating machine has
        inertia loads at all."""
        theta = math.radians(degrees)

        state = slider_crank(
            crank_mm=self.r, rod_mm=self.rod, angle_rad=theta, rate_rad_s=self.omega
        )

        assert state.acceleration_mm_s2 == pytest.approx(
            slider_d2x_dtheta2(self.r, self.rod, theta) * self.omega**2, rel=1e-9, abs=1e-9
        )

    def test_top_dead_centre_acceleration_is_minus_r_plus_r_squared_over_l(self) -> None:
        """The peak inertia load of a press, and the number a drawing quotes.

        Written out rather than taken from the general formula above so that a
        change to that formula cannot move this one with it.
        """
        state = slider_crank(
            crank_mm=self.r, rod_mm=self.rod, angle_rad=0.0, rate_rad_s=self.omega
        )

        expected = -(self.r + self.r**2 / self.rod) * self.omega**2
        assert state.acceleration_mm_s2 == pytest.approx(expected, rel=1e-12)

    def test_bottom_dead_centre_acceleration_is_plus_r_minus_r_squared_over_l(self) -> None:
        state = slider_crank(
            crank_mm=self.r, rod_mm=self.rod, angle_rad=math.pi, rate_rad_s=self.omega
        )

        expected = (self.r - self.r**2 / self.rod) * self.omega**2
        assert state.acceleration_mm_s2 == pytest.approx(expected, rel=1e-9)

    def test_the_two_dead_centres_are_not_the_same_size(self) -> None:
        """Stated because it is the physical content of the `r²/l` term: a
        connecting rod of finite length makes the top of the stroke the harder
        end, and an implementation that dropped that term would pass every
        symmetric check."""
        top = slider_crank(
            crank_mm=self.r, rod_mm=self.rod, angle_rad=0.0, rate_rad_s=self.omega
        ).acceleration_mm_s2
        bottom = slider_crank(
            crank_mm=self.r, rod_mm=self.rod, angle_rad=math.pi, rate_rad_s=self.omega
        ).acceleration_mm_s2

        assert abs(top) > abs(bottom)
        assert abs(top) - abs(bottom) == pytest.approx(
            2.0 * self.r**2 / self.rod * self.omega**2, rel=1e-9
        )

    def test_crank_acceleration_carries_through_the_chain_rule(self) -> None:
        """`a = x''(θ)·ω² + x'(θ)·α`. At 90° the second term is the whole of the
        difference, so it cannot be dropped without this failing."""
        theta = math.pi / 2.0
        alpha = 3.0

        steady = slider_crank(
            crank_mm=self.r, rod_mm=self.rod, angle_rad=theta, rate_rad_s=self.omega
        )
        accelerating = slider_crank(
            crank_mm=self.r,
            rod_mm=self.rod,
            angle_rad=theta,
            rate_rad_s=self.omega,
            accel_rad_s2=alpha,
        )

        difference = accelerating.acceleration_mm_s2 - steady.acceleration_mm_s2
        assert difference == pytest.approx(
            slider_dx_dtheta(self.r, self.rod, theta) * alpha, rel=1e-9
        )


class TestTheSliderCrankRefusesRatherThanApproximates:
    def test_a_rod_shorter_than_the_crank_cannot_close_at_ninety_degrees(self) -> None:
        with pytest.raises(MechanismError) as caught:
            slider_crank(crank_mm=100.0, rod_mm=50.0, angle_rad=math.pi / 2.0)

        assert "does not close" in str(caught.value)

    def test_the_exactly_degenerate_case_is_refused_not_divided_by(self) -> None:
        """The guard, verified by reasoning about breaking it.

        With `crank == rod` at 90° the discriminant is **exactly** 0.0, so the
        square root is 0.0 and every derivative term divides by it. The guard is
        `discriminant <= 0.0`; written as `< 0.0` this same call reaches
        `d_root = -perpendicular * d_perp / root` with `root == 0.0` and raises
        `ZeroDivisionError` — an exception naming nothing, from a mechanism whose
        real problem is that it is at a limit. The refusal below is what keeps
        that from being what the user sees.
        """
        with pytest.raises(MechanismError):
            slider_crank(crank_mm=100.0, rod_mm=100.0, angle_rad=math.pi / 2.0)

    def test_a_non_positive_link_is_refused_by_name(self) -> None:
        with pytest.raises(MechanismError) as caught:
            slider_crank(crank_mm=0.0, rod_mm=100.0, angle_rad=0.0)

        assert "positive links" in str(caught.value)

    def test_the_offset_case_still_matches_its_own_loop(self) -> None:
        """A desaxe crank is the same loop with the slide displaced, so the
        Pythagoras that defines it must still hold at the returned position."""
        r, rod, offset = 40.0, 150.0, 12.0
        theta = math.radians(71.0)

        state = slider_crank(
            crank_mm=r, rod_mm=rod, angle_rad=theta, offset_mm=offset
        )

        across = r * math.sin(theta) - offset
        along = state.position_mm - r * math.cos(theta)
        assert math.hypot(across, along) == pytest.approx(rod, rel=1e-12)


# -- four-bar -----------------------------------------------------------------

#: The four families, as link lengths. Chosen so the shortest link is a
#: different member in each, which is what the classification turns on.
CRANK_ROCKER = dict(ground_mm=100.0, input_mm=40.0, coupler_mm=120.0, output_mm=80.0)
DOUBLE_CRANK = dict(ground_mm=40.0, input_mm=100.0, coupler_mm=120.0, output_mm=80.0)
DOUBLE_ROCKER = dict(ground_mm=100.0, input_mm=80.0, coupler_mm=40.0, output_mm=120.0)
NON_GRASHOF = dict(ground_mm=60.0, input_mm=40.0, coupler_mm=50.0, output_mm=100.0)
PARALLELOGRAM = dict(ground_mm=100.0, input_mm=100.0, coupler_mm=100.0, output_mm=100.0)


def sweeps_all_the_way_round(**links: float) -> bool:
    """Whether the input link can be driven through a full revolution."""
    for degrees in range(0, 360):
        try:
            four_bar(angle_rad=math.radians(degrees), **links)
        except MechanismError:
            return False
    return True


def output_swing_deg(**links: float) -> float:
    """How far the output link swings over the input angles that assemble."""
    angles = []
    for degrees in range(0, 360):
        try:
            angles.append(four_bar(angle_rad=math.radians(degrees), **links).output_rad)
        except MechanismError:
            continue
    return math.degrees(max(angles) - min(angles))


class TestGrashof:
    """`s + l < p + q` decides whether any link fully rotates.

    The inequality is written out in each test, and then — the half that makes
    this more than a restatement — the *solver* is asked to agree: a linkage the
    inequality says cranks must sweep 360° without refusing, and one it says
    rocks must lock somewhere. Two independent routes to the same answer.
    """

    def test_a_crank_rocker_satisfies_the_inequality(self) -> None:
        s, p, q, longest = sorted(CRANK_ROCKER.values())

        assert s + longest < p + q
        assert "crank-rocker" in grashof(**CRANK_ROCKER)
        assert "input link fully rotates" in grashof(**CRANK_ROCKER)

    def test_and_the_solver_agrees_the_input_cranks_and_the_output_rocks(self) -> None:
        assert sweeps_all_the_way_round(**CRANK_ROCKER)
        # A rocker swings through an arc, not a revolution.
        assert output_swing_deg(**CRANK_ROCKER) < 180.0

    def test_a_double_crank_is_the_one_with_the_shortest_link_grounded(self) -> None:
        s, p, q, longest = sorted(DOUBLE_CRANK.values())

        assert s + longest < p + q
        assert s == DOUBLE_CRANK["ground_mm"]
        assert "double-crank" in grashof(**DOUBLE_CRANK)

    def test_and_the_solver_agrees_the_output_completes_a_revolution(self) -> None:
        """Not the same test as "it never refuses": a crank-rocker also never
        refuses. What distinguishes a double-crank is that the *output* angle,
        unwrapped, comes back through a full turn."""
        assert sweeps_all_the_way_round(**DOUBLE_CRANK)

        previous = None
        total = 0.0
        for degrees in range(0, 361):
            angle = four_bar(angle_rad=math.radians(degrees), **DOUBLE_CRANK).output_rad
            if previous is not None:
                step = (angle - previous + math.pi) % (2.0 * math.pi) - math.pi
                total += step
            previous = angle

        assert abs(math.degrees(total)) == pytest.approx(360.0, abs=1e-6)

    def test_a_double_rocker_is_the_one_with_the_shortest_link_as_coupler(self) -> None:
        s, p, q, longest = sorted(DOUBLE_ROCKER.values())

        assert s + longest < p + q
        assert s == DOUBLE_ROCKER["coupler_mm"]
        assert "double-rocker" in grashof(**DOUBLE_ROCKER)

    def test_and_the_solver_agrees_neither_pivot_can_be_driven_round(self) -> None:
        """The distinction that matters when someone asks where to put the
        motor: a double-rocker's coupler rotates and neither pivot does, so the
        input link cannot be driven through a revolution."""
        assert not sweeps_all_the_way_round(**DOUBLE_ROCKER)

    def test_a_non_grashof_linkage_fails_the_inequality_and_locks(self) -> None:
        s, p, q, longest = sorted(NON_GRASHOF.values())

        assert s + longest > p + q
        assert "non-Grashof" in grashof(**NON_GRASHOF)
        assert not sweeps_all_the_way_round(**NON_GRASHOF)

    def test_a_parallelogram_is_a_change_point_and_not_a_crank_rocker(self) -> None:
        """The commonest four-bar there is. Its four links go collinear once a
        turn and it can flip to the anti-parallelogram branch on its own, which
        is a real property of the lengths and not a numerical wobble."""
        s, p, q, longest = sorted(PARALLELOGRAM.values())

        assert s + longest == p + q
        assert "change-point" in grashof(**PARALLELOGRAM)
        assert "crank-rocker" not in grashof(**PARALLELOGRAM)


class TestAChangePointSurvivesTheArithmetic:
    """A change point built out of ordinary decimals is still a change point.

    **This was a live defect, found here.** The equality test was exact float
    `==`, so the linkage 0.1 / 0.3 / 0.5 / 0.7 — where `s + l = p + q = 0.8` by
    arithmetic anyone would do in their head — came back as *"Grashof
    double-crank: both input and output fully rotate"*, because `0.1 + 0.7` is
    `0.7999999999999999` and `0.3 + 0.5` is `0.8`. The same linkage written in
    millimetres classified correctly, so the answer depended on the unit the
    designer typed in, and the confident half was the wrong half: the solver
    refuses at the collinear angle of that very linkage. Fixed with a relative
    comparison; these tests are what would catch it coming back.
    """

    SMALL = dict(ground_mm=0.1, input_mm=0.3, coupler_mm=0.5, output_mm=0.7)
    SAME_LINKAGE_IN_MM = dict(
        ground_mm=100.0, input_mm=300.0, coupler_mm=500.0, output_mm=700.0
    )

    def test_the_arithmetic_really_does_not_come_out_equal(self) -> None:
        """The premise of the bug, asserted rather than asserted about."""
        assert 0.1 + 0.7 != 0.3 + 0.5

    def test_it_is_reported_as_a_change_point_anyway(self) -> None:
        assert "change-point" in grashof(**self.SMALL)

    def test_scaling_every_link_by_a_thousand_changes_nothing(self) -> None:
        """A classification that depends on the unit is not a classification."""
        assert grashof(**self.SMALL) == grashof(**self.SAME_LINKAGE_IN_MM)

    def test_and_the_solver_confirms_it_locks(self) -> None:
        """The half that makes the misclassification indefensible rather than
        merely untidy: `grashof` said both links fully rotate while `four_bar`
        refuses to assemble the same linkage at 0°."""
        with pytest.raises(MechanismError) as caught:
            four_bar(angle_rad=0.0, **self.SAME_LINKAGE_IN_MM)

        assert "dead centre" in str(caught.value)

    def test_a_link_a_visible_amount_off_is_still_told_apart(self) -> None:
        """The tolerance absorbs float noise and nothing an engineer means. A
        millimetre on a 700 mm link is a change of design, not of rounding."""
        nearly = dict(self.SAME_LINKAGE_IN_MM) | {"output_mm": 701.0}

        assert "change-point" not in grashof(**nearly)


class TestTheFourBarLoopCloses:
    """The vector loop is the definition, so the returned angles must satisfy it."""

    @pytest.mark.parametrize("degrees", [0.0, 37.0, 90.0, 111.0, 180.0, 250.0, 330.0])
    def test_the_loop_residual_is_zero(self, degrees: float) -> None:
        theta = math.radians(degrees)
        state = four_bar(angle_rad=theta, **CRANK_ROCKER)

        x = (
            CRANK_ROCKER["input_mm"] * math.cos(theta)
            + CRANK_ROCKER["coupler_mm"] * math.cos(state.coupler_rad)
            - CRANK_ROCKER["output_mm"] * math.cos(state.output_rad)
            - CRANK_ROCKER["ground_mm"]
        )
        y = (
            CRANK_ROCKER["input_mm"] * math.sin(theta)
            + CRANK_ROCKER["coupler_mm"] * math.sin(state.coupler_rad)
            - CRANK_ROCKER["output_mm"] * math.sin(state.output_rad)
        )

        assert x == pytest.approx(0.0, abs=1e-9)
        assert y == pytest.approx(0.0, abs=1e-9)

    def test_the_crossed_branch_is_a_different_assembly_of_the_same_loop(self) -> None:
        """Two solutions per input angle, both closing the loop; which one a
        linkage is in is decided at assembly and reported, not chosen."""
        theta = math.radians(37.0)

        open_branch = four_bar(angle_rad=theta, **CRANK_ROCKER)
        crossed = four_bar(angle_rad=theta, crossed=True, **CRANK_ROCKER)

        assert crossed.crossed is True
        assert open_branch.output_rad != pytest.approx(crossed.output_rad)

    @pytest.mark.parametrize("degrees", [17.0, 37.0, 95.0, 200.0, 300.0])
    def test_the_rates_are_the_derivatives_of_the_angles(self, degrees: float) -> None:
        """Analytic derivative against a central difference of the *closed-form*
        position solution — not against a recorded number."""
        theta = math.radians(degrees)
        h = 1e-6

        state = four_bar(angle_rad=theta, rate_rad_s=1.0, **CRANK_ROCKER)
        before = four_bar(angle_rad=theta - h, **CRANK_ROCKER)
        after = four_bar(angle_rad=theta + h, **CRANK_ROCKER)

        assert state.output_rate_rad_s == pytest.approx(
            (after.output_rad - before.output_rad) / (2.0 * h), rel=1e-6
        )
        assert state.coupler_rate_rad_s == pytest.approx(
            (after.coupler_rad - before.coupler_rad) / (2.0 * h), rel=1e-6
        )

    @pytest.mark.parametrize("degrees", [37.0, 95.0, 200.0])
    def test_the_accelerations_are_the_derivatives_of_the_rates(self, degrees: float) -> None:
        theta = math.radians(degrees)
        h = 1e-6

        state = four_bar(angle_rad=theta, rate_rad_s=1.0, **CRANK_ROCKER)
        before = four_bar(angle_rad=theta - h, rate_rad_s=1.0, **CRANK_ROCKER)
        after = four_bar(angle_rad=theta + h, rate_rad_s=1.0, **CRANK_ROCKER)

        assert state.output_accel_rad_s2 == pytest.approx(
            (after.output_rate_rad_s - before.output_rate_rad_s) / (2.0 * h), rel=1e-6
        )

    def test_rates_are_linear_in_the_input_rate(self) -> None:
        """A kinematic ratio is a property of the pose alone, so doubling the
        crank speed doubles the output speed exactly."""
        theta = math.radians(37.0)

        slow = four_bar(angle_rad=theta, rate_rad_s=1.0, **CRANK_ROCKER)
        fast = four_bar(angle_rad=theta, rate_rad_s=2.0, **CRANK_ROCKER)

        assert fast.output_rate_rad_s == pytest.approx(2.0 * slow.output_rate_rad_s, rel=1e-12)

    def test_a_link_that_cannot_reach_is_refused_with_the_span(self) -> None:
        with pytest.raises(MechanismError) as caught:
            four_bar(angle_rad=0.0, **NON_GRASHOF)

        assert "cannot be assembled" in str(caught.value)


# -- centripetal acceleration -------------------------------------------------


def spinner(rate_rad_s: float, radius_mm: float, mass_kg: float = 2.0) -> Mechanism:
    """One body on a pin, its centre of mass `radius_mm` off the axis.

    Gravity is deliberately zero so the acceleration under test is the
    centripetal term and nothing else.
    """
    return Mechanism(
        name="spinner",
        bodies=(
            Body(name="rotor", mass_kg=mass_kg, centre_of_mass_mm=(radius_mm, 0.0, 0.0)),
        ),
        joints=(
            Joint(name="shaft", kind="revolute", body="rotor", parent=None, axis=(0.0, 0.0, 1.0)),
        ),
        drivers=(Driver(joint="shaft", kind="constant", rate=rate_rad_s),),
        gravity_mm_s2=(0.0, 0.0, 0.0),
    )


class TestCentripetalAcceleration:
    """`a = ω²r`, at every instant, for a body at constant rotation."""

    def test_the_magnitude_is_omega_squared_r(self) -> None:
        omega, radius = 7.0, 30.0

        path = kinematics.evaluate(spinner(omega, radius), MotionRange(duration_s=1.0, samples=9))

        for acceleration in path.motion("rotor").acceleration_mm_s2:
            assert norm(acceleration) == pytest.approx(omega * omega * radius, rel=1e-12)

    def test_it_points_at_the_axis(self) -> None:
        """Centripetal means inward. A sign error here gives a bearing load of
        the right size pointing the wrong way, which sizes a lug backwards."""
        omega, radius = 7.0, 30.0
        path = kinematics.evaluate(spinner(omega, radius), MotionRange(duration_s=1.0, samples=9))
        motion = path.motion("rotor")

        for position, acceleration in zip(
            motion.position_mm, motion.acceleration_mm_s2, strict=True
        ):
            outward = norm(position)
            assert outward == pytest.approx(radius, rel=1e-12)
            # a · r̂ = −ω²r: exactly opposed to the radius vector.
            projected = sum(a * p for a, p in zip(acceleration, position, strict=True)) / outward
            assert projected == pytest.approx(-omega * omega * radius, rel=1e-12)
            assert acceleration[2] == pytest.approx(0.0, abs=1e-9)

    def test_it_is_quadratic_in_rate_and_linear_in_radius(self) -> None:
        """Two independent scalings, because `ω²r` has been written as `ωr²`
        and as `ωr` in real code and both look plausible in one reading."""
        base = kinematics.evaluate(spinner(5.0, 20.0), MotionRange(duration_s=1.0, samples=3))
        faster = kinematics.evaluate(spinner(10.0, 20.0), MotionRange(duration_s=1.0, samples=3))
        wider = kinematics.evaluate(spinner(5.0, 40.0), MotionRange(duration_s=1.0, samples=3))

        one = base.motion("rotor").peak_acceleration_mm_s2
        assert faster.motion("rotor").peak_acceleration_mm_s2 == pytest.approx(4.0 * one, rel=1e-12)
        assert wider.motion("rotor").peak_acceleration_mm_s2 == pytest.approx(2.0 * one, rel=1e-12)

    def test_a_body_on_the_axis_has_no_acceleration_at_all(self) -> None:
        path = kinematics.evaluate(spinner(7.0, 0.0), MotionRange(duration_s=1.0, samples=5))

        assert path.motion("rotor").peak_acceleration_mm_s2 == pytest.approx(0.0, abs=1e-12)

    def test_the_angular_velocity_is_the_drivers_rate_in_radians(self) -> None:
        """`ω × (ω × r)` in degrees per second is wrong by 57.3², which is a
        factor of 3283 — plausible-looking on a plot and useless."""
        path = kinematics.evaluate(spinner(7.0, 30.0), MotionRange(duration_s=1.0, samples=3))

        assert path.motion("rotor").angular_velocity_rad_s[0] == pytest.approx(
            (0.0, 0.0, 7.0), rel=1e-12
        )

    def test_the_whirling_reaction_is_m_omega_squared_r(self) -> None:
        """The same closed form, one layer up: what the bearing carries."""
        omega, radius, mass = 7.0, 30.0, 2.0
        mechanism = spinner(omega, radius, mass)
        path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=9))

        reaction = reactions.compute(mechanism, path)["shaft"]

        # kg·mm/s² is a millinewton; MASS_KG_TO_TONNE is the one conversion.
        assert reaction.peak_force_n == pytest.approx(
            mass * omega * omega * radius * 1e-3, rel=1e-12
        )


# -- the pendulum period ------------------------------------------------------


def pendulum(length_mm: float, amplitude_rad: float, frequency_hz: float) -> Mechanism:
    """A point mass on a rigid massless rod, swinging in the xz plane.

    The pin is about +y, so a positive angle swings the bob towards −x, and the
    bob hangs at `(0, 0, −l)` in the body frame at zero.
    """
    return Mechanism(
        name="pendulum",
        bodies=(Body(name="bob", mass_kg=1.0, centre_of_mass_mm=(0.0, 0.0, -length_mm)),),
        joints=(
            Joint(name="pivot", kind="revolute", body="bob", parent=None, axis=(0.0, 1.0, 0.0)),
        ),
        drivers=(
            Driver(
                joint="pivot",
                kind="harmonic",
                amplitude=amplitude_rad,
                frequency_hz=frequency_hz,
            ),
        ),
        gravity_mm_s2=(0.0, 0.0, -G),
    )


def worst_tangential_force_n(
    length_mm: float, amplitude_rad: float, frequency_hz: float
) -> float:
    """The largest force the pivot must apply *across* the rod, over one cycle.

    A rod (or a string) can only pull along its own length. So if the motion
    imposed on the pendulum is its true free motion, the tangential reaction is
    zero — and any tangential force that is left is the amount by which the
    imposed motion is *not* free swinging. That makes this the strongest
    available check on the period: it is a physical statement about the answer,
    not a comparison against a stored number.
    """
    mechanism = pendulum(length_mm, amplitude_rad, frequency_hz)
    period_s = 1.0 / frequency_hz
    path = kinematics.evaluate(mechanism, MotionRange(duration_s=period_s, samples=25))
    reaction = reactions.compute(mechanism, path)["pivot"]

    omega = 2.0 * math.pi * frequency_hz
    worst = 0.0
    for index, t in enumerate(path.times_s):
        theta = amplitude_rad * math.sin(omega * t)
        # Unit vector along the swing, perpendicular to the rod.
        tangent = (-math.cos(theta), 0.0, math.sin(theta))
        force = reaction.force_n[index]
        along = sum(a * b for a, b in zip(force, tangent, strict=True))
        worst = max(worst, abs(along))
    return worst


class TestThePendulumPeriod:
    """`T = 2π√(l/g)` for small amplitude, checked through the rod's reaction.

    The package prescribes motion rather than integrating it, so there is no
    "simulate and read the period off" to do. The equivalent — and a stronger
    statement — is to *impose* the small-amplitude period and show the mechanism
    then needs no tangential force to sustain it. The residual is not merely
    small: it is `mg·A³/6`, the third-order term of `sin θ ≈ θ`, so the test
    asserts the size of the error rather than a tolerance somebody chose.
    """

    length_mm = 250.0
    amplitude_rad = 0.02

    def natural_frequency_hz(self) -> float:
        return math.sqrt(G / self.length_mm) / (2.0 * math.pi)

    def test_the_period_is_two_pi_root_l_over_g(self) -> None:
        """The formula itself, in the units this package uses. mm and mm/s²
        divide out, so the period is in seconds whatever the length unit — which
        is why `l` in mm against `g` in mm/s² is not a unit bug."""
        expected_s = 2.0 * math.pi * math.sqrt(self.length_mm / G)

        assert 1.0 / self.natural_frequency_hz() == pytest.approx(expected_s, rel=1e-12)

    def test_swinging_at_that_period_needs_no_tangential_force(self) -> None:
        worst = worst_tangential_force_n(
            self.length_mm, self.amplitude_rad, self.natural_frequency_hz()
        )

        # mg·A³/6, in newtons: the exact third-order residual of sin θ ≈ θ.
        third_order_n = 1.0 * (G * 1e-3) * self.amplitude_rad**3 / 6.0
        assert worst == pytest.approx(third_order_n, rel=1e-3)

    def test_the_residual_is_third_order_in_the_amplitude(self) -> None:
        """Halving the swing must cut the leftover force by eight. This is what
        says the residual is the small-angle approximation and not an error in
        the recursion, which would not scale that way."""
        frequency = self.natural_frequency_hz()

        big = worst_tangential_force_n(self.length_mm, 0.04, frequency)
        small = worst_tangential_force_n(self.length_mm, 0.02, frequency)

        assert big / small == pytest.approx(8.0, rel=1e-2)

    @pytest.mark.parametrize("wrong_factor", [0.8, 1.25])
    def test_the_wrong_period_needs_a_real_force_to_sustain(self, wrong_factor: float) -> None:
        """The guard verified by breaking what it guards.

        If the test above passed for *any* period it would be measuring nothing.
        Driven 25% off the natural frequency, the same pendulum needs a
        tangential force orders of magnitude larger — because something has to
        push it.
        """
        right = worst_tangential_force_n(
            self.length_mm, self.amplitude_rad, self.natural_frequency_hz()
        )
        wrong = worst_tangential_force_n(
            self.length_mm, self.amplitude_rad, self.natural_frequency_hz() * wrong_factor
        )

        assert wrong > 100.0 * right

    def test_a_longer_pendulum_is_slower_by_the_square_root(self) -> None:
        """Four times the length is twice the period — the whole content of the
        formula, and the part a linear implementation would get wrong."""
        short = 2.0 * math.pi * math.sqrt(self.length_mm / G)
        long = 2.0 * math.pi * math.sqrt(4.0 * self.length_mm / G)

        assert long == pytest.approx(2.0 * short, rel=1e-12)
        assert worst_tangential_force_n(
            4.0 * self.length_mm,
            self.amplitude_rad,
            math.sqrt(G / (4.0 * self.length_mm)) / (2.0 * math.pi),
        ) == pytest.approx(1.0 * (G * 1e-3) * self.amplitude_rad**3 / 6.0, rel=1e-3)


# -- reactions ----------------------------------------------------------------


def hanging_link(mass_kg: float = 3.0, length_mm: float = 100.0) -> Mechanism:
    """A parked link on a pin: nothing moves, and it still hangs off the pin."""
    return Mechanism(
        name="hanging",
        bodies=(Body(name="link", mass_kg=mass_kg, centre_of_mass_mm=(0.0, 0.0, -length_mm)),),
        joints=(
            Joint(name="pin", kind="revolute", body="link", parent=None, axis=(0.0, 1.0, 0.0)),
        ),
        drivers=(Driver(joint="pin", kind="constant", rate=0.0),),
    )


class TestAParkedMachineStillHangs:
    def test_the_reaction_is_the_weight(self) -> None:
        """`F = m·(a − g)` with `a = 0` is `−m·g`: the weight, upward. A
        recursion using `a` alone reports zero for a parked machine, which is
        not what a bearing feels."""
        mass = 3.0
        mechanism = hanging_link(mass)
        path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=3))

        reaction = reactions.compute(mechanism, path)["pin"]

        assert reaction.force_n[0] == pytest.approx((0.0, 0.0, mass * G * 1e-3), rel=1e-12)

    def test_nothing_is_moving_at_all(self) -> None:
        """Asserted so the test above cannot be satisfied by an acceleration
        that happens to equal g."""
        mechanism = hanging_link()
        path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=3))

        assert path.motion("link").peak_acceleration_mm_s2 == pytest.approx(0.0, abs=1e-12)

    def test_mass_is_converted_once_from_kilograms(self) -> None:
        """3 kg weighs 29.4 N, not 29 420 N. `MASS_KG_TO_TONNE` is the single
        named conversion and a second one would be a factor of a thousand."""
        mechanism = hanging_link(3.0)
        path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=2))

        peak = reactions.compute(mechanism, path)["pin"].peak_force_n

        assert peak is not None
        assert 29.0 < peak < 30.0

    def test_a_two_link_chain_balances_when_summed_either_way(self) -> None:
        """A free-body check: a joint carries its own link plus everything
        hanging off it. Zero to rounding, or the descendant set is wrong."""
        mechanism = Mechanism(
            name="arm",
            bodies=(
                Body(name="upper", mass_kg=4.0, centre_of_mass_mm=(150.0, 0.0, 0.0)),
                Body(name="fore", mass_kg=2.0, centre_of_mass_mm=(100.0, 0.0, 0.0)),
            ),
            joints=(
                Joint(name="shoulder", kind="revolute", body="upper", parent=None, axis=(0, 1, 0)),
                Joint(
                    name="elbow",
                    kind="revolute",
                    body="fore",
                    parent="upper",
                    origin_mm=(300.0, 0.0, 0.0),
                    axis=(0.0, 1.0, 0.0),
                ),
            ),
            drivers=(
                Driver(joint="shoulder", kind="harmonic", amplitude=0.4, frequency_hz=0.7),
                Driver(joint="elbow", kind="harmonic", amplitude=0.9, frequency_hz=1.3),
            ),
        )
        path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=13))
        computed = reactions.compute(mechanism, path)

        assert reactions.free_body_check(mechanism, path, computed) < 1e-9

    def test_the_shoulder_carries_more_than_the_elbow(self) -> None:
        """Stated because a free-body residual of zero is also what a recursion
        that returned the same number for every joint would produce."""
        mechanism = Mechanism(
            name="arm",
            bodies=(
                Body(name="upper", mass_kg=4.0, centre_of_mass_mm=(150.0, 0.0, 0.0)),
                Body(name="fore", mass_kg=2.0, centre_of_mass_mm=(100.0, 0.0, 0.0)),
            ),
            joints=(
                Joint(name="shoulder", kind="revolute", body="upper", parent=None, axis=(0, 1, 0)),
                Joint(
                    name="elbow",
                    kind="revolute",
                    body="fore",
                    parent="upper",
                    origin_mm=(300.0, 0.0, 0.0),
                    axis=(0.0, 1.0, 0.0),
                ),
            ),
            drivers=(
                Driver(joint="shoulder", kind="constant", rate=0.0),
                Driver(joint="elbow", kind="constant", rate=0.0),
            ),
        )
        path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=2))
        computed = reactions.compute(mechanism, path)

        shoulder = computed["shoulder"].peak_force_n
        elbow = computed["elbow"].peak_force_n
        assert shoulder is not None and elbow is not None
        assert shoulder == pytest.approx(6.0 * G * 1e-3, rel=1e-12)
        assert elbow == pytest.approx(2.0 * G * 1e-3, rel=1e-12)


class TestWhatTheEvaluatorRefusesByName:
    """Every one of these is a mechanism somebody will actually write down."""

    def test_a_closed_loop_is_named_as_a_closed_loop(self) -> None:
        loop = Mechanism(
            name="loop",
            bodies=(Body(name="crank", mass_kg=1.0), Body(name="rod", mass_kg=1.0)),
            joints=(
                Joint(name="a", kind="revolute", body="crank", parent=None),
                Joint(name="b", kind="revolute", body="rod", parent="crank"),
                Joint(name="c", kind="revolute", body="rod", parent=None),
            ),
            drivers=(Driver(joint="a", kind="constant", rate=1.0),),
        )

        with pytest.raises(MechanismError) as caught:
            kinematics.topological_order(loop)

        message = str(caught.value)
        assert "closed kinematic loop" in message
        assert "closures" in message

    def test_a_spherical_joint_is_refused_with_the_alternative(self) -> None:
        mechanism = Mechanism(
            name="ball",
            bodies=(Body(name="link", mass_kg=1.0),),
            joints=(Joint(name="ball", kind="spherical", body="link", parent=None),),
        )

        with pytest.raises(MechanismError) as caught:
            kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=2))

        assert "three revolute joints" in str(caught.value)

    def test_an_undriven_coordinate_is_a_dynamics_problem_not_a_zero(self) -> None:
        """Pinning it at zero would answer a question nobody asked, plausibly."""
        mechanism = Mechanism(
            name="free",
            bodies=(Body(name="link", mass_kg=1.0),),
            joints=(Joint(name="pin", kind="revolute", body="link", parent=None),),
        )

        with pytest.raises(MechanismError) as caught:
            kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=2))

        assert "nothing drives it" in str(caught.value)

    def test_a_fixed_joint_with_a_driver_is_a_contradiction(self) -> None:
        mechanism = Mechanism(
            name="fixed",
            bodies=(Body(name="link", mass_kg=1.0),),
            joints=(Joint(name="weld", kind="fixed", body="link", parent=None),),
            drivers=(Driver(joint="weld", kind="constant", rate=1.0),),
        )

        with pytest.raises(MechanismError) as caught:
            kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=2))

        assert "no coordinate to prescribe" in str(caught.value)

    def test_a_body_no_joint_moves_is_named(self) -> None:
        mechanism = Mechanism(
            name="floating",
            bodies=(Body(name="link", mass_kg=1.0), Body(name="orphan", mass_kg=1.0)),
            joints=(Joint(name="pin", kind="revolute", body="link", parent=None),),
            drivers=(Driver(joint="pin", kind="constant", rate=1.0),),
        )

        with pytest.raises(MechanismError) as caught:
            kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=2))

        assert "orphan" in str(caught.value)

    def test_a_tabulated_driver_is_refused_unless_the_caveat_is_accepted(self) -> None:
        """A linear interpolant's second derivative is zero inside every span
        and a step at every knot. That is not an acceleration a load case should
        be built from, so it has to be asked for."""
        mechanism = Mechanism(
            name="duty",
            bodies=(Body(name="slide", mass_kg=1.0),),
            joints=(Joint(name="axis", kind="prismatic", body="slide", parent=None),),
            drivers=(
                Driver(
                    joint="axis",
                    kind="table",
                    times_s=(0.0, 0.5, 1.0),
                    values=(0.0, 10.0, 0.0),
                ),
            ),
        )
        motion = MotionRange(duration_s=1.0, samples=5)

        with pytest.raises(MechanismError) as caught:
            kinematics.evaluate(mechanism, motion)
        assert "finite differences" in str(caught.value)

        path = kinematics.evaluate(mechanism, motion, allow_tabulated_acceleration=True)
        assert path.warnings
        assert "finite differences" in path.method


# -- what could not be computed is UNMEASURED, never a number -----------------


def closed_loop_result() -> MechanismResult:
    """A mechanism whose reactions are genuinely indeterminate for rigid bodies."""
    loop = Mechanism(
        name="loop",
        bodies=(Body(name="crank", mass_kg=1.0), Body(name="rod", mass_kg=1.0)),
        joints=(
            Joint(name="a", kind="revolute", body="crank", parent=None),
            Joint(name="b", kind="revolute", body="rod", parent="crank"),
            Joint(name="c", kind="revolute", body="rod", parent=None),
        ),
        drivers=(Driver(joint="a", kind="constant", rate=1.0),),
    )
    empty = MotionPath(mechanism="loop", times_s=(), bodies={})
    return MechanismResult(
        mechanism="loop",
        engine="closed-form serial-chain recursion",
        path=empty,
        reactions=reactions.compute(loop, empty),
    )


class TestUnmeasuredIsNeverANumber:
    """`app.design.assertions`'s vocabulary, applied where it costs something.

    Summing a closed loop's reactions one way round would be four lines and the
    number would look exactly like the others in the report. It is refused
    instead, the reason travels all the way to the assertion result, and the
    claim comes back `UNMEASURED` — which is not a pass and not a failure.
    """

    def test_the_reaction_carries_a_reason_and_no_numbers(self) -> None:
        result = closed_loop_result()

        reaction = result.reaction("b")
        assert reaction.available is False
        assert reaction.force_n == ()
        assert "closed kinematic loop" in reaction.unavailable_reason

    def test_the_peak_is_none_rather_than_zero(self) -> None:
        """Zero and unknown are the opposite of each other: a joint carrying
        nothing is fine and a joint whose load nobody knows is not."""
        result = closed_loop_result()

        assert result.peak_reaction_force_n is None

    def test_the_payload_records_it_as_unavailable(self) -> None:
        payload = closed_loop_result().to_measurement_payload()

        assert provenance.basis_of(payload, "joints.b.peak_force_n") is provenance.Basis.UNAVAILABLE
        assert "peak_force_n" not in payload["joints"]["b"]

    def test_an_assertion_on_it_comes_back_unmeasured(self) -> None:
        payload = closed_loop_result().to_measurement_payload()
        claim = Assertion(
            name="pin load",
            measure="joints.b.peak_force_n",
            comparison="<=",
            bound=4200.0,
            note="bearing catalogue limit",
        )

        report = check_assertions([claim], payload)

        assert report.results[0].outcome is Outcome.UNMEASURED
        assert report.results[0].measured is None
        assert not report.ok

    def test_the_reason_survives_into_the_assertion_result(self) -> None:
        """An UNMEASURED with no reason is a shrug. The user has to be able to
        tell "the loop is indeterminate" from "you forgot to run it"."""
        payload = closed_loop_result().to_measurement_payload()
        claim = Assertion(name="pin load", measure="joints.b.peak_force_n", comparison="<=", bound=1.0)

        result = check_assertions([claim], payload).results[0]

        assert "closed kinematic loop" in result.reason

    def test_a_point_mass_moment_is_approximated_and_the_force_beside_it_is_not(self) -> None:
        """Per-path provenance, which is the whole reason it is a sidecar:
        `F = m·a` does not involve inertia, so the force stays exact while the
        moment loses `I·α + ω × I·ω` and says so."""
        mechanism = hanging_link()
        path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=3))
        result = MechanismResult(
            mechanism="hanging",
            engine="closed-form serial-chain recursion",
            path=path,
            reactions=reactions.compute(mechanism, path),
        )

        payload = result.to_measurement_payload()

        assert provenance.basis_of(payload, "joints.pin.peak_force_n") is provenance.Basis.MEASURED
        assert (
            provenance.basis_of(payload, "joints.pin.peak_moment_n_mm")
            is provenance.Basis.APPROXIMATED
        )
        assert "point-mass" in provenance.method_for(payload, "joints.pin.peak_moment_n_mm")

    def test_an_inertia_tensor_makes_the_moment_measured(self) -> None:
        """The other half: the caveat is a consequence of missing input, not a
        blanket disclaimer that would stop meaning anything."""
        mechanism = Mechanism(
            name="hanging",
            bodies=(
                Body(
                    name="link",
                    mass_kg=3.0,
                    centre_of_mass_mm=(0.0, 0.0, -100.0),
                    inertia_kg_mm2=(1000.0, 1000.0, 10.0),
                ),
            ),
            joints=(
                Joint(name="pin", kind="revolute", body="link", parent=None, axis=(0.0, 1.0, 0.0)),
            ),
            drivers=(Driver(joint="pin", kind="constant", rate=0.0),),
        )
        path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=3))
        result = MechanismResult(
            mechanism="hanging", engine="closed-form", path=path,
            reactions=reactions.compute(mechanism, path),
        )

        payload = result.to_measurement_payload()

        assert (
            provenance.basis_of(payload, "joints.pin.peak_moment_n_mm")
            is provenance.Basis.MEASURED
        )


# -- the load-case bridge -----------------------------------------------------


class TestTheLoadCaseBridge:
    """Reaction plus inertia into the *existing* `app.solve` vocabulary."""

    def spinning_result(self) -> tuple[Mechanism, MechanismResult]:
        mechanism = spinner(7.0, 30.0, 2.0)
        path = kinematics.evaluate(mechanism, MotionRange(duration_s=1.0, samples=5))
        return mechanism, MechanismResult(
            mechanism="spinner",
            engine="closed-form serial-chain recursion",
            path=path,
            reactions=reactions.compute(mechanism, path),
        )

    def mount(self) -> loadcases.JointMount:
        return loadcases.cylinder_mount(
            "shaft", axis_point=(0.0, 0.0, 0.0), axis_direction=(0.0, 0.0, 1.0), radius_mm=10.0
        )

    def fixtures(self) -> list[Fixture]:
        return [Fixture(where=BoxSelector(min=(-1.0, -1.0, -1.0), max=(1.0, 1.0, 1.0)))]

    def test_the_mount_carries_m_omega_squared_r(self) -> None:
        _, result = self.spinning_result()

        derived = loadcases.build(
            result,
            mounts=[self.mount()],
            material=STEEL,
            fixtures=self.fixtures(),
            gravity_mm_s2=(0.0, 0.0, 0.0),
        )

        assert derived.mount_forces_n["shaft"] == pytest.approx(
            2.0 * 7.0**2 * 30.0 * 1e-3, rel=1e-12
        )

    def test_the_inertial_field_is_gravity_minus_acceleration(self) -> None:
        """d'Alembert, and the reason no new load type was needed: `g − a` is
        exactly what `GravityLoad` already applies."""
        mechanism, result = self.spinning_result()

        derived = loadcases.build(
            result,
            mounts=[self.mount()],
            material=STEEL,
            fixtures=self.fixtures(),
            gravity_mm_s2=mechanism.gravity_mm_s2,
            inertia_of="rotor",
        )

        assert derived.inertial_field_mm_s2 == pytest.approx(7.0**2 * 30.0, rel=1e-12)
        assert loadcases.field_in_g(derived.inertial_field_mm_s2) == pytest.approx(0.1499, abs=1e-3)

    def test_a_spinning_part_is_told_it_was_reduced_to_a_uniform_field(self) -> None:
        """The one approximation the bridge makes, named rather than absorbed."""
        mechanism, result = self.spinning_result()

        derived = loadcases.build(
            result,
            mounts=[self.mount()],
            material=STEEL,
            fixtures=self.fixtures(),
            gravity_mm_s2=mechanism.gravity_mm_s2,
            inertia_of="rotor",
        )

        assert any("rotating at" in note for note in derived.notes)
        assert any("CentrifugalLoad" in note for note in derived.notes)

    def test_the_instant_is_recorded_not_averaged(self) -> None:
        """A load case is one instant of a cycle. An average occurs at no point
        in the machine's life."""
        _, result = self.spinning_result()

        derived = loadcases.build(
            result,
            mounts=[self.mount()],
            material=STEEL,
            fixtures=self.fixtures(),
            gravity_mm_s2=(0.0, 0.0, 0.0),
        )

        assert derived.instant_s in result.path.times_s
        assert derived.summary()["instant_index"] == derived.instant_index

    def test_a_case_cannot_be_built_on_an_unknown_reaction(self) -> None:
        """The refusal that matters: a zero force on a bracket passes every
        check there is, so a closed loop must not produce a load case at all."""
        result = closed_loop_result()

        with pytest.raises(MechanismError) as caught:
            loadcases.build(
                result,
                mounts=[loadcases.cylinder_mount(
                    "b", axis_point=(0.0, 0.0, 0.0), axis_direction=(0.0, 0.0, 1.0), radius_mm=5.0
                )],
                material=STEEL,
                fixtures=self.fixtures(),
                gravity_mm_s2=(0.0, 0.0, -G),
            )

        assert "was not computed" in str(caught.value)
        assert "closed kinematic loop" in str(caught.value)

    def test_a_case_with_no_fixture_is_refused_before_it_is_solved(self) -> None:
        _, result = self.spinning_result()

        with pytest.raises(MechanismError) as caught:
            loadcases.build(
                result,
                mounts=[self.mount()],
                material=STEEL,
                fixtures=[],
                gravity_mm_s2=(0.0, 0.0, 0.0),
            )

        assert "singular" in str(caught.value)

    def test_a_bearing_distribution_needs_a_bore(self) -> None:
        with pytest.raises(MechanismError) as caught:
            loadcases.JointMount(
                joint="shaft",
                where=BoxSelector(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0)),
                bearing=True,
            )

        assert "CylinderSelector" in str(caught.value)

    def test_an_envelope_is_one_case_per_sampled_instant(self) -> None:
        """What fatigue needs: the worst stress is not always at the worst
        force once the load turns relative to the part."""
        _, result = self.spinning_result()

        cases = loadcases.envelope(
            result,
            mounts=[self.mount()],
            material=STEEL,
            fixtures=self.fixtures(),
            gravity_mm_s2=(0.0, 0.0, 0.0),
        )

        assert len(cases) == len(result.path.times_s)
        assert [case.instant_index for case in cases] == list(range(len(result.path.times_s)))
