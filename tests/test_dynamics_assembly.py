"""A mechanism read off the product graph gives the same answers as one typed by hand (E9.2).

The oracle is the closed form the kinematics tests already hold the hand-built mechanism
to: a body whirling on a pin carries `m ω² r` at the pin. Here the same rotor is placed in
an assembly, weighed by the mass roll-up, and its joint declared in the part's own
coordinates. Nothing about the mechanism is typed twice, and the reaction must still be
`m ω² r`, wherever the part sits and however it is turned.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import math

import pytest

from app.assembly.placement import at, compose, turned
from app.assembly.structure import Component, Instance, ProductStructure
from app.dynamics import kinematics, reactions
from app.dynamics.assembly import JointDeclaration, body_name, derive
from app.dynamics.errors import MechanismError
from app.dynamics.pose import Frame, norm
from app.dynamics.types import Driver, MotionRange

OMEGA = 7.0
RADIUS = 30.0
MASS = 2.0
ZERO_G = (0.0, 0.0, 0.0)


def _machine(placement: Frame, *, second_link: bool = False) -> ProductStructure:
    instances = [
        Instance(component="base", tag="base"),
        Instance(component="rotor", tag="rotor", placement=placement),
    ]
    if second_link:
        instances.append(
            Instance(component="rotor", tag="rotor", index=2, placement=at(200.0, 0.0, 0.0))
        )
    return ProductStructure(
        root="machine",
        components=[
            Component(name="machine", instances=tuple(instances)),
            Component(name="base"),
            Component(name="rotor"),
        ],
    )


def _measure(component: str) -> dict[str, object]:
    return {
        "base": {"mass_kg": 50.0, "centre_of_mass_mm": [0.0, 0.0, 0.0]},
        "rotor": {"mass_kg": MASS, "centre_of_mass_mm": [RADIUS, 0.0, 0.0]},
    }[component]


def _shaft(**overrides: object) -> JointDeclaration:
    kwargs: dict[str, object] = {
        "name": "shaft",
        "kind": "revolute",
        "child": "machine/rotor.1",
        "parent": None,
        "at_mm": (0.0, 0.0, 0.0),
        "axis": (0.0, 0.0, 1.0),
    }
    kwargs.update(overrides)
    return JointDeclaration(**kwargs)  # type: ignore[arg-type]


def _peak_reaction(structure: ProductStructure, joints: list[JointDeclaration]) -> float:
    derived = derive(
        structure,
        joints,
        _measure,
        drivers=[Driver(joint="shaft", kind="constant", rate=OMEGA)],
        gravity_mm_s2=ZERO_G,
    )
    path = kinematics.evaluate(derived.mechanism, MotionRange(duration_s=1.0, samples=9))
    reaction = reactions.compute(derived.mechanism, path)["shaft"]
    assert reaction.peak_force_n is not None
    return reaction.peak_force_n


class TestTheBodyIsWhatTheGraphSays:
    def test_the_mass_and_centre_come_from_the_roll_up(self) -> None:
        derived = derive(_machine(at(100.0, 50.0, 0.0)), [_shaft()], _measure)
        [body] = derived.mechanism.bodies
        assert body.name == body_name("machine/rotor.1")
        assert body.mass_kg == pytest.approx(MASS)
        assert body.centre_of_mass_mm == pytest.approx((RADIUS, 0.0, 0.0))
        assert derived.occurrences["machine/rotor.1"] == ("machine/rotor.1",)

    def test_the_joint_origin_is_where_the_part_sits(self) -> None:
        derived = derive(_machine(at(100.0, 50.0, 0.0)), [_shaft()], _measure)
        [joint] = derived.mechanism.joints
        assert joint.origin_mm == pytest.approx((100.0, 50.0, 0.0))
        assert joint.parent is None
        assert derived.joint_positions_mm["shaft"] == pytest.approx((100.0, 50.0, 0.0))

    def test_a_joint_off_the_part_origin_moves_the_origin_and_the_centre_together(self) -> None:
        derived = derive(
            _machine(at(100.0, 0.0, 0.0)), [_shaft(at_mm=(10.0, 0.0, 0.0))], _measure
        )
        [joint] = derived.mechanism.joints
        [body] = derived.mechanism.bodies
        assert joint.origin_mm == pytest.approx((110.0, 0.0, 0.0))
        assert body.centre_of_mass_mm == pytest.approx((RADIUS - 10.0, 0.0, 0.0))

    def test_a_turned_part_turns_its_axis_and_its_centre(self) -> None:
        quarter = compose(at(100.0, 0.0, 0.0), turned((0.0, 0.0, 1.0), math.pi / 2.0))
        derived = derive(_machine(quarter), [_shaft(axis=(1.0, 0.0, 0.0))], _measure)
        [joint] = derived.mechanism.joints
        [body] = derived.mechanism.bodies
        assert joint.axis == pytest.approx((0.0, 1.0, 0.0), abs=1e-12)
        assert body.centre_of_mass_mm == pytest.approx((0.0, RADIUS, 0.0), abs=1e-12)

    def test_every_body_is_a_point_mass_and_says_so(self) -> None:
        derived = derive(_machine(at()), [_shaft()], _measure)
        assert derived.mechanism.bodies[0].is_point_mass
        assert any("point mass" in note for note in derived.notes)


class TestTheReactionIsStillMOmegaSquaredR:
    @pytest.mark.parametrize(
        "placement",
        [
            at(),
            at(100.0, 50.0, -20.0),
            compose(at(-40.0, 10.0, 0.0), turned((0.0, 0.0, 1.0), 1.1)),
        ],
    )
    def test_wherever_the_rotor_is_placed(self, placement: Frame) -> None:
        peak = _peak_reaction(_machine(placement), [_shaft()])
        assert peak == pytest.approx(MASS * OMEGA * OMEGA * RADIUS * 1e-3, rel=1e-12)

    def test_a_second_link_hangs_from_the_first_at_the_distance_the_graph_gives(self) -> None:
        structure = _machine(at(), second_link=True)
        joints = [
            _shaft(),
            JointDeclaration(
                name="elbow",
                kind="fixed",
                child="machine/rotor.2",
                parent="machine/rotor.1",
            ),
        ]
        derived = derive(
            structure,
            joints,
            _measure,
            drivers=[Driver(joint="shaft", kind="constant", rate=OMEGA)],
            gravity_mm_s2=ZERO_G,
        )
        elbow = next(j for j in derived.mechanism.joints if j.name == "elbow")
        assert elbow.origin_mm == pytest.approx((200.0, 0.0, 0.0))

        path = kinematics.evaluate(derived.mechanism, MotionRange(duration_s=1.0, samples=5))
        found = reactions.compute(derived.mechanism, path)
        # The elbow carries the outer rotor: centre at 230 mm from the shaft axis.
        assert found["elbow"].peak_force_n == pytest.approx(
            MASS * OMEGA * OMEGA * (200.0 + RADIUS) * 1e-3, rel=1e-12
        )
        # The shaft carries both: m(r1 + r2) for two equal masses on one line.
        assert found["shaft"].peak_force_n == pytest.approx(
            MASS * OMEGA * OMEGA * (RADIUS + 200.0 + RADIUS) * 1e-3, rel=1e-12
        )
        assert norm(path.motion(body_name("machine/rotor.2")).position_mm[0]) == pytest.approx(
            230.0
        )


class TestWhatItRefuses:
    def test_an_unweighed_part_under_a_body_is_refused_by_name(self) -> None:
        def measure(component: str) -> dict[str, object]:
            if component == "rotor":
                return {"volume_mm3": 1000.0, "mass_is_provisional": True}
            return _measure(component)

        with pytest.raises(MechanismError, match="machine/rotor.1") as caught:
            derive(_machine(at()), [_shaft()], measure)
        assert "too small" in str(caught.value)

    def test_an_unweighed_part_that_is_not_a_body_is_not_a_reason_to_refuse(self) -> None:
        def measure(component: str) -> dict[str, object]:
            if component == "base":
                raise RuntimeError("no document")
            return _measure(component)

        derived = derive(_machine(at()), [_shaft()], measure)
        assert derived.mechanism.bodies[0].mass_kg == pytest.approx(MASS)

    def test_a_parent_that_is_not_a_body_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="not the child of any joint"):
            derive(_machine(at()), [_shaft(parent="machine/base.1")], _measure)

    def test_a_body_hung_from_two_joints_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="more than one joint"):
            derive(_machine(at()), [_shaft(), _shaft(name="again")], _measure)

    def test_a_body_inside_another_body_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="counted twice"):
            derive(
                _machine(at()),
                [
                    _shaft(child="machine"),
                    _shaft(name="inner", child="machine/rotor.1", parent="machine"),
                ],
                _measure,
            )

    def test_a_path_that_is_not_in_the_product_is_refused(self) -> None:
        with pytest.raises(Exception, match="rotor.9"):
            derive(_machine(at()), [_shaft(child="machine/rotor.9")], _measure)

    def test_no_joints_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="at least one joint"):
            derive(_machine(at()), [], _measure)

    def test_an_inertia_for_something_that_is_not_a_body_is_refused(self) -> None:
        with pytest.raises(MechanismError, match="not a body"):
            derive(
                _machine(at()),
                [_shaft()],
                _measure,
                inertia_kg_mm2={"machine/base.1": (1.0, 1.0, 1.0)},
            )


class TestInertiaCanComeFromTheGeometryToo:
    """The gap E9.2 recorded, closed: a body's tensor measured rather than typed.

    Until 2026-09-16 the roll-up carried no inertia at all, so every body was a point
    mass and every joint *moment* omitted the `I*alpha` and `omega x I*omega` terms.
    `derive` now takes a second measurer answering at `Detail.INERTIA` — a separate
    argument from `measure`, because that one answers at `Detail.FULL` and a mass budget
    must not start paying for a fourth integration per component without being asked.

    The oracle is arithmetic: a solid box `a x b x c` of mass `m` has principal moments
    `m(b²+c²)/12` and its cyclic partners about its own centre. The same numbers were
    checked against the real OCCT kernel on 2026-09-16 and agreed to 1e-15.

    **Written on Linux and not run as pytest**; the assertions were each evaluated once
    by a one-off script, and the Windows machine runs the suite.
    """

    #: A 200 x 40 x 20 link. Kept as numbers rather than a payload so the closed form
    #: below is visibly the same arithmetic the fixture is built from.
    LENGTH, WIDTH, HEIGHT = 200.0, 40.0, 20.0
    DENSITY_KG_MM3 = 7850.0e-9

    def _inertia_measure(self, component: str) -> dict[str, object]:
        """`Detail.INERTIA` payloads — mass, volume, centre and the mm^5 tensor."""
        volume = self.LENGTH * self.WIDTH * self.HEIGHT
        link = {
            "mass_kg": volume * self.DENSITY_KG_MM3,
            "volume_mm3": volume,
            "centre_of_mass_mm": [RADIUS, 0.0, 0.0],
            "inertia_tensor_mm5": [
                [volume * (self.WIDTH**2 + self.HEIGHT**2) / 12.0, 0.0, 0.0],
                [0.0, volume * (self.LENGTH**2 + self.HEIGHT**2) / 12.0, 0.0],
                [0.0, 0.0, volume * (self.LENGTH**2 + self.WIDTH**2) / 12.0],
            ],
        }
        base = dict(link, mass_kg=50.0, centre_of_mass_mm=[0.0, 0.0, 0.0])
        return {"base": base, "rotor": link}[component]

    def _mass_measure(self, component: str) -> dict[str, object]:
        """What `Detail.FULL` gives: the same masses and centres, and no tensor."""
        payload = dict(self._inertia_measure(component))
        payload.pop("inertia_tensor_mm5")
        return payload

    def test_a_body_with_no_inertia_measurer_is_still_a_point_mass(self) -> None:
        derived = derive(_machine(at()), [_shaft()], self._mass_measure)
        body = derived.mechanism.bodies[0]
        assert body.is_point_mass
        assert body.inertia_kg_mm2 is None
        assert any("is a point mass" in note for note in derived.notes)

    def test_a_measured_body_carries_its_closed_form_inertia(self) -> None:
        derived = derive(
            _machine(at()),
            [_shaft()],
            self._mass_measure,
            measure_inertia=self._inertia_measure,
        )
        body = derived.mechanism.bodies[0]
        assert body.inertia_kg_mm2 is not None
        mass = self.LENGTH * self.WIDTH * self.HEIGHT * self.DENSITY_KG_MM3
        assert body.inertia_kg_mm2[0] == pytest.approx(
            mass * (self.WIDTH**2 + self.HEIGHT**2) / 12.0, rel=1e-12
        )
        assert body.inertia_kg_mm2[1] == pytest.approx(
            mass * (self.LENGTH**2 + self.HEIGHT**2) / 12.0, rel=1e-12
        )
        assert body.inertia_kg_mm2[2] == pytest.approx(
            mass * (self.LENGTH**2 + self.WIDTH**2) / 12.0, rel=1e-12
        )
        assert not body.is_point_mass

    def test_the_note_says_the_inertia_was_measured_not_supplied(self) -> None:
        """A number's origin travels with it, the way every provenance record here does."""
        derived = derive(
            _machine(at()),
            [_shaft()],
            self._mass_measure,
            measure_inertia=self._inertia_measure,
        )
        assert any("measured from its geometry" in note for note in derived.notes)
        assert not any("is the caller's" in note for note in derived.notes)

    def test_a_caller_who_measured_the_real_part_beats_the_geometry(self) -> None:
        """Somebody who put the part on a bench knows something the model does not.

        Silently preferring the measured-from-geometry tensor would discard that with
        nothing said, so the caller's wins and the note records which body came from
        where.
        """
        derived = derive(
            _machine(at()),
            [_shaft()],
            self._mass_measure,
            measure_inertia=self._inertia_measure,
            inertia_kg_mm2={"machine/rotor.1": (11.0, 22.0, 33.0)},
        )
        assert derived.mechanism.bodies[0].inertia_kg_mm2 == (11.0, 22.0, 33.0)
        assert any("is the caller's" in note for note in derived.notes)

    def test_a_measurer_that_carries_no_tensor_leaves_a_point_mass_and_says_so(self) -> None:
        """`Detail.FULL` passed where `Detail.INERTIA` was wanted is the likely mistake.

        It must not produce a body with zero inertia — zero is not unknown, it is a body
        that resists no angular acceleration, which makes every moment above it smaller.
        """
        derived = derive(
            _machine(at()),
            [_shaft()],
            self._mass_measure,
            measure_inertia=self._mass_measure,
        )
        assert derived.mechanism.bodies[0].is_point_mass
        assert any("no measured inertia" in note for note in derived.notes)

    def test_a_link_whose_products_of_inertia_are_large_stays_a_point_mass(self) -> None:
        """An L-shaped link couples 18% of its largest moment into the other two axes.

        `Body.inertia_kg_mm2` is a diagonal, so handing it over would under-report the
        bearing moment. It is refused, recorded as a note rather than raised — one
        awkward link must not cost the other eleven their inertia — and the note names
        the way out.
        """

        def coupled(component: str) -> dict[str, object]:
            payload = dict(self._inertia_measure(component))
            volume = float(payload["volume_mm3"])  # type: ignore[arg-type]
            rows = [list(row) for row in payload["inertia_tensor_mm5"]]  # type: ignore[index]
            biggest = max(rows[i][i] for i in range(3))
            rows[0][1] = rows[1][0] = 0.2 * biggest
            payload["inertia_tensor_mm5"] = rows
            assert volume > 0.0
            return payload

        derived = derive(
            _machine(at()), [_shaft()], self._mass_measure, measure_inertia=coupled
        )
        assert derived.mechanism.bodies[0].is_point_mass
        assert any("stays a point mass" in note for note in derived.notes)
        assert any("principal axes" in note for note in derived.notes)

    def test_one_body_losing_its_inertia_does_not_cost_the_other(self) -> None:
        """Two links, one coupled and one not: the clean one keeps its tensor."""

        def mixed(component: str) -> dict[str, object]:
            payload = dict(self._inertia_measure(component))
            if component == "base":
                rows = [list(row) for row in payload["inertia_tensor_mm5"]]  # type: ignore[index]
                rows[0][1] = rows[1][0] = 0.5 * max(rows[i][i] for i in range(3))
                payload["inertia_tensor_mm5"] = rows
            return payload

        joints = [
            _shaft(),
            JointDeclaration(
                name="pin",
                kind="revolute",
                child="machine/base.1",
                parent="machine/rotor.1",
                at_mm=(0.0, 0.0, 0.0),
                axis=(0.0, 0.0, 1.0),
            ),
        ]
        derived = derive(
            _machine(at()), joints, self._mass_measure, measure_inertia=mixed
        )
        bodies = {body.name: body for body in derived.mechanism.bodies}
        assert not bodies[body_name("machine/rotor.1")].is_point_mass
        assert bodies[body_name("machine/base.1")].is_point_mass
