"""Shell and beam cross sections — master plan 6.3.

A frame meshed as solids is a mesh nobody can afford. An RHS 60x40x4 post 760 mm
long is four millimetres of wall in three directions: meshed as tetrahedra with
two elements through the wall it is tens of thousands of elements for one member,
and mission M2's portal has three of them. The same member as a beam is a handful
of elements, and the answer for bending and buckling is the one an engineer would
have computed by hand. So beams and shells are not an optimisation, they are what
makes a machine solvable at all.

**What a section is, and what it deliberately is not.** A section carries the
dimensions that are *missing from the mesh*. A shell mesh is a surface with no
thickness; a beam mesh is a line with no cross section. Everything else — the
material, the loads, the restraints — is the vocabulary `types.py` already has
and this module does not restate.

**Second moments are computed here and also by CalculiX, on purpose.** Handing
`SECTION=RECT` and two dimensions to ccx means ccx derives the area and the
second moments itself; the closed forms below are not what it solves with. They
exist so that a section can be *checked* — against a hand calculation, against a
mass roll-up, against the value the solver reports — and so that a caller sizing
a member has the numbers without running anything. Two derivations of one
quantity is normally a drift risk; here it is the point, because the day they
disagree one of them is wrong and neither is currently checked at all.

**Axis 1 and axis 2, and why they are named rather than called x and y.** A beam
has its own frame: the axis runs along the element, and the cross section is
described in the two directions perpendicular to it. Those two are *not* global
x and y — a diagonal brace has neither — so they are called 1 and 2 throughout,
which is CalculiX's own naming, and the deck states which way 1 points as
direction cosines. `second_moment_about_1_mm4` bends the section *in* the
2-direction, which is the pairing people get backwards; the property names say
"about", so a rectangle 60 wide in 1 and 40 deep in 2 has the *smaller* second
moment about 1.

Where the CalculiX facts come from
----------------------------------
**[M]** CalculiX CrunchiX USER'S MANUAL (Guido Dhondt), keyword sections
`*BEAM SECTION` and `*SHELL SECTION`. There is no `ccx` on the machine this was
written on, so the data-line orders below are read from the manual and have not
been round-tripped through the solver — `app/solve/calculix/steps.py` says the
same about itself and it is worth repeating rather than assuming inherited.
`calculix_data` is one short tuple per profile precisely so that the first real
run corrects one line rather than a scattering of format strings.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.solve.types import SolverError


class ShellSection(BaseModel):
    """The thickness a shell mesh does not carry.

    `offset` is where the meshed surface sits within the thickness, as a fraction
    of it: 0 is the mid-surface, +0.5 puts the mesh on the top face and -0.5 on
    the bottom. It matters more than it looks. A shell meshed off the mid-surface
    with `offset` left at zero is a part built half a thickness away from where
    it was drawn, and for a 1.5 mm cover that is invisible; for a 12 mm plate in
    a weldment it is a 6 mm gap or a 6 mm interference at every joint.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["shell"] = "shell"
    thickness_mm: float = Field(gt=0)
    #: In units of thickness, so it stays right when the thickness changes.
    #: `*SHELL SECTION, OFFSET=` takes the same convention. [M]
    offset: float = Field(default=0.0, ge=-0.5, le=0.5)

    @property
    def calculix_data(self) -> tuple[float, ...]:
        """The `*SHELL SECTION` data line: the thickness, on its own. [M]"""
        return (self.thickness_mm,)


class RectangularProfile(BaseModel):
    """A solid rectangle, `width_mm` along axis 1 and `height_mm` along axis 2."""

    model_config = ConfigDict(frozen=True)

    type: Literal["rect"] = "rect"
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)

    @property
    def area_mm2(self) -> float:
        return self.width_mm * self.height_mm

    @property
    def second_moment_about_1_mm4(self) -> float:
        return self.width_mm * self.height_mm**3 / 12.0

    @property
    def second_moment_about_2_mm4(self) -> float:
        return self.height_mm * self.width_mm**3 / 12.0

    @property
    def torsion_constant_mm4(self) -> float | None:
        """None, and that is the honest answer.

        The torsion constant of a solid rectangle is not a closed form — it is a
        hyperbolic series, usually met as a table of `beta` against the aspect
        ratio. Returning the polar second moment instead is the classic mistake
        and overstates the stiffness of a 60x10 bar by about three times.
        CalculiX computes its own from the same two dimensions, so nothing is
        lost by declining to guess here.
        """
        return None

    @property
    def calculix_data(self) -> tuple[float, ...]:
        """`SECTION=RECT`: the dimension along 1, then the dimension along 2. [M]"""
        return (self.width_mm, self.height_mm)


