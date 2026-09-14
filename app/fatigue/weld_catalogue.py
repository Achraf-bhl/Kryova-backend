"""Which detail categories a welded joint may have, read from EN 1993-1-9's tables.

Master plan E8.3: "where a welded frame lives or dies; judgement, not arithmetic". This module
does the part that is not judgement. It holds the rows of Tables 8.3, 8.4 and 8.5 (nominal stress)
and Table B.1 (hot-spot stress), each with its category, its detail numbers, its page, and its
description and requirements in the standard's own words. `classify` then says which rows a joint
can still be, given the facts known about it.

**It never picks a row.** One transverse butt weld in a plate is 112, 90, 80, 71, 50 or 36
depending on whether it was ground flush, welded from both sides, checked by NDT, made on a backing
strip, and how convex the cap is. None of that is in a CAD model. So the answer is the set of rows
the facts do not exclude, with the lowest named as the conservative choice. Choosing a higher one is
the engineer's, and `Candidate.detail` records who chose it.

**Rows are read literally.** A row is excluded only by a fact that contradicts a condition *that
row states*, in its description or its requirements. No condition is inferred from a neighbouring
row. Table 8.3 detail 13's 36 row, for instance, names no inspection, so a one-sided weld checked by
NDT keeps both its 71 and its 36 row. The rule makes every exclusion checkable against the page,
and it can only ever add a candidate, which can only lower the conservative category.

**Two more readings, stated because they are readings:**

1. *Which joints a row belongs to.* `Joint` names what the geometry is. Where a description does
   not say, a row is placed in every joint it could describe. Table 8.3 details 13, 14 and 16 say
   how a butt weld is made rather than where it is, so they are candidates in every plate-splice
   joint.
2. *A value the table leaves uncovered.* Table 8.5 detail 1 covers ℓ < 50 mm and 50 < ℓ ≤ 80 mm,
   and nothing covers ℓ = 50 mm. When no row of a detail admits a value only because it sits on a
   bound both neighbours exclude, both neighbours come back as candidates, flagged `on_boundary`.

Only the conditions that separate one row from another are encoded as facts. Each candidate still
carries every requirement of its row in the standard's words, and those are for the engineer to
confirm. Tables 8.1, 8.2 and 8.6 to 8.10 have not been encoded.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from app.fatigue.errors import FatigueError
from app.fatigue.eurocode3 import (
    STANDARD,
    StressKind,
    eccentric_step_size_factor,
    require_category,
    thickness_size_factor,
)
from app.fatigue.history import StressBasis
from app.fatigue.material import ShearDetail, WeldDetail
from app.fatigue.sources import require_source


class ClassificationError(FatigueError):
    """A classification was asked in terms the catalogue cannot read."""


class Table(StrEnum):
    T8_3 = "Table 8.3"
    T8_4 = "Table 8.4"
    T8_5 = "Table 8.5"
    B_1 = "Table B.1"


#: The standard's page for each table, from `docs/eurocode3-fatigue-reading.md`.
PAGES: Final = {Table.T8_3: (22, 23), Table.T8_4: (24,), Table.T8_5: (25,), Table.B_1: (33,)}


class Joint(StrEnum):
    """What a joint is, as its geometry says. Workmanship is a fact, not a joint."""

    # Table 8.3, transverse butt welds
    PLATE_SPLICE = "transverse butt weld splicing plates or flats"
    TAPERED_PLATE_SPLICE = "transverse butt weld splicing plates tapered in width or thickness"
    PLATE_GIRDER_SPLICE = "transverse butt weld splicing a plate girder's flange or web"
    ROLLED_SECTION_BUTT = "full cross-section butt weld of rolled sections"
    THICKNESS_STEP_BUTT = "transverse butt weld between thicknesses without a transition"
    INTERSECTING_FLANGES_BUTT = "transverse butt weld at intersecting flanges"
    # Table 8.4, weld attachments and stiffeners
    LONGITUDINAL_ATTACHMENT = "longitudinal attachment"
    GUSSET_WITH_RADIUS = "longitudinal fillet welded gusset with a radius transition"
    GUSSET_ON_EDGE = "gusset plate welded to the edge of a plate or beam flange"
    TRANSVERSE_ATTACHMENT = "transverse attachment welded to a plate"
    VERTICAL_STIFFENER = "vertical stiffener welded to a beam or plate girder"
    BOX_GIRDER_DIAPHRAGM = "diaphragm of a box girder welded to the flange or the web"
    SHEAR_STUD_ON_BASE = "welded shear stud, its effect on the base material"
    # Table 8.5, load-carrying welded joints
    CRUCIFORM_OR_TEE_TOE = "cruciform or tee joint, toe failure"
    ATTACHMENT_EDGE_TOE = "toe failure from the edge of an attachment to a plate"
    CRUCIFORM_OR_TEE_ROOT = "partial penetration or fillet welded tee or cruciform joint, root failure"
    LAP_JOINT_MAIN_PLATE = "fillet welded lap joint, main plate"
    LAP_JOINT_OVERLAPPING_PLATE = "fillet welded lap joint, overlapping plates"
    COVER_PLATE_END = "end zone of a cover plate in a beam or plate girder"
    COVER_PLATE_REINFORCED_END = "cover plate with a reinforced transverse end weld"
    SHEAR_FLOW_FILLET = "continuous fillet welds transmitting a shear flow"
    LAP_JOINT_WELD_SHEAR = "fillet welded lap joint, shear in the weld"
    STUD_SHEAR_CONNECTOR = "welded stud shear connector, composite application"
    TUBE_SOCKET_BUTT = "tube socket joint with 80% full penetration butt welds"
    TUBE_SOCKET_FILLET = "tube socket joint with fillet welds"
    # Table B.1, geometric (hot-spot) stress
    HOT_SPOT_BUTT = "full penetration butt joint (hot spot)"
    HOT_SPOT_CRUCIFORM_K_BUTT = "cruciform joint with full penetration K-butt welds (hot spot)"
    HOT_SPOT_NON_LOAD_CARRYING_FILLET = "non load-carrying fillet welds (hot spot)"
    HOT_SPOT_BRACKET_OR_STIFFENER_END = "bracket ends, ends of longitudinal stiffeners (hot spot)"
    HOT_SPOT_COVER_PLATE_END = "cover plate ends and similar joints (hot spot)"
    HOT_SPOT_CRUCIFORM_LOAD_CARRYING_FILLET = "cruciform joints with load-carrying fillet welds (hot spot)"


#: Joints the tables name and send elsewhere. Classifying one returns no candidate and this reason.
REFERRED_ELSEWHERE: Final = {
    Joint.STUD_SHEAR_CONNECTOR: (
        "Table 8.5 detail 10 (p. 25) says 'see EN 1994-2 (90 m=8)'. EN 1994-2 has not been read, "
        "and a category on a slope of 8 is not one Figure 7.2 draws."
    ),
}


@dataclass(frozen=True)
class FactSpec:
    """One fact a classification can be given: a yes/no or a number, and what it means."""

    name: str
    number: bool
    meaning: str


FACTS: Final = {
    spec.name: spec
    for spec in (
        FactSpec("t_mm", True, "t, the thickness drawn in the detail's figure, in mm"),
        FactSpec("t_c_mm", True, "t_c, the cover plate thickness (Table 8.5 details 6 and 7), in mm"),
        FactSpec("L_mm", True, "L, the length of a longitudinal attachment (Table 8.4 details 1 and 2), in mm"),
        FactSpec("l_mm", True, "ℓ, as drawn in the detail's figure (Table 8.4 details 4, 6–8; Table 8.5 details 1, 2, 4), in mm"),
        FactSpec("r_mm", True, "r, the transition radius (Table 8.4 details 3 and 4), in mm"),
        FactSpec("alpha_deg", True, "α, the end angle of a longitudinal attachment (Table 8.4 detail 2), in degrees"),
        FactSpec("convexity_ratio", True, "the height of the weld convexity divided by the weld width (Table 8.3)"),
        FactSpec(
            "misalignment_ratio", True,
            "the misalignment of the load-carrying plates divided by the thickness of the intermediate plate (Table 8.5 details 1–3)",
        ),
        FactSpec("toe_angle_deg", True, "the weld toe angle (Table B.1), in degrees"),
        FactSpec("ground_flush", False, "'All welds ground flush to plate surface parallel to direction of the arrow'; no is 'Weld not ground flush'"),
        FactSpec(
            "run_on_off_pieces_removed", False,
            "'Weld run-on and run-off pieces to be used and subsequently removed, plate edges to be ground flush in direction of stress'",
        ),
        FactSpec("welded_both_sides", False, "'Welded from both sides'; no is 'Butt welds made from one side only'"),
        FactSpec("checked_by_ndt", False, "'checked by NDT'; for Table 8.3 detail 13, 'full penetration checked by appropriate NDT'"),
        FactSpec("flat_position", False, "'Welds made in flat position' (Table 8.3 details 5 and 7)"),
        FactSpec("cope_hole", False, "'with cope holes'; no is 'without cope holes' (Table 8.3)"),
        FactSpec(
            "same_dimensions_without_tolerance_differences", False,
            "'Rolled sections with the same dimensions without tolerance differences' (Table 8.3 detail 8)",
        ),
        FactSpec("backing_strip", False, "a backing strip or bar is present"),
        FactSpec(
            "backing_strip_fillets_end_10mm_or_more_from_edges", False,
            "'Fillet welds attaching the backing strip to terminate ≥ 10 mm from the edges of the stressed plate'",
        ),
        FactSpec("backing_strip_good_fit_guaranteed", False, "a good fit of the backing strip can be guaranteed (Table 8.3 detail 16)"),
        FactSpec("tack_welds_inside_butt_weld", False, "'Tack welds inside the shape of butt welds' (Table 8.3 details 14 and 15)"),
        FactSpec(
            "attachment_thinner_than_its_height", False,
            "'The thickness of the attachment must be less than its height' (Table 8.4 detail 1)",
        ),
        FactSpec("radius_transition", False, "the joint has a transition radius; no is 'As welded, no radius transition'"),
        FactSpec(
            "radius_machined_and_weld_ground", False,
            "'Smooth transition radius r formed by initially machining or gas cutting the gusset plate before welding, "
            "then subsequently grinding the weld area parallel to the direction of the arrow so that the transverse "
            "weld toe is fully removed' (Table 8.4 details 3 and 4)",
        ),
        FactSpec(
            "weld_end_reinforced_longer_than_r", False,
            "'end of fillet weld reinforced (full penetration); length of reinforced weld > r' (Table 8.4 detail 3)",
        ),
        FactSpec(
            "weld_ends_ground_to_remove_undercut", False,
            "'Ends of welds to be carefully ground to remove any undercut that may be present' (Table 8.4 details 6 and 7)",
        ),
        FactSpec(
            "inspected_free_of_discontinuities", False,
            "'Inspected and found free from discontinuities and misalignments outside the tolerances of EN 1090' (Table 8.5 detail 1)",
        ),
        FactSpec(
            "weld_terminations_more_than_10mm_from_plate_edge", False,
            "'Weld terminations more than 10 mm from plate edge' (Table 8.5 details 4, 5 and 9)",
        ),
        FactSpec("end_weld_ground_flush", False, "'Transverse end weld ground flush' (Table 8.5 detail 7)"),
        FactSpec("weld_toe_ground", False, "'Weld toe ground' (Table 8.5 detail 11)"),
    )
}


class _Status(StrEnum):
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    OPEN = "open"


@dataclass(frozen=True)
class Fact:
    """A yes/no condition a row states."""

    name: str
    value: bool

    def describe(self) -> str:
        return f"{self.name} = {'yes' if self.value else 'no'}"

    def evaluate(self, facts: Mapping[str, bool | float]) -> tuple[_Status, bool]:
        if self.name not in facts:
            return _Status.OPEN, False
        return (_Status.CONFIRMED if facts[self.name] is self.value else _Status.CONTRADICTED), False


@dataclass(frozen=True)
class Band:
    """A numeric condition a row states, with each bound open or closed as the table writes it.

    `name` is a fact, or two facts divided (`"r_mm/l_mm"`) where the table bounds a ratio.
    """

    name: str
    above: float | None = None
    above_inclusive: bool = False
    up_to: float | None = None
    up_to_inclusive: bool = False

    def describe(self) -> str:
        text = self.name
        if self.above is not None:
            text = f"{self.above:g} {'≤' if self.above_inclusive else '<'} {text}"
        if self.up_to is not None:
            text = f"{text} {'≤' if self.up_to_inclusive else '<'} {self.up_to:g}"
        return text

    def _value(self, facts: Mapping[str, bool | float]) -> float | None:
        names = self.name.split("/")
        if any(name not in facts for name in names):
            return None
        values = [float(facts[name]) for name in names]
        if len(values) == 1:
            return values[0]
        if values[1] == 0.0:
            raise ClassificationError(f"{names[1]} is zero, so the ratio {self.name} a row is bounded by is undefined.")
        return values[0] / values[1]

    def evaluate(self, facts: Mapping[str, bool | float]) -> tuple[_Status, bool]:
        """The status, and whether a contradiction is only a value sitting on an exclusive bound."""
        value = self._value(facts)
        if value is None:
            return _Status.OPEN, False
        if self.above is not None:
            if value < self.above:
                return _Status.CONTRADICTED, False
            if value == self.above and not self.above_inclusive:
                return _Status.CONTRADICTED, True
        if self.up_to is not None:
            if value > self.up_to:
                return _Status.CONTRADICTED, False
            if value == self.up_to and not self.up_to_inclusive:
                return _Status.CONTRADICTED, True
        return _Status.CONFIRMED, False


Condition = Fact | Band


class SizeEffect(StrEnum):
    NONE = "no size effect"
    THICKNESS = "k_s = (25/t)^0.2 for t > 25 mm"
    ECCENTRIC_STEP = "k_s of Table 8.3 detail 17"


@dataclass(frozen=True)
class Row:
    """One row of a table: a category for some details, in the standard's words.

    `description` and `requirements` are quoted from the page, abridged only by dropping the
    figure's labels. `conditions` are the parts of them that separate this row from another, in
    the vocabulary of `FACTS`.
    """

    table: Table
    details: tuple[int, ...]
    category_mpa: float
    joints: tuple[Joint, ...]
    page: int
    description: str
    requirements: str = ""
    conditions: tuple[Condition, ...] = ()
    stress: StressKind = StressKind.DIRECT
    size_effect: SizeEffect = SizeEffect.NONE
    asterisk: bool = False
    #: A further assessment the table says this detail owes, in its words.
    also: str = ""

    def __post_init__(self) -> None:
        require_category(self.category_mpa, self.stress)
        if self.page not in PAGES[self.table]:
            raise ValueError(f"{self.table} is on page {PAGES[self.table]}, not page {self.page}.")
        if not self.details or not self.joints or not self.description.strip():
            raise ValueError("A catalogue row names its details, its joints and its description.")
        for condition in self.conditions:
            for name in condition.name.split("/"):
                spec = FACTS.get(name)
                if spec is None:
                    raise ValueError(f"A row's condition names {name!r}, which is not a fact.")
                if spec.number is not isinstance(condition, Band):
                    raise ValueError(f"{name!r} is a {'number' if spec.number else 'yes/no'} fact.")

    @property
    def basis(self) -> StressBasis:
        """§7.1(4): Tables 8.1–8.10 are for nominal stresses. §7.1(5): Annex B is for geometric ones."""
        return StressBasis.HOT_SPOT if self.table is Table.B_1 else StressBasis.NOMINAL

    @property
    def slope(self) -> float:
        """m of the curve this row's category sits on: 3 for direct stress, 5 for shear (§7.1(2))."""
        return 3.0 if self.stress is StressKind.DIRECT else 5.0

    def label(self) -> str:
        details = ", ".join(str(d) for d in self.details)
        star = "*" if self.asterisk else ""
        symbol = "Δσc" if self.stress is StressKind.DIRECT else "Δτc"
        return f"{self.table} detail {details}: {symbol} = {self.category_mpa:g}{star} MPa (p. {self.page})"


