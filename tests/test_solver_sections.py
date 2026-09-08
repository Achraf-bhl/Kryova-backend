"""Shell and beam cross sections — master plan 6.3.

Offline and instant: a section is arithmetic over four numbers, so nothing here
needs `ccx`, a database or a mesh.

**The section properties are checked against numerical integration, not against
the formula they were written from.** `area = w * h` asserted equal to `w * h` is
a test of nothing; every one of the closed forms below is instead compared with a
sum over a fine grid of points inside the real section, which is a genuinely
different derivation and is the only kind that can catch the two mistakes that
actually happen: a second moment about the wrong axis, and a hollow section whose
bore was subtracted the wrong way round.

The one that would ship a wrong number quietly is the axis swap. An RHS 60x40
has second moments of 178,005 and 345,045 mm^4 — a factor of 1.94 — and both are
real numbers for that section. Getting them the wrong way round makes a post
that deflects twice as far as reported, with nothing in the output looking odd.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from pydantic import ValidationError

from app.solve.sections import (
    CALCULIX_SECTION_NAMES,
    BeamSection,
    BoxProfile,
    CircularProfile,
    PipeProfile,
    RectangularProfile,
    ShellSection,
    normalise_n1,
)
from app.solve.types import SolverError

#: Cells across the widest dimension of the section when integrating. 2000 puts
#: the discretisation error on a curved boundary near 1e-4 relative, which is
#: three orders below any mistake this is looking for and cheap in numpy.
_GRID = 2000


def _integrate(inside, half_1: float, half_2: float) -> tuple[float, float, float]:
    """Area and the two second moments of a section, by summing over a grid.

    `inside(u, v)` is a boolean mask over coordinates measured from the section's
    centroid, `u` along axis 1 and `v` along axis 2. Returns
    `(area, I about 1, I about 2)`.

    Independent of everything in `sections.py`: it knows only what shape the
    section is, and computes `A = sum dA`, `I_1 = sum v^2 dA`, `I_2 = sum u^2 dA`
    from that. The definition of "about axis 1" it encodes — that bending about
    1 is resisted by material offset in the 2-direction — is the convention under
    test, so a property that returned the other one fails here.
    """
    step_1 = 2.0 * half_1 / _GRID
    step_2 = 2.0 * half_2 / _GRID
    u = (np.arange(_GRID) + 0.5) * step_1 - half_1
    v = (np.arange(_GRID) + 0.5) * step_2 - half_2
    grid_u, grid_v = np.meshgrid(u, v, indexing="ij")

    mask = inside(grid_u, grid_v)
    cell = step_1 * step_2
    area = float(mask.sum()) * cell
    second_about_1 = float((grid_v[mask] ** 2).sum()) * cell
    second_about_2 = float((grid_u[mask] ** 2).sum()) * cell
    return area, second_about_1, second_about_2


def _assert_matches_integration(profile, inside, half_1: float, half_2: float) -> None:
    area, about_1, about_2 = _integrate(inside, half_1, half_2)

    assert profile.area_mm2 == pytest.approx(area, rel=2e-3)
    assert profile.second_moment_about_1_mm4 == pytest.approx(about_1, rel=2e-3)
    assert profile.second_moment_about_2_mm4 == pytest.approx(about_2, rel=2e-3)


class TestASectionReportsTheAreaItReallyHas:
    def test_a_rectangle_matches_integration_over_its_own_outline(self) -> None:
        profile = RectangularProfile(width_mm=60.0, height_mm=40.0)

        _assert_matches_integration(
            profile,
            lambda u, v: np.ones_like(u, dtype=bool),
            half_1=30.0,
            half_2=20.0,
        )

    def test_a_circle_matches_integration_over_its_own_outline(self) -> None:
        profile = CircularProfile(radius_mm=25.0)

        _assert_matches_integration(
            profile,
            lambda u, v: u**2 + v**2 <= 25.0**2,
            half_1=25.0,
            half_2=25.0,
        )

    def test_a_pipe_matches_integration_over_the_wall_only(self) -> None:
        """The bore has to come *out*. A pipe whose inner radius was added rather
        than subtracted has more material than the solid bar it is cut from, and
        nothing in the number looks wrong until it is weighed."""
        profile = PipeProfile(radius_mm=25.0, wall_mm=3.0)

        radius_sq = 25.0**2
        bore_sq = 22.0**2
        _assert_matches_integration(
            profile,
            lambda u, v: (u**2 + v**2 <= radius_sq) & (u**2 + v**2 >= bore_sq),
            half_1=25.0,
            half_2=25.0,
        )

    def test_an_rhs_matches_integration_over_the_wall_only(self) -> None:
        """RHS 60x40x4 — mission M2's own member, which is why this one is here
        rather than a rounder number."""
        profile = BoxProfile(width_mm=60.0, height_mm=40.0, wall_mm=4.0)

        _assert_matches_integration(
            profile,
            lambda u, v: ~((np.abs(u) <= 26.0) & (np.abs(v) <= 16.0)),
            half_1=30.0,
            half_2=20.0,
        )


class TestWhichWayRoundTheSecondMomentsGo:
    """The mistake that halves or doubles a deflection with nothing to see.

    "About axis 1" means bending *in* the 2-direction, so a section that is deep
    in 2 is stiff about 1. Said as a comparison rather than as a number, because
    a number can be transcribed from the implementation and an inequality cannot.
    """

    def test_a_section_deep_in_two_is_stiffer_about_one(self) -> None:
        wide = RectangularProfile(width_mm=60.0, height_mm=40.0)

        assert wide.second_moment_about_1_mm4 < wide.second_moment_about_2_mm4

    def test_turning_the_section_a_quarter_turn_swaps_the_two(self) -> None:
        """The same member laid the other way. Everything else about it is
        identical, so a property that ignored the orientation would pass every
        other test in this file and fail this one."""
        upright = RectangularProfile(width_mm=40.0, height_mm=60.0)
        flat = RectangularProfile(width_mm=60.0, height_mm=40.0)

        assert upright.area_mm2 == pytest.approx(flat.area_mm2)
        assert upright.second_moment_about_1_mm4 == pytest.approx(
            flat.second_moment_about_2_mm4
        )
        assert upright.second_moment_about_2_mm4 == pytest.approx(
            flat.second_moment_about_1_mm4
        )

    def test_a_circle_is_the_same_about_both(self) -> None:
        profile = CircularProfile(radius_mm=12.0)

        assert profile.second_moment_about_1_mm4 == profile.second_moment_about_2_mm4

    def test_an_rhs_is_stiffer_about_its_long_dimension_than_a_square_of_the_same_mass(
        self,
    ) -> None:
        """Why anybody specifies an RHS rather than a bar of the same weight.

        Included because it is the claim the section vocabulary exists to
        support, and because a hollow section whose bore was mishandled fails it
        in the obvious direction.
        """
        hollow = BoxProfile(width_mm=60.0, height_mm=40.0, wall_mm=4.0)
        # A solid bar of the same cross-sectional area, kept square so neither
        # axis is favoured by the comparison.
        side = math.sqrt(hollow.area_mm2)
        solid = RectangularProfile(width_mm=side, height_mm=side)

        assert hollow.area_mm2 == pytest.approx(solid.area_mm2)
        assert hollow.second_moment_about_2_mm4 > 2.0 * solid.second_moment_about_2_mm4


class TestTheTorsionConstantIsStatedOrDeclined:
    def test_a_circle_reports_its_polar_second_moment(self) -> None:
        """The one section where J and the polar second moment coincide."""
        profile = CircularProfile(radius_mm=25.0)

        polar = profile.second_moment_about_1_mm4 + profile.second_moment_about_2_mm4
        assert profile.torsion_constant_mm4 == pytest.approx(polar)

    def test_a_pipe_reports_its_polar_second_moment(self) -> None:
        profile = PipeProfile(radius_mm=25.0, wall_mm=3.0)

        polar = profile.second_moment_about_1_mm4 + profile.second_moment_about_2_mm4
        assert profile.torsion_constant_mm4 == pytest.approx(polar)

    def test_a_solid_rectangle_declines_rather_than_guessing(self) -> None:
        """A zero would read as "no torsional stiffness" and the polar second
        moment would overstate a 60x10 bar's by about three times. Neither is
        better than saying so."""
        assert RectangularProfile(width_mm=60.0, height_mm=10.0).torsion_constant_mm4 is None

    def test_a_closed_box_uses_bredt_and_not_the_polar_second_moment(self) -> None:
        """Bredt's `4 A_m^2 t / s` is a different number from `I_1 + I_2`, and a
        box quietly reporting the polar moment would overstate it."""
        profile = BoxProfile(width_mm=60.0, height_mm=40.0, wall_mm=4.0)
        torsion = profile.torsion_constant_mm4
        assert torsion is not None

        enclosed = (60.0 - 4.0) * (40.0 - 4.0)
        perimeter = 2.0 * ((60.0 - 4.0) + (40.0 - 4.0))
        assert torsion == pytest.approx(4.0 * enclosed**2 * 4.0 / perimeter)
        polar = profile.second_moment_about_1_mm4 + profile.second_moment_about_2_mm4
        assert torsion != pytest.approx(polar)


class TestASectionThatCannotExistIsRefused:
    def test_a_wall_thicker_than_the_radius_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="does not fit inside"):
            PipeProfile(radius_mm=5.0, wall_mm=5.0)

    def test_two_walls_that_meet_in_the_middle_are_refused(self) -> None:
        """A 40 mm deep box with 20 mm walls is a solid bar described as a tube,
        and its area formula returns the right number for the wrong reason. A
        21 mm wall returns a *negative* bore area, which reads as a section
        lighter than solid."""
        with pytest.raises(ValidationError, match="solid or inside out"):
            BoxProfile(width_mm=60.0, height_mm=40.0, wall_mm=20.0)

    def test_a_zero_thickness_shell_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ShellSection(thickness_mm=0.0)

    def test_an_offset_past_the_face_is_refused(self) -> None:
        """The offset is a fraction of the thickness, so beyond +-0.5 the mesh is
        outside the material it is supposed to represent."""
        with pytest.raises(ValidationError):
            ShellSection(thickness_mm=2.0, offset=0.75)

    def test_a_beam_with_no_orientation_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="no direction"):
            BeamSection(profile=CircularProfile(radius_mm=5.0), n1=(0.0, 0.0, 0.0))

    def test_normalise_refuses_a_zero_vector_rather_than_dividing_by_it(self) -> None:
        with pytest.raises(SolverError, match="no direction"):
            normalise_n1((0.0, 0.0, 0.0))


class TestWhatCalculixIsTold:
    """The data lines, which are where a transcription error lands.

    None of these is a parse error: every one is a row of floats that ccx reads
    happily and interprets as a different section.
    """

    def test_a_rectangle_states_axis_one_first(self) -> None:
        profile = RectangularProfile(width_mm=60.0, height_mm=40.0)

        assert profile.calculix_data == (60.0, 40.0)

    def test_a_pipe_states_the_outer_radius_then_the_wall(self) -> None:
        assert PipeProfile(radius_mm=25.0, wall_mm=3.0).calculix_data == (25.0, 3.0)

    def test_a_box_states_four_walls_and_not_one(self) -> None:
        """An omitted field in a fixed-format reader is a zero, and a zero wall
        is a section with no material in it."""
        data = BoxProfile(width_mm=60.0, height_mm=40.0, wall_mm=4.0).calculix_data

        assert data == (60.0, 40.0, 4.0, 4.0, 4.0, 4.0)

    def test_a_shell_states_its_thickness_and_nothing_else(self) -> None:
        assert ShellSection(thickness_mm=1.5).calculix_data == (1.5,)

    def test_every_profile_has_a_calculix_section_name(self) -> None:
        """A profile added without deciding what ccx is to be told it is would
        raise a KeyError at deck-writing time, which is the last moment anybody
        wants to find out."""
        profiles = [
            RectangularProfile(width_mm=1.0, height_mm=1.0),
            CircularProfile(radius_mm=1.0),
            PipeProfile(radius_mm=2.0, wall_mm=1.0),
            BoxProfile(width_mm=10.0, height_mm=10.0, wall_mm=1.0),
        ]

        for profile in profiles:
            section = BeamSection(profile=profile, n1=(0.0, 1.0, 0.0))
            assert section.calculix_section_name == CALCULIX_SECTION_NAMES[profile.type]

    def test_the_names_cover_the_profiles_and_nothing_else(self) -> None:
        declared = {
            RectangularProfile.model_fields["type"].default,
            CircularProfile.model_fields["type"].default,
            PipeProfile.model_fields["type"].default,
            BoxProfile.model_fields["type"].default,
        }

        assert set(CALCULIX_SECTION_NAMES) == declared


class TestMassLeavesInKilograms:
    """`mm^3 * kg/m^3` is not kilograms, and the factor is 1e-9.

    The rest of the codebase reports mass in kilograms; a section that reported
    it in some other unit would be believed, because 736 mm^2 of steel a metre
    long is 5.8 kg and a thousand times that is not obviously absurd for a
    machine.
    """

    def test_a_metre_of_rhs_weighs_what_it_weighs(self) -> None:
        section = BeamSection(
            profile=BoxProfile(width_mm=60.0, height_mm=40.0, wall_mm=4.0),
            n1=(0.0, 1.0, 0.0),
        )

        # 60*40 - 52*32 = 736 mm^2, computed here rather than read off the
        # profile so the mass is not checked against its own input.
        assert section.area_mm2 == pytest.approx(736.0)
        assert section.mass_kg(1000.0, 7850.0) == pytest.approx(736.0 * 1000.0 * 7.85e-6)

    def test_mass_scales_with_length(self) -> None:
        section = BeamSection(profile=CircularProfile(radius_mm=10.0), n1=(1.0, 0.0, 0.0))

        assert section.mass_kg(500.0, 7850.0) == pytest.approx(
            0.5 * section.mass_kg(1000.0, 7850.0)
        )
