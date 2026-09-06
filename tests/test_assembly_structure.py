"""Master plan 14.1 — the product structure is a graph, and its paths are stable.

Two properties are load-bearing here and everything else in this file is in service of
them.

**A bolt used forty times is one component and forty occurrences.** If that is wrong,
the failure is not a wrong answer, it is a machine that will not fit in memory and an
edit that has to be made forty times. `TestTheGraphIsNotATreeOfCopies` measures it
directly: one `Component` object, forty paths, and a million-leaf structure that reports
its size without enumerating a single occurrence.

**An occurrence path survives an edit upstream of it.** This is the topological naming
problem one level up. `TestPathsSurviveAnEdit` inserts a leg at the *head* of the list
and asserts that `frame/leg.2` is still the same leg at the same place — which it is
only because the index is declared rather than derived from position. The test computes
what a positional scheme would have produced and asserts it is different, so the
property is pinned against the implementation it replaced rather than against itself.

Offline: no database fixture, no network, no OCCT. Everything here is arithmetic on
plain tuples.
"""

from __future__ import annotations

import math
import time

import pytest

from app.assembly.errors import StructureError
from app.assembly.placement import at, turned
from app.assembly.structure import (
    Component,
    Instance,
    ProductStructure,
    StructureBuilder,
    spread,
)

# -- fixtures: a frame with three legs, each carrying a bracket and two bolts --


def _bolted_frame(leg_positions=((0.0, 0.0), (100.0, 0.0), (0.0, 100.0))) -> ProductStructure:
    bolt = Component(name="bolt", design="ISO 4762 M6x20", material="steel")
    bracket = Component(
        name="bracket",
        design="bracket_spec",
        instances=spread("bolt", "bolt", [at(0.0, 0.0, 0.0), at(20.0, 0.0, 0.0)]),
    )
    leg = Component(
        name="leg",
        instances=(Instance(component="bracket", tag="bracket", index=1, placement=at(0, 0, 50)),),
    )
    frame = Component(
        name="frame",
        instances=spread("leg", "leg", [at(x, y, 0.0) for x, y in leg_positions]),
    )
    return ProductStructure(root="frame", components=[frame, leg, bracket, bolt])


class TestTheGraphIsNotATreeOfCopies:
    def test_one_component_serves_every_occurrence(self) -> None:
        structure = _bolted_frame()

        assert len(structure) == 4  # frame, leg, bracket, bolt — four definitions
        assert structure.occurrence_count() == 6  # three legs x two bolts

        # The identity check, not merely the count: every bolt occurrence resolves to
        # the *same* Component object. A tree of copies would give six.
        components = {
            id(structure.component(o.component)) for o in structure.occurrences()
        }
        assert len(components) == 1

    def test_forty_bolts_are_forty_occurrences_of_one_definition(self) -> None:
        bolt = Component(name="bolt")
        plate = Component(
            name="plate",
            instances=spread("bolt", "bolt", [at(10.0 * n, 0.0, 0.0) for n in range(40)]),
        )
        structure = ProductStructure(root="plate", components=[plate, bolt])

        assert structure.occurrence_count() == 40
        assert structure.quantities()["bolt"] == 40
        assert len(structure.bill_of_materials()) == 1
        assert structure.bill_of_materials()[0].quantity == 40
        assert len(structure) == 2

    def test_size_is_answered_without_enumerating(self) -> None:
        """A million leaves counted by multiplying over the graph, not by walking it.

        Four children, ten levels deep: 4^10 = 1,048,576 leaf occurrences. Enumerating
        them takes minutes; multiplying over eleven components takes microseconds. The
        wall-clock assertion is deliberately loose — it is testing that the algorithm is
        the graph one, not that this machine is fast.
        """
        components = [Component(name="level10")]
        for depth in range(9, -1, -1):
            components.append(
                Component(
                    name=f"level{depth}",
                    instances=spread(
                        f"level{depth + 1}",
                        "child",
                        [at(float(n), 0.0, 0.0) for n in range(4)],
                    ),
                )
            )
        structure = ProductStructure(root="level0", components=components)

        started = time.perf_counter()
        assert structure.occurrence_count() == 4**10
        assert time.perf_counter() - started < 1.0

    def test_a_dag_is_allowed_and_counted_correctly(self) -> None:
        """Two sub-assemblies sharing one bracket is one bracket, counted twice.

        Named `left_arm` rather than `left` on purpose: `left` is in the operation
        vocabulary and `_check_segment` refuses it, which is the inherited refusal
        `test_a_name_from_the_operation_vocabulary_is_refused` pins.
        """
        bracket = Component(name="bracket")
        left = Component(name="left_arm", instances=spread("bracket", "bracket", [at()]))
        right = Component(
            name="right_arm", instances=spread("bracket", "bracket", [at(), at(50.0)])
        )
        machine = Component(
            name="machine",
            instances=(
                Instance(component="left_arm", tag="arm", index=1),
                Instance(component="right_arm", tag="arm", index=2),
            ),
        )
        structure = ProductStructure(
            root="machine", components=[machine, left, right, bracket]
        )

        assert structure.quantities()["bracket"] == 3
        assert structure.used_by("bracket") == ("left_arm", "right_arm")
        assert len(structure) == 4
        # One tag, two components: `arm.1` and `arm.2` are different things, and the
        # path says which without the component name having to appear in it.
        assert [o.path for o in structure.occurrences()] == [
            "machine/arm.1/bracket.1",
            "machine/arm.2/bracket.1",
            "machine/arm.2/bracket.2",
        ]


