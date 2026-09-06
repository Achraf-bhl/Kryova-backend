"""The agent-facing surface over `app/assembly/` — six tools, driven the way dispatch drives them.

`app/assembly/` shipped 2,916 lines and 108 tests with no tool, no route and no
persistence behind it. A capability the agent cannot reach is a capability the product
does not have, and this repository has already recorded that failure twice (the OCCT
kernel green on capability for an era with no backend selection in `dispatch.py`;
`app/render/`, `app/ai/vision.py` and `app/design/sensitivity.py` with zero callers
outside tests). These are the tests for the surface that closes it.

**Every call here goes through `dispatch.validate()` against the model-facing schema
first.** That is defect **D9** in one sentence: a test called
`runner("catia_pad", {"name": "slab", ...})` — an argument `validate()` rejects — so it
passed while proving nothing about the path it was quoted for. `run()` below refuses to
make a call the model would not have been allowed to make, so an argument that is not in
the spec fails here rather than reading as a passing test. `TestTheModelFacingSchemaIsWhatIsTested`
breaks that guard deliberately, in both directions.

Not called end-to-end tests, and deliberately not: an end-to-end claim goes through the
Ollama chatbot, and this starts at the runner. What it *does* pin is that the arguments
it uses are the arguments the model is offered, and that the tools are in
`backends.local_tool_names()` — the list `dispatch.available_tools` hands the model on
the open kernel — so the gap between this and the chat endpoint is the model's choice of
tool and nothing else.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.catia.ops import registry
from app.catia.tool_specs import get_spec
from app.catia.validation import SchemaError, validate
from app.geometry import backends
from app.kernel.errors import GeometryError
from app.kernel.occt.operations import HANDLERS, RECORDED
from app.kernel.occt.operations import assembly_ops as ops

PRODUCT_CREATE = "catia_product_create"
COMPONENT = "catia_assembly_component"
PLACE = "catia_assembly_place"
BOM = "catia_bill_of_materials"
CLASH = "catia_assembly_clash"
ANALYSIS = "catia_assembly_analysis"

THE_SIX = (PRODUCT_CREATE, COMPONENT, PLACE, BOM, CLASH, ANALYSIS)


def _kernel_available() -> bool:
    try:
        from app.kernel.occt.binding import require

        require()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_kernel = pytest.mark.skipif(not _kernel_available(), reason="OCCT not installed")


def run(runner: Any, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the arguments the way `dispatch.call_catia` does, then run the tool.

    The whole point of the helper. `dispatch` validates against
    `Operation.json_schema()` — the model-facing one, with `additionalProperties:
    false` and every server-supplied field removed — *before* anything reaches a
    backend. A test that skipped this step could pass on an argument the product
    would refuse, which is D9.
    """
    spec = get_spec(tool)
    assert spec is not None, f"{tool} is not in the tool registry"
    validate(arguments or {}, spec.parameters)
    return dict(runner(tool, arguments or {}))


@pytest.fixture
def runner() -> Any:
    from app.kernel.occt.runner import OcctRunner

    return OcctRunner()


def build_bar(runner: Any, name: str, *, section_mm: float = 40.0, length_mm: float = 100.0,
              material: str | None = "steel-1018") -> None:
    """A square bar as its own part: `section_mm` square about the origin, padded +Z.

    Built through `run()` like everything else, so even the fixture geometry is made with
    arguments the model is allowed to send — which is how this file found that
    `catia_surface_primitive` declares only `sphere` and `cylinder` while the OCCT
    handler also implements `box`. The handler's box branch is unreachable from the
    model, so the fixture sketches and pads one instead, which is the path an agent
    actually takes.

    `material=None` builds a part with a volume and no density, which is the case the
    mass roll-up has to refuse rather than round to zero.
    """
    run(runner, "catia_new_part", {"name": name})
    run(runner, "catia_sketch_create", {"support": "xy", "name": "profile"})
    run(runner, "catia_sketch_rectangle",
        {"sketch": "profile", "width_mm": section_mm, "height_mm": section_mm})
    run(runner, "catia_pad", {"sketch": "profile", "length_mm": length_mm})
    if material is not None:
        run(runner, "catia_set_material", {"material": material})


