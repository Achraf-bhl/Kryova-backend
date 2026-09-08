"""The element strategy, and the deck it writes — master plan 6.3.

Offline and instant: writing a deck is string generation, so nothing here needs
`ccx`. That cuts both ways and is stated rather than glossed — **none of this has
been round-tripped through the real solver**, so what these tests pin is that the
deck says what `app/solve/calculix/elements.py` claims the manual says, and that
the three quiet failures below cannot happen. Whether ccx agrees is the first
Windows run's measurement.

The three quiet failures, which are what the whole task is about:

1. **A shell or beam result read off the expanded model.** ccx builds a solid out
   of the element before solving, and by default writes the results at the nodes
   of that solid — more numerous than the mesh submitted, all of them in range.
   `frd.displacements(frd, mesh.node_count)` would read a different model's
   answer with nothing to complain about. `OUTPUT=2D` is the whole of the fix.
2. **A clamp that is really a pin.** A solid node has three degrees of freedom
   and a shell or beam node has six. A `clamp` writing only 1-3 on a beam leaves
   the end free to rotate, and a cantilever on a pin is a different structure —
   with a single element, not a solvable one at all.
3. **A section of the wrong kind, or pointing along its own member.** Neither is
   a parse error; the second makes the local frame degenerate and the profile
   has nowhere to point.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.mesh.structural import BeamMesh, ShellMesh
from app.solve.calculix.deck import write_frame_deck, write_frame_model
from app.solve.calculix.elements import (
    BEAM,
    EXPANSIONS,
    OUTPUT_REQUEST,
    SHELL,
    SOLID,
    choose_element,
    describe,
    element_rows,
    output_request_lines,
    require_orientation,
    require_section_for,
    section_lines,
)
from app.solve.constraints import (
    SOLID_DOFS,
    STRUCTURAL_DOFS,
    check_restraints,
    local_dofs,
)
from app.solve.materials import MATERIALS
from app.solve.sections import BeamSection, BoxProfile, ShellSection
from app.solve.types import BoxSelector, FaceSelector, Fixture, SolverError

STEEL = MATERIALS["steel-1018"]
RHS = BeamSection(
    profile=BoxProfile(width_mm=60.0, height_mm=40.0, wall_mm=4.0),
    n1=(0.0, 1.0, 0.0),
)
PLATE = ShellSection(thickness_mm=1.5)


def _beam(segments: int = 4, length: float = 800.0) -> BeamMesh:
    xs = np.linspace(0.0, length, segments + 1)
    nodes = np.stack([xs, np.zeros_like(xs), np.zeros_like(xs)], axis=1)
    return BeamMesh(nodes=nodes, segments=np.array([[i, i + 1] for i in range(segments)]))


def _shell(quadratic: bool = False) -> ShellMesh:
    nodes = np.array(
        [
            [0.0, 0.0, 0.0],
            [50.0, 0.0, 0.0],
            [50.0, 40.0, 0.0],
            [0.0, 40.0, 0.0],
        ]
    )
    faces = np.array([[0, 1, 2, 3]])
    if not quadratic:
        return ShellMesh(nodes=nodes, faces=faces)
    midside_nodes = np.array(
        [[25.0, 0.0, 0.0], [50.0, 20.0, 0.0], [25.0, 40.0, 0.0], [0.0, 20.0, 0.0]]
    )
    return ShellMesh(
        nodes=np.vstack([nodes, midside_nodes]),
        faces=faces,
        midside=np.array([[4, 5, 6, 7]]),
    )


def _clamped_end() -> list[Fixture]:
    return [Fixture(where=FaceSelector(type="face", axis="x", side="min"), kind="clamp")]


def _rows(deck: str, keyword: str) -> list[str]:
    """Data rows under one card, up to the next one."""
    rows: list[str] = []
    collecting = False
    for line in deck.splitlines():
        if line.startswith("*"):
            if collecting:
                break
            collecting = line.upper().startswith(keyword.upper())
            continue
        if collecting:
            rows.append(line.strip())
    return rows


class TestTheElementIsChosenAndTheReasonIsCarried:
    def test_a_linear_solid_is_c3d4_and_a_quadratic_one_is_c3d10(self) -> None:
        mesh = box_mesh((10.0, 10.0, 10.0), divisions=(1, 1, 1))

        assert choose_element(mesh).calculix_type == "C3D4"
        assert choose_element(promote_to_tet10(mesh)).calculix_type == "C3D10"

    def test_shells_map_onto_the_four_shell_elements(self) -> None:
        triangle = ShellMesh(nodes=np.zeros((3, 3)) + np.eye(3), faces=np.array([[0, 1, 2]]))

        assert choose_element(triangle).calculix_type == "S3"
        assert choose_element(_shell()).calculix_type == "S4"
        assert choose_element(_shell(quadratic=True)).calculix_type == "S8R"

    def test_beams_map_onto_b31_and_b32(self) -> None:
        linear = _beam()
        quadratic = BeamMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [5.0, 0.0, 0.0]]),
            segments=np.array([[0, 1]]),
            midside=np.array([[2]]),
        )

        assert choose_element(linear).calculix_type == "B31"
        assert choose_element(quadratic).calculix_type == "B32"

    def test_the_family_is_named_and_not_inferred_from_the_type_string(self) -> None:
        assert choose_element(_beam()).family == BEAM
        assert choose_element(_shell()).family == SHELL
        assert choose_element(box_mesh((1.0, 1.0, 1.0), divisions=(1, 1, 1))).family == SOLID

    def test_every_choice_carries_a_reason_a_user_could_read(self) -> None:
        """Decision 3 binds a result to what produced it, and "which element" is
        part of that: a frequency computed on shells and one computed on solids
        are not the same claim about the same part. A reason living only in a
        comment cannot reach a report."""
        for mesh in (
            box_mesh((1.0, 1.0, 1.0), divisions=(1, 1, 1)),
            _shell(),
            _shell(quadratic=True),
            _beam(),
        ):
            choice = choose_element(mesh)
            assert len(choice.reason) > 20
            assert choice.calculix_type in describe(choice, STEEL)
            assert STEEL.name in describe(choice, STEEL)

    def test_a_solid_mesh_is_not_silently_promoted_to_the_recommended_element(self) -> None:
        """C3D10 is what the manual recommends and C3D4 is what a linear mesh
        *is*. Promoting would answer a different question, and without midside
        nodes there is nothing to promote it with."""
        assert choose_element(box_mesh((1.0, 1.0, 1.0), divisions=(1, 1, 1))).calculix_type == (
            "C3D4"
        )


class TestWhatCalculixDoesWithAShellOrABeam:
    """The expansion — the fact the master plan singles out as the surprise."""

    def test_every_expanded_element_has_an_expansion_recorded(self) -> None:
        for name in ("S3", "S4", "S6", "S8R", "B31", "B32"):
            assert name in EXPANSIONS

    def test_a_solid_is_not_expanded(self) -> None:
        """It is already a solid. A note that fired on everything would be a note
        nobody reads."""
        solid = choose_element(box_mesh((1.0, 1.0, 1.0), divisions=(1, 1, 1)))

        assert solid.expansion is None
        assert not solid.expanded
        assert solid.through_thickness_note() == ""

    def test_the_documented_pairs_are_the_ones_recorded(self) -> None:
        """The two the master plan names by hand, so a table edit that lost them
        is caught by the plan's own words rather than by the table agreeing with
        itself."""
        assert EXPANSIONS["S8R"].into == "C3D20R"
        assert EXPANSIONS["S8R"].nodes == 20
        assert EXPANSIONS["B31"].into == "C3D8I"
        assert EXPANSIONS["B31"].nodes == 8

    def test_an_expanded_element_warns_about_stress_through_the_thickness(self) -> None:
        """The sentence has to say all three things: what it becomes, that it is
        one element deep, and what to do instead. A warning that only names the
        element leaves the reader with a fact and no action."""
        note = choose_element(_shell(quadratic=True)).through_thickness_note()

        assert "S8R" in note
        assert "C3D20R" in note
        assert "one element" in note
        assert "solid" in note

    def test_a_shell_or_beam_node_carries_six_degrees_of_freedom(self) -> None:
        assert choose_element(_shell()).dofs_per_node == STRUCTURAL_DOFS
        assert choose_element(_beam()).dofs_per_node == STRUCTURAL_DOFS
        assert choose_element(
            box_mesh((1.0, 1.0, 1.0), divisions=(1, 1, 1))
        ).dofs_per_node == SOLID_DOFS


class TestTheResultsAreAskedForAtTheNodesThatWereSubmitted:
    """Quiet failure 1, and the one with no symptom at all.

    The expanded model has more nodes than the mesh that was submitted and every
    number in the `.frd` is a plausible displacement of a real node — just not of
    the node the caller thinks. There is no length mismatch to catch it: `frd.py`
    is handed `mesh.node_count` and takes that many.
    """

    def test_an_expanded_element_asks_for_two_dimensional_output(self) -> None:
        lines = output_request_lines(choose_element(_beam()), node="U", element="S")

        assert lines[0] == f"*NODE FILE, {OUTPUT_REQUEST}"
        assert lines[2] == f"*EL FILE, {OUTPUT_REQUEST}"

    def test_a_solid_asks_for_nothing_extra(self) -> None:
        """`OUTPUT=2D` on a solid is meaningless — there is no original 1-D or
        2-D element to map back to — and writing it would be a keyword ccx has
        to make sense of for no reason."""
        lines = output_request_lines(
            choose_element(box_mesh((1.0, 1.0, 1.0), divisions=(1, 1, 1))),
            node="U",
            element="S",
        )

        assert lines[0] == "*NODE FILE"
        assert OUTPUT_REQUEST not in "\n".join(lines)

    def test_the_frame_deck_carries_it_on_both_output_cards(self) -> None:
        deck = write_frame_deck(
            _beam(),
            STEEL,
            _clamped_end(),
            RHS,
            forces=_tip_force(_beam()),
        )

        assert f"*NODE FILE, {OUTPUT_REQUEST}" in deck
        assert f"*EL FILE, {OUTPUT_REQUEST}" in deck

    def test_the_solid_deck_is_untouched_by_any_of_this(self) -> None:
        """The whole point of a strategy module is that the path that already
        worked keeps working. A regression here would be silent on every solid
        model in the product."""
        from app.solve.calculix import write_deck
        from app.solve.types import ForceLoad, LoadCase

        case = LoadCase(
            material=STEEL,
            fixtures=[Fixture(where=FaceSelector(type="face", axis="z", side="min"))],
            loads=[
                ForceLoad(
                    where=FaceSelector(type="face", axis="z", side="max"),
                    force_n=(0.0, 0.0, 1000.0),
                )
            ],
        )
        deck = write_deck(box_mesh((20.0, 20.0, 60.0), divisions=(2, 2, 3)), case)

        assert OUTPUT_REQUEST not in deck
        assert "*NODE FILE" in deck


def _tip_force(mesh: BeamMesh) -> np.ndarray:
    forces = np.zeros(3 * mesh.node_count)
    forces[3 * (mesh.node_count - 1) + 2] = -1000.0
    return forces


class TestAClampOnABeamIsBuiltInAndNotPinned:
    """Quiet failure 2. A pinned cantilever is not a slightly softer clamped one;
    with a single element it is a mechanism, and with several it deflects by a
    factor more.
    """

    def test_a_clamp_on_a_solid_holds_three(self) -> None:
        fixture = Fixture(where=BoxSelector(type="box", min=(0, 0, 0), max=(1, 1, 1)))

        assert local_dofs(fixture, rotations=False) == (0, 1, 2)

    def test_a_clamp_on_a_beam_holds_all_six(self) -> None:
        fixture = Fixture(
            where=BoxSelector(type="box", min=(0, 0, 0), max=(1, 1, 1)), kind="clamp"
        )

        assert local_dofs(fixture, rotations=True) == (0, 1, 2, 3, 4, 5)

    def test_a_custom_fixture_naming_three_letters_is_a_pin_and_stays_one(self) -> None:
        """`dofs` is a list of *letters*, and letters are translations. Upgrading
        it silently would make two spellings of the same fixture solve
        differently with nothing to read — and the caller who wanted a built-in
        end has the word `clamp`."""
        pin = Fixture(
            where=BoxSelector(type="box", min=(0, 0, 0), max=(1, 1, 1)),
            dofs=["x", "y", "z"],
        )

        assert pin.kind == "custom"
        assert local_dofs(pin, rotations=True) == (0, 1, 2)

    def test_a_roller_holds_no_rotation(self) -> None:
        """A roller is named for a support that does not resist a moment."""
        roller = Fixture(
            where=BoxSelector(type="box", min=(0, 0, 0), max=(1, 1, 1)),
            kind="roller",
            normal="z",
        )

        assert local_dofs(roller, rotations=True) == (2,)

    def test_symmetry_holds_the_two_rotations_the_plane_forbids(self) -> None:
        """Material crossing a plane of symmetry arrives perpendicular to it. A
        symmetry condition written as a roller lets the section rotate through
        the plane, which is exactly what symmetry forbids — and it is the half
        model that then reports the wrong stiffness."""
        symmetry = Fixture(
            where=BoxSelector(type="box", min=(0, 0, 0), max=(1, 1, 1)),
            kind="symmetry",
            normal="x",
        )

        # Translation along x, and rotations about y and z.
        assert local_dofs(symmetry, rotations=True) == (0, 4, 5)

    def test_the_deck_writes_degrees_of_freedom_four_to_six_for_a_clamped_beam(self) -> None:
        _lines, boundary = write_frame_model(_beam(), STEEL, _clamped_end(), RHS)
        held = {int(row.split(",")[1]) for row in boundary}

        assert held == {1, 2, 3, 4, 5, 6}

    def test_a_solid_deck_never_mentions_a_rotational_degree_of_freedom(self) -> None:
        """A solid element has none to hold, so naming one would be meaningless
        rather than conservative."""
        from app.solve.calculix.deck import write_model

        mesh = box_mesh((20.0, 20.0, 20.0), divisions=(1, 1, 1))
        _lines, boundary = write_model(mesh, STEEL, _clamped_end())
        held = {int(row.split(",")[1]) for row in boundary}

        assert held == {1, 2, 3}


class TestARestraintCheckThatUnderstandsRotations:
    """Why the six-degree-of-freedom form of `check_restraints` had to exist.

    Both of these are *correct models* that the three-degree-of-freedom check
    refuses, and refusing a correct model is as bad as accepting a wrong one —
    the engineer goes looking for a fault that is not there.
    """

    def test_a_beam_clamped_at_one_end_is_properly_restrained(self) -> None:
        report = check_restraints(
            _beam(), _clamped_end(), dofs_per_node=STRUCTURAL_DOFS
        )

        assert report.restrained
        assert report.rank == 6

    def test_the_three_degree_of_freedom_check_would_have_refused_it(self) -> None:
        """A straight beam is collinear, so its rotation about its own axis moves
        no node at all — the three-DOF form calls the mesh degenerate. That is
        the right answer for a solid and the wrong one for a member."""
        with pytest.raises(SolverError, match="no three-dimensional extent"):
            check_restraints(_beam(), _clamped_end(), dofs_per_node=SOLID_DOFS)

    def test_a_beam_pinned_at_one_end_is_refused(self) -> None:
        """The check has to still bite. A pin leaves the member free to spin."""
        pinned = [
            Fixture(
                where=FaceSelector(type="face", axis="x", side="min"),
                dofs=["x", "y", "z"],
            )
        ]
        report = check_restraints(_beam(), pinned, dofs_per_node=STRUCTURAL_DOFS)

        assert not report.restrained
        assert "under-constrained" in report.message()

    def test_a_shell_held_at_one_edge_by_a_clamp_is_restrained(self) -> None:
        report = check_restraints(_shell(), _clamped_end(), dofs_per_node=STRUCTURAL_DOFS)

        assert report.restrained

    def test_a_frame_deck_refuses_an_unrestrained_model(self) -> None:
        loose = [
            Fixture(
                where=FaceSelector(type="face", axis="x", side="min"), dofs=["z"]
            )
        ]

        with pytest.raises(SolverError, match="under-constrained"):
            write_frame_deck(_beam(), STEEL, loose, RHS, forces=_tip_force(_beam()))


class TestTheSectionCardSaysWhatTheMeshDoesNot:
    def test_a_shell_states_its_thickness(self) -> None:
        lines, _boundary = write_frame_model(_shell(), STEEL, _clamped_end(), PLATE)
        deck = "\n".join(lines)

        assert "*SHELL SECTION, ELSET=EALL, MATERIAL=STEEL_1018" in deck
        assert _rows(deck, "*SHELL SECTION") == ["1.5"]

    def test_an_offset_shell_states_the_offset_and_a_centred_one_does_not(self) -> None:
        """Zero is the default and writing it would be noise; a non-zero offset
        moves the part by half a thickness and must be visible."""
        choice = choose_element(_shell())
        centred = section_lines(
            choice, ShellSection(thickness_mm=2.0), element_set="EALL", material_name="S"
        )
        offset = section_lines(
            choice,
            ShellSection(thickness_mm=2.0, offset=-0.5),
            element_set="EALL",
            material_name="S",
        )

        assert "OFFSET" not in centred[0]
        assert "OFFSET=-0.5" in offset[0]

    def test_a_beam_states_its_profile_then_its_orientation_in_that_order(self) -> None:
        """Two rows of floats. Swapping them is not a parse error — it is a
        section of the wrong size pointing the wrong way."""
        lines, _boundary = write_frame_model(_beam(), STEEL, _clamped_end(), RHS)
        deck = "\n".join(lines)

        assert "*BEAM SECTION" in deck
        assert "SECTION=BOX" in deck
        rows = _rows(deck, "*BEAM SECTION")
        assert rows[0] == "60.0, 40.0, 4.0, 4.0, 4.0, 4.0"
        assert rows[1] == "0.0, 1.0, 0.0"

    def test_the_orientation_is_written_as_a_unit_vector(self) -> None:
        """ccx wants direction *cosines*. An unnormalised vector is a direction
        with a magnitude attached, and nothing here would notice."""
        section = BeamSection(profile=RHS.profile, n1=(0.0, 3.0, 4.0))
        lines, _boundary = write_frame_model(_beam(), STEEL, _clamped_end(), section)

        assert _rows("\n".join(lines), "*BEAM SECTION")[1] == "0.0, 0.6, 0.8"

    def test_a_solid_section_has_no_data_line(self) -> None:
        choice = choose_element(box_mesh((1.0, 1.0, 1.0), divisions=(1, 1, 1)))

        assert section_lines(choice, None, element_set="EALL", material_name="S") == [
            "*SOLID SECTION, ELSET=EALL, MATERIAL=S"
        ]

    def test_a_thickness_is_written_at_full_precision(self) -> None:
        """A thickness rounded to six figures is a different part, exactly as a
        node coordinate is."""
        lines = section_lines(
            choose_element(_shell()),
            ShellSection(thickness_mm=1.2345678901234567),
            element_set="EALL",
            material_name="S",
        )

        assert float(lines[1]) == 1.2345678901234567


class TestASectionThatDoesNotBelongIsRefused:
    """Quiet failure 3. Neither of these is a type error at the boundary — both
    are a `Section` — and ccx would complain about the element type rather than
    about the mistake.
    """

    def test_a_beam_section_on_a_shell_mesh_is_refused(self) -> None:
        with pytest.raises(SolverError, match="shell mesh needs a ShellSection"):
            require_section_for(_shell(), RHS)

    def test_a_shell_section_on_a_beam_mesh_is_refused(self) -> None:
        with pytest.raises(SolverError, match="beam mesh needs a BeamSection"):
            require_section_for(_beam(), PLATE)

    def test_the_frame_writer_refuses_before_it_writes_anything(self) -> None:
        with pytest.raises(SolverError, match="needs a BeamSection"):
            write_frame_model(_beam(), STEEL, _clamped_end(), PLATE)

    def test_an_orientation_along_the_member_is_refused(self) -> None:
        """The local 2-direction is `axis x n1`, so an n1 along the axis makes it
        a zero vector. Refused rather than corrected: every direction this could
        pick instead would be as arbitrary as the last."""
        along = BeamSection(profile=RHS.profile, n1=(1.0, 0.0, 0.0))

        with pytest.raises(SolverError, match="no orientation about it"):
            require_orientation(_beam(), along)

    def test_the_refusal_names_which_member_it_lies_along(self) -> None:
        """A frame's members do not all run the same way, and an n1 that suits
        the posts is exactly the one that lies along the header. "Segment 3" is
        what turns the message into something a caller can act on."""
        portal = BeamMesh(
            nodes=np.array(
                [
                    [0.0, 0.0, 0.0],
                    [0.0, 0.0, 760.0],
                    [800.0, 0.0, 760.0],
                    [800.0, 0.0, 0.0],
                ]
            ),
            segments=np.array([[0, 1], [1, 2], [3, 2]]),
        )
        # Fine for the two posts (which run in z), fatal for the header.
        along_the_header = BeamSection(profile=RHS.profile, n1=(1.0, 0.0, 0.0))

        with pytest.raises(SolverError, match="Segment 2"):
            require_orientation(portal, along_the_header)

    def test_an_orientation_across_every_member_is_accepted(self) -> None:
        portal = BeamMesh(
            nodes=np.array(
                [
                    [0.0, 0.0, 0.0],
                    [0.0, 0.0, 760.0],
                    [800.0, 0.0, 760.0],
                    [800.0, 0.0, 0.0],
                ]
            ),
            segments=np.array([[0, 1], [1, 2], [3, 2]]),
        )

        require_orientation(portal, BeamSection(profile=RHS.profile, n1=(0.0, 1.0, 0.0)))


class TestTheFrameDeckAsAWhole:
    def test_the_element_card_names_the_chosen_element(self) -> None:
        deck = write_frame_deck(
            _beam(), STEEL, _clamped_end(), RHS, forces=_tip_force(_beam())
        )

        assert "*ELEMENT, TYPE=B31, ELSET=EALL" in deck

    def test_element_numbering_is_one_based_like_the_solid_path(self) -> None:
        """An off-by-one does not crash; it moves every load and restraint onto
        the neighbouring node and returns a plausible field."""
        rows = element_rows(_beam(segments=3))

        assert [row[0] for row in rows] == [1, 2, 3]
        assert min(min(row[1]) for row in rows) == 1
        assert max(max(row[1]) for row in rows) == _beam(segments=3).node_count

    def test_a_quadratic_shell_writes_its_midside_nodes_after_its_corners(self) -> None:
        mesh = _shell(quadratic=True)
        (_id, connectivity), = element_rows(mesh)

        assert len(connectivity) == 8
        assert connectivity[:4] == [1, 2, 3, 4]
        assert connectivity[4:] == [5, 6, 7, 8]

    def test_the_load_reaches_the_node_the_caller_meant(self) -> None:
        mesh = _beam()
        deck = write_frame_deck(mesh, STEEL, _clamped_end(), RHS, forces=_tip_force(mesh))

        # Node 5 (1-based), degree of freedom 3, -1000 N.
        assert _rows(deck, "*CLOAD") == [f"{mesh.node_count}, 3, -1000.0"]

    def test_a_force_vector_of_the_wrong_length_is_refused(self) -> None:
        """Reshaping it would put loads on the wrong nodes, and every node number
        would still be in range."""
        with pytest.raises(SolverError, match="three translational components"):
            write_frame_deck(
                _beam(), STEEL, _clamped_end(), RHS, forces=np.zeros(7)
            )

    def test_a_temperature_change_reaches_a_frame_deck_too(self) -> None:
        """6.4's cards are not solid-only. A heated frame is the commonest
        thermal-stress problem there is."""
        mesh = _beam()
        deck = write_frame_deck(
            mesh, STEEL, _clamped_end(), RHS, forces=_tip_force(mesh), delta_t_k=60.0
        )

        assert "*INITIAL CONDITIONS, TYPE=TEMPERATURE" in deck
        assert _rows(deck, "*TEMPERATURE") == ["NALL, 60.0"]

    def test_a_frame_deck_refuses_a_material_that_cannot_expand(self) -> None:
        cold = STEEL.model_copy(update={"thermal_expansion_per_k": None})
        mesh = _beam()

        with pytest.raises(SolverError, match="thermal_expansion_per_k"):
            write_frame_deck(
                mesh, cold, _clamped_end(), RHS, forces=_tip_force(mesh), delta_t_k=60.0
            )

    def test_the_deck_is_in_the_order_calculix_reads_it(self) -> None:
        """Model cards before the first `*STEP`, step cards inside it. Getting
        either the wrong side is a parse error at best and an ignored load at
        worst."""
        mesh = _beam()
        deck = write_frame_deck(
            mesh, STEEL, _clamped_end(), RHS, forces=_tip_force(mesh), delta_t_k=10.0
        )
        lines = deck.splitlines()
        step = lines.index("*STEP")

        for model_card in ("*NODE, NSET=NALL", "*ELEMENT, TYPE=B31, ELSET=EALL"):
            assert lines.index(model_card) < step
        assert lines.index("*INITIAL CONDITIONS, TYPE=TEMPERATURE") < step
        for step_card in ("*STATIC", "*BOUNDARY", "*CLOAD", "*TEMPERATURE"):
            assert lines.index(step_card) > step
        assert lines[-1] == "*END STEP"
        assert deck.endswith("\n"), "a deck must end in a newline; ccx's reader wants one"

    def test_the_beam_section_names_the_same_element_set_the_elements_are_in(self) -> None:
        """An element written without its section is refused by ccx — the one
        loud failure in this module — and a section naming a set that does not
        exist is the same mistake spelled differently."""
        deck = write_frame_deck(
            _beam(), STEEL, _clamped_end(), RHS, forces=_tip_force(_beam())
        )

        assert "*ELEMENT, TYPE=B31, ELSET=EALL" in deck
        assert "*BEAM SECTION, ELSET=EALL, MATERIAL=STEEL_1018, SECTION=BOX" in deck

    def test_the_density_conversion_is_the_solid_path_s_own(self) -> None:
        """One material writer, so a frame and a solid cannot disagree about what
        steel weighs. Out by 1e12 is what the alternative looks like."""
        deck = write_frame_deck(
            _beam(), STEEL, _clamped_end(), RHS, forces=_tip_force(_beam())
        )

        assert float(_rows(deck, "*DENSITY")[0]) == pytest.approx(7.87e-9, rel=1e-12)
