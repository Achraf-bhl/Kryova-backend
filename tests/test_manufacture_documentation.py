"""Technical documentation drafted from the product structure, and never presented as finished (E17.6).

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import datetime

import pytest

from app.assembly.placement import at
from app.assembly.structure import StructureBuilder
from app.compliance.boundary import Stance, claims_in
from app.compliance.instructions import ACCOMPANIED, DigitalDelivery, Marking, PaperOffer
from app.manufacture.documentation import (
    DocumentationError,
    DocumentKind,
    assembly_sequence,
    delivery_gaps,
    draft_assembly_instructions,
    draft_instructions_for_use,
    draft_parts_catalogue,
    draft_service_manual,
    exploded_shape,
    exploded_view,
    parts_catalogue,
    render_exploded,
)


def _press():  # type: ignore[no-untyped-def]
    builder = StructureBuilder()
    builder.define("press")
    builder.define("frame")
    builder.define("leg", design="LEG-01", material="steel", revision="B", description="upright")
    builder.define("beam", design="BEAM-01")
    builder.define("bolt", design="ISO 4014 M8x60 8.8")
    builder.add("press", "frame", placement=at(0.0, 0.0, 1000.0))
    builder.add("frame", "leg", placement=at(-500.0, 0.0, 0.0))
    builder.add("frame", "leg", placement=at(500.0, 0.0, 0.0))
    builder.add("frame", "beam", note="weld after squaring the legs")
    for x in (100.0, 200.0, 300.0, 400.0):
        builder.add("press", "bolt", placement=at(x, 0.0, 0.0))
    return builder.build("press")


class TestThePartsCatalogue:
    def test_items_are_numbered_over_the_bill_of_materials(self) -> None:
        items = parts_catalogue(_press())
        assert [(i.item, i.component, i.quantity) for i in items] == [
            (1, "beam", 1),
            (2, "bolt", 4),
            (3, "leg", 2),
        ]
        leg = items[2]
        assert (leg.design, leg.material, leg.revision, leg.description) == (
            "LEG-01",
            "steel",
            "B",
            "upright",
        )


class TestTheAssemblySequence:
    def test_a_sub_assembly_is_built_before_the_assembly_that_uses_it(self) -> None:
        steps = assembly_sequence(_press())
        assert [s.assembly for s in steps] == ["frame", "press"]
        frame, press = steps
        assert [(f.component, f.count) for f in frame.fits] == [("leg", 2), ("beam", 1)]
        assert [(f.component, f.count) for f in press.fits] == [("frame", 1), ("bolt", 4)]

    def test_the_author_notes_travel_with_the_step(self) -> None:
        frame = assembly_sequence(_press())[0]
        assert frame.notes == ("beam.1: weld after squaring the legs",)

    def test_each_step_reads_as_a_sentence(self) -> None:
        assert assembly_sequence(_press())[0].sentence() == (
            "Step 1: assemble frame from 2 x leg, 1 x beam."
        )


class TestTheExplodedView:
    def test_factor_zero_moves_nothing(self) -> None:
        for placement in exploded_view(_press(), factor=0.0):
            assert placement.exploded_origin_mm == pytest.approx(placement.origin_mm)

    def test_a_sub_assembly_moves_as_a_whole_and_spreads_within(self) -> None:
        by_path = {p.path: p for p in exploded_view(_press(), factor=1.0)}
        # frame 1000 up doubles to 2000; the leg 500 out from it doubles to 1000.
        assert by_path["press/frame.1/leg.1"].origin_mm == pytest.approx((-500.0, 0.0, 1000.0))
        assert by_path["press/frame.1/leg.1"].exploded_origin_mm == pytest.approx((-1000.0, 0.0, 2000.0))
        assert by_path["press/frame.1/beam.1"].exploded_origin_mm == pytest.approx((0.0, 0.0, 2000.0))
        assert by_path["press/bolt.1"].exploded_origin_mm == pytest.approx((200.0, 0.0, 0.0))

    def test_only_parts_are_placed(self) -> None:
        paths = {p.path for p in exploded_view(_press(), factor=1.0)}
        assert "press/frame.1" not in paths
        assert len(paths) == 7

    def test_a_negative_factor_is_refused(self) -> None:
        with pytest.raises(DocumentationError, match="0 or more"):
            exploded_view(_press(), factor=-0.5)


class TestEveryDocumentIsADraftForANamedPerson:
    @pytest.mark.parametrize(
        "make",
        [draft_parts_catalogue, draft_assembly_instructions, draft_service_manual, draft_instructions_for_use],
    )
    def test_the_stance_is_draft_and_nothing_marks_it_finished(self, make) -> None:  # type: ignore[no-untyped-def]
        draft = make(_press(), product_model="P-100", prepared_for="A. Engineer")
        payload = draft.to_dict()
        assert draft.stance is Stance.DRAFT_FOR_A_NAMED_PERSON
        assert payload["status"] == "draft"
        assert "finished" not in payload
        assert draft.not_written

    def test_a_draft_for_nobody_is_refused(self) -> None:
        with pytest.raises(DocumentationError, match="named person"):
            draft_instructions_for_use(_press(), product_model="P-100", prepared_for=" ")

    def test_a_draft_with_no_product_model_is_refused(self) -> None:
        with pytest.raises(DocumentationError, match="product model"):
            draft_parts_catalogue(_press(), product_model="", prepared_for="A. Engineer")

    def test_the_service_manual_takes_it_apart_in_reverse(self) -> None:
        draft = draft_service_manual(_press(), product_model="P-100", prepared_for="A. Engineer")
        assert [d["assembly"] for d in draft.sections["disassembly"]] == ["press", "frame"]
        assert draft.sections["disassembly"][0]["remove"] == ["bolt", "frame"]

    def test_the_instructions_do_not_paraphrase_annex_iii(self) -> None:
        draft = draft_instructions_for_use(_press(), product_model="P-100", prepared_for="A. Engineer")
        assert any("Annex III" in item and "not quoted" in item for item in draft.not_written)

    def test_no_draft_makes_a_forbidden_claim(self) -> None:
        for make in (draft_parts_catalogue, draft_assembly_instructions, draft_service_manual, draft_instructions_for_use):
            draft = make(_press(), product_model="P-100", prepared_for="A. Engineer")
            assert claims_in(str(draft.to_dict())) == []


def _delivery(product_model: str) -> DigitalDelivery:
    return DigitalDelivery(
        product_model=product_model,
        model_named_in_instructions="whatever the caller typed",
        marking=Marking.ON_THE_MACHINE,
        marking_on_machine_impossible_because="",
        printable=True,
        downloadable=True,
        savable_on_a_device=True,
        embedded_in_machine_software=False,
        embedded_copy_printable_downloadable_savable=False,
        placed_on_market=datetime.date(2027, 2, 1),
        expected_end_of_life=datetime.date(2040, 1, 1),
        online_until=datetime.date(2040, 1, 1),
        paper=PaperOffer(free_of_charge=True, delivered_within_days=20),
    )


class TestDigitalDelivery:
    def test_a_plan_for_the_drafted_model_has_no_gap_the_checker_sees(self) -> None:
        draft = draft_instructions_for_use(_press(), product_model="P-100", prepared_for="A. Engineer")
        assert delivery_gaps(draft, _delivery("P-100")) == ()

    def test_a_plan_for_another_model_is_caught_with_the_drafts_own_model(self) -> None:
        draft = draft_instructions_for_use(_press(), product_model="P-100", prepared_for="A. Engineer")
        [gap] = delivery_gaps(draft, _delivery("P-200"))
        assert gap.clause is ACCOMPANIED
        assert "P-100" in gap.because

    def test_only_instructions_for_use_are_checked_against_article_10_7(self) -> None:
        draft = draft_parts_catalogue(_press(), product_model="P-100", prepared_for="A. Engineer")
        assert draft.kind is DocumentKind.PARTS_CATALOGUE
        with pytest.raises(DocumentationError, match="Article 10"):
            delivery_gaps(draft, _delivery("P-100"))


def _kernel_available() -> bool:
    try:
        from app.kernel.occt.binding import require

        require()
    except Exception:
        return False
    return True


needs_kernel = pytest.mark.skipif(not _kernel_available(), reason="OCCT not installed")


@needs_kernel
class TestTheExplodedViewIsDrawn:
    def _shapes(self) -> dict[str, object]:
        from app.kernel.occt.binding import symbol

        box = symbol("BRepPrimAPI_MakeBox")
        return {
            "leg": box(20.0, 20.0, 400.0).Shape(),
            "beam": box(1000.0, 20.0, 20.0).Shape(),
            "bolt": box(8.0, 8.0, 60.0).Shape(),
        }

    def test_every_part_is_in_the_compound_at_its_exploded_place(self) -> None:
        from app.kernel.occt.metrology import bounding_box_mm, volume_mm3

        shapes = self._shapes()
        tight = exploded_shape(_press(), shapes, factor=0.0)
        spread = exploded_shape(_press(), shapes, factor=1.0)
        parts = 2 * 20 * 20 * 400 + 1000 * 20 * 20 + 4 * 8 * 8 * 60
        assert volume_mm3(spread) == pytest.approx(parts, rel=1e-9)
        # the frame rises from 1000 to 2000, so the top of a leg rises from 1400 to 2400.
        assert bounding_box_mm(tight)["max"][2] == pytest.approx(1400.0, abs=1e-6)
        assert bounding_box_mm(spread)["max"][2] == pytest.approx(2400.0, abs=1e-6)

    def test_the_picture_is_deterministic_and_changes_with_the_factor(self) -> None:
        shapes = self._shapes()
        first = render_exploded(_press(), shapes, factor=1.0)
        again = render_exploded(_press(), shapes, factor=1.0)
        tight = render_exploded(_press(), shapes, factor=0.0)
        assert first.digest == again.digest
        assert first.digest != tight.digest

    def test_a_part_with_no_shape_is_refused_by_name(self) -> None:
        shapes = self._shapes()
        del shapes["bolt"]
        with pytest.raises(DocumentationError, match="bolt"):
            exploded_shape(_press(), shapes, factor=1.0)
