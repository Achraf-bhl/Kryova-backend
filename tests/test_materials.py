"""The material library: back-compatibility, provenance, and the unit boundary.

Phase 12.2. `app/solve/materials.py` was rewritten from a flat table of floats
into records carrying source, condition and status. Three things have to survive
that and are pinned here:

1. **`MATERIALS` is unchanged** — same type, same eight slugs, same numbers.
   Every stress, displacement and mass in the codebase is computed against those
   figures, so a modulus that moved by a per cent during the rewrite would move
   every result quietly. The expected values below are transcribed from
   `git show d64ebb0^:app/solve/materials.py`, the last version before the
   rewrite, and are checked for **exact** equality rather than approximately:
   the point is that nothing moved at all, not that nothing moved much.
2. **An absent property reads as absent.** Never zero, never a plausible
   default, and the refusal names it. This is the difference between "no fatigue
   data is held for this alloy" and "this alloy has no fatigue strength", and
   that difference is somebody's bracket.
3. **An unknown slug is refused, never substituted.** The measured defect this
   phase exists to fix: an unrecognised name silently attached steel and a part
   came back about three times its real mass.

No database, no network, no gmsh — this suite runs offline in well under a
second, the same property the physics tests have and for the same reason.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.solve.materials import (
    MATERIAL_PROPERTIES,
    MATERIALS,
    PROPERTY_UNITS,
    RECORDS,
    SOLVER_REQUIRED,
    Condition,
    IncompleteMaterial,
    MaterialRecord,
    Property,
    Source,
    SourceKind,
    Status,
    UnknownMaterial,
    available,
    convert,
    material_for,
    register_quantity,
    resolve,
    transcribe,
)
from app.solve.types import Material

# The library exactly as it stood before provenance existed, transcribed from
# git show d64ebb0^:app/solve/materials.py. Order: E (MPa), nu, Re (MPa),
# rho (kg/m3), alpha (1/K).
BEFORE_THE_REWRITE: dict[str, tuple[float, float, float, float, float]] = {
    "aluminium-6061-t6": (68_900, 0.33, 276, 2700, 23.6e-6),
    "aluminium-7075-t6": (71_700, 0.33, 503, 2810, 23.4e-6),
    "steel-1018": (205_000, 0.29, 370, 7870, 11.7e-6),
    "stainless-304": (193_000, 0.29, 215, 8000, 17.3e-6),
    "titanium-ti6al4v": (113_800, 0.342, 880, 4430, 8.6e-6),
    "abs": (2_200, 0.35, 40, 1040, 90.0e-6),
    "pla": (3_500, 0.36, 50, 1240, 68.0e-6),
    "nylon-pa12": (1_700, 0.39, 48, 1010, 110.0e-6),
}

A_SOURCE = Source(citation="A datasheet somebody can go and read", kind=SourceKind.DATASHEET)


def a_property(name: str, value: float, status: Status = Status.TYPICAL) -> Property:
    return Property(name=name, value=value, status=status, source=A_SOURCE)


class TestBackwardCompatibility:
    """`MATERIALS` is what it always was. The whole solver rests on this."""

    def test_materials_is_a_plain_dict_of_material(self) -> None:
        # Dozens of existing tests do MATERIALS["steel-1018"] and iterate it as
        # a mapping. A Mapping proxy or a lazy view would break them, so the
        # concrete type is part of the contract, not an implementation detail.
        assert isinstance(MATERIALS, dict)
        assert all(isinstance(key, str) for key in MATERIALS)
        assert all(isinstance(value, Material) for value in MATERIALS.values())

    def test_the_same_eight_slugs(self) -> None:
        assert set(MATERIALS) == set(BEFORE_THE_REWRITE)

    @pytest.mark.parametrize("slug", sorted(BEFORE_THE_REWRITE))
    def test_the_numbers_did_not_move(self, slug: str) -> None:
        modulus, poisson, yield_strength, density, expansion = BEFORE_THE_REWRITE[slug]
        material = MATERIALS[slug]
        # Exact, not approximate. `transcribe("youngs_modulus_mpa", 68.9, "GPa")`
        # multiplies by 1000 in floating point, and 68.9 * 1000.0 lands exactly
        # on 68900.0 — if a future edit changes that it is a real change to
        # every stress computed against this grade and must be seen.
        assert material.youngs_modulus_mpa == modulus
        assert material.poissons_ratio == poisson
        assert material.yield_strength_mpa == yield_strength
        assert material.density_kg_m3 == density
        assert material.thermal_expansion_per_k == expansion

    def test_the_name_field_is_still_the_slug(self) -> None:
        # `Material.name` is what a job row and a CATIA call carry.
        for slug, material in MATERIALS.items():
            assert material.name == slug

    def test_material_is_still_frozen(self) -> None:
        with pytest.raises(Exception):  # pydantic raises ValidationError here
            MATERIALS["steel-1018"].youngs_modulus_mpa = 1.0  # type: ignore[misc]

    def test_materials_is_derived_from_records_not_typed_twice(self) -> None:
        # The claim in the module docstring. Two tables of the same physical
        # constants drift; this is what stops them being two tables.
        assert set(RECORDS) == set(MATERIALS)
        for slug, record in RECORDS.items():
            assert record.to_material() == MATERIALS[slug]


class TestUnknownMaterialIsRefused:
    """The measured defect: an unrecognised slug silently became steel."""

    def test_an_unknown_slug_raises(self) -> None:
        with pytest.raises(UnknownMaterial) as excinfo:
            resolve("unobtanium")
        assert "unobtanium" in str(excinfo.value)

    def test_the_refusal_names_what_is_available(self) -> None:
        with pytest.raises(UnknownMaterial) as excinfo:
            resolve("unobtanium")
        message = str(excinfo.value)
        for slug in available():
            assert slug in message

    def test_a_material_class_is_not_a_material(self) -> None:
        # "Aluminium" is exactly what an engineer types, and exactly what used
        # to attach steel. It must not resolve, and the message must say why a
        # class is not enough rather than just listing slugs.
        with pytest.raises(UnknownMaterial) as excinfo:
            resolve("aluminium")
        assert "aluminium-6061-t6" in str(excinfo.value)

    def test_a_known_alias_still_does_not_resolve(self) -> None:
        # `also_known_as` makes a refusal helpful; it never resolves a lookup.
        for alias in ("steel", "inox", "6061", "ti64"):
            with pytest.raises(UnknownMaterial):
                resolve(alias)

    def test_case_differences_are_refused_rather_than_guessed(self) -> None:
        with pytest.raises(UnknownMaterial) as excinfo:
            resolve("Steel-1018")
        assert "steel-1018" in str(excinfo.value)

    def test_nothing_near_by_is_substituted(self) -> None:
        # The heart of it: a near miss produces a refusal, not a part.
        with pytest.raises(UnknownMaterial):
            material_for("steel-1020")

    def test_material_for_returns_the_solver_material_for_a_real_slug(self) -> None:
        assert material_for("steel-1018") is not None
        assert material_for("steel-1018") == MATERIALS["steel-1018"]

    def test_available_is_sorted_and_complete(self) -> None:
        assert available() == tuple(sorted(RECORDS))


class TestAbsentIsAbsent:
    """A property with no source is missing, and reads as missing."""

    @pytest.mark.parametrize("slug", sorted(RECORDS))
    def test_no_grade_claims_fatigue_data(self, slug: str) -> None:
        # The module docstring's claim: nobody here holds an S-N curve, so the
        # property is absent in every record rather than approximated.
        assert RECORDS[slug].get("fatigue_strength_mpa") is None

    def test_get_returns_none_not_zero(self) -> None:
        assert RECORDS["steel-1018"].get("fatigue_strength_mpa") is None

    def test_value_returns_none_not_zero(self) -> None:
        # The trap this guards: `record.value(...) * area` on a missing property
        # must be a TypeError somebody sees, never a silent zero stress.
        missing = RECORDS["steel-1018"].value("fatigue_strength_mpa")
        assert missing is None
        with pytest.raises(TypeError):
            _ = missing * 2.0  # type: ignore[operator]

    def test_require_names_the_missing_property(self) -> None:
        with pytest.raises(IncompleteMaterial) as excinfo:
            RECORDS["steel-1018"].require("fatigue_strength_mpa")
        message = str(excinfo.value)
        assert "fatigue_strength_mpa" in message
        assert "steel-1018" in message

    def test_the_refusal_says_absent_not_zero_and_lists_what_is_held(self) -> None:
        with pytest.raises(IncompleteMaterial) as excinfo:
            RECORDS["steel-1018"].require("fatigue_strength_mpa")
        message = str(excinfo.value)
        assert "absent, not zero" in message
        # Naming what *is* held is how the caller decides what to do next.
        assert "yield_strength_mpa" in message

    def test_missing_lists_absences_in_order(self) -> None:
        record = RECORDS["pla"]
        assert record.missing(("youngs_modulus_mpa", "fatigue_strength_mpa")) == (
            "fatigue_strength_mpa",
        )

    def test_to_dict_publishes_the_absences(self) -> None:
        # A consumer serialising a record must be able to see the gap without
        # knowing the vocabulary; "absent" is that list.
        payload = RECORDS["steel-1018"].to_dict()
        assert "fatigue_strength_mpa" in payload["absent"]  # type: ignore[operator]
        assert "fatigue_strength_mpa" not in payload["properties"]  # type: ignore[operator]

    def test_a_record_missing_a_solver_property_refuses_to_be_solved_with(self) -> None:
        # Break the thing the guard guards: build a record with no density and
        # ask for the solver's view of it.
        incomplete = MaterialRecord(
            slug="mystery-alloy",
            display_name="Mystery alloy",
            category="test",
            condition=Condition(note="fabricated for this test"),
            properties={
                "youngs_modulus_mpa": a_property("youngs_modulus_mpa", 200_000.0),
                "poissons_ratio": a_property("poissons_ratio", 0.3),
                "yield_strength_mpa": a_property("yield_strength_mpa", 250.0),
            },
        )
        with pytest.raises(IncompleteMaterial) as excinfo:
            incomplete.to_material()
        assert "density_kg_m3" in str(excinfo.value)

    def test_every_curated_record_carries_the_solver_properties(self) -> None:
        for slug, record in RECORDS.items():
            assert record.missing(SOLVER_REQUIRED) == (), slug


class TestStatusAndDesignBasis:
    """A typical value is not a design minimum, and the record says which it is."""

    def test_specified_and_measured_are_a_design_basis(self) -> None:
        assert Status.SPECIFIED.is_design_basis
        assert Status.MEASURED.is_design_basis

    def test_typical_and_estimated_are_not(self) -> None:
        assert not Status.TYPICAL.is_design_basis
        assert not Status.ESTIMATED.is_design_basis

    def test_nothing_in_the_curated_set_claims_to_be_measured(self) -> None:
        # The docstring's claim: these are aggregated handbook figures, not lot
        # certificates. A MEASURED value here would be a false claim about a
        # test report nobody holds.
        for slug, record in RECORDS.items():
            for name, prop in record.properties.items():
                assert prop.status is not Status.MEASURED, f"{slug}.{name}"

    def test_the_curated_strengths_are_flagged_as_not_a_design_basis(self) -> None:
        # Every curated yield strength is TYPICAL, so a reviewer asking "what
        # here is a population average rather than a guaranteed minimum?" gets
        # a non-empty answer rather than silence.
        for slug, record in RECORDS.items():
            assert "yield_strength_mpa" in record.not_design_basis(), slug

    def test_a_specified_strength_is_not_flagged(self) -> None:
        record = MaterialRecord(
            slug="certified",
            display_name="Certified plate",
            category="test",
            condition=Condition(),
            properties={
                "yield_strength_mpa": a_property(
                    "yield_strength_mpa", 355.0, status=Status.SPECIFIED
                )
            },
        )
        assert record.not_design_basis() == ()

    def test_an_estimate_must_say_how_it_was_estimated(self) -> None:
        # Break the guard: an ESTIMATED value with no note.
        with pytest.raises(ValueError) as excinfo:
            Property(
                name="poissons_ratio",
                value=0.3,
                status=Status.ESTIMATED,
                source=A_SOURCE,
            )
        assert "how it was estimated" in str(excinfo.value)

    def test_an_estimate_with_a_note_is_accepted(self) -> None:
        prop = Property(
            name="poissons_ratio",
            value=0.3,
            status=Status.ESTIMATED,
            source=A_SOURCE,
            note="Class-typical for a glassy thermoplastic.",
        )
        assert prop.status is Status.ESTIMATED


class TestProvenanceIsCheckable:
    """A number a reviewer cannot trace is not provenance."""

    def test_a_source_needs_a_citation(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            Source(citation="   ", kind=SourceKind.DATASHEET)
        assert "citation" in str(excinfo.value)

    def test_every_curated_property_carries_a_source_and_a_status(self) -> None:
        for slug, record in RECORDS.items():
            for name, prop in record.properties.items():
                assert prop.source.citation.strip(), f"{slug}.{name}"
                assert isinstance(prop.status, Status), f"{slug}.{name}"

    def test_every_curated_record_states_its_condition(self) -> None:
        # 6061-O and 6061-T6 differ by five in yield strength. A record with no
        # stated condition is not usable, so none of ours may have one.
        for slug, record in RECORDS.items():
            assert str(record.condition) != "unspecified condition", slug

    def test_a_property_filed_under_the_wrong_key_is_refused(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            MaterialRecord(
                slug="mislabelled",
                display_name="Mislabelled",
                category="test",
                condition=Condition(),
                properties={"density_kg_m3": a_property("youngs_modulus_mpa", 200_000.0)},
            )
        assert "must agree" in str(excinfo.value)

    def test_to_dict_round_trips_the_source(self) -> None:
        payload = RECORDS["steel-1018"].to_dict()
        properties = payload["properties"]
        assert isinstance(properties, dict)
        modulus = properties["youngs_modulus_mpa"]
        assert isinstance(modulus, dict)
        assert modulus["unit"] == "MPa"
        assert modulus["source"]["citation"]  # type: ignore[index]


class TestTheVocabulary:
    """A name that is not in the table has no declared unit, so it cannot be stored."""

    def test_a_property_has_no_unit_field_to_get_wrong(self) -> None:
        # This is the mechanism that makes "one conversion, at the boundary"
        # enforceable: there is nowhere downstream for a GPa figure to hide.
        fields = {f.name for f in dataclasses.fields(Property)}
        assert "unit" not in fields
        assert Property(
            name="youngs_modulus_mpa", value=1.0, status=Status.TYPICAL, source=A_SOURCE
        ).unit == "MPa"

    def test_an_unlisted_property_name_is_refused(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            a_property("tensile_modulus", 200_000.0)
        assert "not a property this library knows" in str(excinfo.value)

    def test_a_near_miss_gets_a_suggestion(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            a_property("young_modulus_mpa", 200_000.0)
        assert "youngs_modulus_mpa" in str(excinfo.value)

    def test_every_vocabulary_entry_has_a_unit_and_a_meaning(self) -> None:
        for name in MATERIAL_PROPERTIES:
            unit, meaning = PROPERTY_UNITS[name]
            assert unit, name
            assert meaning, name

    def test_property_units_is_read_only(self) -> None:
        with pytest.raises(TypeError):
            PROPERTY_UNITS["invented"] = ("m", "nonsense")  # type: ignore[index]

    def test_the_material_vocabulary_is_frozen_at_import(self) -> None:
        # `app.parts` registers a couple of dozen fastener quantities into the
        # shared registry. That must not make "proof load" a material property,
        # or `MaterialRecord.to_dict()["absent"]` starts reporting every bolt
        # dimension as missing from an alloy.
        import app.parts  # noqa: F401  (imported for its registration side effect)

        assert "proof_load_n" in PROPERTY_UNITS
        assert "proof_load_n" not in MATERIAL_PROPERTIES
        assert set(MATERIAL_PROPERTIES) <= set(PROPERTY_UNITS)

    def test_registering_the_same_quantity_twice_is_a_no_op(self) -> None:
        register_quantity("test_quantity_mm", "mm", "A quantity invented by this test")
        register_quantity("test_quantity_mm", "mm", "A quantity invented by this test")
        assert PROPERTY_UNITS["test_quantity_mm"] == (
            "mm",
            "A quantity invented by this test",
        )

    def test_registering_a_conflicting_unit_is_refused(self) -> None:
        # Break the guard: the same name with a different unit. Resolving this
        # by import order is how a torque in N.m lands where N.mm is expected.
        register_quantity("test_conflict_n_mm", "N.mm", "A torque")
        with pytest.raises(ValueError) as excinfo:
            register_quantity("test_conflict_n_mm", "N.m", "A torque")
        assert "cannot be redeclared" in str(excinfo.value)


class TestTheUnitBoundary:
    """`transcribe` is the one place a foreign unit becomes mm-N-MPa."""

    @pytest.mark.parametrize(
        ("value", "unit", "expected"),
        [
            (68.9, "GPa", 68_900.0),
            (205.0, "GPa", 205_000.0),
            (450.0, "MPa", 450.0),
            (450.0, "N/mm2", 450.0),
            (10.0, "Msi", 68_947.57293168361),
            (36.0, "ksi", 248.21126255406098),
            (1000.0, "psi", 6.894757293168361),
            (1e6, "Pa", 1.0),
            (1000.0, "kPa", 1.0),
        ],
    )
    def test_stress_units_convert_once_at_the_boundary(
        self, value: float, unit: str, expected: float
    ) -> None:
        prop = transcribe(
            "youngs_modulus_mpa", value, unit, status=Status.TYPICAL, source=A_SOURCE
        )
        assert prop.value == pytest.approx(expected, rel=1e-12)
        assert prop.unit == "MPa"

    @pytest.mark.parametrize(
        ("value", "unit", "expected"),
        [
            (2700.0, "kg/m3", 2700.0),
            (2.70, "g/cm3", 2700.0),
            (7.87, "kg/dm3", 7870.0),
        ],
    )
    def test_density_units_convert(self, value: float, unit: str, expected: float) -> None:
        prop = transcribe(
            "density_kg_m3", value, unit, status=Status.TYPICAL, source=A_SOURCE
        )
        assert prop.value == pytest.approx(expected, rel=1e-12)
        assert prop.unit == "kg/m3"

    @pytest.mark.parametrize(
        ("unit", "expected"),
        [
            # Derived from the SI definitions rather than copied out of the
            # table, so this checks the factor instead of restating it. The
            # international pound is exactly 0.45359237 kg and the inch exactly
            # 0.0254 m; rounding either is a silent error at the fourth digit
            # of every American datasheet.
            ("lb/in3", 0.45359237 / 0.0254**3),
            # A pound-force over a square inch, in pascals, then to MPa.
            ("psi", 0.45359237 * 9.80665 / 0.0254**2 * 1e-6),
            ("ksi", 0.45359237 * 9.80665 / 0.0254**2 * 1e-3),
            ("Msi", 0.45359237 * 9.80665 / 0.0254**2),
        ],
    )
    def test_the_imperial_factors_are_the_exact_definitions(
        self, unit: str, expected: float
    ) -> None:
        assert convert(1.0, unit, "kg/m3" if unit == "lb/in3" else "MPa") == pytest.approx(
            expected, rel=1e-15
        )

    @pytest.mark.parametrize(
        ("value", "unit", "expected"),
        [
            (11.7e-6, "1/K", 11.7e-6),
            (11.7, "ppm/K", 11.7e-6),
            (11.7, "ppm/C", 11.7e-6),
            # A kelvin is 1.8 degF, so a strain per degF is 1.8 times smaller
            # than the same strain per kelvin: per K = per degF x 1.8.
            (6.5e-6, "1/F", 1.17e-5),
            (6.5, "ppm/F", 1.17e-5),
        ],
    )
    def test_thermal_expansion_converts(
        self, value: float, unit: str, expected: float
    ) -> None:
        prop = transcribe(
            "thermal_expansion_per_k", value, unit, status=Status.TYPICAL, source=A_SOURCE
        )
        assert prop.value == pytest.approx(expected, rel=1e-12)
        assert prop.unit == "1/K"

    def test_celsius_is_an_offset_not_a_scaling(self) -> None:
        # The one non-multiplicative pair. Scaling it would put a melting point
        # of 1450 C at 1450 K, which is below room temperature in kelvin terms.
        assert convert(0.0, "C", "K") == pytest.approx(273.15)
        assert convert(1450.0, "C", "K") == pytest.approx(1723.15)

    def test_an_unknown_conversion_is_refused_not_assumed(self) -> None:
        # Break the guard: quietly treating an unrecognised unit as
        # already-correct is exactly what this exists to prevent.
        with pytest.raises(ValueError) as excinfo:
            convert(1.0, "furlongs", "MPa")
        assert "No conversion" in str(excinfo.value)

    def test_a_same_unit_conversion_is_exact(self) -> None:
        assert convert(1234.5678, "MPa", "MPa") == 1234.5678

    def test_a_foreign_unit_cannot_reach_a_property_directly(self) -> None:
        # `Property` takes no unit, so the only way a GPa figure becomes a
        # stored value is through `transcribe`. Constructing one by hand stores
        # the number under the canonical unit, which is why nobody may do it
        # with a foreign figure — and why every curated value goes through
        # `transcribe` even when it needs no conversion.
        direct = Property(
            name="youngs_modulus_mpa", value=68.9, status=Status.TYPICAL, source=A_SOURCE
        )
        assert direct.unit == "MPa"  # 68.9 MPa, not 68.9 GPa. The number is wrong.
        via = transcribe(
            "youngs_modulus_mpa", 68.9, "GPa", status=Status.TYPICAL, source=A_SOURCE
        )
        assert via.value == 68_900.0

    def test_transcribing_an_unknown_name_still_refuses(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            transcribe("not_a_property", 1.0, "MPa", status=Status.TYPICAL, source=A_SOURCE)
        assert "not a property this library knows" in str(excinfo.value)

    def test_every_curated_value_is_already_in_the_canonical_unit(self) -> None:
        # The claim in the module docstring: nothing downstream converts.
        for slug, record in RECORDS.items():
            for name, prop in record.properties.items():
                assert prop.unit == PROPERTY_UNITS[name][0], f"{slug}.{name}"

    def test_the_curated_numbers_are_physically_sane_in_our_units(self) -> None:
        # A unit slip is a factor of 1000 or 1e6, so a coarse band catches it
        # where a tight one would only pin the table to itself.
        for slug, record in RECORDS.items():
            modulus = record.require("youngs_modulus_mpa").value
            density = record.require("density_kg_m3").value
            assert 1_000.0 < modulus < 500_000.0, f"{slug}: E in MPa, not GPa or Pa"
            assert 500.0 < density < 25_000.0, f"{slug}: density in kg/m3, not g/cm3"
            expansion = record.value("thermal_expansion_per_k")
            assert expansion is not None
            assert 1e-7 < expansion < 1e-3, f"{slug}: expansion per K, not per micro-K"