class TestPathsSurviveAnEdit:
    def test_inserting_a_leg_at_the_head_renumbers_nothing(self) -> None:
        before = _bolted_frame()
        original = before.occurrence("frame/leg.2/bracket.1/bolt.2")

        frame = before.component("frame")
        inserted = Instance(
            component="leg",
            tag="leg",
            index=frame.next_index("leg"),
            placement=at(-100.0, 0.0, 0.0),
        )
        # Deliberately at the HEAD of the list — the position a positional scheme
        # renumbers from.
        after = ProductStructure(
            root="frame",
            components=[
                frame.with_instances((inserted, *frame.instances)),
                before.component("leg"),
                before.component("bracket"),
                before.component("bolt"),
            ],
        )

        moved = after.occurrence("frame/leg.2/bracket.1/bolt.2")
        assert moved.frame.origin_mm == original.frame.origin_mm
        assert moved.component == original.component

        # And the pinning half: a positional scheme *would* have moved it. The new leg
        # sits at list position 0, so `leg.2` positionally is what used to be `leg.1`.
        positional = after.component("frame").instances[1]
        assert positional.index == 1  # declaration order 2nd, declared index 1
        assert positional.placement.origin_mm == (0.0, 0.0, 0.0)

    def test_a_path_still_resolves_after_a_leg_is_deleted(self) -> None:
        """Deleting `leg.2` must not promote `leg.3` into its path."""
        before = _bolted_frame()
        frame = before.component("frame")
        kept = tuple(i for i in frame.instances if i.index != 2)
        after = ProductStructure(
            root="frame",
            components=[
                frame.with_instances(kept),
                before.component("leg"),
                before.component("bracket"),
                before.component("bolt"),
            ],
        )

        assert after.occurrence("frame/leg.3/bracket.1/bolt.1").frame.origin_mm == (
            0.0,
            100.0,
            50.0,
        )
        with pytest.raises(StructureError, match="leg.2"):
            after.occurrence("frame/leg.2/bracket.1/bolt.1")

    def test_next_index_does_not_reuse_a_deleted_number(self) -> None:
        component = Component(
            name="rail",
            instances=(
                Instance(component="bolt", tag="bolt", index=1),
                Instance(component="bolt", tag="bolt", index=3),
            ),
        )
        assert component.next_index("bolt") == 4
        assert component.next_index("clip") == 1


