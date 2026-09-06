"""A dimension that knows which decision put it there — master plan Phase 17.1.

`tests/test_manufacture_drawing.py` pins that the two sources are *distinguishable*.
This goes the distance the distinction is actually worth: a dimension carrying
`DimensionSource.PARAMETER` must be able to **name the parameter**, and a number
the code could not trace must not claim one.

**Why that is the whole point of the package.** Every CAD system can put 12 on a
drawing by measuring two faces. What none of them can say is whether 12 is a
decision somebody made or a consequence of two others — and the difference
decides what a reviewer does with it. `=thick_mm` is a number you can change and
regenerate from; a literal `12.0` typed into the feature is a number that will
still say 12 after every other dimension has moved. They print identically on the
sheet, because a shop measures to the number either way, and they must not read
alike to the engineer who signs it.

**This found a live defect and it is pinned here.** `layout._add_linear` labelled
*any* stated length it could match to an overall extent as `PARAMETER`, whether or
not a parameter stood behind it. A plate built from three literals therefore came
back with three PARAMETER dimensions each naming no parameter at all, and
`DimensionReport.fully_traced` — the provenance question, the one an engineer
reviewing the drawing asks — was `True` for a design with no parameters in it.
`_place_rounds` had it right the whole time; the two disagreeing is how it
survived. `Dimension.__post_init__` now refuses the combination outright, so it
is unrepresentable rather than merely untested, and
`TestADimensionCannotClaimAProvenanceItCannotName` breaks that guard directly.

**The honesty block, in detail.** `DimensionReport` has three buckets and says
they are disjoint by construction. `TestTheThreeBucketsAccountForEverything`
checks both halves of that claim: nothing is in two of them, and — the half that
was broken — nothing the design stated falls through all three. A pattern's
`count` used to: it is a number with no dimensional suffix, so it matched none of
the length, round or angle filters and left no trace anywhere. A drawing that
silently omits something is how a part gets made wrong, and every line that IS on
it is correct, so nothing looks wrong while it happens.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pytest

from app.design.compile import Plan, compile_spec
from app.design.execute import execute_plan
from app.design.params import Parameter, Unit
from app.design.spec import DesignSpec, FeatureSpec, expr, ref
from app.kernel import OcctRunner
from app.manufacture.dimensions import (
    classify_argument,
    from_plan,
    is_dimensional,
    trace_dimensions,
)
from app.manufacture.drawing import (
    Dimension,
    DimensionKind,
    DimensionReport,
    DimensionSource,
    Drawing,
    TracedDimension,
)
from app.manufacture.errors import DrawingError
from app.manufacture.layout import LayoutRequest, lay_out

# -- the part everything on this sheet is drawn from ------------------------


def bracket() -> DesignSpec:
    """Rung 2 of the ladder: several features that have to agree.

    Six holes on a bolt circle, four corner fillets, a bore off the centre line
    and an edge break, every one of them dimensioned from a parameter rather
    than typed twice. Deliberately not a plate: a plate has three numbers and
    every one of them is an overall extent, so a drawing of one cannot fail in
    any of the ways this file is about.

    The bore is off centre on purpose. A symmetric part is chiral-blind — a
    mirrored drawing of one is indistinguishable from a correct one, which is
    exactly the failure `app/render/` records as having shipped for a day.
    """
    return DesignSpec.of(
        "bracket",
        material="steel-1018",
        description="Mounting bracket; the drawing fixture for Phase 17.",
        parameters=[
            Parameter("width_mm", Unit.MM, value=120.0, description="Overall across."),
            Parameter("depth_mm", Unit.MM, value=80.0, description="Overall front to back."),
            Parameter("thick_mm", Unit.MM, value=12.0, description="Plate thickness."),
            Parameter("bore_mm", Unit.MM, value=30.0, description="Central bore."),
            Parameter("corner_mm", Unit.MM, expression="thick_mm / 2"),
            Parameter("break_mm", Unit.MM, value=2.0, description="Edge break."),
            Parameter("bolt_mm", Unit.MM, value=8.0, description="Bolt clearance hole."),
        ],
        features=[
            FeatureSpec("plate.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "plate.outline",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("plate.profile"),
                    "width_mm": expr("width_mm"),
                    "height_mm": expr("depth_mm"),
                },
            ),
            FeatureSpec(
                "plate.bore",
                "catia_sketch_circle",
                {
                    "sketch": ref("plate.profile"),
                    "diameter_mm": expr("bore_mm"),
                    "at": [20.0, 10.0],
                },
            ),
            FeatureSpec(
                "plate.body",
                "catia_pad",
                {"sketch": ref("plate.profile"), "length_mm": expr("thick_mm")},
                note="Extrude the footprint to thickness.",
            ),
            FeatureSpec(
                "plate.corners",
                "catia_fillet",
                {
                    "radius_mm": expr("corner_mm"),
                    "edges": "vertical",
                    "feature": ref("plate.body"),
                },
                note="Handling radius; nothing mates here.",
            ),
            FeatureSpec(
                "plate.edge_break",
                "catia_chamfer",
                {"length_mm": expr("break_mm"), "edges": "top"},
            ),
            FeatureSpec("plate.bolt_profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "plate.bolt_circle",
                "catia_sketch_circle",
                {
                    "sketch": ref("plate.bolt_profile"),
                    "diameter_mm": expr("bolt_mm"),
                    "at": [45.0, 0.0],
                },
            ),
            FeatureSpec(
                "plate.bolt",
                "catia_pocket",
                {"sketch": ref("plate.bolt_profile"), "through_all": True},
            ),
            FeatureSpec(
                "plate.bolt_ring",
                "catia_pattern_circular",
                {"count": 6, "feature": ref("plate.bolt")},
            ),
        ],
    )


class Built:
    """A design, its plan, the solid it built and the dimensions it states."""

    def __init__(self, design: DesignSpec) -> None:
        self.design = design
        self.plan: Plan = compile_spec(design)
        runner = OcctRunner()
        self.build = execute_plan(self.plan, runner)
        if self.build.failure is not None:  # pragma: no cover - a broken fixture
            raise AssertionError(f"the fixture did not build: {self.build.failure}")
        self.shape: Any = runner._context.document.shape
        self.traced, self.suppressed = trace_dimensions(design, self.plan)

    def drawing(self, **overrides: Any) -> Drawing:
        request = {
            "title": "Bracket",
            "drawing_number": "KRY-0002",
            "views": ("front", "top", "right"),
            "include_iso": False,
        }
        request.update(overrides)
        return lay_out(
            self.shape,
            LayoutRequest(**request),
            traced=self.traced,
            suppressed=self.suppressed,
        )


@lru_cache(maxsize=None)
def built() -> Built:
    """The bracket, built once. Kernel rebuilds are the expensive part here."""
    return Built(bracket())


def _literal_plate() -> Built:
    """The same shape with every number typed straight into the feature.

    A part built call by call in a conversation looks like this: no parameters,
    so nothing on its drawing can be traced. It is a perfectly good drawing; it
    just cannot promise that an edit to the design would move any of its numbers.
    """
    return Built(
        DesignSpec.of(
            "literal",
            features=[
                FeatureSpec("plate.profile", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "plate.outline",
                    "catia_sketch_rectangle",
                    {"sketch": ref("plate.profile"), "width_mm": 120.0, "height_mm": 80.0},
                ),
                FeatureSpec(
                    "plate.body",
                    "catia_pad",
                    {"sketch": ref("plate.profile"), "length_mm": 12.0},
                ),
            ],
        )
    )


def _dimension_for(drawing: Drawing, parameter: str) -> Dimension:
    for one in drawing.dimensions:
        if one.parameter == parameter:
            return one
    raise AssertionError(
        f"no dimension on this sheet names {parameter!r}; the parameters named are "
        f"{[one.parameter for one in drawing.dimensions]}"
    )


def _key(one: Any) -> tuple[str | None, str | None]:
    """The identity a stated number carries in every bucket it can land in."""
    return (getattr(one, "feature", None), getattr(one, "argument", None))


# -- 1. a PARAMETER dimension names its parameter ---------------------------


class TestADimensionCannotClaimAProvenanceItCannotName:
    """Structural, not merely tested: the combination cannot be constructed.

    A guard in a `__post_init__` is worth more than an assertion in a test,
    because every future placement routine gets it for free — and the defect this
    replaces existed precisely because one placement routine had the rule and the
    other did not.
    """

    def test_a_parameter_dimension_with_no_parameter_is_refused(self) -> None:
        with pytest.raises(DrawingError) as refusal:
            Dimension(
                view="front",
                kind=DimensionKind.LINEAR_HORIZONTAL,
                source=DimensionSource.PARAMETER,
                value=120.0,
                start=(0.0, 0.0),
                end=(120.0, 0.0),
            )

        assert "which one" in str(refusal.value)

    def test_an_empty_parameter_name_is_not_a_name(self) -> None:
        """`", ".join(())` is `""`, which is falsy and would otherwise sail past
        a `parameter is None` check while printing nothing on the sheet."""
        with pytest.raises(DrawingError):
            Dimension(
                view="front",
                kind=DimensionKind.LINEAR_HORIZONTAL,
                source=DimensionSource.PARAMETER,
                value=120.0,
                parameter="",
                start=(0.0, 0.0),
                end=(120.0, 0.0),
            )

    def test_the_same_dimension_measured_is_allowed(self) -> None:
        """The refusal is about the claim, not about the number: the identical
        dimension with an honest source is fine and is what the fix produces."""
        measured = Dimension(
            view="front",
            kind=DimensionKind.LINEAR_HORIZONTAL,
            source=DimensionSource.GEOMETRY,
            value=120.0,
            start=(0.0, 0.0),
            end=(120.0, 0.0),
        )

        assert measured.parameter is None


class TestEveryStatedDimensionNamesTheParameterBehindIt:
    def test_every_parameter_dimension_on_the_sheet_names_a_declared_parameter(
        self,
    ) -> None:
        fixture = built()
        drawing = fixture.drawing()
        declared = set(fixture.design.parameters.names())

        stated = [
            one for one in drawing.dimensions if one.source is DimensionSource.PARAMETER
        ]
        assert stated, "the bracket states its dimensions; none reached the sheet"
        for one in stated:
            assert one.parameter
            for name in one.parameter.split(", "):
                assert name in declared, (
                    f"{one.feature} claims parameter {name!r}, which this design does "
                    "not declare"
                )

    def test_the_thickness_dimension_names_thick_mm_and_not_the_feature(self) -> None:
        """A feature name is not a parameter name. `plate.body` is where the
        number was used; `thick_mm` is the decision, and it is the one a reviewer
        can go and change."""
        dimension = _dimension_for(built().drawing(), "thick_mm")

        assert dimension.value == pytest.approx(12.0)
        assert dimension.feature == "plate.body"
        assert dimension.argument == "length_mm"

    def test_a_derived_parameter_is_named_as_itself_not_as_its_source(self) -> None:
        """`corner_mm = thick_mm / 2`, and the fillet argument reads `corner_mm`.

        The dimension names *that* — the parameter whose value it is — and not
        the one two hops upstream. Naming `thick_mm` here would be the drawing
        collapsing the design's own chain of reasoning: a reviewer told the
        fillet is controlled by `thick_mm` would look for a formula on the
        feature and find one on a parameter. The chain stays reachable one hop
        at a time, in the place that holds it.
        """
        fixture = built()
        drawing = fixture.drawing()

        fillet = next(
            one for one in drawing.dimensions if one.kind is DimensionKind.RADIUS
        )
        assert fillet.source is DimensionSource.PARAMETER
        assert fillet.parameter == "corner_mm"
        assert fillet.value == pytest.approx(6.0)

        upstream = next(
            one for one in fixture.design.parameters if one.name == "corner_mm"
        )
        assert upstream.is_derived
        assert upstream.expression == "thick_mm / 2"

    def test_the_bolt_circle_is_one_dimension_for_six_holes(self) -> None:
        """ISO 129-1's count prefix, and the reason `count` is on `Dimension`:
        six leaders pointing at six identical holes is a drawing nobody reads."""
        drawing = built().drawing()

        bolts = next(one for one in drawing.dimensions if one.parameter == "bolt_mm")
        assert bolts.count == 6
        assert bolts.text == "6X Ø8"

    def test_moving_the_parameter_moves_the_dimension(self) -> None:
        """The property that makes a traced dimension worth ten inferred ones: it
        survives the next edit. A measured dimension would still be right about
        the old part and silently wrong about this one."""
        thicker = Built(bracket().set_parameter("thick_mm", 20.0))

        drawing = lay_out(
            thicker.shape,
            LayoutRequest(
                title="Bracket",
                drawing_number="KRY-0002",
                views=("front", "top"),
                include_iso=False,
            ),
            traced=thicker.traced,
            suppressed=thicker.suppressed,
        )

        thickness = _dimension_for(drawing, "thick_mm")
        assert thickness.value == pytest.approx(20.0)
        assert thickness.text == "20"
        # The derived fillet moved with it, and still names its own source.
        fillet = next(one for one in drawing.dimensions if one.kind is DimensionKind.RADIUS)
        assert fillet.value == pytest.approx(10.0)
        assert fillet.parameter == "corner_mm"


class TestANumberTheDesignDidNotDeclareDoesNotClaimAParameter:
    """The regression for the defect this file's docstring describes."""

    def test_a_part_built_from_literals_has_no_parameter_dimensions(self) -> None:
        fixture = _literal_plate()

        drawing = fixture.drawing(title="Literal", drawing_number="KRY-0003")

        assert fixture.design.parameters.names() == ()
        assert drawing.dimensions, "the sheet should still be dimensioned"
        for one in drawing.dimensions:
            assert one.source is DimensionSource.GEOMETRY, (
                f"{one.feature}.{one.argument} claims a design parameter set it, and "
                "this design has no parameters at all"
            )

    def test_such_a_sheet_is_complete_but_not_fully_traced(self) -> None:
        """Two different completeness questions, and conflating them would hide
        one: the shop can make this part, and nobody can promise an edit would
        move any of its numbers."""
        drawing = _literal_plate().drawing(title="Literal", drawing_number="KRY-0003")

        assert drawing.report.complete is True
        assert drawing.report.fully_traced is False

    def test_and_it_says_so_on_the_sheet(self) -> None:
        drawing = _literal_plate().drawing(title="Literal", drawing_number="KRY-0003")

        statement = " ".join(drawing.report.statement())
        assert "no design parameter" in statement
        assert "DO NOT MANUFACTURE" not in statement, (
            "a complete drawing whose numbers were measured is usable; printing the "
            "same warning for both teaches a reader to ignore it"
        )