# -- what the model is offered ----------------------------------------------


class TestTheToolsExistWhereTheProductLooksForThem:
    """A handler nothing routes to is the failure this whole module is written against."""

    @pytest.mark.parametrize("tool", THE_SIX)
    def test_each_tool_is_declared_and_implemented(self, tool: str) -> None:
        assert registry.get(tool) is not None, f"{tool} is not in the operation registry"
        assert tool in HANDLERS, f"{tool} has no OCCT handler"

    @pytest.mark.parametrize("tool", THE_SIX)
    @needs_kernel
    def test_each_tool_is_offered_on_the_open_kernel(self, tool: str) -> None:
        """`dispatch.available_tools` offers exactly `backends.local_tool_names()` here."""
        assert tool in backends.local_tool_names()

    @pytest.mark.parametrize("tool", (COMPONENT, PLACE))
    def test_the_two_new_tools_name_no_backend_method(self, tool: str) -> None:
        """They are `server_only`: there is no COM method behind either, by design.

        A seat's sequence is `catia_save_part` then `catia_component_add`, against files
        on disk. These compose an assembly out of parts built in the conversation, which
        is what the open kernel has instead of files. Declaring a method neither backend
        implements is how `test_com_backend_covers_the_registry` starts failing for a
        tool nobody can run.
        """
        operation = registry.get(tool)
        assert operation is not None
        assert operation.server_only is True
        assert operation.method == ""
        assert tool in registry.SERVER_ONLY
        assert tool not in registry.TOOL_METHODS

    def test_the_mutating_assembly_tools_are_kept_out_of_the_build_journal(self) -> None:
        """Break this and `catia_set_parameter` cannot rebuild anything afterwards.

        A replay re-runs the journal into a *fresh* context. `catia_assembly_component`
        hands the open document to the assembly and leaves nothing open, so a replayed
        one empties the rebuild half way through and every entry after it fails with
        "no document is open".
        """
        assert ops.MUTATING == {PRODUCT_CREATE, COMPONENT, PLACE}
        assert ops.MUTATING.isdisjoint(RECORDED)


class TestThePlacementVocabulary:
    """Position and turn, because a model cannot type a rotation matrix.

    `app/catia/ops/placement.py` records the measured version of this failure one
    dimension down: asked for four holes on a 70 mm bolt circle, the model wrote
    (35, 35) — a 99 mm circle — because the vocabulary made it do the trigonometry. In
    3D the value it would have to invent is nine numbers, so the schema does not ask for
    one.
    """

    def test_a_placement_is_a_position_and_a_turn(self) -> None:
        schema = registry.get(PLACE).json_schema()  # type: ignore[union-attr]
        properties = schema["properties"]
        assert {"at", "turn_axis", "turn_deg"} <= set(properties)
        assert properties["turn_axis"]["enum"] == ["x", "y", "z"]
        assert properties["at"]["minItems"] == properties["at"]["maxItems"] == 3

    @pytest.mark.parametrize(
        "spelling", ["rotation", "matrix", "rotation_matrix", "frame", "placement", "position",
                     "origin_mm", "quaternion", "euler"]
    )
    def test_no_way_to_hand_the_tool_a_transform(self, spelling: str) -> None:
        """The arithmetic lives in the tool, so there is nowhere to send a wrong answer."""
        assert spelling not in registry.get(PLACE).json_schema()["properties"]  # type: ignore[union-attr]


