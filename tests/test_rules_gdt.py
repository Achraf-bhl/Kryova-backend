"""GD&T held as data and checked for sense — Phase 13.2.

A feature control frame is a sentence in a formal language, and most of what is
wrong with a real one is wrong *in the sentence*: a datum letter nothing
establishes, a material modifier on a characteristic that cannot take one, a
diametral zone on a flatness, a zero tolerance with no MMC to make it mean
anything. Those are the refusals below, and each one is checked for the message
as well as for the raising — an engineer who is told "invalid frame" has been
told nothing, and the house register here is that a refusal names what is wrong.

**The boundary is tested as hard as the grammar.** `app/rules/gdt.py` does not
evaluate a tolerance zone, and `TestNothingHereEvaluatesAToleranceZone` is the
guard on that. Whether an actual axis lies inside a Ø0.2 cylindrical zone at MMC
needs the as-produced feature size to know the bonus tolerance, and as-produced
sizes are CMM data. A module that answered it from the model would be answering
a different question in the same words, and — this is the part that matters —
the answer would be believed.

**Over-refusal is not the safe direction.** The recovery from a refusal is to
write something else, so a frame refused wrongly becomes a drawing that says
something wrong. Every characteristic is therefore checked to be constructible
in its plainest form, and the places where practice genuinely varies are checked
to be *permitted*, not to be refused.

Offline: no kernel, no payload, no seat. Nothing in this module imports one.
"""

from __future__ import annotations

import ast
import inspect
import math
import pathlib

import pytest

from app.rules import gdt as gdt_module
from app.rules.errors import GdtError, RuleError
from app.rules.gdt import (
    GRAMMAR,
    MAXIMUM_DATUM_REFERENCES,
    RESERVED_DATUM_LETTERS,
    Category,
    Characteristic,
    Datum,
    DatumReference,
    DatumRule,
    DatumScheme,
    FeatureControlFrame,
    MaterialCondition,
    Tolerancing,
    datum_scheme,
)

#: Characteristics that must carry a datum reference to be legal at all, so a
#: minimal frame for one needs a primary.
_NEEDS_A_DATUM = tuple(
    c for c in Characteristic if GRAMMAR[c].datums is DatumRule.REQUIRED
)


def _minimal(characteristic: Characteristic) -> FeatureControlFrame:
    """The plainest legal frame for a characteristic: a width, no modifier."""
    grammar = GRAMMAR[characteristic]
    datums = (DatumReference("A"),) if grammar.datums is DatumRule.REQUIRED else ()
    return FeatureControlFrame(
        feature="feature",
        characteristic=characteristic,
        tolerance_mm=0.1,
        datums=datums,
    )