# -- 2. the honesty block, in detail ----------------------------------------


class TestTheThreeBucketsAccountForEverything:
    def test_nothing_the_design_stated_is_in_two_buckets(self) -> None:
        fixture = built()
        report = fixture.drawing().report

        placed = {_key(one) for one in report.placed if one.feature}
        tabled = {_key(one.dimension) for one in report.tabled}
        other = {_key(one) for one in report.non_dimensional}

        assert not placed & tabled, f"placed and tabled share {placed & tabled}"
        assert not placed & other, f"placed and non-dimensional share {placed & other}"
        assert not tabled & other, f"tabled and non-dimensional share {tabled & other}"

    def test_nothing_the_design_stated_falls_through_all_three(self) -> None:
        """The half that was broken. A pattern's `count` has no dimensional
        suffix, so it matched neither the length, the round nor the angle filter
        and vanished — and "the report did not mention it" reads exactly like
        "the report decided it did not matter"."""
        fixture = built()
        report = fixture.drawing().report

        accounted = (
            {_key(one) for one in report.placed if one.feature}
            | {_key(one.dimension) for one in report.tabled}
            | {_key(one) for one in report.non_dimensional}
        )
        missing = {_key(one) for one in fixture.traced} - accounted
        assert not missing, f"the design stated these and the report never mentions them: {missing}"

    def test_the_pattern_count_is_named_as_not_being_a_dimension(self) -> None:
        report = built().drawing().report

        assert ("plate.bolt_ring", "count") in {
            _key(one) for one in report.non_dimensional
        }

    def test_a_position_is_not_a_dimension_and_is_listed_rather_than_drawn(self) -> None:
        """`at` is where the bore sits, not how big it is. This build places no
        positional dimension chain, so the coordinates go in the list rather than
        being drawn as though they had been dimensioned from a datum."""
        report = built().drawing().report

        listed = {_key(one) for one in report.non_dimensional}
        assert ("plate.bore", "at[0]") in listed
        assert ("plate.bore", "at[1]") in listed

    def test_a_caller_that_already_partitioned_is_not_double_counted(self) -> None:
        fixture = built()
        others = tuple(one for one in fixture.traced if not is_dimensional(one))

        drawing = lay_out(
            fixture.shape,
            LayoutRequest(
                title="Bracket",
                drawing_number="KRY-0002",
                views=("front", "top"),
                include_iso=False,
            ),
            traced=fixture.traced,
            suppressed=fixture.suppressed,
            non_dimensional=others,
        )

        keys = [_key(one) for one in drawing.report.non_dimensional]
        assert len(keys) == len(set(keys))