class TestTheModelFacingSchemaIsWhatIsTested:
    """D9, broken deliberately: the guard has to reject as well as accept."""

    def test_an_undeclared_argument_is_refused_before_any_backend_sees_it(self) -> None:
        spec = get_spec(PLACE)
        assert spec is not None
        with pytest.raises(SchemaError):
            validate({"component": "leg", "rotation": [1, 0, 0, 0, 1, 0, 0, 0, 1]},
                     spec.parameters)

    def test_a_turn_about_something_that_is_not_an_axis_is_refused_by_the_schema(self) -> None:
        spec = get_spec(PLACE)
        assert spec is not None
        with pytest.raises(SchemaError):
            validate({"component": "leg", "turn_axis": "w"}, spec.parameters)

    def test_the_required_argument_is_required(self) -> None:
        spec = get_spec(PLACE)
        assert spec is not None
        with pytest.raises(SchemaError):
            validate({"at": [0, 0, 0]}, spec.parameters)

    def test_an_analysis_kind_outside_the_enum_never_reaches_the_handler(self) -> None:
        spec = get_spec(ANALYSIS)
        assert spec is not None
        with pytest.raises(SchemaError):
            validate({"kind": "cost"}, spec.parameters)

    @needs_kernel
    def test_the_helper_itself_refuses_a_call_the_model_could_not_make(self, runner: Any) -> None:
        """If `run()` stopped validating, every other test here would prove nothing."""
        with pytest.raises(SchemaError):
            run(runner, PRODUCT_CREATE, {"name": "frame", "made_up": 1})


# -- composing one ------------------------------------------------------------


@needs_kernel
class TestComposingAnAssembly:
    def test_a_part_becomes_a_component_and_the_next_one_can_be_started(
        self, runner: Any
    ) -> None:
        """The hand-off, which is the mechanism and not a side effect.

        `app/ai/tools.py` refuses a second `catia_new_part` while a document is still
        live in memory. Without the hand-off the agent could build exactly one part per
        conversation, and an assembly would be unreachable from the product — which is
        the D2 shape of failure all over again.
        """
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        result = run(runner, COMPONENT, {"name": "leg"})

        assert result["component"] == "leg"
        assert result["material"] == "steel-1018"
        assert result["mass_kg"] == pytest.approx(1.2592, rel=1e-6)
        assert runner.document is None, (
            "the part is still open, so catia_new_part will be refused and no second "
            "component can ever be built"
        )
        build_bar(runner, "rail", length_mm=300.0)
        assert runner.document is not None

    def test_the_build_journal_goes_with_the_component(self, runner: Any) -> None:
        """Otherwise `catia_set_parameter` rewrites the wrong part.

        A replay rebuilds from the top of the journal. Left alone, the journal after two
        components would replay the leg's box into the rail's document — so what
        `catia_list_parameters` reports after a hand-off is the check that it did not.
        """
        build_bar(runner, "leg", length_mm=100.0)
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        build_bar(runner, "rail", length_mm=300.0)

        parameters = run(runner, "catia_list_parameters", {})["parameters"]
        assert [p["name"] for p in parameters] == [
            "profile\\width_mm", "profile\\height_mm", "Pad.1\\length_mm"
        ], "the leg's own dimensions are still in the rail's journal"
        assert parameters[-1]["value"] == 300.0

        rebuilt = run(runner, "catia_set_parameter",
                      {"name": "Pad.1\\length_mm", "value": 250.0, "unit": "mm"})
        assert rebuilt["parameter"]["value"] == 250.0
        assert run(runner, ANALYSIS, {"kind": "mass", "component": "leg"})["mass_kg"] == (
            pytest.approx(1.2592, rel=1e-6)
        ), "rebuilding the rail changed the component already recorded"

    def test_one_component_placed_four_times_is_one_component(self, runner: Any) -> None:
        """The graph earning its keep: four legs, one definition, four occurrence paths."""
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        paths = [
            run(runner, PLACE, {"component": "leg", "at": [x, y, 0]})["occurrence"]
            for x, y in ((0, 0), (200, 0), (0, 200), (200, 200))
        ]
        assert paths == ["frame/leg.1", "frame/leg.2", "frame/leg.3", "frame/leg.4"]

        bom = run(runner, BOM, {})
        assert bom["lines"] == [
            {"component": "leg", "quantity": 4, "design": "leg", "material": "steel-1018"}
        ]
        assert bom["occurrence_count"] == 4
        assert bom["defined_but_never_placed"] == []

    def test_a_component_recorded_and_never_placed_is_reported_not_dropped(
        self, runner: Any
    ) -> None:
        """`ProductStructure.orphans()`' rule: a part drawn and never assembled is a finding."""
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        build_bar(runner, "gusset", section_mm=20.0, length_mm=20.0)
        run(runner, COMPONENT, {"name": "gusset"})
        run(runner, PLACE, {"component": "leg"})

        bom = run(runner, BOM, {"format": "detailed"})
        assert bom["defined_but_never_placed"] == ["gusset"]
        assert [o["path"] for o in bom["occurrences"]] == ["frame/leg.1"]

    def test_a_tag_names_the_run_of_instances_rather_than_the_component(
        self, runner: Any
    ) -> None:
        build_bar(runner, "bar")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "bar"})
        assert run(runner, PLACE, {"component": "bar", "tag": "post"})["occurrence"] == (
            "frame/post.1"
        )
        assert run(runner, PLACE, {"component": "bar", "tag": "post"})["occurrence"] == (
            "frame/post.2"
        )
        assert run(runner, PLACE, {"component": "bar", "tag": "rail"})["occurrence"] == (
            "frame/rail.1"
        )