class CircularProfile(BaseModel):
    """A solid round bar."""

    model_config = ConfigDict(frozen=True)

    type: Literal["circ"] = "circ"
    radius_mm: float = Field(gt=0)

    @property
    def area_mm2(self) -> float:
        return math.pi * self.radius_mm**2

    @property
    def second_moment_about_1_mm4(self) -> float:
        return math.pi * self.radius_mm**4 / 4.0

    @property
    def second_moment_about_2_mm4(self) -> float:
        return self.second_moment_about_1_mm4

    @property
    def torsion_constant_mm4(self) -> float | None:
        """The polar second moment, which for a circle *is* the torsion constant.

        A circle is the one section where the two coincide, because it is the one
        section whose cross sections do not warp. Every other profile here either
        states a different number or declines to state one.
        """
        return math.pi * self.radius_mm**4 / 2.0

    @property
    def calculix_data(self) -> tuple[float, ...]:
        """`SECTION=CIRC`: the radius. [M]"""
        return (self.radius_mm,)


class PipeProfile(BaseModel):
    """A round tube: outer radius and wall thickness, CHS in a frame."""

    model_config = ConfigDict(frozen=True)

    type: Literal["pipe"] = "pipe"
    radius_mm: float = Field(gt=0)
    wall_mm: float = Field(gt=0)

    @model_validator(mode="after")
    def _wall_fits_inside(self) -> "PipeProfile":
        if self.wall_mm >= self.radius_mm:
            raise ValueError(
                f"A wall of {self.wall_mm} mm does not fit inside a radius of "
                f"{self.radius_mm} mm — the bore would be zero or negative. Give a "
                "wall thinner than the radius, or a solid bar as a 'circ' profile."
            )
        return self

    @property
    def inner_radius_mm(self) -> float:
        return self.radius_mm - self.wall_mm

    @property
    def area_mm2(self) -> float:
        return math.pi * (self.radius_mm**2 - self.inner_radius_mm**2)

    @property
    def second_moment_about_1_mm4(self) -> float:
        return math.pi * (self.radius_mm**4 - self.inner_radius_mm**4) / 4.0

    @property
    def second_moment_about_2_mm4(self) -> float:
        return self.second_moment_about_1_mm4

    @property
    def torsion_constant_mm4(self) -> float | None:
        """Polar second moment again, exact for the same reason a solid circle is."""
        return math.pi * (self.radius_mm**4 - self.inner_radius_mm**4) / 2.0

    @property
    def calculix_data(self) -> tuple[float, ...]:
        """`SECTION=PIPE`: outer radius, then wall thickness. [M]"""
        return (self.radius_mm, self.wall_mm)