class TestAFeatureNobodyCouldDimensionIsNamedWithAReason:
    def test_the_edge_break_is_tabled_and_says_why(self) -> None:
        """2 mm is a real stated dimension of the part and no overall extent is
        2 mm long, so there is no pair of edges on the sheet to draw it between.
        Naming it is what lets a reader add the view that would carry it."""
        report = built().drawing().report

        chamfer = next(
            one for one in report.tabled if one.dimension.feature == "plate.edge_break"
        )
        assert "no overall extent" in chamfer.reason
        assert chamfer.dimension.label == "2"

    def test_every_tabled_dimension_has_a_reason_a_reader_can_act_on(self) -> None:
        report = built().drawing().report

        assert report.tabled, "the bracket has a dimension this build cannot place"
        for one in report.tabled:
            assert len(one.reason) > 20, (
                "a list of names with no reason is only a longer way of omitting them"
            )
            assert one.to_dict()["reason"] == one.reason

    def test_a_dimension_is_tabled_once_however_many_views_could_not_carry_it(
        self,
    ) -> None:
        """The same stated length is offered to every extent that could take it,
        so appending per offer made the honesty block itself wrong about how much
        was missing — which is the one thing it exists not to be."""
        report = built().drawing(views=("front", "top", "right")).report

        keys = [_key(one.dimension) for one in report.tabled]
        assert len(keys) == len(set(keys))

    def test_the_sheet_carries_the_do_not_manufacture_banner_when_incomplete(
        self,
    ) -> None:
        drawing = built().drawing()

        assert drawing.fully_dimensioned is False
        assert drawing.title_block.notes == drawing.report.statement()
        assert "DO NOT MANUFACTURE" in drawing.title_block.notes[0]

    def test_two_parameters_of_the_same_value_are_reported_not_guessed(self) -> None:
        """`corner_mm = 5` and `boss_r_mm = 5` are indistinguishable by value.
        Attributing the overall size to one of them would be a guess printed as a
        fact, so the drawing is still dimensioned and the collision is named."""
        square = Built(
            DesignSpec.of(
                "square",
                parameters=[
                    Parameter("across_mm", Unit.MM, value=80.0),
                    Parameter("deep_mm", Unit.MM, value=80.0),
                    Parameter("thick_mm", Unit.MM, value=12.0),
                ],
                features=[
                    FeatureSpec("p.profile", "catia_sketch_create", {"support": "XY"}),
                    FeatureSpec(
                        "p.outline",
                        "catia_sketch_rectangle",
                        {
                            "sketch": ref("p.profile"),
                            "width_mm": expr("across_mm"),
                            "height_mm": expr("deep_mm"),
                        },
                    ),
                    FeatureSpec(
                        "p.body",
                        "catia_pad",
                        {"sketch": ref("p.profile"), "length_mm": expr("thick_mm")},
                    ),
                ],
            )
        )

        report = square.drawing(title="Square", drawing_number="KRY-0004").report

        assert report.ambiguous, "80 mm matches two parameters and nothing said so"
        assert any("p.outline.width_mm" in one for one in report.ambiguous)
        overall = [one for one in report.placed if one.value == pytest.approx(80.0)]
        assert overall, "the sheet must still carry its overall dimensions"
        assert all(one.source is DimensionSource.GEOMETRY for one in overall)
        assert "Ambiguous" in " ".join(report.statement())

    def test_a_suppressed_feature_is_named_rather_than_silently_absent(self) -> None:
        """"There is no pocket" is a mystery; "the pocket is not there below
        6 mm" is an answer."""
        design = bracket()
        design = design.with_features(
            [
                *design.features,
                FeatureSpec(
                    "plate.lightening",
                    "catia_pocket",
                    {"sketch": ref("plate.bolt_profile"), "through_all": True},
                    when="thick_mm >= 20",
                ),
            ]
        )
        fixture = Built(design)

        report = fixture.drawing().report

        assert "plate.lightening" in report.suppressed
        assert "plate.lightening" in " ".join(report.statement())
        assert not any(
            one.dimension.feature == "plate.lightening" for one in report.tabled
        )