# ---------------------------------------------------------------------------------------------
# Table 8.3, transverse butt welds (pp. 22–23)
# ---------------------------------------------------------------------------------------------

_FLUSH_REQUIREMENTS = (
    "All welds ground flush to plate surface parallel to direction of the arrow. Weld run-on and "
    "run-off pieces to be used and subsequently removed, plate edges to be ground flush in "
    "direction of stress. Welded from both sides; checked by NDT."
)
_FLUSH = (
    Fact("ground_flush", True),
    Fact("run_on_off_pieces_removed", True),
    Fact("welded_both_sides", True),
    Fact("checked_by_ndt", True),
)
_CONVEX_10 = (
    "The height of the weld convexity to be not greater than 10% of the weld width, with smooth "
    "transition to the plate surface. Weld run-on and run-off pieces to be used and subsequently "
    "removed, plate edges to be ground flush in direction of stress. Welded from both sides; "
    "checked by NDT."
)
_CONVEX_20 = (
    "The height of the weld convexity to be not greater than 20% of the weld width, with smooth "
    "transition to the plate surface. Weld not ground flush. Weld run-on and run-off pieces to be "
    "used and subsequently removed, plate edges to be ground flush in direction of stress. Welded "
    "from both sides; checked by NDT."
)
_RUN_BOTH_NDT = (
    Fact("run_on_off_pieces_removed", True),
    Fact("welded_both_sides", True),
    Fact("checked_by_ndt", True),
)
_SPLICES = (Joint.PLATE_SPLICE, Joint.TAPERED_PLATE_SPLICE, Joint.PLATE_GIRDER_SPLICE)
_T83 = SizeEffect.THICKNESS