class TestTheStructureRefusesWhatItCannotWalk:
    def test_two_instances_with_the_same_path_segment_are_refused(self) -> None:
        """The guard, broken: two `leg.2`s make every reference to `leg.2` a coin flip."""
        with pytest.raises(StructureError, match=r"both 'leg\.2'"):
            Component(
                name="frame",
                instances=(
                    Instance(component="leg", tag="leg", index=2, placement=at(0.0)),
                    Instance(component="leg", tag="leg", index=2, placement=at(500.0)),
                ),
            )

    def test_a_component_that_contains_itself_is_refused_by_name(self) -> None:
        with pytest.raises(StructureError, match="frame -> leg -> frame"):
            ProductStructure(
                root="frame",
                components=[
                    Component(name="frame", instances=spread("leg", "leg", [at()])),
                    Component(name="leg", instances=spread("frame", "frame", [at()])),
                ],
            )

    def test_an_unreachable_cycle_is_still_refused(self) -> None:
        """A cycle nobody instances is still a hang waiting for a re-root."""
        with pytest.raises(StructureError, match="contains itself"):
            ProductStructure(
                root="plate",
                components=[
                    Component(name="plate"),
                    Component(name="a", instances=spread("b", "b", [at()])),
                    Component(name="b", instances=spread("a", "a", [at()])),
                ],
            )

    def test_an_instance_of_an_undefined_component_names_what_is_defined(self) -> None:
        with pytest.raises(StructureError, match="Defined: frame, leg"):
            ProductStructure(
                root="frame",
                components=[
                    Component(name="frame", instances=spread("leg", "leg", [at()])),
                    Component(name="leg", instances=spread("bolt", "bolt", [at()])),
                ],
            )

    def test_two_components_of_the_same_name_are_refused(self) -> None:
        with pytest.raises(StructureError, match="both called 'leg'"):
            ProductStructure(
                root="leg", components=[Component(name="leg"), Component(name="leg")]
            )

    def test_a_missing_root_names_what_exists(self) -> None:
        with pytest.raises(StructureError, match="Defined: leg"):
            ProductStructure(root="frame", components=[Component(name="leg")])

    def test_a_dotted_component_name_is_refused_because_a_path_would_be_ambiguous(
        self,
    ) -> None:
        with pytest.raises(StructureError, match="contains a dot"):
            Component(name="press.frame")

    def test_a_name_from_the_operation_vocabulary_is_refused(self) -> None:
        """`top` and `left` collide with the vocabulary; names.py owns the refusal.

        Inherited rather than re-decided here. It is a real constraint on the author —
        an assembly's two halves cannot be called `left` and `right` — and the message
        says what to call them instead, which is the register the repository's error
        convention asks for.
        """
        for reserved in ("top", "left", "xy"):
            with pytest.raises(StructureError, match="operation vocabulary"):
                Component(name=reserved)

    def test_an_occurrence_number_below_one_is_refused(self) -> None:
        with pytest.raises(StructureError, match="whole number from 1"):
            Instance(component="bolt", tag="bolt", index=0)

    def test_a_placement_that_is_not_a_frame_is_refused(self) -> None:
        with pytest.raises(StructureError, match="pose.Frame"):
            Instance(component="bolt", tag="bolt", placement=(0.0, 0.0, 10.0))  # type: ignore[arg-type]

    def test_a_path_that_does_not_start_at_the_root_is_refused(self) -> None:
        structure = _bolted_frame()
        with pytest.raises(StructureError, match="Paths are absolute"):
            structure.occurrence("leg.1/bracket.1")


class TestPlacementsComposeDownTheChain:
    def test_a_translation_chain_adds_up(self) -> None:
        structure = _bolted_frame()
        # frame at origin, leg.2 at (100,0,0), bracket.1 at (0,0,50), bolt.2 at (20,0,0)
        assert structure.occurrence("frame/leg.2/bracket.1/bolt.2").frame.origin_mm == (
            120.0,
            0.0,
            50.0,
        )

    def test_a_rotated_sub_assembly_carries_its_children_round(self) -> None:
        """A leg turned 90 degrees about Z puts its +X bolt on +Y, offset and all.

        Checked against the closed form rather than against recorded output: the bolt is
        at local (20, 0, 50) inside a leg that is rotated a quarter turn about Z and then
        moved to (100, 0, 0), so it lands at (100, 20, 50).
        """
        bolt = Component(name="bolt")
        leg = Component(
            name="leg", instances=spread("bolt", "bolt", [at(20.0, 0.0, 50.0)])
        )
        frame = Component(
            name="frame",
            instances=(
                Instance(
                    component="leg",
                    tag="leg",
                    index=1,
                    # Rotate about Z, then translate: compose(at, turned) is exactly that.
                    placement=_placed(100.0, 0.0, 0.0, math.pi / 2),
                ),
            ),
        )
        structure = ProductStructure(root="frame", components=[frame, leg, bolt])

        x, y, z = structure.occurrence("frame/leg.1/bolt.1").frame.origin_mm
        assert (x, y, z) == pytest.approx((100.0, 20.0, 50.0), abs=1e-12)

    def test_the_walk_and_the_direct_resolve_agree(self) -> None:
        structure = _bolted_frame()
        walked = {o.path: o.frame.origin_mm for o in structure.occurrences()}
        for path, origin in walked.items():
            assert structure.occurrence(path).frame.origin_mm == origin


