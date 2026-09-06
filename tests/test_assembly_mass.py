"""What the assembly weighs, and — the point of the module — what was left out of it.

An assembly mass that silently omits a part is wrong in the direction every mass budget
passes: too light. So the property under test is not "the sum is right" (it is, and
`TestTheSumAndTheCentreAreExact` checks it against arithmetic done by hand); it is that
**an incomplete sum cannot be read as the machine's mass**.

`TestAnOmittedPartIsNotAWeightlessPart` is the guard verified by breaking it: the same
roll-up with a part that has no density publishes `measured_mass_kg`, withholds
`mass_kg`, and an assertion on `mass_kg` comes back `UNMEASURED`. The test then writes
the assertion against the partial path and shows it comes back `PASSED` — which is
exactly the false green that publishing a partial sum under the headline name would
produce, and it is invisible in every other field of the report.

Real geometry: `TestAgainstRealGeometry` builds a 20 mm steel cube through `OcctRunner`
and weighs a four-off assembly of it. 8000 mm3 at 7870 kg/m3 is 0.06296 kg, so four of
them is 0.25184 kg — known before the kernel is asked, in the discipline the solver
tests keep.
"""

from __future__ import annotations

import pytest

from app.assembly.mass import MassRollup, from_document, roll_up
from app.assembly.placement import at, turned
from app.assembly.structure import Component, Instance, ProductStructure, spread
from app.design.assertions import Assertion, Outcome, check_assertions
from app.kernel.occt.binding import available

# -- fixtures -----------------------------------------------------------------


def _two_cubes(second_at: float = 100.0) -> ProductStructure:
    """Two identical cubes, the second `second_at` mm along +X."""
    return ProductStructure(
        root="pair",
        components=[
            Component(
                name="pair",
                instances=spread("cube", "cube", [at(0.0), at(second_at)]),
            ),
            Component(name="cube", material="steel-1018"),
        ],
    )


def _payload(mass_kg: float, centre=(0.0, 0.0, 0.0)) -> dict:
    return {"mass_kg": mass_kg, "centre_of_mass_mm": list(centre)}


def _measurer(payloads: dict, calls: list[str] | None = None):
    def measure(component: str):
        if calls is not None:
            calls.append(component)
        return payloads[component]

    return measure


# -- the arithmetic -----------------------------------------------------------