_TABLE_8_3: Final = (
    Row(Table.T8_3, (1,), 112.0, (Joint.PLATE_SPLICE,), 22,
        "Without backing bar: Transverse splices in plates and flats.",
        _FLUSH_REQUIREMENTS, (*_FLUSH, Fact("backing_strip", False)), size_effect=_T83),
    Row(Table.T8_3, (2,), 112.0, (Joint.PLATE_GIRDER_SPLICE,), 22,
        "Without backing bar: Flange and web splices in plate girders before assembly.",
        _FLUSH_REQUIREMENTS, (*_FLUSH, Fact("backing_strip", False)), size_effect=_T83),
    Row(Table.T8_3, (3,), 112.0, (Joint.ROLLED_SECTION_BUTT,), 22,
        "Without backing bar: Full cross-section butt welds of rolled sections without cope holes.",
        _FLUSH_REQUIREMENTS + " Detail 3): Applies only to joints of rolled sections, cut and welded.",
        (*_FLUSH, Fact("backing_strip", False), Fact("cope_hole", False)), size_effect=_T83),
    Row(Table.T8_3, (4,), 112.0, (Joint.TAPERED_PLATE_SPLICE,), 22,
        "Without backing bar: Transverse splices in plates or flats tapered in width or in "
        "thickness, with a slope ≤ 1/4.",
        _FLUSH_REQUIREMENTS, (*_FLUSH, Fact("backing_strip", False)), size_effect=_T83),
    Row(Table.T8_3, (5,), 90.0, (Joint.PLATE_SPLICE,), 22,
        "Transverse splices in plates or flats. Translation of welds to be machined notch free.",
        _CONVEX_10 + " Details 5 and 7: Welds made in flat position.",
        (Band("convexity_ratio", up_to=0.10, up_to_inclusive=True), *_RUN_BOTH_NDT, Fact("flat_position", True)),
        size_effect=_T83),
    Row(Table.T8_3, (6,), 90.0, (Joint.ROLLED_SECTION_BUTT,), 22,
        "Full cross-section butt welds of rolled sections without cope holes. Translation of welds "
        "to be machined notch free.",
        _CONVEX_10,
        (Band("convexity_ratio", up_to=0.10, up_to_inclusive=True), *_RUN_BOTH_NDT, Fact("cope_hole", False)),
        size_effect=_T83),
    Row(Table.T8_3, (7,), 90.0, (Joint.TAPERED_PLATE_SPLICE,), 22,
        "Transverse splices in plates or flats tapered in width or in thickness with a slope ≤ 1/4. "
        "Translation of welds to be machined notch free.",
        _CONVEX_10 + " Details 5 and 7: Welds made in flat position.",
        (Band("convexity_ratio", up_to=0.10, up_to_inclusive=True), *_RUN_BOTH_NDT, Fact("flat_position", True)),
        size_effect=_T83),
    Row(Table.T8_3, (8,), 90.0, (Joint.ROLLED_SECTION_BUTT,), 22,
        "As detail 3) but with cope holes.",
        _FLUSH_REQUIREMENTS + " Rolled sections with the same dimensions without tolerance differences.",
        (*_FLUSH, Fact("cope_hole", True), Fact("same_dimensions_without_tolerance_differences", True)),
        size_effect=_T83),
    Row(Table.T8_3, (9,), 80.0, (Joint.PLATE_GIRDER_SPLICE,), 22,
        "Transverse splices in welded plate girders without cope hole.",
        _CONVEX_20,
        (Band("convexity_ratio", up_to=0.20, up_to_inclusive=True), Fact("ground_flush", False),
         *_RUN_BOTH_NDT, Fact("cope_hole", False)),
        size_effect=_T83),
    Row(Table.T8_3, (10,), 80.0, (Joint.ROLLED_SECTION_BUTT,), 22,
        "Full cross-section butt welds of rolled sections with cope holes.",
        _CONVEX_20 + " Detail 10: The height of the weld convexity to be not greater than 10% of "
        "the weld width, with smooth transition to the plate surface.",
        (Band("convexity_ratio", up_to=0.10, up_to_inclusive=True), Fact("ground_flush", False),
         *_RUN_BOTH_NDT, Fact("cope_hole", True)),
        size_effect=_T83),
    Row(Table.T8_3, (11,), 80.0,
        (Joint.PLATE_SPLICE, Joint.ROLLED_SECTION_BUTT, Joint.PLATE_GIRDER_SPLICE), 22,
        "Transverse splices in plates, flats, rolled sections or plate girders.",
        _CONVEX_20,
        (Band("convexity_ratio", up_to=0.20, up_to_inclusive=True), Fact("ground_flush", False), *_RUN_BOTH_NDT),
        size_effect=_T83),
    Row(Table.T8_3, (12,), 63.0, (Joint.ROLLED_SECTION_BUTT,), 22,
        "Full cross-section butt welds of rolled sections without cope hole.",
        "Weld run-on and run-off pieces to be used and subsequently removed, plate edges to be "
        "ground flush in direction of stress. Welded from both sides.",
        (Fact("run_on_off_pieces_removed", True), Fact("welded_both_sides", True), Fact("cope_hole", False))),
    Row(Table.T8_3, (13,), 36.0, _SPLICES, 23,
        "Butt welds made from one side only.",
        "Without backing strip.",
        (Fact("welded_both_sides", False), Fact("backing_strip", False))),
    Row(Table.T8_3, (13,), 71.0, _SPLICES, 23,
        "Butt welds made from one side only when full penetration checked by appropriate NDT.",
        "",
        (Fact("welded_both_sides", False), Fact("checked_by_ndt", True)),
        size_effect=_T83),
    Row(Table.T8_3, (14,), 71.0, (Joint.PLATE_SPLICE, Joint.PLATE_GIRDER_SPLICE), 23,
        "With backing strip: Transverse splice. Also valid for curved plates.",
        "Fillet welds attaching the backing strip to terminate ≥ 10 mm from the edges of the "
        "stressed plate. Tack welds inside the shape of butt welds.",
        (Fact("backing_strip", True), Fact("backing_strip_fillets_end_10mm_or_more_from_edges", True),
         Fact("tack_welds_inside_butt_weld", True)),
        size_effect=_T83),
    Row(Table.T8_3, (15,), 71.0, (Joint.TAPERED_PLATE_SPLICE,), 23,
        "With backing strip: Transverse butt weld tapered in width or thickness with a slope ≤ 1/4. "
        "Also valid for curved plates.",
        "Fillet welds attaching the backing strip to terminate ≥ 10 mm from the edges of the "
        "stressed plate. Tack welds inside the shape of butt welds.",
        (Fact("backing_strip", True), Fact("backing_strip_fillets_end_10mm_or_more_from_edges", True),
         Fact("tack_welds_inside_butt_weld", True)),
        size_effect=_T83),
    # Detail 16 is one row with two alternatives ("or"), so it is two rows here.
    Row(Table.T8_3, (16,), 50.0, _SPLICES, 23,
        "Transverse butt weld on a permanent backing strip tapered in width or thickness with a "
        "slope ≤ 1/4. Also valid for curved plates.",
        "Where backing strip fillet welds end < 10 mm from the plate edge, or if a good fit cannot "
        "be guaranteed.",
        (Fact("backing_strip", True), Fact("backing_strip_fillets_end_10mm_or_more_from_edges", False)),
        size_effect=_T83),
    Row(Table.T8_3, (16,), 50.0, _SPLICES, 23,
        "Transverse butt weld on a permanent backing strip tapered in width or thickness with a "
        "slope ≤ 1/4. Also valid for curved plates.",
        "Where backing strip fillet welds end < 10 mm from the plate edge, or if a good fit cannot "
        "be guaranteed.",
        (Fact("backing_strip", True), Fact("backing_strip_good_fit_guaranteed", False)),
        size_effect=_T83),
    Row(Table.T8_3, (17,), 71.0, (Joint.THICKNESS_STEP_BUTT,), 23,
        "Transverse butt weld, different thicknesses without transition, centrelines aligned.",
        "Size effect for t>25mm and/or generalization for eccentricity (t2 ≥ t1).",
        size_effect=SizeEffect.ECCENTRIC_STEP),
    Row(Table.T8_3, (18,), 40.0, (Joint.INTERSECTING_FLANGES_BUTT,), 23,
        "Transverse butt weld at intersecting flanges.",
        "Details 18) and 19): The fatigue strength of the continuous component has to be checked "
        "with Table 8.4, detail 4 or detail 5.",
        also="The continuous component has to be checked with Table 8.4, detail 4 or detail 5."),
)