def _placed(x: float, y: float, z: float, angle_rad: float):
    from app.assembly.placement import compose

    return compose(at(x, y, z), turned((0.0, 0.0, 1.0), angle_rad))


class TestWhereUsedAndTheBillOfMaterials:
    def test_used_by_is_graph_level_and_paths_of_is_occurrence_level(self) -> None:
        structure = _bolted_frame()

        assert structure.used_by("bolt") == ("bracket",)
        assert structure.paths_of("bracket") == (
            "frame/leg.1/bracket.1",
            "frame/leg.2/bracket.1",
            "frame/leg.3/bracket.1",
        )

    def test_the_bom_carries_the_designation_and_the_quantity(self) -> None:
        lines = {line.component: line for line in _bolted_frame().bill_of_materials()}

        assert lines["bolt"].quantity == 6
        assert lines["bolt"].design == "ISO 4762 M6x20"
        assert lines["bolt"].material == "steel"
        assert "frame" not in lines  # a product is not a line item of itself
        assert "bracket" not in lines  # not a leaf; leaves_only is the buy/make list

    def test_the_indented_bom_includes_sub_assemblies(self) -> None:
        lines = {
            line.component: line.quantity
            for line in _bolted_frame().bill_of_materials(leaves_only=False)
        }
        assert lines == {"leg": 3, "bracket": 3, "bolt": 6}

    def test_a_component_nobody_uses_is_reported_rather_than_ignored(self) -> None:
        structure = ProductStructure(
            root="frame",
            components=[
                Component(name="frame", instances=spread("leg", "leg", [at()])),
                Component(name="leg"),
                Component(name="spare"),
            ],
        )
        assert structure.orphans() == ("spare",)
        assert "never used: spare" in structure.summary()


class TestPersistence:
    def test_a_structure_round_trips_through_its_dictionary_form(self) -> None:
        before = _bolted_frame()
        after = ProductStructure.from_dict(before.to_dict())

        assert after.digest() == before.digest()
        assert [o.path for o in after.occurrences()] == [
            o.path for o in before.occurrences()
        ]
        assert after.occurrence("frame/leg.3/bracket.1/bolt.2").frame.origin_mm == (
            20.0,
            100.0,
            50.0,
        )

    def test_a_rotated_placement_round_trips_exactly(self) -> None:
        rotated = Component(
            name="frame",
            instances=(
                Instance(
                    component="leg",
                    tag="leg",
                    placement=turned((0.0, 0.0, 1.0), math.pi / 3),
                ),
            ),
        )
        before = ProductStructure(
            root="frame", components=[rotated, Component(name="leg")]
        )
        after = ProductStructure.from_dict(before.to_dict())

        assert (
            after.component("frame").instances[0].placement.rotation
            == rotated.instances[0].placement.rotation
        )

    def test_the_digest_moves_when_a_placement_moves(self) -> None:
        before = _bolted_frame()
        after = _bolted_frame(leg_positions=((0.0, 0.0), (100.5, 0.0), (0.0, 100.0)))
        assert before.digest() != after.digest()

    def test_an_unknown_format_version_is_refused_rather_than_guessed_at(self) -> None:
        data = _bolted_frame().to_dict()
        data["format_version"] = 99
        with pytest.raises(StructureError, match="Refusing to guess"):
            ProductStructure.from_dict(data)

    def test_an_unknown_key_is_refused_rather_than_dropped(self) -> None:
        data = _bolted_frame().to_dict()
        data["components"][0]["effectivity"] = "from serial 400"
        with pytest.raises(StructureError, match="unknown keys"):
            ProductStructure.from_dict(data)


class TestTheBuilder:
    def test_it_allocates_occurrence_numbers_and_never_reuses_one(self) -> None:
        builder = StructureBuilder()
        builder.define("frame")
        builder.define("leg")
        for x in (0.0, 100.0, 200.0):
            builder.add("frame", "leg", placement=at(x, 0.0, 0.0))
        structure = builder.build("frame")

        assert [o.path for o in structure.occurrences()] == [
            "frame/leg.1",
            "frame/leg.2",
            "frame/leg.3",
        ]

    def test_defining_a_component_twice_is_refused(self) -> None:
        builder = StructureBuilder()
        builder.define("leg")
        with pytest.raises(StructureError, match="already defined"):
            builder.define("leg")
