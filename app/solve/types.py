"""Load case definition and result types.

Units are the self-consistent mm-N-MPa system: lengths in mm, forces in N,
moduli and stresses in MPa. Displacements come back in mm. Nothing in this
codebase converts, and nothing should start.

**Backwards compatibility is deliberate here.** `Load` was a single shape —
a region and a force vector — and is now a discriminated union of six. A stored
load case written before that change has no `type` field, so `_tagged_force`
supplies one: an untagged load is a force load, which is what it was when it was
written. Saved simulations must keep re-solving to the same answer, and a
migration that silently reinterpreted them would be the worst possible way to
find out otherwise.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

#: Standard gravity in this codebase's units: mm/s².
STANDARD_GRAVITY_MM_S2 = 9806.65


class Material(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    youngs_modulus_mpa: float = Field(gt=0)
    poissons_ratio: float = Field(gt=-1.0, lt=0.5)
    yield_strength_mpa: float = Field(gt=0)
    density_kg_m3: float = Field(gt=0)
    #: Coefficient of linear thermal expansion, per kelvin. Optional because a
    #: static or modal run never touches it, and a caller supplying a custom
    #: material for a stress check should not have to look one up; the thermal
    #: solver refuses by name when it is missing rather than assuming a value.
    #: Per kelvin, not per micro-kelvin: 23.6e-6 for aluminium, not 23.6.
    thermal_expansion_per_k: float | None = Field(default=None, gt=0, lt=1e-2)


# -- selectors ---------------------------------------------------------------
#
# How a load or restraint names the region it applies to. Every one of these
# resolves to a set of node indices; what differs is how the region is described
# to someone who cannot see the mesh.


class FaceSelector(BaseModel):
    """The extreme face of the part along one axis, e.g. 'the bottom'."""

    type: Literal["face"] = "face"
    axis: Literal["x", "y", "z"]
    side: Literal["min", "max"]
    tolerance: float = Field(
        default=0.01, gt=0, le=1.0, description="Band depth as a fraction of the bbox size"
    )


class BoxSelector(BaseModel):
    """Every node inside an axis-aligned box, in mm."""

    type: Literal["box"] = "box"
    min: tuple[float, float, float]
    max: tuple[float, float, float]


class CylinderSelector(BaseModel):
    """Every node within a hollow cylinder — the wall of a hole, or a shaft.

    This is what a bolt hole or a bearing seat actually is, and picking one with
    a box catches the material around it as well. `radius_tolerance` is a band
    either side of `radius`, so a hole of nominally 5 mm radius is selected with
    `radius=5` rather than by finding its exact meshed radius.
    """

    type: Literal["cylinder"] = "cylinder"
    axis_point: tuple[float, float, float]
    axis_direction: tuple[float, float, float]
    radius: float = Field(gt=0)
    radius_tolerance: float = Field(default=0.5, gt=0)
    #: Extent along the axis from `axis_point`, in mm. None selects the full length.
    length: float | None = Field(default=None, gt=0)


class SphereSelector(BaseModel):
    """Every node within a sphere. Useful for a point-ish load or support."""

    type: Literal["sphere"] = "sphere"
    centre: tuple[float, float, float]
    radius: float = Field(gt=0)


class BodySelector(BaseModel):
    """Every node in the mesh.

    Only meaningful for the body loads — gravity and centrifugal — which act on
    all the material rather than on a surface. Using it for a surface force
    would spread the load through the interior, which is not a load anyone means.
    """

    type: Literal["body"] = "body"


Selector = Annotated[
    FaceSelector | BoxSelector | CylinderSelector | SphereSelector | BodySelector,
    Field(discriminator="type"),
]


# -- restraints --------------------------------------------------------------


class Fixture(BaseModel):
    """A restrained region.

    By default all three translations are held (an encastre clamp). Restraining
    a subset makes a roller or a symmetry plane -- e.g. `dofs=["z"]` on a flat
    face lets the part slide in that plane while holding it out of plane, which
    is how a real support usually behaves and how symmetry is exploited to cut
    a model down.

    `kind` is a name for the common combinations. It sets `dofs` when `dofs` is
    not given explicitly, so the two can never contradict each other: naming
    both is refused rather than silently preferring one.
    """

    where: Selector
    dofs: list[Literal["x", "y", "z"]] | None = Field(default=None, min_length=1)
    kind: Literal["clamp", "roller", "slider", "symmetry", "custom"] = "custom"
    #: Which axis a roller, slider or symmetry restraint is normal to.
    normal: Literal["x", "y", "z"] | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _resolve_dofs(self) -> "Fixture":
        """Fill `dofs` from `kind`, and refuse a `dofs` that contradicts it.

        Written to be **idempotent**: validating an already-validated Fixture
        must succeed and change nothing. Pydantic re-validates a model instance
        in some nesting configurations, and by then this validator has itself
        filled `dofs` in — so a rule phrased as "kind and dofs may not both be
        present" rejects the fixture it just built. Phrasing it as "they may not
        *disagree*" catches the real mistake and survives revalidation.
        """
        implied = self._implied_dofs()
        if implied is None:
            # kind == "custom": dofs as given, or all three.
            if self.dofs is None:
                object.__setattr__(self, "dofs", ["x", "y", "z"])
            return self

        if self.dofs is not None and sorted(self.dofs) != sorted(implied):
            raise ValueError(
                f"A {self.kind!r} fixture holds {implied}, but dofs={self.dofs} was "
                "given as well. Give `kind` or `dofs`, not two answers that disagree."
            )
        object.__setattr__(self, "dofs", implied)
        return self

    @property
    def held(self) -> list[Literal["x", "y", "z"]]:
        """The degrees of freedom this fixture holds. Never None.

        `dofs` is optional only at the boundary — None is how "not given" is
        spelled in a request, and `_resolve_dofs` has replaced it before any
        solver sees the fixture. Assembly reads this instead, so the invariant
        is written down once here rather than assumed in three separate places
        that a type checker was right to object to.
        """
        if self.dofs is None:  # only reachable via model_construct()
            return ["x", "y", "z"]
        return self.dofs

    def _implied_dofs(self) -> list[str] | None:
        """The degrees of freedom `kind` implies, or None when it implies none."""
        if self.kind == "custom":
            return None
        if self.kind == "clamp":
            return ["x", "y", "z"]
        if self.normal is None:
            raise ValueError(
                f"A {self.kind!r} fixture needs `normal` — the axis it holds. A roller "
                "on the bottom face is normal to z."
            )
        if self.kind in {"roller", "symmetry"}:
            # Holds only the out-of-plane translation; the face may slide freely
            # within its own plane. Symmetry is the same restraint, named for
            # what it means rather than for what it does.
            return [self.normal]
        # slider: free along `normal`, held in the other two.
        return [axis for axis in ("x", "y", "z") if axis != self.normal]


# -- loads -------------------------------------------------------------------


class ForceLoad(BaseModel):
    """A force spread over a region, given as a total force vector in N.

    The total is distributed over the selected surface by tributary area, so
    refining the mesh does not change the applied load.
    """

    type: Literal["force"] = "force"
    where: Selector
    force_n: tuple[float, float, float]
    name: str | None = None


class PressureLoad(BaseModel):
    """A uniform pressure on a surface, acting along its own outward normal.

    Different from a force in the way that matters: a force is a fixed total
    however large the face is, a pressure scales with the area it acts on. A
    hydraulic seat, a vacuum, a wind load are all pressures, and modelling them
    as a force means the answer changes when the face is resized.

    Positive pushes inward (compressive, the usual sense); negative pulls.
    """

    type: Literal["pressure"] = "pressure"
    where: Selector
    pressure_mpa: float
    name: str | None = None


class MomentLoad(BaseModel):
    """A moment about an axis through the region's centroid, in N·mm.

    Applied as a statically equivalent set of nodal forces — each node gets a
    tangential force proportional to its distance from the axis. That is the
    correct resultant and it is *not* the same as a rigid coupling: the surface
    is free to deform, so stresses very close to the loaded region are softer
    than a real bolted joint would give. Away from it, Saint-Venant applies and
    the answer is right.
    """

    type: Literal["moment"] = "moment"
    where: Selector
    moment_n_mm: tuple[float, float, float]
    name: str | None = None


class BearingLoad(BaseModel):
    """A force on a cylindrical face, distributed as a bearing pressure.

    A pin in a hole does not push uniformly: it bears on the half of the bore
    facing the load, with a roughly cosine distribution that peaks where the
    load points and falls to zero at 90°. Modelling it as a uniform force on the
    whole bore understates the peak stress at the contact, which is exactly
    where a lug fails.
    """

    type: Literal["bearing"] = "bearing"
    where: Selector
    force_n: tuple[float, float, float]
    #: Cosine exponent. 1.0 is the classical distribution; higher concentrates it.
    distribution: float = Field(default=1.0, ge=0.5, le=3.0)
    name: str | None = None


class GravityLoad(BaseModel):
    """Self-weight, or any uniform acceleration of the whole body.

    `direction` need not be normalised. The default magnitude is standard
    gravity, so a part that only has to hold itself up needs nothing but a
    direction.
    """

    type: Literal["gravity"] = "gravity"
    direction: tuple[float, float, float] = (0.0, 0.0, -1.0)
    magnitude_mm_s2: float = Field(default=STANDARD_GRAVITY_MM_S2, gt=0)
    name: str | None = None


class CentrifugalLoad(BaseModel):
    """Rotation about an axis, as a body load.

    The force on each element is ρ·ω²·r away from the axis. This is what sizes
    a rotor, an impeller or a flywheel, and it is quadratic in speed — doubling
    the rpm quadruples the load, which is the thing people get wrong when
    scaling a design up.
    """

    type: Literal["centrifugal"] = "centrifugal"
    axis_point: tuple[float, float, float]
    axis_direction: tuple[float, float, float]
    rpm: float = Field(gt=0)
    name: str | None = None


def _tagged_force(value: Any) -> Any:
    """Treat an untagged load as a force load.

    Load cases stored before `Load` became a union have `{where, force_n}` and
    no `type`. They were force loads then and must stay force loads now: a saved
    simulation that re-solves to a different answer after a deploy is a far
    worse outcome than a slightly less tidy schema.
    """
    if isinstance(value, dict) and "type" not in value:
        return {**value, "type": "force"}
    return value


Load = Annotated[
    Annotated[
        ForceLoad | PressureLoad | MomentLoad | BearingLoad | GravityLoad | CentrifugalLoad,
        Field(discriminator="type"),
    ],
    BeforeValidator(_tagged_force),
]

#: Loads that act on the whole body rather than on a selected region. They carry
#: no `where`, and the solver integrates them over every element.
BODY_LOADS = (GravityLoad, CentrifugalLoad)


class LoadCase(BaseModel):
    name: str = "Load case"
    material: Material
    fixtures: list[Fixture] = Field(min_length=1)
    loads: list[Load] = Field(min_length=1)
    #: Uniform temperature change over the whole part, in kelvin. None is the
    #: isothermal case and costs nothing -- no thermal term is assembled. A
    #: value needs `thermal_expansion_per_k` on the material, and the solver
    #: says so by name rather than assuming one. See `app/solve/thermal.py`.
    delta_t_k: float | None = Field(default=None, ge=-2000.0, le=5000.0)


class ModalCase(BaseModel):
    """What to solve for a natural-frequency run.

    No loads, deliberately. A modal analysis asks what the structure does when
    nothing pushes it, so a `LoadCase` with its mandatory loads is the wrong
    shape -- passing one would invite the caller to believe the forces
    influenced the answer.

    Fixtures are optional here, and that is also deliberate. A free-free modal
    run is a real and useful analysis: it is how you check a part in isolation,
    and its first six modes come out at zero because they are the rigid-body
    motions. `LoadCase` requires at least one fixture because a static solve
    without one is singular; this one does not.
    """

    name: str = "Modal analysis"
    material: Material
    fixtures: list[Fixture] = Field(default_factory=list)
    modes: int = Field(default=6, ge=1, le=100)


class ModalResult(BaseModel):
    """Summary of a modal run. Mode shapes are large and stay out of the DB."""

    frequencies_hz: list[float]
    rigid_body_modes: int
    mass_kg: float
    volume_mm3: float
    node_count: int
    element_count: int
    solve_seconds: float
    warnings: list[str] = Field(default_factory=list)

    @property
    def fundamental_hz(self) -> float | None:
        """The lowest non-rigid frequency -- the one an engineer means by "the"
        natural frequency. None when every mode found was a rigid-body one."""
        elastic = self.frequencies_hz[self.rigid_body_modes :]
        return elastic[0] if elastic else None

    def summary(self) -> dict[str, Any]:
        return self.model_dump() | {"fundamental_hz": self.fundamental_hz}


class BucklingCase(BaseModel):
    """What to solve for a linear buckling run.

    Loads are required and fixtures are required, unlike `ModalCase`: buckling
    is a property of a structure *under load*, and the answer is a multiplier on
    the loads given here. Halve the load and the factor doubles; the buckling
    shape does not change.
    """

    name: str = "Buckling"
    material: Material
    fixtures: list[Fixture] = Field(min_length=1)
    loads: list[Load] = Field(min_length=1)
    modes: int = Field(default=3, ge=1, le=50)


class BucklingResult(BaseModel):
    """Summary of a linear buckling run. Mode shapes stay out of the DB."""

    load_factors: list[float]
    mass_kg: float
    volume_mm3: float
    node_count: int
    element_count: int
    solve_seconds: float
    warnings: list[str] = Field(default_factory=list)

    @property
    def critical_load_factor(self) -> float | None:
        """Multiply the applied loads by this to reach the buckling load.

        Below 1 means the structure buckles under the load as given. None when
        no positive factor was found, which means it does not buckle under this
        load in this direction -- reverse the load and ask again.
        """
        positive = [factor for factor in self.load_factors if factor > 0.0]
        return positive[0] if positive else None

    def summary(self) -> dict[str, Any]:
        return self.model_dump() | {"critical_load_factor": self.critical_load_factor}


class StaticResult(BaseModel):
    """Summary of a linear static run. Full fields stay out of the DB."""

    max_displacement_mm: float
    max_displacement_node: int
    max_von_mises_mpa: float
    max_von_mises_element: int
    factor_of_safety: float
    yields: bool
    mass_kg: float
    volume_mm3: float
    node_count: int
    element_count: int
    solve_seconds: float
    warnings: list[str] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return self.model_dump()


class SolverError(RuntimeError):
    """The model could not be solved as posed."""