# ---------------------------------------------------------------------------------------------
# Table 8.4, weld attachments and stiffeners (p. 24)
# ---------------------------------------------------------------------------------------------

_GUSSET_REQUIREMENTS = (
    "Details 3) and 4): Smooth transition radius r formed by initially machining or gas cutting "
    "the gusset plate before welding, then subsequently grinding the weld area parallel to the "
    "direction of the arrow so that the transverse weld toe is fully removed."
)
_DETAIL_4 = "Gusset plate, welded to the edge of a plate or beam flange."
_DETAIL_19 = " (Table 8.3 detail 19, 'With transition radius according to Table 8.4, detail 4', p. 23.)"
_ON_EDGE = (Joint.GUSSET_ON_EDGE, Joint.INTERSECTING_FLANGES_BUTT)
_MACHINED = (Fact("radius_transition", True), Fact("radius_machined_and_weld_ground", True))
_TRANSVERSE = (
    (Joint.TRANSVERSE_ATTACHMENT, 6, "Transverse attachments: Welded to plate.",
     "Details 6) and 7): Ends of welds to be carefully ground to remove any undercut that may be present.",
     (Fact("weld_ends_ground_to_remove_undercut", True),)),
    (Joint.VERTICAL_STIFFENER, 7, "Transverse attachments: Vertical stiffeners welded to a beam or plate girder.",
     "Details 6) and 7): Ends of welds to be carefully ground to remove any undercut that may be "
     "present. 7) Δσ to be calculated using principal stresses if the stiffener terminates in the web.",
     (Fact("weld_ends_ground_to_remove_undercut", True),)),
    (Joint.BOX_GIRDER_DIAPHRAGM, 8,
     "Transverse attachments: Diaphragm of box girders welded to the flange or the web. May not be "
     "possible for small hollow sections. The values are also valid for ring stiffeners.",
     "", ()),
)
_LONGITUDINAL = "Longitudinal attachments: The detail category varies according to the length of the attachment L."
_THINNER = "The thickness of the attachment must be less than its height. If not see Table 8.5, details 5 or 6."