class TestTheSumAndTheCentreAreExact:
    def test_two_equal_masses_put_the_centre_halfway_between_them(self) -> None:
        """Closed form: two 1 kg cubes 100 mm apart have their centre at x = 50."""
        rollup = roll_up(_two_cubes(), _measurer({"cube": _payload(1.0)}))

        assert rollup.complete
        assert rollup.mass_kg == pytest.approx(2.0)
        assert rollup.centre_of_mass_mm == pytest.approx((50.0, 0.0, 0.0), abs=1e-12)

    def test_the_centre_is_mass_weighted_not_a_midpoint(self) -> None:
        """A 3 kg block at x=0 and a 1 kg block at x=100 balance at x=25."""
        structure = ProductStructure(
            root="pair",
            components=[
                Component(
                    name="pair",
                    instances=(
                        Instance(component="heavy", tag="heavy", index=1, placement=at(0.0)),
                        Instance(component="light", tag="light", index=1, placement=at(100.0)),
                    ),
                ),
                Component(name="heavy"),
                Component(name="light"),
            ],
        )
        rollup = roll_up(
            structure,
            _measurer({"heavy": _payload(3.0), "light": _payload(1.0)}),
        )
        assert rollup.mass_kg == pytest.approx(4.0)
        assert rollup.centre_of_mass_mm[0] == pytest.approx(25.0)

    def test_a_components_own_centre_is_placed_by_the_occurrence_frame(self) -> None:
        """The component's local centre is at (5, 0, 0); the occurrence is at (100, 0, 0)."""
        structure = ProductStructure(
            root="rail",
            components=[
                Component(name="rail", instances=spread("block", "block", [at(100.0)])),
                Component(name="block"),
            ],
        )
        rollup = roll_up(
            structure, _measurer({"block": _payload(2.0, centre=(5.0, 0.0, 0.0))})
        )
        assert rollup.centre_of_mass_mm == pytest.approx((105.0, 0.0, 0.0), abs=1e-12)

    def test_a_rotated_occurrence_carries_its_centre_round_with_it(self) -> None:
        """A part whose centre is at local (10, 0, 0), turned a quarter turn about Z."""
        import math

        structure = ProductStructure(
            root="rail",
            components=[
                Component(
                    name="rail",
                    instances=(
                        Instance(
                            component="block",
                            tag="block",
                            index=1,
                            placement=turned((0.0, 0.0, 1.0), math.pi / 2),
                        ),
                    ),
                ),
                Component(name="block"),
            ],
        )
        rollup = roll_up(
            structure, _measurer({"block": _payload(1.0, centre=(10.0, 0.0, 0.0))})
        )
        assert rollup.centre_of_mass_mm == pytest.approx((0.0, 10.0, 0.0), abs=1e-12)

    def test_each_component_is_measured_once_however_often_it_is_used(self) -> None:
        """Forty bolts, one integration. The graph paying for itself."""
        asked: list[str] = []
        structure = ProductStructure(
            root="plate",
            components=[
                Component(
                    name="plate",
                    instances=spread("bolt", "bolt", [at(10.0 * n) for n in range(40)]),
                ),
                Component(name="bolt"),
            ],
        )
        rollup = roll_up(structure, _measurer({"bolt": _payload(0.01)}, asked))

        assert asked == ["bolt"]
        assert rollup.components_measured == 1
        assert len(rollup.weighed) == 40
        assert rollup.mass_kg == pytest.approx(0.4)

    def test_mass_by_component_sums_the_occurrences(self) -> None:
        rollup = roll_up(_two_cubes(), _measurer({"cube": _payload(1.5)}))
        assert rollup.by_component() == {"cube": 3.0}

    def test_heaviest_ranks_the_occurrences(self) -> None:
        structure = ProductStructure(
            root="pair",
            components=[
                Component(
                    name="pair",
                    instances=(
                        Instance(component="light", tag="light", index=1),
                        Instance(component="heavy", tag="heavy", index=1, placement=at(50.0)),
                    ),
                ),
                Component(name="light"),
                Component(name="heavy"),
            ],
        )
        rollup = roll_up(
            structure, _measurer({"light": _payload(0.2), "heavy": _payload(9.0)})
        )
        assert rollup.heaviest(1)[0].component == "heavy"


# -- the guard ----------------------------------------------------------------


