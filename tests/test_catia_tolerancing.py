"""Datums and feature control frames as bridge operations — THE QUEUE E7, 2026-09-20.

E7 asked for "one datum and one position frame on the bracket through FTA". Both were created
on a V5-R33 seat on 2026-09-19; this is the half that makes them something the agent can ask
for. Every constant here came off that seat.

**The claim these tests exist to protect is that the characteristic index is per family.**
`3` is *flatness* without a datum frame and *parallelism* with one. Nothing errors if the two
are confused — CATIA draws a different symbol, on a part that looks finished. That is why the
vocabulary is a name, why `CHARACTERISTICS` is two tables, and why `characteristic_index`
takes `with_datums` rather than inferring anything.

Offline: no seat, no COM. `app/catia/ops/tolerancing.py` imports nothing from `win32com`;
the COM half lives in `scripts/catia_bridge/com/tolerancing.py` and is `pragma: no cover` like
the rest of the backend.
"""

from __future__ import annotations

import pytest

from app.catia.ops import OPERATIONS
from app.catia.ops.spec import Tier, Workbench
from app.catia.ops.tolerancing import (
    CHARACTERISTICS,
    FORM_CHARACTERISTICS,
    REFERENCED_CHARACTERISTICS,
    TYPE_LIBRARY,
    UNIMPLEMENTED,
    characteristic_index,
)
from app.kernel.occt.refusals import REASONS

#: What the seat answered on a planar face, both families. Measured 2026-09-19.
SEAT_WITHOUT_DATUMS = {1: "Rectitude", 3: "Planéité", 6: "Profil ligne", 7: "Profil surface"}
SEAT_WITH_DATUMS = {3: "Parallélisme", 4: "Localisation", 7: "Profil ligne", 8: "Profil surface"}

NAMES = ("catia_tolerance_datum", "catia_tolerance_frame", "catia_tolerance_list")


class TestTheIndexIsPerFamily:
    """The finding the whole module is shaped around."""

    def test_three_means_two_different_things(self) -> None:
        """Flatness without a frame, parallelism with one — the same number. A schema keyed
        on the index alone would put the wrong symbol on a part and nothing would error."""
        assert characteristic_index("flatness", with_datums=False) == 3
        assert characteristic_index("parallelism", with_datums=True) == 3

    @pytest.mark.parametrize(("name", "index"), sorted(FORM_CHARACTERISTICS.items()))
    def test_the_form_family_matches_the_seat(self, name: str, index: int) -> None:
        assert characteristic_index(name, with_datums=False) == index
        assert index in SEAT_WITHOUT_DATUMS

    @pytest.mark.parametrize(("name", "index"), sorted(REFERENCED_CHARACTERISTICS.items()))
    def test_the_referenced_family_matches_the_seat(self, name: str, index: int) -> None:
        assert characteristic_index(name, with_datums=True) == index
        assert index in SEAT_WITH_DATUMS

    def test_the_profiles_are_in_both_families_at_different_indices(self) -> None:
        """A profile tolerance is legitimately either, and CATIA numbers it differently in
        each — the one case where the same name must not share a number."""
        for profile in ("line_profile", "surface_profile"):
            assert characteristic_index(profile, with_datums=False) != characteristic_index(
                profile, with_datums=True
            )


class TestItRefusesByNamingTheOtherFamily:
    def test_position_without_datums_says_it_needs_them(self) -> None:
        """"Unknown characteristic" would send the caller hunting for a typo. The fault is a
        missing datum, and the message says so."""
        with pytest.raises(ValueError, match="needs at least one in `datums`"):
            characteristic_index("position", with_datums=False)

    def test_flatness_with_datums_says_it_takes_none(self) -> None:
        with pytest.raises(ValueError, match="takes no datums"):
            characteristic_index("flatness", with_datums=True)

    def test_a_name_nobody_measured_is_refused_with_the_list(self) -> None:
        """Cylindricity and perpendicularity are real GD&T and were *refused by the seat* at
        every index tried on a planar face. An index nobody has watched succeed is not a
        capability, so they are absent rather than guessed."""
        for unmeasured in ("cylindricity", "perpendicularity", "runout", "concentricity"):
            with pytest.raises(ValueError, match="not a characteristic"):
                characteristic_index(unmeasured, with_datums=True)