_TABLE_8_4: Final = (
    Row(Table.T8_4, (1,), 80.0, (Joint.LONGITUDINAL_ATTACHMENT,), 24, _LONGITUDINAL, _THINNER,
        (Band("L_mm", up_to=50.0, up_to_inclusive=True), Fact("attachment_thinner_than_its_height", True))),
    Row(Table.T8_4, (1,), 71.0, (Joint.LONGITUDINAL_ATTACHMENT,), 24, _LONGITUDINAL, _THINNER,
        (Band("L_mm", 50.0, False, 80.0, True), Fact("attachment_thinner_than_its_height", True))),
    Row(Table.T8_4, (1,), 63.0, (Joint.LONGITUDINAL_ATTACHMENT,), 24, _LONGITUDINAL, _THINNER,
        (Band("L_mm", 80.0, False, 100.0, True), Fact("attachment_thinner_than_its_height", True))),
    Row(Table.T8_4, (1,), 56.0, (Joint.LONGITUDINAL_ATTACHMENT,), 24, _LONGITUDINAL, _THINNER,
        (Band("L_mm", above=100.0), Fact("attachment_thinner_than_its_height", True))),
    Row(Table.T8_4, (2,), 71.0, (Joint.LONGITUDINAL_ATTACHMENT,), 24,
        "Longitudinal attachments to plate or tube.", "",
        (Band("L_mm", above=100.0), Band("alpha_deg", up_to=45.0))),
    Row(Table.T8_4, (3,), 80.0, (Joint.GUSSET_WITH_RADIUS,), 24,
        "Longitudinal fillet welded gusset with radius transition to plate or tube; end of fillet "
        "weld reinforced (full penetration); length of reinforced weld > r.",
        _GUSSET_REQUIREMENTS,
        (Band("r_mm", above=150.0), *_MACHINED, Fact("weld_end_reinforced_longer_than_r", True))),
    # Detail 4's 90 row is "r/ℓ ≥ 1/3 or r > 150 mm": two alternatives, two rows.
    Row(Table.T8_4, (4,), 90.0, _ON_EDGE, 24, _DETAIL_4 + _DETAIL_19, _GUSSET_REQUIREMENTS,
        (Band("r_mm/l_mm", above=1.0 / 3.0, above_inclusive=True), *_MACHINED)),
    Row(Table.T8_4, (4,), 90.0, _ON_EDGE, 24, _DETAIL_4 + _DETAIL_19, _GUSSET_REQUIREMENTS,
        (Band("r_mm", above=150.0), *_MACHINED)),
    Row(Table.T8_4, (4,), 71.0, _ON_EDGE, 24, _DETAIL_4 + _DETAIL_19, _GUSSET_REQUIREMENTS,
        (Band("r_mm/l_mm", 1.0 / 6.0, True, 1.0 / 3.0, True), *_MACHINED)),
    Row(Table.T8_4, (4,), 50.0, _ON_EDGE, 24, _DETAIL_4 + _DETAIL_19, _GUSSET_REQUIREMENTS,
        (Band("r_mm/l_mm", up_to=1.0 / 6.0), *_MACHINED)),
    Row(Table.T8_4, (5,), 40.0, (Joint.GUSSET_ON_EDGE,), 24,
        "As welded, no radius transition.", "", (Fact("radius_transition", False),)),
    *(
        Row(Table.T8_4, (detail,), category, (joint,), 24, description, requirements, (band, *facts))
        for joint, detail, description, requirements, facts in _TRANSVERSE
        for category, band in (
            (80.0, Band("l_mm", up_to=50.0, up_to_inclusive=True)),
            (71.0, Band("l_mm", 50.0, False, 80.0, True)),
        )
    ),
    Row(Table.T8_4, (9,), 80.0, (Joint.SHEAR_STUD_ON_BASE,), 24,
        "The effect of welded shear studs on base material."),
)

# ---------------------------------------------------------------------------------------------
# Table 8.5, load carrying welded joints (p. 25)
# ---------------------------------------------------------------------------------------------

