"""M7 — the 6-axis robot arm. The first rung whose answer is not a dimension.

Every rung below this one asks how big something is or whether two things fit.
M1 builds a part, M2 fits several together, M3 holds one part in two descriptions,
M4 makes a dimension a consequence of a tooth count, M5 makes a whole vertical
chain a consequence of a crank. All of them are geometry. An arm's geometry is the
easy half — what an engineer buys a robot for is what its shoulder bearing carries
while the thing is moving, and no amount of geometry contains that number.

So M7 is the first `MovingDesign`, the ladder's fourth kind of rung, and this file
is as much about that kind as about the arm. The three existing kinds each exist
because a machine could not be said in the ones before it: `assembly` because a
product is not a bigger part, `folded` because a sheet-metal part is two
descriptions that must agree. `moving` is the same argument one level up — an arm
is a product graph *and* a chain of joints, and the load at a joint is a
consequence of both together, so neither half alone can state it.

**The joints are declared against the product graph, not beside it**, which is the
whole design. Every `JointDeclaration.child` is an occurrence path in the same
`ProductStructure` the mass roll-up and the clash check walk. So the mass a
reaction is computed against *is* the mass the geometry claims were checked
against, and `derive` refuses a body it cannot weigh rather than returning a
reaction that is too small — too small being the direction in which every check
passes.

Six parts:

* **The arm as arithmetic** — the stack, the masses, the inertia tensors, all
  re-derived here from the link dimensions and never read back from `missions.py`.
* **The mechanism derived from the product** — that it has the right bodies, that
  the base is ground and not a body, that renaming a part breaks it loudly.
* **The motion** — exact, closed form, with no time stepping, and what that buys.
* **The reactions**, against the one case with a closed-form answer: standing
  still, the base joint carries exactly the weight above it.
* **The arm built wrong** — one break per guard.
* **The rung in the ladder** — what it does not claim, and that it travels.

**These tests were written on Linux on 2026-09-16 and were not run as pytest**, at
the user's instruction. Every number below was measured first by building the real
arm through the real OCCT kernel and running the real dynamics with a one-off
script.

Two findings from that run, both recorded here as tests:

1. **A payload key containing a dot is not a path — it is a key containing a dot.**
   `_motion_payload` first published flat keys spelled `"motion.total_mass_kg"`,
   and every motion claim came back NOT CHECKED against a payload that visibly
   contained them: the resolver reads `.` as a separator and looked for
   `payload["motion"]["total_mass_kg"]`. Nesting is not a style choice here.
2. **`app.dynamics` refuses an undriven revolute joint by name**, rather than
   integrating something. That refusal is the package declining to turn a
   kinematics question into a dynamics one behind the caller's back, and it is
   tested below because it is the behaviour a future rung will be tempted to work
   around.

The kernel is imported inside the tests that need it, as `test_mission_m2.py`
does, so collecting this file does not drag ~166 MB of OCP into every run.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from app.assembly.errors import StructureError
from app.design import missions as m
from app.design.errors import SpecError
from app.design.missions import Mission, MissionResult, mission, run_mission
from app.dynamics.errors import MechanismError
from app.dynamics.types import Driver

# --------------------------------------------------------------------------
# The arm, written out here from its own drawing.
# --------------------------------------------------------------------------

BASE_DIAMETER_MM = 300.0
BASE_MM = 200.0
SHOULDER_MM = (260.0, 260.0, 220.0)
UPPER_ARM_MM = (160.0, 180.0, 600.0)
FOREARM_MM = (140.0, 160.0, 500.0)
WRIST_MM = (120.0, 120.0, 160.0)
HAND_MM = (100.0, 100.0, 120.0)
FLANGE_DIAMETER_MM = 90.0
FLANGE_MM = 40.0
DENSITY_KG_M3 = 7870.0

LINKS = {
    "shoulder": SHOULDER_MM,
    "upper_arm": UPPER_ARM_MM,
    "forearm": FOREARM_MM,
    "wrist": WRIST_MM,
    "hand": HAND_MM,
}

REACH_MM = (
    BASE_MM + SHOULDER_MM[2] + UPPER_ARM_MM[2] + FOREARM_MM[2]
    + WRIST_MM[2] + HAND_MM[2] + FLANGE_MM
)

JOINT_NAMES = ("j1_base", "j2_shoulder", "j3_elbow", "j4_roll", "j5_pitch", "j6_flange")

#: Measured on the real kernel and the real dynamics on 2026-09-16, not predicted.
MEASURED_MASS_KG = 482.01889489576644
MEASURED_MOVING_MASS_KG = 370.7593910688839
MEASURED_COM_Z_MM = 635.570168248912
MEASURED_RESIDUAL_N = 9.094947017729282e-13
MEASURED_TOTAL_WEIGHT_N = 3635.90758242567
MEASURED_STATIC_BASE_FORCE_N = 3635.9075824256697
MEASURED_PEAK_BASE_FORCE_N = 4048.4330030871592
MEASURED_PEAK_JOINT_FORCE_N = {
    "j1_base": 4048.4330030871592,
    "j2_shoulder": 2907.886538807791,
    "j3_elbow": 1624.01121320954,
    "j4_roll": 424.5356662836504,
    "j5_pitch": 204.8236948104018,
    "j6_flange": 34.88322282533327,
}
MEASURED_FLANGE_TRAVEL_MM = 2330.7609915768967


def cylinder_mm3(diameter_mm: float, length_mm: float) -> float:
    return math.pi / 4.0 * diameter_mm**2 * length_mm


def expected_volumes() -> dict[str, float]:
    volumes = {name: a * b * c for name, (a, b, c) in LINKS.items()}
    volumes["base"] = cylinder_mm3(BASE_DIAMETER_MM, BASE_MM)
    volumes["flange"] = cylinder_mm3(FLANGE_DIAMETER_MM, FLANGE_MM)
    return volumes


def expected_mass_kg() -> float:
    return sum(expected_volumes().values()) * 1e-9 * DENSITY_KG_M3


def expected_moving_mass_kg() -> float:
    volumes = expected_volumes()
    return (
        sum(v for name, v in volumes.items() if name != "base") * 1e-9 * DENSITY_KG_M3
    )


def _rung(**overrides: object) -> Mission:
    return Mission(
        rung="M7",
        title="6-axis robot arm",
        era="VI",
        hard="Kinematics, dynamic loads, stiffness under motion",
        moving=m._m7_design(**overrides),  # type: ignore[arg-type]
        assertions=m._M7_ASSERTIONS,
        unproven=m._M7_UNPROVEN,
    )


def _run(rung: Mission | None = None) -> MissionResult:
    from app.kernel import OcctRunner

    return run_mission(rung or mission("M7"), runner_factory=OcctRunner)


def _failed_claims(result: MissionResult) -> set[str]:
    if result.checks is None:
        return set()
    return {check.name for check in result.checks.failed}


def _at_rest() -> Mission:
    """The same arm with every joint held still. The one case with a closed form."""
    return _rung(
        drivers=tuple(
            Driver(joint=name, kind="constant", rate=0.0) for name in JOINT_NAMES
        )
    )


class TestTheArmIsArithmeticBeforeItIsAShape:
    """Every mass and every tensor re-derived here from the link dimensions."""

    def test_the_stack_reaches_the_flange_face(self) -> None:
        assert m.m7_reach_mm() == pytest.approx(REACH_MM)
        assert m.m7_reach_mm() == pytest.approx(1840.0)

    def test_each_link_sits_on_the_one_below_it(self) -> None:
        """Derived by stacking rather than typed, M5's rule: a thickness changed in one
        of two places is the failure, and there is only one place."""
        assert m._M7_BASE_TOP_MM == pytest.approx(BASE_MM)
        assert m._M7_SHOULDER_TOP_MM == pytest.approx(BASE_MM + SHOULDER_MM[2])
        assert m._M7_HAND_TOP_MM + FLANGE_MM == pytest.approx(m.m7_reach_mm())

    def test_every_part_is_the_volume_of_its_own_shape(self) -> None:
        assert m._m7_volumes() == pytest.approx(expected_volumes())

    def test_the_arm_weighs_what_its_parts_weigh(self) -> None:
        assert m.m7_mass_kg() == pytest.approx(expected_mass_kg(), rel=1e-12)
        assert m.m7_mass_kg() == pytest.approx(MEASURED_MASS_KG, rel=1e-12)

    def test_the_moving_mass_excludes_the_base(self) -> None:
        """The base is bolted to the floor: it is in the arm's mass and not in the
        mechanism's. Getting that backwards is the one thing a reader would get wrong
        about this rung, and it makes every reaction 30% larger — which is the safe
        direction, so nothing downstream would complain."""
        assert m.m7_moving_mass_kg() == pytest.approx(expected_moving_mass_kg(), rel=1e-12)
        assert m.m7_moving_mass_kg() < m.m7_mass_kg()
        assert m.m7_mass_kg() - m.m7_moving_mass_kg() == pytest.approx(
            cylinder_mm3(BASE_DIAMETER_MM, BASE_MM) * 1e-9 * DENSITY_KG_M3
        )

    def test_a_blocks_inertia_is_the_textbook_diagonal(self) -> None:
        """`m(b^2 + c^2)/12` and its two rotations, exact for the solid this rung
        draws. Written out here rather than imported, because a tensor taken from the
        thing it checks agrees with any mistake that thing makes."""
        mass = 12.0
        a, b, c = 100.0, 200.0, 300.0
        assert m._m7_box_inertia((a, b, c), mass) == pytest.approx(
            (
                mass * (b * b + c * c) / 12.0,
                mass * (a * a + c * c) / 12.0,
                mass * (a * a + b * b) / 12.0,
            )
        )

    def test_a_cube_has_the_same_inertia_about_every_axis(self) -> None:
        """The degenerate case that catches a transposed pair of dimensions, which the
        general formula would otherwise hide behind three plausible numbers."""
        ixx, iyy, izz = m._m7_box_inertia((50.0, 50.0, 50.0), 3.0)

        assert ixx == pytest.approx(iyy) == pytest.approx(izz)

    def test_a_cylinders_axial_inertia_is_half_m_r_squared(self) -> None:
        radius = FLANGE_DIAMETER_MM / 2.0
        mass = 2.0
        across, also_across, about_axis = m._m7_cylinder_inertia(
            FLANGE_DIAMETER_MM, FLANGE_MM, mass
        )

        assert about_axis == pytest.approx(mass * radius * radius / 2.0)
        assert across == pytest.approx(also_across)
        assert across == pytest.approx(
            mass * (3.0 * radius * radius + FLANGE_MM * FLANGE_MM) / 12.0
        )

    def test_a_long_link_is_hardest_to_swing_about_its_short_axes(self) -> None:
        """The upper arm is 600 mm tall and 160 wide, so its two transverse inertias
        are several times its axial one. A tensor that came out the other way round
        would be a transposition, and every reaction would still look plausible."""
        inertia = m._m7_inertia()[m._m7_path("upper_arm")]

        assert inertia[0] > inertia[2] * 5.0
        assert inertia[1] > inertia[2] * 5.0

    def test_every_moving_link_has_a_tensor_and_the_base_does_not(self) -> None:
        """The base is ground, so it is not a body, so it has no tensor to supply."""
        inertia = m._m7_inertia()

        assert set(inertia) == {
            m._m7_path(name) for name in (*LINKS, "flange")
        }
        assert m._m7_path("base") not in inertia


class TestTheMechanismComesFromTheProductGraph:
    """The joints are declared against the graph, not beside it."""

    def test_every_joint_hangs_from_the_link_below_it(self) -> None:
        joints = m._m7_joints()
        parents = {joint.name: joint.parent for joint in joints}

        assert [joint.name for joint in joints] == list(JOINT_NAMES)
        assert parents["j1_base"] is None
        assert parents["j2_shoulder"] == m._m7_path("shoulder")
        assert parents["j6_flange"] == m._m7_path("hand")

    def test_every_body_is_a_path_the_graph_resolves(self) -> None:
        """The reason a body is named by path rather than by component: a typo is
        refused, naming what the graph does hold, instead of resolving to the wrong
        link and reporting a plausible reaction for a different arm."""
        structure = m._m7_structure()

        for joint in m._m7_joints():
            assert structure.occurrence(joint.child) is not None

    def test_a_joint_on_a_part_the_graph_does_not_have_is_refused_by_name(self) -> None:
        joints = tuple(
            dataclasses.replace(joint, child="arm/gripper.1")
            if joint.name == "j6_flange"
            else joint
            for joint in m._m7_joints()
        )

        # `StructureError`, not `MechanismError`: the refusal comes from the product
        # graph being asked for an instance it does not hold, which is the right place
        # for it — `derive` resolves every child against the structure *before* it
        # builds anything, so the graph answers first and names what it does contain.
        with pytest.raises(StructureError) as excinfo:
            _run(_rung(joints=joints))

        message = str(excinfo.value)
        assert "gripper.1" in message
        assert "flange.1" in message

    def test_the_six_axes_alternate_the_way_a_wrist_needs(self) -> None:
        """Three axes cannot span orientation: a wrist whose last three joints share
        an axis has lost a degree of freedom, and the arm silently cannot reach some
        orientations while every dimension on it stays right."""
        axes = {joint.name: joint.axis for joint in m._m7_joints()}

        assert axes["j4_roll"] != axes["j5_pitch"]
        assert axes["j5_pitch"] != axes["j6_flange"]
        assert len({axes[name] for name in ("j4_roll", "j5_pitch", "j6_flange")}) == 2

    def test_a_driver_on_a_joint_nobody_declared_is_refused(self) -> None:
        """It would move nothing and report no error at run time."""
        with pytest.raises(SpecError) as excinfo:
            m._m7_design(
                drivers=(Driver(joint="j9_nonexistent", kind="constant", rate=1.0),)
            )

        assert "j9_nonexistent" in str(excinfo.value)
        assert "j1_base" in str(excinfo.value)

    def test_a_moving_rung_with_nothing_driven_is_refused(self) -> None:
        with pytest.raises(SpecError) as excinfo:
            m._m7_design(drivers=())

        assert "static one wearing a time axis" in str(excinfo.value)

    def test_a_moving_rung_with_no_joints_is_an_assembly_rung(self) -> None:
        with pytest.raises(SpecError) as excinfo:
            m._m7_design(joints=())

        assert "is an assembly rung" in str(excinfo.value)

    def test_an_undriven_revolute_joint_is_refused_by_the_dynamics(self) -> None:
        """**`app.dynamics` declining to turn a kinematics question into a dynamics
        one behind the caller's back.** A revolute nobody drives has a motion that is
        an *output* of the forces on it, which needs an integrator and a contact model
        and would be a different claim entirely. The package says so instead of
        guessing, and this rung's ground joint is `fixed` because of it."""
        joints = tuple(
            dataclasses.replace(joint, kind="revolute") if joint.name == "j0_floor"
            else joint
            for joint in m._m7_joints(base_is_a_body=True)
        )

        with pytest.raises(MechanismError) as excinfo:
            _run(_rung(joints=joints))

        message = str(excinfo.value)
        assert "j0_floor" in message
        assert "nothing drives it" in message