@needs_kernel
class TestPlacementActuallyMovesTheGeometry:
    """A placement that is only recorded, and never applied, looks identical in the payload.

    So the turn is checked against a *volume*, closed-form: the bar is 40 x 40 in section
    and 100 long along +Z. Turned 90 degrees about X it lies along -Y instead, and a probe
    parked where the un-turned bar cannot reach then overlaps it by exactly
    40 x 40 x 20 = 32,000 mm3. The number is arithmetic on the boxes, not a recorded
    output.
    """

    def _frame_with_a_turned_bar(self, runner: Any, turn: dict[str, Any]) -> dict[str, Any]:
        build_bar(runner, "bar")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "bar"})
        run(runner, PLACE, {"component": "bar", **turn})
        run(runner, PLACE, {"component": "bar", "tag": "probe", "at": [0, -60, 0]})
        return run(runner, CLASH, {})

    def test_a_turned_bar_reaches_where_an_unturned_one_cannot(self, runner: Any) -> None:
        report = self._frame_with_a_turned_bar(runner, {"turn_axis": "x", "turn_deg": 90})
        assert report["clash_count"] == 1
        assert report["interference_volume_mm3"] == pytest.approx(32_000.0, rel=1e-6)

    def test_the_same_placement_without_the_turn_does_not_reach(self, runner: Any) -> None:
        """The other half of the same measurement — otherwise the number proves nothing."""
        report = self._frame_with_a_turned_bar(runner, {})
        assert report["clash_count"] == 0
        assert report["interference_volume_mm3"] == 0.0

    def test_turn_then_position_and_in_that_order(self, runner: Any) -> None:
        """`turned` rotates about the assembly origin; `at` then moves the result there.

        Composed the other way round, a part turned 90 degrees and placed 300 mm up would
        end up 300 mm sideways instead — which is the class of error that reads as a
        plausible machine and is wrong.
        """
        build_bar(runner, "bar")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "bar"})
        placed = run(runner, PLACE,
                     {"component": "bar", "at": [0, 0, 300], "turn_axis": "x", "turn_deg": 90})
        assert placed["placed_at_mm"] == pytest.approx([0.0, 0.0, 300.0])
        assert placed["turned"] == "90 degrees about x"