#: Detail 1's grid of ℓ and t, which details 2 and 4 are classified "As detail 1".
_DETAIL_1_GRID: Final = (
    (80.0, (Band("l_mm", up_to=50.0),)),
    (71.0, (Band("l_mm", 50.0, False, 80.0, True),)),
    (63.0, (Band("l_mm", 80.0, False, 100.0, True),)),
    (56.0, (Band("l_mm", 100.0, False, 120.0, True),)),
    (56.0, (Band("l_mm", above=120.0), Band("t_mm", up_to=20.0, up_to_inclusive=True))),
    (50.0, (Band("l_mm", 120.0, False, 200.0, True), Band("t_mm", above=20.0))),
    (50.0, (Band("l_mm", above=200.0), Band("t_mm", 20.0, False, 30.0, True))),
    (45.0, (Band("l_mm", 200.0, False, 300.0, True), Band("t_mm", above=30.0))),
    (45.0, (Band("l_mm", above=300.0), Band("t_mm", 30.0, False, 50.0, True))),
    (40.0, (Band("l_mm", above=300.0), Band("t_mm", above=50.0))),
)
_MISALIGNED = Band("misalignment_ratio", up_to=0.15, up_to_inclusive=True)
_MISALIGNMENT_TEXT = (
    "Details 1) to 3): The misalignment of the load-carrying plates should not exceed 15 % of the "
    "thickness of the intermediate plate."
)
_ROOT_TOO = (
    "3) In partial penetration joints two fatigue assessments are required. Firstly, root cracking "
    "evaluated according to stresses defined in section 5, using category 36* for Δσw and category "
    "80 for Δτw. Secondly, toe cracking is evaluated by determining Δσ in the load-carrying plate."
)
_TERMINATIONS = Fact("weld_terminations_more_than_10mm_from_plate_edge", True)
_GRID_DETAILS = (
    (1, Joint.CRUCIFORM_OR_TEE_TOE,
     "Cruciform and Tee joints: Toe failure in full penetration butt welds and all partial penetration joints.",
     "1) Inspected and found free from discontinuities and misalignments outside the tolerances of "
     "EN 1090. 2) For computing Δσ, use modified nominal stress. " + _MISALIGNMENT_TEXT,
     (Fact("inspected_free_of_discontinuities", True), _MISALIGNED), _ROOT_TOO),
    (2, Joint.ATTACHMENT_EDGE_TOE,
     "Toe failure from edge of attachment to plate, with stress peaks at weld ends due to local "
     "plate deformations (flexible panel). As detail 1 in Table 8.5.",
     _MISALIGNMENT_TEXT, (_MISALIGNED,), ""),
    (4, Joint.LAP_JOINT_MAIN_PLATE,
     "Overlapped welded joints: Fillet welded lap joint (stressed area of main panel: slope = 1/2). "
     "As detail 1 in Table 8.5.",
     "4) Δσ in the main plate to be calculated on the basis of area shown in the sketch. Details 4) "
     "and 5): Weld terminations more than 10 mm from plate edge. Shear cracking in the weld should "
     "be checked using detail 8).",
     (_TERMINATIONS,), "Shear cracking in the weld should be checked using detail 8)."),
)
_COVER_REQUIREMENTS = (
    "6) If the cover plate is wider than the flange, a transverse end weld is needed. This weld "
    "should be carefully ground to remove undercut. The minimum length of the cover plate is "
    "300 mm. For shorter attachments size effect see detail 1)."
)
_THINNER_COVER = Band("t_c_mm/t_mm", up_to=1.0)
_THICKER_COVER = Band("t_c_mm/t_mm", above=1.0, above_inclusive=True)
_COVER_GRID: Final = (
    (56.0, True, (_THINNER_COVER, Band("t_mm", up_to=20.0, up_to_inclusive=True))),
    (50.0, False, (_THINNER_COVER, Band("t_mm", 20.0, False, 30.0, True))),
    (50.0, False, (_THICKER_COVER, Band("t_mm", up_to=20.0, up_to_inclusive=True))),
    (45.0, False, (_THINNER_COVER, Band("t_mm", 30.0, False, 50.0, True))),
    (45.0, False, (_THICKER_COVER, Band("t_mm", 20.0, False, 30.0, True))),
    (40.0, False, (_THINNER_COVER, Band("t_mm", above=50.0))),
    (40.0, False, (_THICKER_COVER, Band("t_mm", 30.0, False, 50.0, True))),
    (36.0, False, (_THICKER_COVER, Band("t_mm", above=50.0))),
)

_TABLE_8_5: Final = (
    *(
        Row(Table.T8_5, (detail,), category, (joint,), 25, description, requirements, (*bands, *facts), also=also)
        for detail, joint, description, requirements, facts, also in _GRID_DETAILS
        for category, bands in _DETAIL_1_GRID
    ),
    Row(Table.T8_5, (3,), 36.0, (Joint.CRUCIFORM_OR_TEE_ROOT,), 25,
        "Root failure in partial penetration Tee-butt joints or fillet welded joint and in Tee-butt "
        "weld, according to Figure 4.6 in EN 1993-1-8:2005.",
        _MISALIGNMENT_TEXT + " (Δσw, from detail 1's requirement 3.)",
        (_MISALIGNED,), asterisk=True),
    Row(Table.T8_5, (3,), 80.0, (Joint.CRUCIFORM_OR_TEE_ROOT,), 25,
        "Root failure in partial penetration Tee-butt joints or fillet welded joint and in Tee-butt "
        "weld, according to Figure 4.6 in EN 1993-1-8:2005.",
        _ROOT_TOO + " " + _MISALIGNMENT_TEXT,
        (_MISALIGNED,), stress=StressKind.SHEAR),
    Row(Table.T8_5, (5,), 45.0, (Joint.LAP_JOINT_OVERLAPPING_PLATE,), 25,
        "Overlapped: Fillet welded lap joint.",
        "5) Δσ to be calculated in the overlapping plates. Details 4) and 5): Weld terminations more "
        "than 10 mm from plate edge. Shear cracking in the weld should be checked using detail 8).",
        (_TERMINATIONS,), asterisk=True,
        also="Shear cracking in the weld should be checked using detail 8)."),
    *(
        Row(Table.T8_5, (6,), category, (Joint.COVER_PLATE_END,), 25,
            "Cover plates in beams and plate girders: End zones of single or multiple welded cover "
            "plates, with or without transverse end weld.",
            _COVER_REQUIREMENTS, bands, asterisk=starred)
        for category, starred, bands in _COVER_GRID
    ),
    Row(Table.T8_5, (7,), 56.0, (Joint.COVER_PLATE_REINFORCED_END,), 25,
        "Cover plates in beams and plate girders. 5t_c is the minimum length of the reinforcement weld.",
        "7) Transverse end weld ground flush. In addition, if t_c>20mm, front of plate at the end "
        "ground with a slope < 1 in 4.",
        (Fact("end_weld_ground_flush", True),)),
    Row(Table.T8_5, (8,), 80.0, (Joint.SHEAR_FLOW_FILLET,), 25,
        "Continuous fillet welds transmitting a shear flow, such as web to flange welds in plate girders.",
        "8) Δτ to be calculated from the weld throat area.",
        stress=StressKind.SHEAR),
    Row(Table.T8_5, (9,), 80.0, (Joint.LAP_JOINT_WELD_SHEAR,), 25,
        "Fillet welded lap joint.",
        "9) Δτ to be calculated from the weld throat area considering the total length of the weld. "
        "Weld terminations more than 10 mm from the plate edge, see also 4) and 5) above.",
        (_TERMINATIONS,), stress=StressKind.SHEAR),
    Row(Table.T8_5, (11,), 71.0, (Joint.TUBE_SOCKET_BUTT,), 25,
        "Tube socket joint with 80% full penetration butt welds.",
        "11) Weld toe ground. Δσ computed in tube.",
        (Fact("weld_toe_ground", True),)),
    Row(Table.T8_5, (12,), 40.0, (Joint.TUBE_SOCKET_FILLET,), 25,
        "Tube socket joint with fillet welds.",
        "12) Δσ computed in tube."),
)