class TestTheMotionIsExact:
    """Closed form, so there is no step-size error to argue about."""

    def test_it_is_not_integrated(self) -> None:
        result = _run()
        assert result.motion is not None

        assert "No time integration" in result.motion.path.method
        assert result.motion.path.warnings == ()

    def test_every_driver_differentiates_in_closed_form(self) -> None:
        """A tabulated driver would be finite-differenced and would say so in
        `MotionPath.warnings`. Harmonic and constant are exact, so the peak
        acceleration is the peak and not a sampling artefact."""
        assert all(driver.derivatives_are_exact for driver in m._m7_drivers())

    def test_the_harmonic_joints_are_the_ones_carrying_the_mass(self) -> None:
        """A constant rate is a *steady* rotation: zero angular acceleration, so the
        reaction never changes sign. That is the easy load case. The three joints
        holding the arm up reverse twice a cycle, which is where a robot's peak joint
        torque actually occurs."""
        kinds = {driver.joint: driver.kind for driver in m._m7_drivers()}

        assert kinds["j1_base"] == "harmonic"
        assert kinds["j2_shoulder"] == "harmonic"
        assert kinds["j3_elbow"] == "harmonic"

    def test_the_shoulder_spins_without_going_anywhere(self) -> None:
        """A physics check that costs nothing and would catch a great deal: the
        shoulder turns about a vertical axis through its own centre of mass, so its
        centre of mass does not move at all. Any bug that displaced bodies along the
        chain would move it."""
        result = _run()
        assert result.assembly is not None
        bodies = result.assembly.payload["motion"]["body"]

        shoulder = bodies[m._m7_body_name("shoulder")]
        assert shoulder["travel_mm"] == pytest.approx(0.0, abs=1e-9)
        assert shoulder["peak_acceleration_mm_s2"] == pytest.approx(0.0, abs=1e-9)

    def test_the_flange_travels_furthest_because_it_is_furthest_out(self) -> None:
        result = _run()
        assert result.assembly is not None
        bodies = result.assembly.payload["motion"]["body"]

        travel = {name: body["travel_mm"] for name, body in bodies.items()}
        assert travel[m._m7_body_name("flange")] == pytest.approx(
            MEASURED_FLANGE_TRAVEL_MM, rel=1e-9
        )
        assert travel[m._m7_body_name("flange")] == max(travel.values())
        assert travel[m._m7_body_name("upper_arm")] < travel[m._m7_body_name("forearm")]

    def test_it_is_sampled_where_it_was_asked_to_be(self) -> None:
        result = _run()
        assert result.motion is not None

        assert len(result.motion.path.times_s) == m._M7_SAMPLES
        assert result.motion.path.times_s[0] == pytest.approx(0.0)
        assert result.motion.path.times_s[-1] == pytest.approx(m._M7_DURATION_S)