@needs_kernel
class TestTheClashCheck:
    def _two_bars(self, runner: Any, second_at: list[float]) -> dict[str, Any]:
        build_bar(runner, "bar")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "bar"})
        run(runner, PLACE, {"component": "bar", "at": [0, 0, 0]})
        run(runner, PLACE, {"component": "bar", "at": second_at})
        return run(runner, CLASH, {"clearance_mm": 100.0})

    def test_overlapping_parts_are_reported_with_the_volume_they_share(
        self, runner: Any
    ) -> None:
        # Sections span x in [-20, 20] and [0, 40]; the shared block is 20 x 40 x 100.
        report = self._two_bars(runner, [20.0, 0.0, 0.0])
        assert report["interferes"] is True
        assert report["clash_count"] == 1
        assert report["interference_volume_mm3"] == pytest.approx(80_000.0, rel=1e-6)

    def test_a_gap_is_measured_and_the_check_says_it_looked_at_everything(
        self, runner: Any
    ) -> None:
        report = self._two_bars(runner, [80.0, 0.0, 0.0])
        assert report["interferes"] is False
        assert report["complete"] is True
        assert report["minimum_clearance_mm"] == pytest.approx(40.0, rel=1e-6)
        assert report["unchecked"] == []

    def test_restricting_the_check_excludes_pairs_by_name_rather_than_hiding_them(
        self, runner: Any
    ) -> None:
        """A filter that made pairs vanish would be the lie `app/assembly/clash.py` exists against."""
        build_bar(runner, "bar")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "bar"})
        build_bar(runner, "shim", section_mm=10.0, length_mm=10.0)
        run(runner, COMPONENT, {"name": "shim"})
        for at in ([0, 0, 0], [20, 0, 0]):
            run(runner, PLACE, {"component": "bar", "at": at})
        run(runner, PLACE, {"component": "shim", "at": [500, 0, 0]})

        report = run(runner, CLASH, {"components": ["shim"]})
        assert report["pair_count"] == 3
        assert report["excluded_pair_count"] == 1
        assert report["excluded"][0]["reason"].startswith("neither part is among")
        assert report["clash_count"] == 0, "the bar-on-bar clash was excluded, not measured"

    def test_a_component_with_no_geometry_is_an_unchecked_pair_and_not_a_pass(
        self, runner: Any
    ) -> None:
        """A component with no shape is not a component with no clashes.

        Reached by breaking the thing the guard protects: the shape is removed from the
        recorded component after it was placed, which is the state an evicted or failed
        build would leave behind.
        """
        build_bar(runner, "bar")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "bar"})
        build_bar(runner, "shim", section_mm=10.0, length_mm=10.0)
        run(runner, COMPONENT, {"name": "shim"})
        run(runner, PLACE, {"component": "bar"})
        run(runner, PLACE, {"component": "shim", "at": [0, 0, 0]})

        state = runner._context.assembly
        state.documents["shim"]._bodies[state.documents["shim"].active_body] = None

        report = run(runner, CLASH, {})
        assert report["complete"] is False
        assert report["unchecked_pair_count"] == 1
        reason = report["unchecked"][0]["reason"]
        assert "shim" in reason
        # The filter in `_shapes` is what buys this sentence: a component with no shape
        # is *left out* of the map, so the clash module's own KeyError explains itself.
        # Passing a null shape through instead produces an OCCT type name and no advice.
        assert "a component with no geometry is not a component with no clashes" in reason
        assert "minimum_clearance_mm" not in report, (
            "an incomplete check must not publish a minimum, which over-estimates room"
        )


@needs_kernel
class TestTheMassRollup:
    def test_four_legs_weigh_four_legs_and_the_centre_is_where_it_should_be(
        self, runner: Any
    ) -> None:
        # 40 x 40 x 100 mm of 1018 steel (7870 kg/m3) is 1.2592 kg; four of them, on a
        # 200 mm square, put the centre of gravity at the middle of the square.
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        for x, y in ((0, 0), (200, 0), (0, 200), (200, 200)):
            run(runner, PLACE, {"component": "leg", "at": [x, y, 0]})

        rollup = run(runner, ANALYSIS, {"kind": "mass"})
        assert rollup["complete"] is True
        assert rollup["mass_kg"] == pytest.approx(5.0368, rel=1e-6)
        assert rollup["centre_of_mass_mm"] == pytest.approx([100.0, 100.0, 50.0], abs=1e-6)
        assert rollup["by_component"] == {"leg": pytest.approx(5.0368, rel=1e-6)}

    def test_a_component_with_no_material_leaves_the_assembly_with_no_mass(
        self, runner: Any
    ) -> None:
        """Breaking the thing the roll-up guards: one part with a volume and no density.

        The wrong answer available here is a *lighter* machine, which is the direction
        every mass budget passes in. So there is no `mass_kg` at all, the partial sum
        goes out under `measured_mass_kg`, and the occurrence that could not be weighed
        is named.
        """
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        build_bar(runner, "shim", section_mm=10.0, length_mm=10.0, material=None)
        run(runner, COMPONENT, {"name": "shim"})
        run(runner, PLACE, {"component": "leg"})
        run(runner, PLACE, {"component": "shim", "at": [200, 0, 0]})

        rollup = run(runner, ANALYSIS, {"kind": "mass"})
        assert rollup["complete"] is False
        assert "mass_kg" not in rollup
        assert rollup["measured_mass_kg"] == pytest.approx(1.2592, rel=1e-6)
        assert [m["component"] for m in rollup["missing"]] == ["shim"]
        assert "density" in rollup["missing"][0]["reason"]

    def test_one_component_can_be_weighed_on_its_own(self, runner: Any) -> None:
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        run(runner, PLACE, {"component": "leg"})
        run(runner, PLACE, {"component": "leg", "at": [200, 0, 0]})

        one = run(runner, ANALYSIS, {"kind": "mass", "component": "leg"})
        assert one["mass_kg"] == pytest.approx(1.2592, rel=1e-6)
        assert one["occurrences_of_component"] == 2