class TestAnOmittedPartIsNotAWeightlessPart:
    def test_a_component_with_no_density_is_missing_not_zero(self) -> None:
        """`metrology.measure`'s provisional-mass flag, honoured one level up.

        A payload with a volume and no `mass_kg` is a part whose material was never
        set. Treating it as weightless would put that guard back to sleep at exactly the
        level where the number is quoted to a customer.
        """
        rollup = roll_up(
            _two_cubes(),
            _measurer(
                {"cube": {"volume_mm3": 8000.0, "mass_is_provisional": True}}
            ),
        )

        assert not rollup.complete
        assert rollup.mass_kg is None
        assert rollup.measured_mass_kg == 0.0
        assert len(rollup.missing) == 2
        assert "no density" in rollup.missing[0].reason
        assert rollup.missing[0].path == "pair/cube.1"

    def test_the_partial_sum_is_published_under_a_different_name(self) -> None:
        structure = ProductStructure(
            root="pair",
            components=[
                Component(
                    name="pair",
                    instances=(
                        Instance(component="known", tag="known", index=1),
                        Instance(component="unknown", tag="unknown", index=1, placement=at(100.0)),
                    ),
                ),
                Component(name="known"),
                Component(name="unknown"),
            ],
        )
        rollup = roll_up(
            structure,
            _measurer({"known": _payload(2.0), "unknown": {"mass_is_provisional": True}}),
        )
        payload = rollup.to_payload()

        assert "mass_kg" not in payload
        assert payload["measured_mass_kg"] == pytest.approx(2.0)
        assert "centre_of_mass_mm" not in payload
        assert payload["measured_centre_of_mass_mm"] == [0.0, 0.0, 0.0]
        assert payload["unmeasured_occurrence_count"] == 1
        assert payload["complete"] is False

    def test_an_assertion_on_an_incomplete_mass_is_unmeasured_not_passed(self) -> None:
        """The guard, broken.

        The same claim written against the partial path passes — a 2 kg budget met by a
        machine whose second half nobody weighed. That is what publishing the partial
        sum as `mass_kg` would produce, and nothing else in the payload would show it.
        """
        structure = ProductStructure(
            root="pair",
            components=[
                Component(
                    name="pair",
                    instances=(
                        Instance(component="known", tag="known", index=1),
                        Instance(component="unknown", tag="unknown", index=1, placement=at(100.0)),
                    ),
                ),
                Component(name="known"),
                Component(name="unknown"),
            ],
        )
        payload = roll_up(
            structure,
            _measurer({"known": _payload(2.0), "unknown": {"mass_is_provisional": True}}),
        ).to_payload()

        budget = Assertion(name="mass_budget", measure="mass_kg", comparison="<=", bound=2.5)
        (honest,) = check_assertions([budget], payload)
        assert honest.outcome is Outcome.UNMEASURED
        assert "could not be weighed" in honest.reason
        assert "pair/unknown.1" in honest.reason

        naive = Assertion(
            name="mass_budget", measure="measured_mass_kg", comparison="<=", bound=2.5
        )
        (false_green,) = check_assertions([naive], payload)
        assert false_green.outcome is Outcome.PASSED

    def test_a_measurer_that_raises_names_every_occurrence_it_cost(self) -> None:
        def broken(component: str):
            raise FileNotFoundError("the part file is not on this machine")

        rollup = roll_up(_two_cubes(), broken)

        assert not rollup.complete
        assert len(rollup.missing) == 2
        assert "FileNotFoundError" in rollup.missing[0].reason
        assert "the part file is not on this machine" in rollup.missing[0].reason
        assert "lower bound" in rollup.summary()

    def test_a_shape_with_no_solid_says_so(self) -> None:
        rollup = roll_up(_two_cubes(), _measurer({"cube": {"has_solid": False}}))
        assert "encloses no solid" in rollup.missing[0].reason

    def test_a_recorded_provenance_reason_is_quoted_verbatim(self) -> None:
        rollup = roll_up(
            _two_cubes(),
            _measurer(
                {
                    "cube": {
                        "provenance": {
                            "mass_kg": {
                                "basis": "unavailable",
                                "reason": "the boolean that made this part failed",
                            }
                        }
                    }
                }
            ),
        )
        assert rollup.missing[0].reason == "the boolean that made this part failed"

    def test_a_mass_with_no_centre_cannot_be_placed_and_says_so(self) -> None:
        rollup = roll_up(_two_cubes(), _measurer({"cube": {"mass_kg": 1.0}}))
        assert not rollup.complete
        assert "no centre of mass" in rollup.missing[0].reason

    def test_nothing_weighed_at_all_is_unavailable_not_zero(self) -> None:
        payload = roll_up(_two_cubes(), _measurer({"cube": {}})).to_payload()
        assert "mass_kg" not in payload
        assert "measured_mass_kg" not in payload
        from app.kernel import provenance

        assert provenance.basis_of(payload, "mass_kg") is provenance.Basis.UNAVAILABLE

    def test_a_zero_mass_assembly_has_no_centre_of_mass(self) -> None:
        """A centroid of nothing is not the origin, and must not be reported as one."""
        rollup = roll_up(_two_cubes(), _measurer({"cube": _payload(0.0)}))
        assert rollup.centre_of_mass_mm is None
        assert rollup.measured_mass_kg == 0.0

    def test_an_empty_rollup_is_not_complete(self) -> None:
        assert not MassRollup().complete
        assert MassRollup().summary() == "Nothing to weigh."


