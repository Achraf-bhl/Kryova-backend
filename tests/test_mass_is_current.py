"""The mass a part reports must be the mass of the material it has now.

Measured end to end on 2026-09-05, driving the real chat endpoint: the agent
built a correct flange, called `catia_set_material` with aluminium 6061-T6, got
`ok` and a density of 2700 back, measured the part — and was handed a payload
carrying the material name, no `mass_kg` at all, and `mass_is_provisional: true`.
It then reported a mass of 273 kg for a part weighing 0.27 kg.

Inventing that number was the model's fault. Being unable to read one was ours.
`PartDocument.measure` cached the whole payload against the shape and invalidated
it on geometry changes, and `catia_set_material` changes the density and no
geometry — so a material set after the first measurement never reached a mass
for the rest of the session, while the payload went on naming the material
beside the missing number.

The rule these pin: **what is cached must be a function of the shape alone.**
The module docstring already said so about feature names, one paragraph above
the line that broke it for mass.
"""

from __future__ import annotations

import math

import pytest

ALUMINIUM_KG_M3 = 2700.0
STEEL_KG_M3 = 7850.0


def _plate(runner, width=100.0, height=100.0, thickness=12.0):
    runner("catia_new_part", {"name": "Plate"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner(
        "catia_sketch_rectangle",
        {"sketch": "outline", "width_mm": width, "height_mm": height},
    )
    return runner("catia_pad", {"sketch": "outline", "length_mm": thickness})


def _mass_of(volume_mm3: float, density_kg_m3: float) -> float:
    return volume_mm3 * density_kg_m3 * 1e-9


class TestAMaterialSetAfterTheBuildStillWeighsThePart:
    """The exact sequence the agent used, in the order it used it."""

    def test_measure_then_set_material_then_measure_again(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)

        before = runner("catia_measure", {})
        assert before.get("mass_is_provisional") is True, (
            "no material yet, so there is honestly no mass"
        )
        assert "mass_kg" not in before

        runner("catia_set_material", {"material": "aluminium-6061-t6",
                                      "density_kg_m3": ALUMINIUM_KG_M3})
        after = runner("catia_measure", {})

        assert "mass_kg" in after, (
            "the material was set and acknowledged; a payload with no mass is what "
            "made the agent invent one"
        )
        assert after["mass_kg"] == pytest.approx(
            _mass_of(100.0 * 100.0 * 12.0, ALUMINIUM_KG_M3)
        )

    def test_the_provisional_flag_comes_off_when_a_real_mass_arrives(self) -> None:
        """Leaving it on beside a real number is the same lie the other way."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)
        runner("catia_measure", {})
        runner("catia_set_material", {"material": "steel-s235",
                                      "density_kg_m3": STEEL_KG_M3})

        after = runner("catia_measure", {})

        assert "mass_is_provisional" not in after

    def test_the_density_is_reported_beside_the_mass(self) -> None:
        """A mass with no density behind it cannot be checked by anyone."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)
        runner("catia_measure", {})
        runner("catia_set_material", {"material": "aluminium-6061-t6",
                                      "density_kg_m3": ALUMINIUM_KG_M3})

        assert runner("catia_measure", {})["density_kg_m3"] == ALUMINIUM_KG_M3

    def test_changing_the_material_changes_the_mass(self) -> None:
        """The cache is keyed on the shape, and the shape did not move."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)
        runner("catia_set_material", {"material": "aluminium-6061-t6",
                                      "density_kg_m3": ALUMINIUM_KG_M3})
        light = runner("catia_measure", {})["mass_kg"]

        runner("catia_set_material", {"material": "steel-s235",
                                      "density_kg_m3": STEEL_KG_M3})
        heavy = runner("catia_measure", {})["mass_kg"]

        assert heavy > light
        assert heavy / light == pytest.approx(STEEL_KG_M3 / ALUMINIUM_KG_M3)

    def test_the_mass_is_kilograms_and_not_grams(self) -> None:
        """mm-N-MPa everywhere, and mass output is already kilograms. The
        fabricated 273 kg was 0.27 kg read as though it were grams."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)
        runner("catia_set_material", {"material": "aluminium-6061-t6",
                                      "density_kg_m3": ALUMINIUM_KG_M3})

        mass = runner("catia_measure", {})["mass_kg"]

        assert mass == pytest.approx(0.324)
        assert mass < 1.0, "a 100x100x12 aluminium plate does not weigh 324 kg"


class TestTheGeometryIsStillCached:
    """The fix must not turn every measurement into a fresh integration."""

    def test_two_measurements_of_an_unchanged_shape_agree_exactly(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)

        first = runner("catia_measure", {})
        second = runner("catia_measure", {})

        assert first["volume_mm3"] == second["volume_mm3"]
        assert first["surface_area_mm2"] == second["surface_area_mm2"]

    def test_the_shape_is_only_integrated_once(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)
        document = runner.document
        document.measure()

        cached = dict(document._measurement_cache)
        document.measure()

        assert document._measurement_cache == cached, "a second call re-integrated"

    def test_what_is_cached_carries_no_density(self) -> None:
        """The invariant, stated directly: a cached entry must be a function of
        the shape alone, or it outlives its truth."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)
        runner("catia_set_material", {"material": "aluminium-6061-t6",
                                      "density_kg_m3": ALUMINIUM_KG_M3})
        runner("catia_measure", {})

        for entry in runner.document._measurement_cache.values():
            assert "mass_kg" not in entry
            assert "density_kg_m3" not in entry

    def test_a_geometry_change_still_moves_the_mass(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)
        runner("catia_set_material", {"material": "aluminium-6061-t6",
                                      "density_kg_m3": ALUMINIUM_KG_M3})
        before = runner("catia_measure", {})["mass_kg"]

        runner("catia_sketch_create", {"support": "XY", "name": "bore"})
        runner("catia_sketch_circle", {"sketch": "bore", "diameter_mm": 40.0})
        runner("catia_pocket", {"sketch": "bore", "through_all": True})
        after = runner("catia_measure", {})["mass_kg"]

        removed = _mass_of(math.pi * 20.0**2 * 12.0, ALUMINIUM_KG_M3)
        assert before - after == pytest.approx(removed, rel=1e-6)


class TestAPartWithNoMaterialStillSaysSo:
    """The honesty convention this must not erode: never invent a density."""

    def test_no_material_means_no_mass_and_a_flag(self) -> None:
        from app.kernel import OcctRunner

        runner = OcctRunner()
        _plate(runner)

        payload = runner("catia_measure", {})

        assert payload["mass_is_provisional"] is True
        assert "mass_kg" not in payload
        assert "density_kg_m3" not in payload

    def test_a_shape_level_measurement_has_nothing_to_weigh(self) -> None:
        """Below FULL there is no volume, so there is no mass and no flag to lift."""
        from app.kernel import OcctRunner
        from app.kernel.measurement import Detail

        runner = OcctRunner()
        _plate(runner)
        runner("catia_set_material", {"material": "aluminium-6061-t6",
                                      "density_kg_m3": ALUMINIUM_KG_M3})

        payload = runner.document.measure(detail=Detail.SHAPE)

        assert "mass_kg" not in payload