# -- the refusals -------------------------------------------------------------


@needs_kernel
class TestWhatIsRefusedAndWhy:
    """Every refusal names what to do next; a bare failure reads to an agent as a fault
    in the part, and it will damage a good one trying to fix it."""

    def test_an_assembly_tool_before_there_is_an_assembly(self, runner: Any) -> None:
        build_bar(runner, "leg")
        with pytest.raises(GeometryError, match=PRODUCT_CREATE):
            run(runner, COMPONENT, {"name": "leg"})

    def test_a_second_assembly_is_refused_rather_than_replacing_the_first(
        self, runner: Any
    ) -> None:
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        with pytest.raises(GeometryError, match="already composing"):
            run(runner, PRODUCT_CREATE, {"name": "chassis"})
        assert run(runner, BOM, {})["components"] == ["leg"]

    def test_recording_a_part_that_has_no_geometry(self, runner: Any) -> None:
        run(runner, "catia_new_part", {"name": "empty"})
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        with pytest.raises(GeometryError, match="no geometry"):
            run(runner, COMPONENT, {"name": "empty"})
        assert runner.document is not None, "a refused hand-off must not close the part"

    def test_recording_a_component_under_the_assemblys_own_name(self, runner: Any) -> None:
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        with pytest.raises(GeometryError, match="assembly's own name"):
            run(runner, COMPONENT, {"name": "frame"})

    def test_placing_a_component_that_was_never_recorded(self, runner: Any) -> None:
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        with pytest.raises(GeometryError, match="Recorded so far: leg"):
            run(runner, PLACE, {"component": "rail"})

    def test_a_turn_with_no_axis_to_turn_about(self, runner: Any) -> None:
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        with pytest.raises(GeometryError, match="turn_axis"):
            run(runner, PLACE, {"component": "leg", "turn_deg": 90})

    @pytest.mark.parametrize(
        "kind", ["constraints", "degrees_of_freedom", "broken_links", "dependencies"]
    )
    def test_an_analysis_this_backend_cannot_answer_says_why(
        self, runner: Any, kind: str
    ) -> None:
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        run(runner, PLACE, {"component": "leg"})
        with pytest.raises(GeometryError) as raised:
            run(runner, ANALYSIS, {"kind": kind})
        assert "kind='mass' is" in str(raised.value)
        assert len(str(raised.value)) > 80, "a refusal with no reason in it"

    def test_a_clearance_check_with_no_clearance_asked_for(self, runner: Any) -> None:
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        run(runner, PLACE, {"component": "leg"})
        with pytest.raises(GeometryError, match="clearance_mm"):
            run(runner, CLASH, {"kind": "clearance"})

    @pytest.mark.parametrize(
        ("tool", "arguments"), [(CLASH, {}), (ANALYSIS, {"kind": "mass"})]
    )
    def test_a_whole_assembly_question_before_anything_is_placed(
        self, runner: Any, tool: str, arguments: dict[str, Any]
    ) -> None:
        """Break the guard and the walk answers about the root as its own leaf occurrence.

        `ProductStructure` treats a component with no instances as a leaf, so an empty
        assembly walks as one occurrence — *itself* — and a clash check would go looking
        for the geometry of a part called `frame`. The refusal is what stops the report
        being about something that does not exist.
        """
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        with pytest.raises(GeometryError, match="nothing has been placed"):
            run(runner, tool, arguments)