class TestNothingHereEvaluatesAToleranceZone:
    """The boundary the module docstring draws, made checkable.

    Named for the thing it forbids rather than for what it allows, because the
    failure it guards against is silent: a method that answered "is this feature
    within its position tolerance" from model geometry would look like the
    feature Kryova is missing, and would be a different question with the same
    words on it.
    """

    def test_the_module_imports_no_geometry_no_measurement_and_no_solver(self) -> None:
        tree = ast.parse(pathlib.Path(gdt_module.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        forbidden = {
            name
            for name in imported
            if name.startswith(("app.kernel", "app.mesh", "app.solve", "app.design"))
        }

        assert forbidden == set(), (
            f"app/rules/gdt.py imports {sorted(forbidden)}. Evaluating a tolerance zone "
            "needs the as-produced feature size, which is inspection data — a module "
            "that answered it from the model would be believed."
        )

    def test_no_public_callable_accepts_a_measurement_payload_or_a_shape(self) -> None:
        """Signatures, not names: a method called `check` that takes nothing but a
        frame is fine; one that takes `measurements` is the module quietly
        acquiring an evaluator."""
        inspection_arguments = {
            "measurements",
            "measurement",
            "payload",
            "shape",
            "document",
            "mesh",
            "actual",
            "as_produced",
            "inspection",
        }
        offenders: list[str] = []
        for name in gdt_module.__all__:
            member = getattr(gdt_module, name)
            candidates = [member] if callable(member) else []
            if inspect.isclass(member):
                candidates = [
                    attribute
                    for attribute_name, attribute in vars(member).items()
                    if callable(attribute) and not attribute_name.startswith("_")
                ]
            for candidate in candidates:
                try:
                    signature = inspect.signature(candidate)
                except (TypeError, ValueError):  # pragma: no cover - builtins
                    continue
                clash = set(signature.parameters) & inspection_arguments
                if clash:
                    offenders.append(f"{name}.{candidate.__name__}{sorted(clash)}")

        assert offenders == []

    def test_a_frame_reports_no_verdict_of_its_own(self) -> None:
        """No `passed`, no `ok`, no `within_tolerance`. There is nothing here for
        such a property to be computed from, and a false one would be read."""
        frame = _minimal(Characteristic.FLATNESS)

        for verdict in ("passed", "ok", "within_tolerance", "conforms", "evaluate"):
            assert not hasattr(frame, verdict)


class TestADatumIsALetterAndAFeature:
    def test_a_reserved_letter_is_refused_and_says_why(self) -> None:
        for letter in sorted(RESERVED_DATUM_LETTERS):
            with pytest.raises(GdtError) as caught:
                Datum(letter=letter, feature="base face")
            assert letter in str(caught.value)
            assert "read as digits" in str(caught.value)

    def test_the_reserved_set_is_the_three_y145_names(self) -> None:
        assert RESERVED_DATUM_LETTERS == frozenset({"I", "O", "Q"})

    def test_a_multi_letter_identifier_is_refused_and_names_the_compound_case(self) -> None:
        """'A-B' is two datum features establishing one datum, and the refusal has
        to say so or the writer will simply try 'AB' next."""
        with pytest.raises(GdtError) as caught:
            Datum(letter="A-B", feature="two flats")

        assert "Compound datums" in str(caught.value)

    @pytest.mark.parametrize("letter", ["", "  ", "1", "?"])
    def test_a_non_letter_is_refused(self, letter: str) -> None:
        with pytest.raises(GdtError):
            Datum(letter=letter, feature="base face")

    def test_a_letter_is_normalised_to_upper_case_and_stripped(self) -> None:
        assert Datum(letter=" b ", feature="bore").letter == "B"
        assert DatumReference(" c ").letter == "C"

    def test_a_datum_that_names_no_feature_is_refused(self) -> None:
        """A letter in a frame nobody can set up to."""
        with pytest.raises(GdtError) as caught:
            Datum(letter="A", feature="   ")

        assert "put an indicator on" in str(caught.value)

    def test_a_letter_cannot_name_two_datums(self) -> None:
        with pytest.raises(GdtError) as caught:
            DatumScheme((Datum("A", "base face"), Datum("a", "bore")))

        assert "declared twice" in str(caught.value)

    def test_a_scheme_reports_its_letters(self) -> None:
        scheme = datum_scheme([Datum("A", "base"), Datum("B", "bore")])

        assert scheme.letters == frozenset({"A", "B"})
        assert scheme.get("b") is not None
        assert scheme.get("Z") is None


class TestTheGrammarTableIsCompleteAndConsistent:
    """The table is Y14.5 read as data, so the invariants are Y14.5's."""

    def test_every_characteristic_has_an_entry(self) -> None:
        assert set(GRAMMAR) == set(Characteristic)

    def test_every_characteristic_is_constructible_in_its_plainest_form(self) -> None:
        """RFS is what an unmarked frame means, so every characteristic must
        accept it — an over-refusal here would make a legal drawing unwritable."""
        for characteristic in Characteristic:
            assert MaterialCondition.RFS in GRAMMAR[characteristic].conditions
            assert _minimal(characteristic).category is GRAMMAR[characteristic].category

    def test_form_controls_and_only_form_controls_forbid_datums(self) -> None:
        for characteristic, grammar in GRAMMAR.items():
            forbidden = grammar.datums is DatumRule.FORBIDDEN
            assert forbidden is (grammar.category is Category.FORM), characteristic

    def test_orientation_location_and_runout_all_require_a_datum(self) -> None:
        relational = {Category.ORIENTATION, Category.LOCATION, Category.RUNOUT}
        for characteristic, grammar in GRAMMAR.items():
            if grammar.category in relational:
                assert grammar.datums is DatumRule.REQUIRED, characteristic

    def test_profile_is_the_one_that_may_go_either_way(self) -> None:
        """Profile may be a free-standing form-and-size control or related to a
        datum reference frame. Both are correct, so both must be accepted."""
        for characteristic in (
            Characteristic.PROFILE_OF_A_LINE,
            Characteristic.PROFILE_OF_A_SURFACE,
        ):
            assert GRAMMAR[characteristic].datums is DatumRule.OPTIONAL
            FeatureControlFrame(
                feature="skin", characteristic=characteristic, tolerance_mm=0.4
            )
            FeatureControlFrame(
                feature="skin",
                characteristic=characteristic,
                tolerance_mm=0.4,
                datums=(DatumReference("A"), DatumReference("B")),
            )

    def test_the_rfs_only_characteristics_are_the_ones_with_no_feature_of_size(self) -> None:
        """Circularity and cylindricity control surface elements; concentricity
        and symmetry are defined on derived median points and are RFS by
        definition; runout is measured with the part rotating."""
        rfs_only = {
            characteristic
            for characteristic, grammar in GRAMMAR.items()
            if grammar.conditions == frozenset({MaterialCondition.RFS})
        }

        assert rfs_only == {
            Characteristic.CIRCULARITY,
            Characteristic.CYLINDRICITY,
            Characteristic.PROFILE_OF_A_LINE,
            Characteristic.PROFILE_OF_A_SURFACE,
            Characteristic.CONCENTRICITY,
            Characteristic.SYMMETRY,
            Characteristic.CIRCULAR_RUNOUT,
            Characteristic.TOTAL_RUNOUT,
        }

    def test_every_entry_carries_the_reason_its_refusals_will_quote(self) -> None:
        for characteristic, grammar in GRAMMAR.items():
            assert grammar.reason.strip(), characteristic
            assert grammar.symbol.strip(), characteristic

    def test_position_is_the_one_that_takes_everything(self) -> None:
        grammar = GRAMMAR[Characteristic.POSITION]

        assert grammar.diametral_zone is True
        assert grammar.conditions == frozenset(MaterialCondition)


class TestAnInconsistentFrameIsRefusedWithWhatIsWrong:
    def test_a_form_control_with_a_datum_names_the_datum_it_cannot_have(self) -> None:
        with pytest.raises(GdtError) as caught:
            FeatureControlFrame(
                feature="top face",
                characteristic=Characteristic.FLATNESS,
                tolerance_mm=0.1,
                datums=(DatumReference("A"),),
            )

        message = str(caught.value)
        assert "top face" in message
        assert "takes no datum reference" in message
        # The letter appears in both halves of the message. It used to appear in
        # one of them as the literal text '{listed}' — an f-string prefix missing
        # from the third line of the message — which was fixed on 2026-09-06.
        assert "{listed}" not in message
        assert message.count("A") >= 2

    def test_a_relationship_with_no_datum_is_refused(self) -> None:
        with pytest.raises(GdtError) as caught:
            FeatureControlFrame(
                feature="bore",
                characteristic=Characteristic.POSITION,
                tolerance_mm=0.2,
            )

        assert "is a relationship" in str(caught.value)

    def test_a_modifier_illegal_for_the_characteristic_is_refused_with_the_reason(
        self,
    ) -> None:
        with pytest.raises(GdtError) as caught:
            FeatureControlFrame(
                feature="journal",
                characteristic=Characteristic.CIRCULARITY,
                tolerance_mm=0.05,
                condition=MaterialCondition.MMC,
            )

        message = str(caught.value)
        assert "surface elements" in message  # the table's own reason, quoted
        assert "Allowed here: rfs" in message

    def test_the_same_modifier_is_accepted_where_there_is_a_feature_of_size(self) -> None:
        """The paired case. A refusal that fired on both would be useless."""
        frame = FeatureControlFrame(
            feature="bore",
            characteristic=Characteristic.POSITION,
            tolerance_mm=0.2,
            condition=MaterialCondition.MMC,
            diametral=True,
            datums=(DatumReference("A"),),
        )

        assert frame.condition is MaterialCondition.MMC

    def test_a_diametral_zone_on_a_planar_zone_is_refused(self) -> None:
        with pytest.raises(GdtError) as caught:
            FeatureControlFrame(
                feature="top face",
                characteristic=Characteristic.FLATNESS,
                tolerance_mm=0.1,
                diametral=True,
            )

        assert "diametral zone is not available" in str(caught.value)
        assert "two parallel planes" in str(caught.value)

    def test_a_zero_tolerance_regardless_of_feature_size_is_refused(self) -> None:
        """It demands perfect geometry, and no part can be made to it."""
        with pytest.raises(GdtError) as caught:
            FeatureControlFrame(
                feature="bore",
                characteristic=Characteristic.POSITION,
                tolerance_mm=0.0,
                datums=(DatumReference("A"),),
            )

        assert "only at MMC or LMC" in str(caught.value)

    def test_a_zero_tolerance_at_mmc_is_the_legal_callout_and_is_accepted(self) -> None:
        frame = FeatureControlFrame(
            feature="bore",
            characteristic=Characteristic.POSITION,
            tolerance_mm=0.0,
            condition=MaterialCondition.MMC,
            diametral=True,
            datums=(DatumReference("A"),),
        )

        assert frame.tolerance_mm == 0.0

    @pytest.mark.parametrize("tolerance", [-0.1, math.nan, math.inf])
    def test_a_zone_that_is_not_a_width_is_refused(self, tolerance: float) -> None:
        with pytest.raises(GdtError):
            FeatureControlFrame(
                feature="top face",
                characteristic=Characteristic.FLATNESS,
                tolerance_mm=tolerance,
            )

    def test_a_frame_with_no_feature_is_refused(self) -> None:
        with pytest.raises(GdtError) as caught:
            FeatureControlFrame(
                feature="", characteristic=Characteristic.FLATNESS, tolerance_mm=0.1
            )

        assert "needs the feature it applies to" in str(caught.value)

    def test_a_fourth_datum_reference_has_no_precedence_to_sit_in(self) -> None:
        assert MAXIMUM_DATUM_REFERENCES == 3
        with pytest.raises(GdtError) as caught:
            FeatureControlFrame(
                feature="bore",
                characteristic=Characteristic.POSITION,
                tolerance_mm=0.2,
                datums=tuple(DatumReference(letter) for letter in "ABCD"),
            )

        assert "at most 3" in str(caught.value)

    def test_three_datum_references_are_accepted(self) -> None:
        """The boundary on the permitted side — an off-by-one here refuses every
        fully constrained position callout on a real drawing."""
        frame = FeatureControlFrame(
            feature="bore",
            characteristic=Characteristic.POSITION,
            tolerance_mm=0.2,
            datums=tuple(DatumReference(letter) for letter in "ABC"),
        )

        assert len(frame.datums) == 3

    def test_one_datum_cannot_be_both_primary_and_secondary(self) -> None:
        with pytest.raises(GdtError) as caught:
            FeatureControlFrame(
                feature="bore",
                characteristic=Characteristic.POSITION,
                tolerance_mm=0.2,
                datums=(DatumReference("A"), DatumReference("A")),
            )

        assert "referenced twice" in str(caught.value)

    def test_a_datum_referenced_at_a_boundary_the_characteristic_cannot_use(self) -> None:
        """Runout is measured with the part rotating about the datum axis; there
        is no size term for a material boundary to act on, on either side of the
        frame."""
        with pytest.raises(GdtError) as caught:
            FeatureControlFrame(
                feature="rim",
                characteristic=Characteristic.TOTAL_RUNOUT,
                tolerance_mm=0.05,
                datums=(DatumReference("A", MaterialCondition.MMC),),
            )

        assert "material boundary this characteristic cannot use" in str(caught.value)

    def test_a_datum_referenced_at_mmc_is_accepted_where_position_allows_it(self) -> None:
        frame = FeatureControlFrame(
            feature="bore",
            characteristic=Characteristic.POSITION,
            tolerance_mm=0.2,
            diametral=True,
            datums=(DatumReference("A"), DatumReference("B", MaterialCondition.MMC)),
        )

        assert frame.datums[1].condition is MaterialCondition.MMC

    def test_a_gdt_error_is_a_rule_error_and_not_a_spec_error(self) -> None:
        """A malformed frame is the rule book being wrong, not the part. A
        correction loop catching it as a spec problem would change geometry in
        response to a typo on a drawing."""
        from app.design.errors import SpecError

        with pytest.raises(RuleError) as caught:
            FeatureControlFrame(
                feature="top", characteristic=Characteristic.FLATNESS, tolerance_mm=-1.0
            )

        assert isinstance(caught.value, GdtError)
        assert not isinstance(caught.value, SpecError)


class TestFramesAgainstTheDatumSchemeTheyReference:
    """The commonest thing wrong with a real drawing: B is referenced and never
    established. It needs both halves in one place to be visible at all."""

    def test_a_frame_referencing_an_undeclared_datum_is_refused(self) -> None:
        scheme = datum_scheme([Datum("A", "base face")])
        frame = FeatureControlFrame(
            feature="bore",
            characteristic=Characteristic.POSITION,
            tolerance_mm=0.2,
            datums=(DatumReference("B"),),
        )

        with pytest.raises(GdtError) as caught:
            Tolerancing(scheme=scheme, frames=(frame,))

        message = str(caught.value)
        assert "references datum B" in message
        assert "(A)" in message  # what *is* declared, so the fix is in the message

    def test_the_frame_alone_cannot_see_it(self) -> None:
        """Break the guard by removing the half that knows: the same frame builds
        without complaint on its own, which is why the check cannot live in
        `FeatureControlFrame.__post_init__`."""
        frame = FeatureControlFrame(
            feature="bore",
            characteristic=Characteristic.POSITION,
            tolerance_mm=0.2,
            datums=(DatumReference("B"),),
        )

        assert frame.datums[0].letter == "B"

    def test_an_empty_scheme_says_so_rather_than_printing_an_empty_list(self) -> None:
        frame = FeatureControlFrame(
            feature="bore",
            characteristic=Characteristic.POSITION,
            tolerance_mm=0.2,
            datums=(DatumReference("B"),),
        )

        with pytest.raises(GdtError) as caught:
            Tolerancing(frames=(frame,))

        assert "none are declared" in str(caught.value)

    def test_a_declared_and_unused_datum_is_reported_not_refused(self) -> None:
        """A datum scheme is often written before the frames that use it, and
        refusing the intermediate state would make the document unwritable in the
        order people write it."""
        tolerancing = Tolerancing(
            scheme=datum_scheme([Datum("A", "base"), Datum("C", "pad")]),
            frames=(
                FeatureControlFrame(
                    feature="bore",
                    characteristic=Characteristic.POSITION,
                    tolerance_mm=0.2,
                    datums=(DatumReference("A"),),
                ),
            ),
        )

        assert [d.letter for d in tolerancing.unreferenced_datums] == ["C"]

    def test_frames_are_found_by_feature(self) -> None:
        flat = FeatureControlFrame(
            feature="top", characteristic=Characteristic.FLATNESS, tolerance_mm=0.05
        )
        position = FeatureControlFrame(
            feature="bore",
            characteristic=Characteristic.POSITION,
            tolerance_mm=0.2,
            datums=(DatumReference("A"),),
        )
        tolerancing = Tolerancing(
            scheme=datum_scheme([Datum("A", "base")]), frames=(flat, position)
        )

        assert tolerancing.frames_for("bore") == (position,)
        assert tolerancing.frames_for("nothing") == ()

    def test_an_empty_tolerancing_is_legal(self) -> None:
        """Nothing to check is not an error; it is a part with no GD&T on it."""
        assert Tolerancing().frames == ()


class TestARenderedFrameReadsLikeADrawing:
    def test_a_frame_prints_its_symbol_zone_modifier_and_datums(self) -> None:
        frame = FeatureControlFrame(
            feature="bore",
            characteristic=Characteristic.POSITION,
            tolerance_mm=0.2,
            condition=MaterialCondition.MMC,
            diametral=True,
            datums=(DatumReference("A"), DatumReference("B", MaterialCondition.MMC)),
        )

        rendered = str(frame)

        assert rendered.startswith("bore: [")
        assert "Ø0.2" in rendered
        assert "Ⓜ" in rendered
        assert "| A |" in rendered
        assert "BⓂ" in rendered

    def test_an_rfs_frame_carries_no_modifier_symbol(self) -> None:
        """RFS is what an unmarked frame means, so printing a symbol for it would
        be inventing a callout nobody wrote."""
        rendered = str(_minimal(Characteristic.FLATNESS))

        assert "Ⓜ" not in rendered
        assert "Ⓛ" not in rendered

    def test_to_dict_carries_the_frame_and_the_scheme_and_the_loose_ends(self) -> None:
        tolerancing = Tolerancing(
            scheme=datum_scheme([Datum("A", "base"), Datum("B", "bore", note="jig bored")]),
            frames=(
                FeatureControlFrame(
                    feature="rim",
                    characteristic=Characteristic.TOTAL_RUNOUT,
                    tolerance_mm=0.05,
                    datums=(DatumReference("A"),),
                    note="checked on centres",
                ),
            ),
        )

        as_dict = tolerancing.to_dict()

        assert [d["letter"] for d in as_dict["datums"]] == ["A", "B"]
        assert as_dict["datums"][1]["note"] == "jig bored"
        assert as_dict["frames"][0]["characteristic"] == "total_runout"
        assert as_dict["frames"][0]["category"] == "runout"
        assert as_dict["frames"][0]["condition"] == "rfs"
        assert as_dict["frames"][0]["note"] == "checked on centres"
        assert as_dict["unreferenced_datums"] == ["B"]

    @pytest.mark.parametrize("characteristic", list(Characteristic))
    def test_every_characteristic_round_trips_through_to_dict(
        self, characteristic: Characteristic
    ) -> None:
        as_dict = _minimal(characteristic).to_dict()

        assert as_dict["characteristic"] == str(characteristic)
        assert as_dict["tolerance_mm"] == 0.1
        assert (len(as_dict["datums"]) > 0) is (characteristic in _NEEDS_A_DATUM)