class TestTheReactionsAgainstAClosedForm:
    """The one case an inverse-dynamics run can be checked against by hand."""

    def test_standing_still_the_base_carries_exactly_the_weight_above_it(self) -> None:
        """**The strongest claim in this file, and the only one with a closed form.**
        Hold every joint still and the whole mechanism is a statics problem with one
        answer: the ground joint carries `m g` and nothing else. Measured
        3635.9075824256697 N against a weight of 3635.90758242567 N — the same number.
        A dropped link, a wrong density, a gravity term applied twice or a unit lost
        between the roll-up's kilogrammes and the reaction's newtons all fail here,
        and none of them is visible in any geometry claim."""
        result = _run(_at_rest())
        assert result.assembly is not None
        motion = result.assembly.payload["motion"]

        assert motion["joint"]["j1_base"]["peak_force_n"] == pytest.approx(
            motion["total_weight_n"], rel=1e-12
        )
        assert motion["joint"]["j1_base"]["peak_force_n"] == pytest.approx(
            expected_moving_mass_kg() * 9806.65 * 1e-3, rel=1e-9
        )

    def test_moving_it_loads_the_base_eleven_percent_harder(self) -> None:
        """The number the rung exists to produce, and it is not one geometry contains."""
        result = _run()
        assert result.assembly is not None
        motion = result.assembly.payload["motion"]

        peak = motion["joint"]["j1_base"]["peak_force_n"]
        assert peak == pytest.approx(MEASURED_PEAK_BASE_FORCE_N, rel=1e-9)
        assert peak / MEASURED_STATIC_BASE_FORCE_N == pytest.approx(1.1135, abs=5e-4)

    def test_the_load_falls_monotonically_out_along_the_chain(self) -> None:
        """Each joint carries everything beyond it, so the base carries most and the
        flange least. A reaction computed on the wrong subtree breaks the ordering
        while every individual number stays the right sort of size."""
        result = _run()
        assert result.assembly is not None
        joints = result.assembly.payload["motion"]["joint"]

        peaks = [joints[name]["peak_force_n"] for name in JOINT_NAMES]
        assert peaks == sorted(peaks, reverse=True)
        for name, expected in MEASURED_PEAK_JOINT_FORCE_N.items():
            assert joints[name]["peak_force_n"] == pytest.approx(expected, rel=1e-9)

    def test_the_free_body_balance_closes_to_rounding(self) -> None:
        """Newton-Euler consistency. **Not a claim that the answer is right** — a claim
        that it is self-consistent, which is the strongest thing an inverse-dynamics
        run can say about itself. The recursion is closed form, so anything above
        rounding is a missing term rather than a tolerance."""
        result = _run()
        assert result.motion is not None

        assert result.motion.free_body_residual_n == pytest.approx(
            MEASURED_RESIDUAL_N, abs=1e-9
        )
        assert result.motion.free_body_residual_n < 1e-6

    def test_the_inertia_notes_say_the_tensors_were_not_measured(self) -> None:
        """`derive` says so itself, per body, and the rung carries it rather than
        letting a closed-form tensor read as a measured one. The caveat in `unproven`
        is the same statement one level up."""
        result = _run()
        assert result.motion is not None

        assert len(result.motion.notes) == 6
        assert all("nothing here measured it" in note for note in result.motion.notes)