class TestAnEmptyReportIsNotAGreenOne:
    """The rule `app.design.assertions` applies to an unmeasured assertion, and
    the reason a drawing is where it matters most."""

    def test_a_report_with_nothing_in_it_is_not_complete(self) -> None:
        assert DimensionReport().complete is False
        assert DimensionReport().fully_traced is False

    def test_and_it_says_no_dimension_could_be_placed(self) -> None:
        statement = DimensionReport().statement()

        assert "DO NOT MANUFACTURE" in statement[0]
        assert "No dimension could be placed on any view." in statement

    def test_a_report_always_ends_by_disclaiming_tolerancing(self) -> None:
        """None of it exists in the design IR, so none of it is invented — and
        an engineer must not read the absence of a tolerance frame as a general
        tolerance somebody chose."""
        report = built().drawing().report

        assert report.statement()[-1].startswith("No geometric tolerancing")


# -- the tracing layer itself ------------------------------------------------


class TestWhatCountsAsADimension:
    """Name-based, and not value-based: 5.0 cannot tell a radius from a length."""

    @pytest.mark.parametrize(
        ("argument", "kind"),
        [
            ("length_mm", DimensionKind.LINEAR),
            ("width_mm", DimensionKind.LINEAR),
            ("diameter_mm", DimensionKind.DIAMETER),
            ("head_diameter_mm", DimensionKind.DIAMETER),
            ("radius_mm", DimensionKind.RADIUS),
            ("angle_deg", DimensionKind.ANGULAR),
            ("count", None),
            ("instance", None),
            ("name", None),
        ],
    )
    def test_the_suffix_convention_decides(
        self, argument: str, kind: DimensionKind | None
    ) -> None:
        assert classify_argument(argument) is kind

    def test_the_longest_suffix_wins(self) -> None:
        """Ordered, or `diameter_mm` matches the bare `_mm` rule and loses its Ø
        — a bore drawn as a length is a bore a machinist reads as a radius."""
        assert classify_argument("diameter_mm") is DimensionKind.DIAMETER
        assert classify_argument("diameter_mm") is not DimensionKind.LINEAR

    def test_a_flag_is_not_a_one_millimetre_dimension(self) -> None:
        """`isinstance(True, int)` is true in Python, so without the explicit
        exclusion `through_all: true` becomes a 1 mm depth — a plausible number
        on a drawing, attached to a feature that has no depth at all."""
        traced, _ = trace_dimensions(
            DesignSpec.of(
                "flagged",
                features=[
                    FeatureSpec("s.profile", "catia_sketch_create", {"support": "XY"}),
                    FeatureSpec(
                        "s.outline",
                        "catia_sketch_rectangle",
                        {"sketch": ref("s.profile"), "width_mm": 10.0, "height_mm": 10.0},
                    ),
                    FeatureSpec(
                        "s.body", "catia_pad", {"sketch": ref("s.profile"), "length_mm": 5.0}
                    ),
                    FeatureSpec(
                        "s.cut",
                        "catia_pocket",
                        {"sketch": ref("s.profile"), "through_all": True},
                    ),
                ],
            )
        )

        assert not [one for one in traced if one.argument == "through_all"]

    def test_the_dimensional_predicate_is_the_unit(self) -> None:
        assert is_dimensional(
            TracedDimension("f", "op", "length_mm", DimensionKind.LINEAR, 12.0, "mm")
        )
        assert not is_dimensional(
            TracedDimension("f", "op", "count", DimensionKind.LINEAR, 6.0, "")
        )