class TestTheCompletePayload:
    def test_a_complete_rollup_publishes_the_kernels_own_spellings(self) -> None:
        payload = roll_up(_two_cubes(), _measurer({"cube": _payload(1.0)})).to_payload()

        assert payload["mass_kg"] == pytest.approx(2.0)
        assert payload["centre_of_mass_mm"] == pytest.approx([50.0, 0.0, 0.0])
        assert payload["complete"] is True

        from app.kernel import measurement, provenance

        assert measurement.MASS_KG in payload
        assert provenance.basis_of(payload, measurement.MASS_KG) is provenance.Basis.MEASURED

    def test_a_mass_budget_assertion_reads_it_unchanged(self) -> None:
        payload = roll_up(_two_cubes(), _measurer({"cube": _payload(1.0)})).to_payload()
        budget = Assertion(
            name="under_five_kilos", measure="mass_kg", comparison="<=", bound=5.0
        )
        (result,) = check_assertions([budget], payload)
        assert result.outcome is Outcome.PASSED
        assert result.measured == pytest.approx(2.0)

    def test_the_local_provenance_key_matches_the_kernels(self) -> None:
        from app.assembly.mass import PROVENANCE_KEY
        from app.kernel.provenance import PROVENANCE_KEY as KERNEL_KEY

        assert PROVENANCE_KEY == KERNEL_KEY

    def test_the_payload_key_names_match_the_kernels(self) -> None:
        from app.assembly import mass
        from app.kernel import measurement

        assert mass.MASS_KG == measurement.MASS_KG
        assert mass.CENTRE_OF_MASS_MM == measurement.CENTRE_OF_MASS_MM


# -- real geometry ------------------------------------------------------------


@pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)
class TestAgainstRealGeometry:
    def _cube_document(self, size: float = 20.0, material: str = "steel-1018"):
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Cube"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner(
            "catia_sketch_rectangle",
            {"sketch": "outline", "width_mm": size, "height_mm": size},
        )
        runner("catia_pad", {"sketch": "outline", "length_mm": size})
        if material:
            runner("catia_set_material", {"material": material})
        return runner.document

    def test_four_steel_cubes_weigh_four_times_one(self) -> None:
        """8000 mm3 x 7870 kg/m3 = 0.06296 kg each; four of them is 0.25184 kg.

        Computed by hand from the density the material table holds, not read off a
        previous run. Units per the repository rule: mm3 in, kilograms out, and the one
        conversion happens in `app.kernel.measurement.mass_kg`, below this module.
        """
        documents = {"cube": self._cube_document()}
        structure = ProductStructure(
            root="stack",
            components=[
                Component(
                    name="stack",
                    instances=spread(
                        "cube", "cube", [at(0.0), at(40.0), at(80.0), at(120.0)]
                    ),
                ),
                Component(name="cube", material="steel-1018"),
            ],
        )
        rollup = roll_up(structure, from_document(documents))

        assert rollup.complete
        assert rollup.mass_kg == pytest.approx(0.25184, rel=1e-9)
        # Each cube's own centre is at (0, 0, 10); the row is at x = 0, 40, 80, 120.
        assert rollup.centre_of_mass_mm == pytest.approx((60.0, 0.0, 10.0), abs=1e-9)

    def test_a_cube_with_no_material_is_missing_from_the_mass(self) -> None:
        """The real payload, not a synthetic one: no density, so no mass, so no sum."""
        documents = {"cube": self._cube_document(material="")}
        rollup = roll_up(_two_cubes(), from_document(documents))

        assert not rollup.complete
        assert rollup.mass_kg is None
        assert "no density" in rollup.missing[0].reason

    def test_a_component_with_no_document_is_named(self) -> None:
        rollup = roll_up(_two_cubes(), from_document({}))
        assert "no document was supplied" in rollup.missing[0].reason
        assert rollup.missing[0].component == "cube"