class TestTheArmBuilds:
    """Built on the real kernel, moved by the real dynamics."""

    def test_it_passes(self) -> None:
        result = _run()

        assert result.passed, result.reason

    def test_the_roll_up_equals_the_closed_form(self) -> None:
        result = _run()
        assert result.assembly is not None

        assert result.assembly.payload["mass_kg"] == pytest.approx(
            expected_mass_kg(), rel=1e-9
        )

    def test_the_mechanism_and_the_product_agree_on_what_moves(self) -> None:
        """The claim joining the two halves of the rung: the left side is the product
        graph rolled up through `derive`, the right is arithmetic over the same links."""
        result = _run()
        assert result.assembly is not None
        payload = result.assembly.payload

        assert payload["motion"]["total_mass_kg"] == pytest.approx(
            expected_moving_mass_kg(), rel=1e-9
        )
        assert payload["motion"]["total_mass_kg"] < payload["mass_kg"]

    def test_nothing_clashes_in_the_shipped_pose(self) -> None:
        """Six of the twenty-one pairs are close enough to measure; each link touches
        the next, which reads as zero clearance and not as a clash."""
        result = _run()
        assert result.assembly is not None
        clash = result.assembly.payload["clash"]

        assert clash["occurrence_count"] == 7
        assert clash["clash_count"] == 0
        assert clash["complete"] is True
        assert clash["unchecked_pair_count"] == 0
        assert clash["minimum_clearance_mm"] == pytest.approx(0.0, abs=1e-6)

    def test_the_arm_is_the_size_it_was_drawn(self) -> None:
        result = _run()
        assert result.assembly is not None
        envelope = result.assembly.payload["envelope_mm"]

        assert envelope["size"][2] == pytest.approx(REACH_MM, abs=1e-3)
        assert envelope["size"][0] == pytest.approx(BASE_DIAMETER_MM, abs=1e-3)
        assert envelope["min"][2] == pytest.approx(0.0, abs=1e-6)

    def test_it_is_stacked_on_its_own_axis(self) -> None:
        result = _run()
        assert result.assembly is not None
        com = result.assembly.payload["centre_of_mass_mm"]

        assert com[0] == pytest.approx(0.0, abs=1e-6)
        assert com[1] == pytest.approx(0.0, abs=1e-6)
        assert com[2] == pytest.approx(MEASURED_COM_Z_MM, rel=1e-9)

    def test_every_joint_reported_a_reaction(self) -> None:
        result = _run()
        assert result.assembly is not None
        motion = result.assembly.payload["motion"]

        assert motion["unavailable_reaction_count"] == 0
        assert set(motion["joint"]) == set(JOINT_NAMES)

    def test_the_motion_result_is_on_the_result_and_the_geometry_is_where_it_was(
        self,
    ) -> None:
        """A moving rung *is* an assembly rung with joints, so its builds, clash and
        roll-up stay on `assembly` where an assembly rung's always are. Putting them
        anywhere else would give "what did it build" a second answer."""
        result = _run()

        assert result.motion is not None
        assert result.assembly is not None
        assert result.assembly.clash is not None
        assert result.build is None
        assert result.folded is None