class TestWhatTheRegistryPromises:
    def test_the_three_operations_are_declared(self) -> None:
        declared = {o.name for o in OPERATIONS if o.name.startswith("catia_tolerance_")}
        assert set(NAMES) <= declared

    def test_they_are_on_the_fta_workbench(self) -> None:
        for operation in OPERATIONS:
            if operation.name in NAMES:
                assert operation.workbench is Workbench.FTA

    def test_listing_is_a_read_and_the_rest_are_writes(self) -> None:
        tiers = {o.name: o.tier for o in OPERATIONS if o.name in NAMES}
        assert tiers["catia_tolerance_list"] is Tier.READ
        assert tiers["catia_tolerance_datum"] is Tier.WRITE
        assert tiers["catia_tolerance_frame"] is Tier.WRITE

    def test_the_enum_offers_exactly_what_can_be_built(self) -> None:
        frame = next(o for o in OPERATIONS if o.name == "catia_tolerance_frame")
        offered = set(frame.json_schema()["properties"]["characteristic"]["enum"])

        assert offered == set(CHARACTERISTICS)
        assert offered == set(FORM_CHARACTERISTICS) | set(REFERENCED_CHARACTERISTICS)

    def test_no_tolerance_value_is_advertised(self) -> None:
        """**The promise not made.** Creating a frame was measured; setting its magnitude was
        not. A `value_mm` the bridge silently failed to apply would be a drawing that says
        0.05 and means CATIA's default, which is worse than an obviously incomplete frame."""
        frame = next(o for o in OPERATIONS if o.name == "catia_tolerance_frame")
        properties = set(frame.json_schema()["properties"])

        assert properties == {"face", "characteristic", "datums"}
        assert "tolerance_value" in UNIMPLEMENTED

    def test_the_summary_says_the_value_is_not_set(self) -> None:
        """The agent reads summaries, not this test file. If the limit is only recorded here
        the model will assume a created frame is a finished one."""
        frame = next(o for o in OPERATIONS if o.name == "catia_tolerance_frame")

        assert "not implemented" in frame.summary
        assert "default" in frame.summary


class TestTheOpenKernelRefusesEachOne:
    @pytest.mark.parametrize("name", NAMES)
    def test_it_has_a_reason(self, name: str) -> None:
        assert name in REASONS
        assert len(REASONS[name]) > 50

    def test_no_refusal_claims_this_product_cannot_tolerance_a_part(self) -> None:
        """It can — `app/rules/gdt.py` holds the tolerancing and `app/manufacture/drawing.py`
        puts it on a sheet. What the open kernel lacks is *annotations on the solid*, which is
        a different thing, and saying otherwise would be a false statement about this
        repository's own capability."""
        for name in NAMES:
            reason = REASONS[name]
            assert "cannot tolerance" not in reason
            assert "no tolerancing" not in reason.lower()
            assert "drawing" in reason or "design record" in reason or "gdt" in reason.lower()


class TestWhatIsRecordedRatherThanBuilt:
    def test_the_unimplemented_ones_carry_a_reason(self) -> None:
        """Declared as data rather than simply absent, for `sheet_metal.py`'s reason: a
        missing operation reads as "nobody got to it yet" and invites somebody to write one
        blind."""
        assert set(UNIMPLEMENTED) == {"tolerance_value", "datum_target", "roughness"}
        for name, reason in UNIMPLEMENTED.items():
            assert len(reason) > 40, name

    def test_none_of_them_is_declared(self) -> None:
        declared = {o.name for o in OPERATIONS}
        for name in UNIMPLEMENTED:
            assert f"catia_tolerance_{name}" not in declared

    def test_the_type_library_is_recorded(self) -> None:
        """Late binding sees none of this workbench — `part.AnnotationSets` comes back as
        `<COMObject <unknown>>`, which reads exactly like an unlicensed seat. Third API here
        with that trap."""
        assert TYPE_LIBRARY == "{88D26C84-D8E9-0000-0280-020CC3000000}"