class TestTracingReadsTheDesignAndNotTheSolid:
    def test_an_argument_records_the_expression_it_was_authored_as(self) -> None:
        """The authored form, not the resolved one. `=corner_mm` is what somebody
        wrote; 6.0 is what it came out as, and both are carried because a review
        of provenance needs the first and the sheet prints the second."""
        traced, _ = trace_dimensions(bracket())

        fillet = next(one for one in traced if one.feature == "plate.corners")
        assert fillet.parameters == ("corner_mm",)
        assert fillet.expression == "corner_mm"
        assert fillet.value == pytest.approx(6.0)

    def test_a_built_in_inside_a_formula_is_not_reported_as_a_parameter(self) -> None:
        """An expression can call `max` or `sqrt`, and listing a built-in as a
        design parameter would put a decision nobody made into the manifest."""
        design = DesignSpec.of(
            "clamped",
            parameters=[
                Parameter("a_mm", Unit.MM, value=10.0),
                Parameter("b_mm", Unit.MM, value=4.0),
            ],
            features=[
                FeatureSpec("s.profile", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "s.outline",
                    "catia_sketch_rectangle",
                    {
                        "sketch": ref("s.profile"),
                        "width_mm": expr("max(a_mm, b_mm)"),
                        "height_mm": expr("a_mm"),
                    },
                ),
            ],
        )

        traced, _ = trace_dimensions(design)

        width = next(one for one in traced if one.argument == "width_mm")
        assert width.parameters == ("a_mm", "b_mm")
        assert "max" not in width.parameters

    def test_a_per_edge_list_is_one_dimension_per_edge(self) -> None:
        """"The four vertical corners at 2, 3, 4 and 5 mm" is four dimensions,
        and each row has to name which edge it belongs to."""
        design = DesignSpec.of(
            "varied",
            features=[
                FeatureSpec("s.profile", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "s.outline",
                    "catia_sketch_rectangle",
                    {"sketch": ref("s.profile"), "width_mm": 40.0, "height_mm": 30.0},
                ),
                FeatureSpec(
                    "s.body", "catia_pad", {"sketch": ref("s.profile"), "length_mm": 10.0}
                ),
                FeatureSpec(
                    "s.corners",
                    "catia_fillet",
                    {
                        "radius_mm": [2.0, 3.0, 4.0, 5.0],
                        "edges": "vertical",
                        "feature": ref("s.body"),
                    },
                ),
            ],
        )

        traced, _ = trace_dimensions(design)

        radii = [one for one in traced if one.feature == "s.corners"]
        assert [one.argument for one in radii] == [
            "radius_mm[0]",
            "radius_mm[1]",
            "radius_mm[2]",
            "radius_mm[3]",
        ]
        assert [one.value for one in radii] == [2.0, 3.0, 4.0, 5.0]

    def test_a_suppressed_feature_is_not_dimensioned_at_all(self) -> None:
        """Dimensioning it would put a number on the drawing for something that
        is not on the part — the single worst thing a drawing can do."""
        design = bracket()
        design = design.with_features(
            [
                *design.features,
                FeatureSpec(
                    "plate.rib",
                    "catia_pad",
                    {"sketch": ref("plate.bolt_profile"), "length_mm": 40.0},
                    when="thick_mm >= 20",
                ),
            ]
        )

        traced, suppressed = trace_dimensions(design)

        assert "plate.rib" in suppressed
        assert not [one for one in traced if one.feature == "plate.rib"]

    def test_a_plan_alone_traces_nothing_and_says_so(self) -> None:
        """The path for a part assembled call by call: there is no authored
        expression to read, so every number is a literal with nothing behind it.
        Reporting that honestly is the accurate description of what the shop is
        being handed."""
        traced = from_plan(compile_spec(bracket()))

        assert traced, "a plan still carries its numbers"
        assert not any(one.is_traced for one in traced)
        assert not any(one.parameters for one in traced)

    def test_a_dimension_prints_without_a_unit_and_an_angle_keeps_its_sign(self) -> None:
        """ISO 129-1: the title block says the sheet is in millimetres, so
        repeating it on every dimension is what the standard exists to stop. A
        bare angle next to a linear one would be read as mm."""
        assert (
            TracedDimension("f", "op", "length_mm", DimensionKind.LINEAR, 12.5, "mm").label
            == "12.5"
        )
        assert (
            TracedDimension("f", "op", "angle_deg", DimensionKind.ANGULAR, 45.0, "deg").label
            == "45°"
        )
        assert (
            TracedDimension(
                "f", "op", "diameter_mm", DimensionKind.DIAMETER, 30.0, "mm"
            ).label
            == "Ø30"
        )

    def test_a_kernel_rounding_artefact_is_not_printed_on_a_drawing(self) -> None:
        """A sheet that says 60.000001 because a face integrated to a part in
        1e8 is a sheet an inspector rejects the part against."""
        assert (
            TracedDimension(
                "f", "op", "length_mm", DimensionKind.LINEAR, 60.0000001, "mm"
            ).label
            == "60"
        )