@needs_kernel
class TestNames:
    """Normalised before refused — and refused when normalising cannot save them."""

    def test_the_name_a_model_types_is_lowercased_rather_than_refused(
        self, runner: Any
    ) -> None:
        build_bar(runner, "leg")
        assert run(runner, PRODUCT_CREATE, {"name": "Frame Table"})["assembly"] == (
            "frame_table"
        )
        assert run(runner, COMPONENT, {"name": "Front-Leg"})["component"] == "front_leg"

    def test_the_normalised_name_is_what_a_later_reference_resolves_against(
        self, runner: Any
    ) -> None:
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "Frame"})
        run(runner, COMPONENT, {"name": "Front Leg"})
        assert run(runner, PLACE, {"component": "Front Leg"})["occurrence"] == (
            "frame/front_leg.1"
        )

    @pytest.mark.parametrize("name", ["9leg", "leg.1", "top"])
    def test_a_name_normalising_cannot_save_is_refused_with_the_reason(
        self, runner: Any, name: str
    ) -> None:
        """`top` is in `app.design.names.RESERVED` — an operation could not tell the
        component from the named face of the same spelling.

        The second assertion is the one with teeth. `ProductStructure` validates names
        too, so dropping the check in `_segment` still fails the call — but only *after*
        the part has been taken and filed under the bad name, leaving an assembly that
        cannot be walked at all. Refusing at the argument is what keeps a bad name from
        reaching the state.
        """
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        with pytest.raises(GeometryError, match=name.replace(".", r"\.")):
            run(runner, COMPONENT, {"name": name})
        assert run(runner, BOM, {})["components"] == []
        assert runner.document is not None, "a refused name must not close the part either"


@needs_kernel
class TestRecordingAComponentTwice:
    def test_the_second_recording_replaces_the_definition_for_every_instance(
        self, runner: Any
    ) -> None:
        """The stated recovery for "I need to change a component I have already recorded".

        One component, many instances, so replacing the definition has to reach all of
        them — that is the whole point of the graph, and a replacement that only reached
        the newest placement would be the forty-place edit done in thirty-nine.
        """
        build_bar(runner, "leg", length_mm=100.0)
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        run(runner, PLACE, {"component": "leg"})
        run(runner, PLACE, {"component": "leg", "at": [200, 0, 0]})
        assert run(runner, ANALYSIS, {"kind": "mass"})["mass_kg"] == pytest.approx(
            2.5184, rel=1e-6
        )

        build_bar(runner, "leg", length_mm=200.0)
        replaced = run(runner, COMPONENT, {"name": "leg"})
        assert replaced["replaced"] is True
        assert replaced["placed"] == 2
        assert run(runner, ANALYSIS, {"kind": "mass"})["mass_kg"] == pytest.approx(
            5.0368, rel=1e-6
        )


@needs_kernel
class TestARefusedPlacementLeavesNothingBehind:
    def test_a_placement_the_structure_rejects_is_rolled_back(
        self, runner: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The structure is what validates a placement, so a refused one must not persist.

        `_segment` catches every malformed name before it gets this far, so the rollback
        is reached here by making `_structure` refuse — the state a future validation
        rule would produce. Left in, the *next* call would fail on a placement the agent
        did not just make, and name a fault it cannot find.
        """
        build_bar(runner, "leg")
        run(runner, PRODUCT_CREATE, {"name": "frame"})
        run(runner, COMPONENT, {"name": "leg"})
        run(runner, PLACE, {"component": "leg"})

        def refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise GeometryError("the structure refuses this")

        monkeypatch.setattr(ops, "_structure", refuse)
        with pytest.raises(GeometryError, match="refuses this"):
            run(runner, PLACE, {"component": "leg", "at": [200, 0, 0]})
        monkeypatch.undo()

        assert run(runner, BOM, {})["occurrence_count"] == 1
        assert run(runner, PLACE, {"component": "leg", "at": [200, 0, 0]})["occurrence"] == (
            "frame/leg.2"
        ), "the rolled-back placement consumed an occurrence number it never used"