class TestTheArmBuiltWrong:
    """One break per guard, each run through the real kernel on 2026-09-16."""

    def test_the_base_declared_as_a_moving_body_is_caught(self) -> None:
        """111 kg of bolted-down casting in the arm's moving mass. It makes every
        reaction *larger*, which is the conservative direction, so nothing downstream
        would complain — which is exactly why the claim is written.

        It fails a second claim as well, and that one was not predicted: the base is
        not in `_m7_inertia()`, because ground needs no tensor, so promoting it to a
        body silently makes it a **point mass**. Two independent claims catch one
        mistake from two directions, which is the property that makes a break test
        worth running rather than reasoning about."""
        result = _run(_rung(joints=m._m7_joints(base_is_a_body=True)))

        assert not result.passed
        assert _failed_claims(result) == {
            "the mechanism carries everything above the first joint and nothing below",
            "no link was reduced to a point mass",
        }

    def test_links_reduced_to_point_masses_are_caught(self) -> None:
        """A point mass has no rotary term, so a spinning link contributes nothing to
        the torque at the joint driving it. The arm still builds, still weighs the
        right amount, and every joint torque is wrong."""
        result = _run(_rung(inertia_kg_mm2=None))

        assert not result.passed
        assert _failed_claims(result) == {"no link was reduced to a point mass"}

    def test_a_tool_flange_machined_off_centre_is_caught(self) -> None:
        """No part changed size, so no dimension claim moves. The centre of mass does."""
        result = _run(_rung(structure=m._m7_structure(flange_offset_mm=20.0)))

        assert not result.passed
        assert _failed_claims(result) == {"the arm is stacked on its own axis"}

    def test_a_motion_sampled_at_the_wrong_rate_is_caught(self) -> None:
        result = _run(_rung(samples=21))

        assert not result.passed
        assert _failed_claims(result) == {"the motion was sampled as asked"}

    def test_the_arm_built_right_fails_nothing(self) -> None:
        """The control. Without it a break test passes for a suite that is red
        everywhere, which is the mutation-run failure `CLAUDE.md` records."""
        result = _run()

        assert _failed_claims(result) == set()