class BoxProfile(BaseModel):
    """A rectangular hollow section — RHS, the workhorse of a welded frame.

    `width_mm` is the outside dimension along axis 1 and `height_mm` along axis
    2, which is how a section is called out: an RHS 60x40x4 laid with its 60 mm
    face in the 1-direction is `width_mm=60, height_mm=40, wall_mm=4`.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["box"] = "box"
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    wall_mm: float = Field(gt=0)

    @model_validator(mode="after")
    def _walls_leave_a_bore(self) -> "BoxProfile":
        if 2.0 * self.wall_mm >= min(self.width_mm, self.height_mm):
            raise ValueError(
                f"Two {self.wall_mm} mm walls do not fit across "
                f"{min(self.width_mm, self.height_mm)} mm — this section is solid or "
                "inside out. Give a thinner wall, or a solid 'rect' profile."
            )
        return self

    @property
    def area_mm2(self) -> float:
        inner_1 = self.width_mm - 2.0 * self.wall_mm
        inner_2 = self.height_mm - 2.0 * self.wall_mm
        return self.width_mm * self.height_mm - inner_1 * inner_2

    @property
    def second_moment_about_1_mm4(self) -> float:
        inner_1 = self.width_mm - 2.0 * self.wall_mm
        inner_2 = self.height_mm - 2.0 * self.wall_mm
        return (self.width_mm * self.height_mm**3 - inner_1 * inner_2**3) / 12.0

    @property
    def second_moment_about_2_mm4(self) -> float:
        inner_1 = self.width_mm - 2.0 * self.wall_mm
        inner_2 = self.height_mm - 2.0 * self.wall_mm
        return (self.height_mm * self.width_mm**3 - inner_2 * inner_1**3) / 12.0

    @property
    def torsion_constant_mm4(self) -> float | None:
        """Bredt's formula for a single-cell closed thin-walled section:
        `J = 4 * A_m^2 * t / s`, with `A_m` the area enclosed by the wall
        mid-line and `s` that mid-line's length.

        Exact only in the thin-wall limit, and it is stated as the closed form it
        is rather than dressed up: for an RHS with a wall under about a tenth of
        the smaller side it is within a few percent, which is the regime a frame
        is built in.
        """
        mid_1 = self.width_mm - self.wall_mm
        mid_2 = self.height_mm - self.wall_mm
        enclosed = mid_1 * mid_2
        perimeter = 2.0 * (mid_1 + mid_2)
        return 4.0 * enclosed**2 * self.wall_mm / perimeter

    @property
    def calculix_data(self) -> tuple[float, ...]:
        """`SECTION=BOX`: a, b, then the four wall thicknesses. [M]

        Four, not one: CalculiX lets each wall differ. A uniform wall is written
        as the same number four times rather than by omitting the fields, because
        an omitted field in a fixed-format reader is a zero, and a zero wall is a
        section with no material in it.
        """
        return (
            self.width_mm,
            self.height_mm,
            self.wall_mm,
            self.wall_mm,
            self.wall_mm,
            self.wall_mm,
        )


BeamProfile = Annotated[
    RectangularProfile | CircularProfile | PipeProfile | BoxProfile,
    Field(discriminator="type"),
]

#: Each profile's `SECTION=` keyword, keyed by its discriminator. Derived from
#: the `type` literal rather than carried on the model, so a new profile cannot
#: be added without deciding what CalculiX is to be told it is.
CALCULIX_SECTION_NAMES: dict[str, str] = {
    "rect": "RECT",
    "circ": "CIRC",
    "pipe": "PIPE",
    "box": "BOX",
}


class BeamSection(BaseModel):
    """A profile, and which way up it is.

    **`n1` is the half people forget, and it silently halves or doubles the
    answer.** A beam mesh is a line: it says where the member runs and nothing
    about its orientation about that line. An RHS 60x40 stood on its 40 mm face
    is two and a quarter times stiffer in bending than the same member laid on
    its 60 mm face, and both are the same mesh with the same section. `n1` is the
    direction the profile's axis 1 points, given in global coordinates, and it is
    required rather than defaulted for exactly that reason.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["beam"] = "beam"
    profile: BeamProfile
    #: Direction cosines of the local 1-direction, in global coordinates. Need
    #: not be normalised — the deck writer normalises before writing.
    n1: tuple[float, float, float]

    @model_validator(mode="after")
    def _n1_has_a_direction(self) -> "BeamSection":
        if not any(component != 0.0 for component in self.n1):
            raise ValueError(
                "n1 = (0, 0, 0) has no direction, so the cross section has no "
                "orientation about the member. Give the direction the profile's "
                "axis 1 points — for a beam running along x with its width across "
                "y, that is (0, 1, 0)."
            )
        return self

    @property
    def calculix_section_name(self) -> str:
        return CALCULIX_SECTION_NAMES[self.profile.type]

    @property
    def area_mm2(self) -> float:
        return self.profile.area_mm2

    def mass_kg(self, length_mm: float, density_kg_m3: float) -> float:
        """What a length of this member weighs.

        Here rather than in the caller because `mm^3 * kg/m^3` is not kilograms
        and the factor is easy to lose: a cubic millimetre is 1e-9 cubic metres.
        The rest of the codebase reports mass in kilograms already, and this
        keeps that true without anything else learning the conversion.
        """
        return self.area_mm2 * float(length_mm) * 1e-9 * float(density_kg_m3)


Section = Annotated[ShellSection | BeamSection, Field(discriminator="type")]


def normalise_n1(n1: tuple[float, float, float]) -> tuple[float, float, float]:
    """`n1` as a unit vector. Refuses a zero vector rather than dividing by zero."""
    length = math.sqrt(sum(component * component for component in n1))
    if length <= 0.0:
        raise SolverError(
            "n1 = (0, 0, 0) has no direction, so the cross section has no orientation "
            "about the member."
        )
    return (n1[0] / length, n1[1] / length, n1[2] / length)


__all__ = [
    "CALCULIX_SECTION_NAMES",
    "BeamProfile",
    "BeamSection",
    "BoxProfile",
    "CircularProfile",
    "PipeProfile",
    "RectangularProfile",
    "Section",
    "ShellSection",
    "normalise_n1",
]