# ---------------------------------------------------------------------------------------------
# Table B.1, detail categories for use with the geometric (hot spot) stress method (p. 33)
# ---------------------------------------------------------------------------------------------

_B1_NOTES = (
    " NOTE 1: Table B.1 does not cover effects of misalignment. They have to be considered "
    "explicitly in determination of stress. NOTE 2: Table B.1 does not cover fatigue initiation "
    "from the root followed by propagation through the throat."
)
_TOE_60 = Band("toe_angle_deg", up_to=60.0, up_to_inclusive=True)

_TABLE_B_1: Final = (
    Row(Table.B_1, (1,), 112.0, (Joint.HOT_SPOT_BUTT,), 33,
        "Full penetration butt joint.",
        "All welds ground flush to plate surface parallel to direction of the arrow. Weld run-on and "
        "run-off pieces to be used and subsequently removed, plate edges to be ground flush in "
        "direction of stress. Welded from both sides, checked by NDT." + _B1_NOTES,
        _FLUSH),
    Row(Table.B_1, (2,), 100.0, (Joint.HOT_SPOT_BUTT,), 33,
        "Full penetration butt joint.",
        "Weld not ground flush. Weld run-on and run-off pieces to be used and subsequently removed, "
        "plate edges to be ground flush in direction of stress. Welded from both sides." + _B1_NOTES,
        (Fact("ground_flush", False), Fact("run_on_off_pieces_removed", True), Fact("welded_both_sides", True))),
    Row(Table.B_1, (3,), 100.0, (Joint.HOT_SPOT_CRUCIFORM_K_BUTT,), 33,
        "Cruciform joint with full penetration K-butt welds.", "Weld toe angle ≤60°." + _B1_NOTES, (_TOE_60,)),
    Row(Table.B_1, (4,), 100.0, (Joint.HOT_SPOT_NON_LOAD_CARRYING_FILLET,), 33,
        "Non load-carrying fillet welds.", "Weld toe angle ≤60°." + _B1_NOTES, (_TOE_60,)),
    Row(Table.B_1, (5,), 100.0, (Joint.HOT_SPOT_BRACKET_OR_STIFFENER_END,), 33,
        "Bracket ends, ends of longitudinal stiffeners.", "Weld toe angle ≤60°." + _B1_NOTES, (_TOE_60,)),
    Row(Table.B_1, (6,), 100.0, (Joint.HOT_SPOT_COVER_PLATE_END,), 33,
        "Cover plate ends and similar joints.", "Weld toe angle ≤60°." + _B1_NOTES, (_TOE_60,)),
    Row(Table.B_1, (7,), 90.0, (Joint.HOT_SPOT_CRUCIFORM_LOAD_CARRYING_FILLET,), 33,
        "Cruciform joints with load-carrying fillet welds.", "Weld toe angle ≤60°." + _B1_NOTES, (_TOE_60,)),
)

#: Every row, in table order.
ROWS: Final[tuple[Row, ...]] = (*_TABLE_8_3, *_TABLE_8_4, *_TABLE_8_5, *_TABLE_B_1)


@dataclass(frozen=True)
class Candidate:
    """A row the facts did not exclude, and how much of it the facts confirmed."""

    row: Row
    #: Conditions a fact given satisfies.
    confirmed: tuple[str, ...]
    #: Conditions no fact was given for. Each is a question that could exclude this row.
    open: tuple[str, ...]
    #: Conditions reopened because the table leaves the value given uncovered.
    on_boundary: tuple[str, ...] = ()

    @property
    def supported(self) -> bool:
        """Whether every encoded condition was confirmed. The quoted requirements still apply."""
        return not self.open and not self.on_boundary

    def detail(
        self,
        *,
        chosen_by: str,
        t_mm: float | None = None,
        t2_mm: float | None = None,
        e_mm: float | None = None,
    ) -> WeldDetail | ShearDetail:
        """The curve input for this row, with k_s applied where the row has a size effect.

        `chosen_by` names who chose this row among the candidates and why: the classification did
        not choose it. Thickness arguments are refused on a row with no size effect, because a k_s
        there would be invented. The source lists the conditions the facts did not confirm.
        """
        who = require_source(chosen_by, "Choosing a detail category")
        row = self.row
        factor = 1.0
        size = ""
        if row.size_effect is SizeEffect.NONE:
            if t_mm is not None or t2_mm is not None or e_mm is not None:
                raise ClassificationError(
                    f"{row.label()} has no size effect in the table, so no thickness applies to it."
                )
        elif row.size_effect is SizeEffect.THICKNESS:
            if t_mm is None:
                raise ClassificationError(
                    f"{row.label()} carries 'size effect for t>25mm: k_s=(25/t)^0.2'. Give t_mm."
                )
            if t2_mm is not None or e_mm is not None:
                raise ClassificationError(f"{row.label()} takes one thickness, not a step.")
            factor = thickness_size_factor(t_mm)
            size = f"; k_s = (25/t)^0.2 = {factor:.4g} at t = {t_mm:g} mm"
        else:
            if t_mm is None or t2_mm is None or e_mm is None:
                raise ClassificationError(
                    f"{row.label()}'s k_s depends on t1, t2 and the eccentricity e. Give t_mm (the "
                    "thinner plate), t2_mm and e_mm."
                )
            factor = eccentric_step_size_factor(t_mm, t2_mm, e_mm)
            size = f"; k_s = {factor:.4g} at t1 = {t_mm:g} mm, t2 = {t2_mm:g} mm, e = {e_mm:g} mm"

        unconfirmed = (*self.open, *self.on_boundary)
        source = (
            f"{STANDARD} {row.label()}{size}"
            + ("; asterisk: located one category lower (§7.1 NOTE 3), the alternative is not applied" if row.asterisk else "")
            + (f"; conditions not confirmed by the facts given: {', '.join(unconfirmed)}" if unconfirmed else "")
            + (f"; requirements to confirm: {row.requirements}" if row.requirements else "")
            + f"; chosen by: {who}"
        )
        if row.stress is StressKind.SHEAR:
            return ShearDetail(detail_category_mpa=row.category_mpa, source=source, description=row.description)
        return WeldDetail(
            detail_category_mpa=row.category_mpa * factor, source=source, description=row.description
        )