class TestTheRungInTheLadder:
    """That it builds, and that what it cannot say travels with it."""

    def test_m7_is_buildable_and_no_longer_pending(self) -> None:
        rung = mission("M7")

        assert rung.buildable
        assert rung.needs == ()
        assert rung.moving is not None

    def test_it_is_a_moving_rung_and_not_an_assembly_rung(self) -> None:
        """Both questions have answers and they are different ones. A moving rung runs
        through the assembly path; whether it *is* an assembly is about the payload."""
        rung = mission("M7")

        assert rung.is_moving
        assert not rung.is_assembly
        assert not rung.is_folded
        assert rung.product is rung.moving.assembly

    def test_a_rung_cannot_be_two_kinds_at_once(self) -> None:
        """A `MovingDesign` carries an `AssemblyDesign`, so a rung setting both would
        build one product and check its claims against the other."""
        with pytest.raises(SpecError) as excinfo:
            Mission(
                rung="M7",
                title="t",
                era="VI",
                hard="h",
                assembly=m._m7_assembly(),
                moving=m._m7_design(),
                assertions=m._M7_ASSERTIONS,
            )

        assert "a mechanism" in str(excinfo.value)

    def test_it_carries_what_it_does_not_claim(self) -> None:
        rung = mission("M7")

        assert len(rung.unproven) == 10
        assert all(caveat.startswith("E") for caveat in rung.unproven)

    def test_stiffness_is_named_as_missing_because_the_column_promises_it(self) -> None:
        """The master plan's own `hard` column for this rung is "kinematics, dynamic
        loads, stiffness under motion". Two of the three are here. A rung that passed
        while silently dropping the third would make the gallery claim a capability
        the code does not have."""
        rung = mission("M7")
        caveat = next(c for c in rung.unproven if "stiffness" in c)

        assert caveat.startswith("E6")
        assert "rigid" in caveat
        assert "stiffness under motion" in rung.hard

    def test_the_closed_form_inertia_is_named_as_not_measured(self) -> None:
        caveat = next(c for c in mission("M7").unproven if "inertia tensors" in c)

        assert caveat.startswith("E9")
        assert "not the kernel's integration" in caveat

    def test_the_swept_volume_gap_is_named(self) -> None:
        """The clash check runs in the shipped pose only, and a serial chain's
        interesting collisions are the ones it finds while moving."""
        caveats = [c for c in mission("M7").unproven if c.startswith("E9.3")]

        assert len(caveats) == 2
        assert any("swept volume" in c for c in caveats)
        assert any("joint limits" in c for c in caveats)

    def test_the_caveats_reach_the_public_gallery(self) -> None:
        """A gallery that printed the passes and dropped `Mission.unproven` would be
        the most misleading page in the product (P10.2)."""
        from app.handbook.gallery import entry_for

        entry = entry_for(mission("M7"))

        assert entry.not_claimed == mission("M7").unproven
        assert entry.rung == "M7"

    def test_the_ladder_is_not_complete_and_m7_is_not_why(self) -> None:
        """Superseding `test_the_ladder_now_stands_at_seven_of_nine`.

        That name and its "7/9" were true on 2026-09-16 and false the day M8 landed,
        which made a rung *advancing* fail a test about M7. The count is derived here
        instead, and what is asserted is the two things M7 is entitled to say: the
        ladder runs green, and it is still short of complete.
        """
        from app.design.missions import LADDER, run_ladder
        from app.kernel import OcctRunner

        report = run_ladder(OcctRunner)

        assert len(report.pending) == sum(1 for rung in LADDER if not rung.buildable)
        assert f"/{len(LADDER)} rungs pass" in report.summary()
        assert report.ok, report.summary()
        assert not report.complete