@dataclass(frozen=True)
class Classification:
    """The rows a joint can still be. There is deliberately no `best` and no `pick`."""

    joint: Joint
    basis: StressBasis
    candidates: tuple[Candidate, ...]
    #: Each excluded row, with the condition that excluded it.
    excluded: tuple[tuple[Row, str], ...] = ()
    #: Why there is no candidate, when there is none.
    uncovered: str | None = None
    #: Further assessments the candidates' rows say are owed.
    also: tuple[str, ...] = field(default=())

    def conservative(self, stress: StressKind = StressKind.DIRECT) -> Candidate | None:
        """The candidate with the lowest category for one kind of stress, or None.

        Per kind, because a direct and a shear category are two assessments of one joint
        (Table 8.5 detail 3 is both), never two alternatives.
        """
        of_kind = [c for c in self.candidates if c.row.stress is stress]
        return min(of_kind, key=lambda c: c.row.category_mpa, default=None)

    def categories(self, stress: StressKind = StressKind.DIRECT) -> tuple[float, ...]:
        """The distinct categories still possible for one kind of stress, highest first."""
        return tuple(
            sorted({c.row.category_mpa for c in self.candidates if c.row.stress is stress}, reverse=True)
        )

    @property
    def undecided(self) -> tuple[str, ...]:
        """The facts that were not given and that some candidate's row states a condition on."""
        names = {
            name
            for candidate in self.candidates
            for text in candidate.open
            for name in _names_in(candidate.row, text)
        }
        return tuple(sorted(names))


def _names_in(row: Row, text: str) -> tuple[str, ...]:
    for condition in row.conditions:
        if condition.describe() == text:
            return tuple(condition.name.split("/"))
    return ()


def _check_facts(facts: Mapping[str, bool | float]) -> None:
    for name, value in facts.items():
        spec = FACTS.get(name)
        if spec is None:
            raise ClassificationError(
                f"{name!r} is not a fact the catalogue reads. The facts are: {', '.join(sorted(FACTS))}."
            )
        if spec.number:
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
                raise ClassificationError(f"{name} is {spec.meaning}: a finite, non-negative number, not {value!r}.")
        elif not isinstance(value, bool):
            raise ClassificationError(f"{name} is a yes/no fact ({spec.meaning}), not {value!r}.")


def classify(
    joint: Joint, facts: Mapping[str, bool | float] | None = None, *, basis: StressBasis
) -> Classification:
    """The rows `joint` can still be, given `facts`, for a stress read on `basis`.

    A fact left out is unknown, and never excludes a row. A row is a candidate when no fact given
    contradicts a condition it states. `basis` must match the tables the joint is in: Tables 8.3 to
    8.5 are for nominal stresses (§7.1(4)), Table B.1 for geometric stresses (§7.1(5)). A notch-root
    stress has no table.
    """
    given = dict(facts or {})
    _check_facts(given)
    if joint in REFERRED_ELSEWHERE:
        return Classification(joint=joint, basis=basis, candidates=(), uncovered=REFERRED_ELSEWHERE[joint])

    rows = [row for row in ROWS if joint in row.joints]
    wanted = {row.basis for row in rows}
    if basis not in wanted:
        (needed,) = wanted
        clause = "§7.1(5) and Annex B" if needed is StressBasis.HOT_SPOT else "§7.1(4) and Tables 8.1–8.10"
        raise ClassificationError(
            f"{joint} is classified for a {needed} stress ({STANDARD} {clause}), and the stress here "
            f"is {basis}. A category read for one basis applied to the other counts the weld's "
            "notch effect twice or not at all."
        )

    candidates: list[Candidate] = []
    excluded: list[tuple[Row, str]] = []
    reopenable: dict[tuple[Table, tuple[int, ...]], list[tuple[Row, list[str], list[str], list[str]]]] = {}
    for row in rows:
        confirmed: list[str] = []
        open_: list[str] = []
        edges: list[str] = []
        hard: str | None = None
        for condition in row.conditions:
            status, edge = condition.evaluate(given)
            text = condition.describe()
            if status is _Status.CONFIRMED:
                confirmed.append(text)
            elif status is _Status.OPEN:
                open_.append(text)
            elif edge:
                edges.append(text)
            elif hard is None:
                hard = text
        if hard is None and not edges:
            candidates.append(Candidate(row, tuple(confirmed), tuple(open_)))
            continue
        excluded.append((row, hard if hard is not None else edges[0]))
        if hard is None:
            reopenable.setdefault((row.table, row.details), []).append((row, confirmed, open_, edges))

    for key, near in reopenable.items():
        if any((c.row.table, c.row.details) == key for c in candidates):
            continue
        for row, confirmed, open_, edges in near:
            candidates.append(Candidate(row, tuple(confirmed), tuple(open_), tuple(edges)))
            excluded[:] = [(r, why) for r, why in excluded if r is not row]

    uncovered = None
    if not candidates:
        uncovered = (
            f"No row of {STANDARD} for a {joint} admits the facts given. "
            + "; ".join(f"{row.label()} needs {why}" for row, why in excluded)
            + ". A joint outside the tables needs a category from the National Annex or from tests "
            "(§7.1 NOTE 2), not the nearest row."
        )
    also = tuple(dict.fromkeys(c.row.also for c in candidates if c.row.also))
    ordered = sorted(candidates, key=lambda c: (c.row.stress, -c.row.category_mpa, ROWS.index(c.row)))
    return Classification(
        joint=joint,
        basis=basis,
        candidates=tuple(ordered),
        excluded=tuple(excluded),
        uncovered=uncovered,
        also=also,
    )


__all__ = [
    "FACTS",
    "PAGES",
    "REFERRED_ELSEWHERE",
    "ROWS",
    "Band",
    "Candidate",
    "Classification",
    "ClassificationError",
    "Fact",
    "FactSpec",
    "Joint",
    "Row",
    "SizeEffect",
    "Table",
    "classify",
]
